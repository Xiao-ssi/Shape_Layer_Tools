# -*- coding: utf-8 -*-
"""图层查询索引（SQLite 加速）。

把矢量图层的属性表写入本地 SQLite 文件，之后"搜索数据"直接走数据库查询，
百万行数据可从十几秒降到毫秒级。索引文件只是缓存，不改动原始数据：

- 默认与数据文件同目录：``<数据文件名>.sltidx.sqlite``；
- 若该目录不可写，则放到 QGIS 配置目录的 ``shape_layer_tools_cache`` 下；
- 源文件大小/修改时间/要素数/字段发生变化后，索引自动判定为过期，可重建；
- 删除该 sqlite 文件即可清除索引，不影响任何原始数据。

本模块独立，可单独修改而不影响其他功能模块。
"""

import os
import time
import sqlite3
import hashlib

from qgis.core import Qgis, QgsApplication, QgsFeatureRequest, QgsMessageLog


INDEX_EXT = '.sltidx.sqlite'
_BATCH = 4000

# 云同步目录特征：索引文件可能较大，放在这些目录会频繁触发上传，改放本地缓存
_SYNC_HINTS = ('onedrive', 'dropbox', 'nutstore', '坚果云', 'googledrive',
               'google drive', 'icloud', 'baidunetdisk', '微云', 'weiyun')


def _is_sync_dir(folder):
    low = str(folder).lower()
    return any(h in low for h in _SYNC_HINTS)


class IndexCancelled(Exception):
    """用户取消了索引建立。"""


def _source_file(layer):
    """取图层背后的磁盘文件路径（去掉 |layername=... 之类的子图层参数）。"""
    src = layer.source() or ''
    if not src or src.startswith('memory'):
        return ''
    return src.split('|')[0]


def _cache_dir():
    base = os.path.join(QgsApplication.qgisSettingsDirPath() or '', 'shape_layer_tools_cache')
    try:
        os.makedirs(base, exist_ok=True)
    except OSError:
        base = os.path.join(os.path.expanduser('~'), '.shape_layer_tools_cache')
        try:
            os.makedirs(base, exist_ok=True)
        except OSError:
            return ''
    return base


def _resolve_path(layer):
    """索引文件路径：优先与数据同目录，其次 QGIS 缓存目录。

    数据在 OneDrive 等同步盘目录时不放在数据旁边，避免大文件被反复上传。
    """
    src = _source_file(layer)
    if src:
        folder = os.path.dirname(os.path.abspath(src))
        if (os.path.isdir(folder) and os.access(folder, os.W_OK)
                and not _is_sync_dir(folder)):
            return os.path.splitext(os.path.abspath(src))[0] + INDEX_EXT
    cache = _cache_dir()
    key = hashlib.md5((layer.source() + '|' + layer.name()).encode('utf-8')).hexdigest()[:16]
    return os.path.join(cache, key + INDEX_EXT) if cache else ''


def _escape_like(text):
    """转义 LIKE 通配符，保证关键字里的 % _ 按普通字符匹配。"""
    return (text.replace('\\', '\\\\')
                .replace('%', '\\%')
                .replace('_', '\\_'))


