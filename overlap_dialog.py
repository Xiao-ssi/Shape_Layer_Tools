# -*- coding: utf-8 -*-
"""同站同覆盖分析：对源图层中的每个面要素，在(源或对比)图层中找出与之重叠
（交集面积 / 较小面积 >= 百分比）且 起始点距离 <= 设定米数 的其他要素，
将匹配要素的所选字段在同一行向右依次累计填写（同覆盖1/同覆盖2/...）。
本模块独立，可单独修改而不影响其他功能模块。"""
import os
import datetime

from qgis.core import (
    QgsProject,
    QgsVectorLayer,
    QgsGeometry,
    QgsWkbTypes,
    QgsCoordinateTransform,
    QgsDistanceArea,
    QgsPointXY,
    QgsSpatialIndex,
    QgsMessageLog,
)
from qgis.core import Qgis
from qgis.PyQt.QtCore import QVariant, Qt
from qgis.PyQt.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QGroupBox,
    QLabel,
    QComboBox,
    QPushButton,
    QSpinBox,
    QListWidget,
    QListWidgetItem,
    QAbstractItemView,
    QLineEdit,
    QFileDialog,
    QMessageBox,
    QProgressBar,
    QApplication,
)
from .utils import session_get, session_set


def _first_point(qg):
    """返回几何的起始点(首点) QgsPointXY；取不到首点则用质心兜底。"""
    try:
        p = qg.vertexAt(0)
        return QgsPointXY(p.x(), p.y())
    except Exception as e:
        QgsMessageLog.logMessage('取起点坐标失败: %s' % e, 'Shape_Layer_Tools', Qgis.MessageLevel.Warning)
    try:
        if qg.type() == QgsWkbTypes.GeometryType.PointGeometry:
            pt = qg.asPoint()
            return QgsPointXY(pt.x(), pt.y())
    except Exception as e:
        QgsMessageLog.logMessage('取点几何坐标失败: %s' % e, 'Shape_Layer_Tools', Qgis.MessageLevel.Warning)
    try:
        c = qg.centroid()
        return QgsPointXY(c.x(), c.y()) if c is not None else None
    except Exception as e:
        QgsMessageLog.logMessage('取质心坐标失败: %s' % e, 'Shape_Layer_Tools', Qgis.MessageLevel.Warning)
    return None


class OverlapDialog(QDialog):
    """同站同覆盖分析：两两重叠 + 起始点距离匹配，同层或跨层皆可，结果导出到本地文件夹。"""

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.setWindowTitle('同站同覆盖分析')
        self.resize(580, 500)
        self._loading_fields = False
        self._build_ui()
        self._restore_session()
        self.refresh_layers()

    def _build_ui(self):
        lay = QVBoxLayout(self)

        grp = QGroupBox('图层选择')
        form = QFormLayout(grp)
        self.cmb_src = QComboBox()
        self.cmb_src.setObjectName('cmb_src')
        self.cmb_src.currentIndexChanged.connect(self._on_src_changed)
        form.addRow('源图层:', self.cmb_src)
        self.cmb_cmp = QComboBox()
        self.cmb_cmp.setObjectName('cmb_cmp')
        self.cmb_cmp.currentIndexChanged.connect(self._refresh_fields)
        form.addRow('对比图层(可与源图层相同):', self.cmb_cmp)
        btn_refresh = QPushButton('刷新图层列表')
        btn_refresh.clicked.connect(self.refresh_layers)
        form.addRow('', btn_refresh)
        lay.addWidget(grp)

        opt = QGroupBox('匹配条件')
        fo = QFormLayout(opt)
        self.spin_pct = QSpinBox()
        self.spin_pct.setRange(1, 100)
        self.spin_pct.setValue(60)
        self.spin_pct.setSuffix(' %')
        fo.addRow('形状重叠度:', self.spin_pct)
        self.spin_dist = QSpinBox()
        self.spin_dist.setRange(1, 1000000)
        self.spin_dist.setValue(100)
        self.spin_dist.setSuffix(' 米')
        fo.addRow('起始点距离:', self.spin_dist)
        lbl_note = QLabel('满足形状重叠度则为同覆盖，满足起始点距离则为同站。')
        lbl_note.setStyleSheet('color:#888;')
        fo.addRow('', lbl_note)
        lay.addWidget(opt)

        fg = QGroupBox('需导出的字段(勾选)')
        ff = QVBoxLayout(fg)
        self.lst_fields = QListWidget()
        self.lst_fields.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        ff.addWidget(self.lst_fields)
        lay.addWidget(fg)

        out = QGroupBox('导出到本地文件夹')
        fout = QFormLayout(out)
        self.edit_dir = QLineEdit()
        self.edit_dir.setReadOnly(True)
        self.edit_dir.setPlaceholderText('选择要保存结果表格的文件夹')
        btn_dir = QPushButton('选择文件夹...')
        btn_dir.clicked.connect(self.on_browse_dir)
        r = QHBoxLayout()
        r.addWidget(self.edit_dir)
        r.addWidget(btn_dir)
        fout.addRow('保存文件夹:', r)
        lay.addWidget(out)

        self.lbl_status = QLabel('请选择图层并设置匹配条件。')
        self.lbl_status.setStyleSheet('color:#666;')
        lay.addWidget(self.lbl_status)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        lay.addWidget(self.progress)

        hb = QHBoxLayout()
        self.btn_run = QPushButton('开始分析')
        self.btn_run.clicked.connect(self.on_run)
        btn_close = QPushButton('关闭')
        btn_close.clicked.connect(self.reject)
        hb.addStretch()
        hb.addWidget(self.btn_run)
        hb.addWidget(btn_close)
        lay.addLayout(hb)

    def refresh_layers(self):
        items = []
        for lid, ly in QgsProject.instance().mapLayers().items():
            if not isinstance(ly, QgsVectorLayer) or ly.wkbType() == QgsWkbTypes.Type.NoGeometry:
                continue
            items.append((ly.name(), lid))
        for cmb_name in ('cmb_src', 'cmb_cmp'):
            cmb = getattr(self, cmb_name)
            cmb.blockSignals(True)
            cmb.clear()
            for name, lid in items:
                cmb.addItem(name, lid)
            prev = session_get('overlap', cmb_name)
            if prev:
                idx = cmb.findData(prev)
                if idx >= 0:
                    cmb.setCurrentIndex(idx)
            cmb.blockSignals(False)
        self._refresh_fields()

    def _restore_session(self):
        try:
            self.spin_pct.setValue(int(session_get('overlap', 'pct', self.spin_pct.value())))
        except (TypeError, ValueError, AttributeError) as e:
            QgsMessageLog.logMessage('恢复重叠比例设置失败: %s' % e, 'Shape_Layer_Tools', Qgis.MessageLevel.Warning)
        try:
            self.spin_dist.setValue(int(session_get('overlap', 'dist', self.spin_dist.value())))
        except (TypeError, ValueError, AttributeError) as e:
            QgsMessageLog.logMessage('恢复距离设置失败: %s' % e, 'Shape_Layer_Tools', Qgis.MessageLevel.Warning)
        d = session_get('overlap', 'out_dir')
        if d:
            self.edit_dir.setText(d)

    def _save_session(self):
        session_set('overlap', 'cmb_src', self.cmb_src.currentData())
        session_set('overlap', 'cmb_cmp', self.cmb_cmp.currentData())
        session_set('overlap', 'pct', self.spin_pct.value())
        session_set('overlap', 'dist', self.spin_dist.value())
        session_set('overlap', 'out_dir', self.edit_dir.text())
        checked = [self.lst_fields.item(i).text()
                   for i in range(self.lst_fields.count())
                   if self.lst_fields.item(i).checkState() == Qt.CheckState.Checked]
        session_set('overlap', 'fields', checked)

    def _on_src_changed(self):
        # 源图层变化：同步刷新字段勾选区（导出字段取自源图层）
        self._refresh_fields()

    @staticmethod
    def _layer_from_cmb(cmb):
        lid = cmb.currentData()
        return QgsProject.instance().mapLayer(lid) if lid else None

    def _refresh_fields(self):
        if self._loading_fields:
            return
        self._loading_fields = True
        ly = self._layer_from_cmb(self.cmb_src)
        self.lst_fields.clear()
        if ly is not None:
            saved = session_get('overlap', 'fields')
            saved_set = set(saved) if saved else None
            for f in ly.fields():
                item = QListWidgetItem(f.name())
                item.setFlags((item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                              & ~Qt.ItemFlag.ItemIsAutoTristate)
                item.setCheckState(Qt.CheckState.Unchecked)
                self.lst_fields.addItem(item)
            # 默认全部勾选；若有会话记忆则按记忆勾选
            for i in range(self.lst_fields.count()):
                name = self.lst_fields.item(i).text()
                checked = saved_set is None or name in saved_set
                self.lst_fields.item(i).setCheckState(
                    Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self._loading_fields = False

    def on_browse_dir(self):
        path = QFileDialog.getExistingDirectory(self, '选择保存文件夹',
                                                self.edit_dir.text() or os.path.expanduser('~'))
        if path:
            self.edit_dir.setText(path)

    def on_run(self):
        try:
            self.btn_run.setEnabled(False)
            self.progress.setValue(0)
            QApplication.processEvents()
            self._save_session()
            self._do_analysis()
        except Exception as exc:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, '分析失败', '执行同站同覆盖分析时发生异常:\n%s' % str(exc))
        finally:
            self.progress.setValue(self.progress.maximum())
            QApplication.processEvents()
            self.btn_run.setEnabled(True)

    @staticmethod
    def _valid(g):
        """退化多边形先 makeValid 修复掉，避免求交集失败。"""
        try:
            if hasattr(g, 'isGeosValid') and hasattr(g, 'makeValid') and not g.isGeosValid():
                g2 = g.makeValid()
                if g2 is not None and not g2.isEmpty():
                    return g2
        except Exception as e:
            QgsMessageLog.logMessage('修复退化几何失败: %s' % e, 'Shape_Layer_Tools', Qgis.MessageLevel.Warning)
        return g

    def _do_analysis(self):
        src = self._layer_from_cmb(self.cmb_src)
        cmp = self._layer_from_cmb(self.cmb_cmp)
        if src is None or cmp is None:
            QMessageBox.warning(self, '提示', '请选择源图层和对比图层。')
            return
        out_dir = self.edit_dir.text().strip()
        if not out_dir:
            QMessageBox.warning(self, '提示', '请选择要保存结果表格的文件夹。')
            return

        selected_fields = [self.lst_fields.item(i).text()
                           for i in range(self.lst_fields.count())
                           if self.lst_fields.item(i).checkState() == Qt.CheckState.Checked]
        if not selected_fields:
            QMessageBox.warning(self, '提示', '请至少勾选一个需要导出的字段。')
            return

        pct_thr = self.spin_pct.value() / 100.0
        dist_thr = float(self.spin_dist.value())

        # 面积/距离计算器（基于源图层 CRS）
        da = QgsDistanceArea()
        da.setSourceCrs(src.crs(), QgsProject.instance().transformContext())
        da.setEllipsoid(QgsProject.instance().ellipsoid())

        src_same = (src.id() == cmp.id())
        xform = None
        if not src_same:
            xform = QgsCoordinateTransform(cmp.crs(), src.crs(),
                                           QgsProject.instance().transformContext())

        # 读入对比图层全部面要素，并建立空间索引（快速按 bbox 找候选）
        cmp_by_id = {}
        index = QgsSpatialIndex()
        for ft in cmp.getFeatures():
            g = ft.geometry()
            if g is None or g.isEmpty():
                continue
            geom = QgsGeometry(g)
            if xform is not None:
                try:
                    geom.transform(xform)
                except Exception as e:
                    QgsMessageLog.logMessage('对比要素坐标变换失败, 跳过: %s' % e, 'Shape_Layer_Tools', Qgis.MessageLevel.Warning)
                    continue
            sp = _first_point(geom)
            if sp is None:
                continue
            geoid = ft.id()
            cmp_by_id[geoid] = (geom, sp, ft.attributes())
            index.addFeature(geoid, geom.boundingBox())

        def _geom_area(geom):
            try:
                return abs(geom.area())
            except Exception:
                return 0.0

        # 预计算对比要素面积（id -> 面积）
        c_area_map = {cid: _geom_area(cg) for cid, (cg, _, _) in cmp_by_id.items()}

        # 结果行
        results = []
        n_rows = 0
        total_feat = list(src.getFeatures())
        total = len(total_feat)
        done = 0
        self.progress.setRange(0, total if total else 1)
        self.progress.setValue(0)
        QApplication.processEvents()

        for feat in total_feat:
            done += 1
            if total:
                self.progress.setValue(done)
            if done % 10 == 0:
                QApplication.processEvents()
            g = feat.geometry()
            if g is None or g.isEmpty():
                continue
            sg = self._valid(QgsGeometry(g))
            sp = _first_point(sg)
            if sp is None:
                continue
            s_area = _geom_area(sg)
            if s_area <= 1e-12:
                continue

            bb = sg.boundingBox()
            matches = []
            # 用空间索引只取 bbox 相交的候选，避免全量遍历
            cand_ids = index.intersects(bb)
            for geoid in cand_ids:
                # 同图层时跳过自身（每个元素各自列出与之重叠的其他元素）
                if src_same and geoid == feat.id():
                    continue
                item = cmp_by_id.get(geoid)
                if item is None:
                    continue
                cg, csp, cattrs = item
                # 起始点距离（米）
                try:
                    d = da.measureLine(sp, csp)
                except Exception:
                    d = None
                nearby = (d is not None and d <= dist_thr)
                # 求交集与重叠比例（交集/较小面积）
                try:
                    inter = sg.intersection(cg)
                except Exception:
                    inter = None
                ratio = 0.0
                overlap_ok = False
                if inter is not None and not inter.isEmpty():
                    inter_area = _geom_area(inter)
                    c_area = c_area_map.get(geoid, 0.0)
                    smaller = min(s_area, c_area)
                    if smaller > 1e-12:
                        ratio = inter_area / smaller
                        overlap_ok = (ratio >= pct_thr)
                # 判定为"或"关系：距离内 或 重叠达标
                if not (nearby or overlap_ok):
                    continue
                # 类型标记：同站 / 同覆盖 / 同站同覆盖
                if nearby and overlap_ok:
                    dtype = '同站同覆盖'
                elif nearby:
                    dtype = '同站'
                else:
                    dtype = '同覆盖'
                matches.append((d if d is not None else 0.0, ratio, dtype, cattrs))
            # 先按类型优先级、再按距离升序（同站同覆盖>同站>同覆盖，距离近的在前）
            order = {'同站同覆盖': 0, '同站': 1, '同覆盖': 2}
            matches.sort(key=lambda m: (order[m[2]], m[0]) if m[0] else 0)
            results.append((feat.attributes(), matches))
            n_rows += 1

        if n_rows == 0:
            QMessageBox.information(self, '分析完成', '未找到任何符合条件的图层要素。')
            return

        # 列结构：第 1 组为主元素所选字段；每组重叠含"字段 + 重叠比例 + 起始点距离(米) + 类型"
        max_over = max((len(m) for _, m in results), default=0)
        nsel = len(selected_fields)
        # 按字段名查索引，确保取到的值不错位（源图层与对比图层分开求）
        src_idx_map = {f.name(): i for i, f in enumerate(src.fields())}
        cmp_idx_map = {f.name(): i for i, f in enumerate(cmp.fields())}
        src_idxs = [src_idx_map.get(f, -1) for f in selected_fields]
        cmp_idxs = [cmp_idx_map.get(f, -1) for f in selected_fields]
        header = list(selected_fields)
        for k in range(1, max_over + 1):
            for fname in selected_fields:
                header.append('同覆盖%d.%s' % (k, fname))
            header.append('同覆盖%d距离(米)' % k)
            header.append('同覆盖%d重叠比例' % k)
            header.append('同覆盖%d类型' % k)

        rows = []
        for attrs, match_list in results:
            row = self._extract_vals(attrs, src_idxs)
            for k in range(1, max_over + 1):
                if k <= len(match_list):
                    d, ratio, dtype, cattrs = match_list[k - 1]
                    row += self._extract_vals(cattrs, cmp_idxs)
                    row.append('%.2f' % d)
                    row.append('%.1f%%' % (ratio * 100.0) if ratio > 0 else '')
                    row.append(dtype)
                else:
                    row += [''] * (nsel + 3)
            rows.append(row)

        # 输出（用 CSV 而非 openpyxl，避免 openpyxl.Workbook() 在本环境触发崩溃）
        stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        out_path = os.path.join(out_dir, '同站同覆盖分析_%s.csv' % stamp)
        try:
            self._write_csv(out_path, header, rows)
            msg = ('同站同覆盖分析完成：共处理 %d 条要素，其中 %d 条存在重叠匹配。\n'
                   '结果表格已导出到：\n%s'
                   % (total, n_rows, out_path))
            QMessageBox.information(self, '分析完成', msg)
        except Exception as exc:
            QMessageBox.warning(self, '导出失败', '表格导出失败：%s' % str(exc))

    @staticmethod
    def _extract_vals(attrs, idxs):
        """按字段索引列表从属性列表取值，索引无效(-1)时取空串，避免错位。"""
        return ['' if (i < 0 or i >= len(attrs) or attrs[i] is None) else str(attrs[i])
                for i in idxs]

    @staticmethod
    def _write_csv(path, header, rows):
        import csv
        with open(path, 'w', newline='', encoding='utf-8-sig') as fh:
            w = csv.writer(fh)
            w.writerow(header)
            for r in rows:
                w.writerow(r)