class LayerIndex(object):
    """单图层属性索引。一个图层对应一个 sqlite 文件。"""

    def __init__(self, layer):
        self.layer = layer
        self.layer_id = layer.id()
        self.names = [f.name() for f in layer.fields()]
        self.path = _resolve_path(layer)

    # ---------------- 基本信息 ----------------
    def exists(self):
        return bool(self.path) and os.path.isfile(self.path)

    def _signature(self):
        """数据变化指纹：源文件 + 要素数 + 字段 + 几何类型。"""
        src = _source_file(self.layer)
        mtime = size = 0
        if src and os.path.isfile(src):
            try:
                st = os.stat(src)
                mtime, size = int(st.st_mtime), int(st.st_size)
            except OSError:
                pass
        try:
            count = self.layer.featureCount()
        except Exception:
            count = -1
        return '|'.join([self.layer.name(), src, str(mtime), str(size),
                         str(count), str(self.layer.wkbType()),
                         ','.join(self.names)])

    def _connect(self):
        con = sqlite3.connect(self.path)
        con.execute('PRAGMA cache_size=-65536')
        con.execute('PRAGMA mmap_size=268435456')
        return con

    def meta(self):
        """读取索引元信息；不可用时返回 None。"""
        if not self.exists():
            return None
        con = None
        try:
            con = self._connect()
            rows = dict(con.execute('SELECT k, v FROM meta').fetchall())
            rows['count'] = int(rows.get('count', 0) or 0)
            rows['size_mb'] = self.size_mb()
            rows['path'] = self.path
            return rows
        except (sqlite3.Error, ValueError):
            return None
        finally:
            if con is not None:
                con.close()

    def size_mb(self):
        try:
            return os.path.getsize(self.path) / 1024.0 / 1024.0
        except OSError:
            return 0.0

    def is_ready(self):
        """索引文件存在、结构完整且与当前数据一致。"""
        if not self.exists():
            return False
        con = None
        try:
            con = self._connect()
            row = con.execute("SELECT v FROM meta WHERE k='signature'").fetchone()
            if not row or row[0] != self._signature():
                return False
            cols = con.execute('PRAGMA table_info(rows)').fetchall()
            return len(cols) == len(self.names) + 1
        except sqlite3.Error:
            return False
        finally:
            if con is not None:
                con.close()

    # ---------------- 建立 ----------------
    @staticmethod
    def _no_geometry_flag():
        """取 "不取几何" 标志位。不同 QGIS 版本暴露的位置不一样：
        >=3.36 在 Qgis.FeatureRequestFlag，3.0~3.34 在 QgsFeatureRequest.Flag，
        老版本还有 QgsFeatureRequest.NoGeometry 这种类常量写法。"""
        enum = getattr(Qgis, 'FeatureRequestFlag', None)
        if enum is not None:
            value = getattr(enum, 'NoGeometry', None)
            if value is not None:
                return value
        enum = getattr(QgsFeatureRequest, 'Flag', None)
        if enum is not None:
            value = getattr(enum, 'NoGeometry', None)
            if value is not None:
                return value
        return getattr(QgsFeatureRequest, 'NoGeometry', None)

    def _iter_features(self):
        """返回要素迭代器：尽量只读属性、不构建几何，索引建立速度差别很大。

        QGIS 各版本对"不要几何"的写法不一致，逐级降级，最终仍能正常建库（只是慢些）。"""
        req = QgsFeatureRequest()
        ready = False
        setter = getattr(req, 'setNoGeometry', None)
        if callable(setter):
            # 有的绑定没保留 C++ 的默认参数，需要显式传 True
            for args in ((), (True,)):
                try:
                    setter(*args)
                    ready = True
                    break
                except TypeError:
                    continue
                except Exception as exc:
                    QgsMessageLog.logMessage('setNoGeometry() 不可用: %s' % exc,
                                             'Shape_Layer_Tools', Qgis.MessageLevel.Warning)
                    break
        if not ready:
            flag = self._no_geometry_flag()
            if flag is not None:
                try:
                    req.setFlags(flag)
                    ready = True
                except Exception as exc:
                    QgsMessageLog.logMessage('setFlags(NoGeometry) 不可用: %s' % exc,
                                             'Shape_Layer_Tools', Qgis.MessageLevel.Warning)
        if not ready:
            QgsMessageLog.logMessage('当前 QGIS 版本无法跳过几何读取，索引建立会稍慢。',
                                     'Shape_Layer_Tools', Qgis.MessageLevel.Info)
            return self.layer.getFeatures()
        try:
            return self.layer.getFeatures(req)
        except Exception as exc:
            QgsMessageLog.logMessage('按请求读取要素失败，改为普通遍历: %s' % exc,
                                     'Shape_Layer_Tools', Qgis.MessageLevel.Warning)
            return self.layer.getFeatures()

    def build(self, progress=None, cancelled=None):
        """遍历图层属性写入 sqlite。返回 (行数, 耗时秒)。progress(已处理, 总数)。"""
        if not self.path:
            raise RuntimeError('无法确定索引文件位置。')
        t0 = time.time()
        try:
            con = self._connect()
        except sqlite3.Error as exc:
            raise RuntimeError('无法创建索引文件：%s\n%s' % (self.path, exc))
        n = 0
        try:
            con.execute('PRAGMA journal_mode=OFF')
            con.execute('PRAGMA synchronous=OFF')
            con.execute('PRAGMA temp_store=MEMORY')
            con.execute('DROP TABLE IF EXISTS rows')
            con.execute('DROP TABLE IF EXISTS meta')
            cols = ''.join(', c%d TEXT' % i for i in range(len(self.names)))
            con.execute('CREATE TABLE rows (fid INTEGER PRIMARY KEY%s)' % cols)
            con.execute('CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT)')
            mark = ','.join(['?'] * (len(self.names) + 1))
            sql = 'INSERT INTO rows VALUES (%s)' % mark

            try:
                total = int(self.layer.featureCount() or 0)
            except Exception:
                total = 0
            width = len(self.names) + 1
            buf = []

            def _flush():
                nonlocal n
                if buf:
                    con.executemany(sql, buf)
                    n += len(buf)
                    del buf[:]
                    if progress:
                        progress(n, total)
                    if cancelled and cancelled():
                        raise IndexCancelled()

            for feat in self._iter_features():
                vals = feat.attributes()
                row = [feat.id()]
                row.extend(['' if v is None else str(v) for v in vals])
                if len(row) < width:
                    row.extend([''] * (width - len(row)))
                buf.append(row[:width])
                if len(buf) >= _BATCH:
                    _flush()
            _flush()

            meta = {
                'signature': self._signature(),
                'count': str(n),
                'fields': ','.join(self.names),
                'built_at': time.strftime('%Y-%m-%d %H:%M:%S'),
                'source': _source_file(self.layer),
                'version': '1',
            }
            con.executemany('INSERT INTO meta VALUES (?, ?)', list(meta.items()))
            con.commit()
        except IndexCancelled:
            con.close()
            self.drop()
            raise
        except Exception:
            try:
                con.close()
            finally:
                self.drop()
            raise
        con.close()
        return n, time.time() - t0

    # ---------------- 查询 ----------------
    def search(self, field, keyword, exact=False, limit=2000):
        """按字段检索，返回 [(fid, [该行所有字段值...]), ...]。"""
        if not self.is_ready():
            raise RuntimeError('索引未就绪')
        try:
            col = self.names.index(field)
        except ValueError:
            raise RuntimeError('索引中没有字段：%s' % field)
        cols = ''.join(', c%d' % i for i in range(len(self.names)))
        if exact:
            where = 'c%d = ?' % col
            arg = keyword
        else:
            where = "c%d LIKE ? ESCAPE '\\'" % col
            arg = '%' + _escape_like(keyword) + '%'
        sql = 'SELECT fid%s FROM rows WHERE %s LIMIT ?' % (cols, where)
        con = None
        out = []
        try:
            con = self._connect()
            for row in con.execute(sql, (arg, int(limit))):
                out.append((row[0], list(row[1:])))
        finally:
            if con is not None:
                con.close()
        return out

    def drop(self):
        """删除索引文件（忽略失败）。"""
        if not self.path:
            return
        for path in (self.path, self.path + '-journal', self.path + '-wal', self.path + '-shm'):
            for _ in range(3):
                try:
                    if os.path.isfile(path):
                        os.remove(path)
                    break
                except OSError as exc:
                    QgsMessageLog.logMessage('删除索引失败: %s' % exc,
                                             'Shape_Layer_Tools', Qgis.MessageLevel.Warning)
                    time.sleep(0.1)
