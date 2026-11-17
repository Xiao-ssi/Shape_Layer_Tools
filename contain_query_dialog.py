# -*- coding: utf-8 -*-
"""图层包含查询：判断"需查询图层"中的要素是否落在所选"区域图层"内。

支持同时勾选多个需查询图层与多个区域图层：
- 需查询图层逐个处理，每个源图层生成一个独立结果图层（几何类型/坐标系跟随源图层）；
- 区域图层可多选，命中结果会记录来自哪个区域图层；单选时输出结构与旧版完全一致；
- 区域图形一次性读入内存并建立空间索引，避免逐点向数据源查询。

本模块独立，可单独修改而不影响其他功能模块。
"""
import os
import csv

from qgis.core import (
    Qgis,
    QgsProject,
    QgsVectorLayer,
    QgsField,
    QgsFeature,
    QgsGeometry,
    QgsWkbTypes,
    QgsCoordinateTransform,
    QgsDistanceArea,
    QgsRectangle,
    QgsPointXY,
    QgsFields,
    QgsSpatialIndex,
    QgsMessageLog,
)
from qgis.PyQt.QtCore import Qt, QVariant
from qgis.PyQt.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QCheckBox,
    QSpinBox,
    QRadioButton,
    QButtonGroup,
    QLineEdit,
    QFileDialog,
    QMessageBox,
    QProgressBar,
    QListWidget,
    QListWidgetItem,
    QApplication,
)
from .utils import session_get, session_set


def _first_point(qg):
    """返回几何的起始点(首点) QgsPointXY，用于以经纬度起点判断是否在区域内；
    取不到首点时返回 None。"""
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
    return None


class _AreaLayer(object):
    """区域图层内存快照：几何 + 属性 + 空间索引，供大量要素快速判定。"""

    def __init__(self, layer):
        self.layer = layer
        self.name = layer.name()
        self.crs = layer.crs()
        self.geographic = bool(self.crs.isGeographic())
        self.field_names = [f.name() for f in layer.fields()]
        self.geoms = {}      # fid -> QgsGeometry
        self.attrs = {}      # fid -> [属性值...]
        self.index = QgsSpatialIndex()
        for feat in layer.getFeatures():
            g = feat.geometry()
            if g is None or g.isEmpty():
                continue
            fid = feat.id()
            self.geoms[fid] = g
            self.attrs[fid] = list(feat.attributes())
            self.index.addFeature(feat)
        self.da = QgsDistanceArea()
        try:
            self.da.setSourceCrs(self.crs, QgsProject.instance().transformContext())
            self.da.setEllipsoid(QgsProject.instance().ellipsoid())
        except Exception as e:
            QgsMessageLog.logMessage('面积计算器初始化失败: %s' % e,
                                     'Shape_Layer_Tools', Qgis.MessageLevel.Warning)

    def candidates(self, rect):
        try:
            return self.index.intersects(rect)
        except Exception as e:
            QgsMessageLog.logMessage('空间索引查询失败，退回全量比对: %s' % e,
                                     'Shape_Layer_Tools', Qgis.MessageLevel.Warning)
            return list(self.geoms.keys())

    def area(self, geom):
        try:
            return self.da.measureArea(geom)
        except Exception:
            return None

    def to_meters(self, dist):
        """把几何距离换算成米（经纬度图层按 1 度≈111319.9 米粗略换算）。"""
        return dist * 111319.9 if self.geographic else dist

    def buffer_deg(self, meters):
        return meters / 111319.9 if self.geographic else meters


class ContainQueryDialog(QDialog):
    """图层包含查询：支持多选需查询图层与多选区域图层。"""

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.setWindowTitle('图层包含查询')
        self.resize(620, 760)
        self._build_ui()
        self._restore_session()
        self.refresh_layers()

    def _build_ui(self):
        lay = QVBoxLayout(self)

        grp = QGroupBox('图层选择（可多选）')
        gv = QVBoxLayout(grp)
        gv.addWidget(QLabel('需查询图层（可勾选多个，每个图层生成一份结果）:'))
        self.lw_src = QListWidget()
        self.lw_src.setObjectName('lw_src')
        self.lw_src.setMinimumHeight(110)
        self.lw_src.itemChanged.connect(self._on_layer_changed)
        gv.addWidget(self.lw_src)
        gv.addLayout(self._check_all_row(self.lw_src))
        gv.addWidget(QLabel('区域图层（可勾选多个，仅面图层，判断是否落在其中）:'))
        self.lw_area = QListWidget()
        self.lw_area.setObjectName('lw_area')
        self.lw_area.setMinimumHeight(90)
        self.lw_area.itemChanged.connect(self._on_layer_changed)
        gv.addWidget(self.lw_area)
        gv.addLayout(self._check_all_row(self.lw_area))
        btn_refresh = QPushButton('刷新图层列表'); btn_refresh.clicked.connect(self.refresh_layers)
        gv.addWidget(btn_refresh)
        lay.addWidget(grp)

        opt = QGroupBox('匹配选项')
        fo = QFormLayout(opt)
        self.chk_nearest = QCheckBox('未匹配点匹配最近图形')
        self.spin_dist = QSpinBox(); self.spin_dist.setRange(10, 1000000)
        self.spin_dist.setValue(500); self.spin_dist.setSuffix(' 米')
        self.spin_dist.setEnabled(self.chk_nearest.isChecked())
        self.chk_nearest.toggled.connect(self.spin_dist.setEnabled)
        r1 = QHBoxLayout(); r1.addWidget(self.chk_nearest); r1.addWidget(self.spin_dist); r1.addStretch()
        fo.addRow('最近图形:', r1)
        self.rb_multi = QRadioButton('1对多匹配'); self.rb_single = QRadioButton('1对1匹配')
        self.rb_multi.setChecked(True)
        g2 = QButtonGroup(self); g2.addButton(self.rb_multi); g2.addButton(self.rb_single)
        r2 = QHBoxLayout(); r2.addWidget(self.rb_multi); r2.addWidget(self.rb_single); r2.addStretch()
        fo.addRow('匹配方式:', r2)
        self.chk_pct = QCheckBox('图形有该百分比面积在区域内即算匹配')
        self.spin_pct = QSpinBox(); self.spin_pct.setRange(1, 100)
        self.spin_pct.setValue(50); self.spin_pct.setSuffix(' %')
        self.spin_pct.setEnabled(self.chk_pct.isChecked())
        self.chk_pct.toggled.connect(self.spin_pct.setEnabled)
        r4 = QHBoxLayout(); r4.addWidget(self.chk_pct); r4.addWidget(self.spin_pct); r4.addStretch()
        fo.addRow('面积占比:', r4)
        lay.addWidget(opt)

        out = QGroupBox('结果导出（表格）')
        fout = QFormLayout(out)
        self.chk_area_attrs = QCheckBox('结果表格包含区域图层属性列')
        self.chk_area_attrs.setChecked(True)
        self.chk_area_attrs.setToolTip('勾选多个区域图层时，每个图层的字段都会作为一列输出；'
                                       '字段较多时可取消勾选，只保留"是否在图形内"与匹配区域图层名。')
        fout.addRow('附加列:', self.chk_area_attrs)
        self.edit_out = QLineEdit(); self.edit_out.setReadOnly(True)
        self.edit_out.setPlaceholderText('默认保存到区域图层所在目录')
        btn_out = QPushButton('另存为...'); btn_out.clicked.connect(self.on_browse_out)
        r3 = QHBoxLayout(); r3.addWidget(self.edit_out); r3.addWidget(btn_out)
        fout.addRow('保存路径:', r3)
        tip = QLabel('勾选多个需查询图层时，会自动在文件名后追加源图层名以便区分。')
        tip.setStyleSheet('color:#888;'); tip.setWordWrap(True)
        fout.addRow('', tip)
        lay.addWidget(out)

        self.lbl_status = QLabel('请勾选需查询图层和区域图层')
        self.lbl_status.setStyleSheet('color:#666;')
        self.lbl_status.setWordWrap(True)
        lay.addWidget(self.lbl_status)

        # 查询进度条
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        lay.addWidget(self.progress)

        hb = QHBoxLayout()
        self.btn_run = QPushButton('开始查询'); self.btn_run.clicked.connect(self.on_run)
        btn_close = QPushButton('关闭'); btn_close.clicked.connect(self.reject)
        hb.addStretch(); hb.addWidget(self.btn_run); hb.addWidget(btn_close)
        lay.addLayout(hb)

    def _check_all_row(self, lw):
        row = QHBoxLayout()
        b_all = QPushButton('全选')
        b_none = QPushButton('清空')
        b_all.clicked.connect(lambda: self._set_all_checked(lw, True))
        b_none.clicked.connect(lambda: self._set_all_checked(lw, False))
        row.addWidget(b_all); row.addWidget(b_none); row.addStretch()
        return row

    @staticmethod
    def _set_all_checked(lw, checked):
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        lw.blockSignals(True)
        for i in range(lw.count()):
            lw.item(i).setCheckState(state)
        lw.blockSignals(False)

    @staticmethod
    def _checked_ids(lw):
        out = []
        for i in range(lw.count()):
            it = lw.item(i)
            if it.checkState() == Qt.CheckState.Checked:
                out.append(it.data(Qt.ItemDataRole.UserRole))
        return out

    @staticmethod
    def _checked_layers(lw):
        out = []
        for lid in ContainQueryDialog._checked_ids(lw):
            ly = QgsProject.instance().mapLayer(lid) if lid else None
            if isinstance(ly, QgsVectorLayer):
                out.append(ly)
        return out

    @staticmethod
    def _fill_list(lw, items, checked_ids):
        lw.blockSignals(True)
        lw.clear()
        for name, lid in items:
            it = QListWidgetItem(name)
            it.setData(Qt.ItemDataRole.UserRole, lid)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if lid in checked_ids
                             else Qt.CheckState.Unchecked)
            lw.addItem(it)
        lw.blockSignals(False)

    @staticmethod
    def _split_ids(text):
        return [x for x in str(text or '').split(',') if x]

    def refresh_layers(self):
        cur_src = set(self._checked_ids(self.lw_src))
        cur_area = set(self._checked_ids(self.lw_area))
        if not cur_src:
            cur_src = set(self._split_ids(session_get('contain', 'src_ids', '')))
        if not cur_area:
            cur_area = set(self._split_ids(session_get('contain', 'area_ids', '')))

        src, poly = [], []
        for lid, ly in QgsProject.instance().mapLayers().items():
            if not isinstance(ly, QgsVectorLayer):
                continue
            # 需查询图层可以是任意矢量图层（点/线/面，甚至无几何）
            src.append((ly.name(), lid))
            if ly.geometryType() == QgsWkbTypes.GeometryType.PolygonGeometry:
                poly.append((ly.name(), lid))

        self._fill_list(self.lw_src, src, cur_src)
        self._fill_list(self.lw_area, poly, cur_area)

        # 首次使用（无任何记忆）时默认勾选第一个图层，避免空列表无从下手
        if src and not self._checked_ids(self.lw_src):
            self._set_first_checked(self.lw_src)
        if poly and not self._checked_ids(self.lw_area):
            self._set_first_checked(self.lw_area)
        self._on_layer_changed()

    @staticmethod
    def _set_first_checked(lw):
        lw.blockSignals(True)
        if lw.count():
            lw.item(0).setCheckState(Qt.CheckState.Checked)
        lw.blockSignals(False)

    def _restore_session(self):
        self.chk_nearest.setChecked(bool(session_get('contain', 'nearest', self.chk_nearest.isChecked())))
        try:
            self.spin_dist.setValue(int(session_get('contain', 'dist', self.spin_dist.value())))
        except (TypeError, ValueError, AttributeError) as e:
            QgsMessageLog.logMessage('恢复查询距离设置失败: %s' % e, 'Shape_Layer_Tools', Qgis.MessageLevel.Warning)
        multi = bool(session_get('contain', 'multi', self.rb_multi.isChecked()))
        self.rb_multi.setChecked(multi); self.rb_single.setChecked(not multi)
        self.chk_pct.setChecked(bool(session_get('contain', 'pct_on', self.chk_pct.isChecked())))
        self.chk_area_attrs.setChecked(
            bool(session_get('contain', 'area_attrs', self.chk_area_attrs.isChecked())))
        try:
            self.spin_pct.setValue(int(session_get('contain', 'pct', self.spin_pct.value())))
        except (TypeError, ValueError, AttributeError) as e:
            QgsMessageLog.logMessage('恢复面积占比设置失败: %s' % e, 'Shape_Layer_Tools', Qgis.MessageLevel.Warning)
        outp = session_get('contain', 'out_path')
        if outp:
            self.edit_out.setText(outp)

    def _save_session(self):
        session_set('contain', 'src_ids', ','.join(self._checked_ids(self.lw_src)))
        session_set('contain', 'area_ids', ','.join(self._checked_ids(self.lw_area)))
        session_set('contain', 'nearest', self.chk_nearest.isChecked())
        session_set('contain', 'dist', self.spin_dist.value())
        session_set('contain', 'multi', self.rb_multi.isChecked())
        session_set('contain', 'pct_on', self.chk_pct.isChecked())
        session_set('contain', 'pct', self.spin_pct.value())
        session_set('contain', 'area_attrs', self.chk_area_attrs.isChecked())
        session_set('contain', 'out_path', self.edit_out.text())

    def _on_layer_changed(self):
        if not self.edit_out.text():
            self._default_out_path()

    def _default_out_path(self):
        base = None
        for lw in (self.lw_area, self.lw_src):
            layers = self._checked_layers(lw)
            if layers:
                src = layers[0].source().split('|')[0]
                if os.path.exists(src):
                    base = os.path.splitext(src)[0]
                    break
        if not base:
            base = os.path.expanduser('~/contain_query_result')
        self.edit_out.setText(base + '_查询结果.csv')
        self._out_path = None

    def on_browse_out(self):
        default = self.edit_out.text() or os.path.expanduser('~/contain_query_result.csv')
        path, _ = QFileDialog.getSaveFileName(self, '选择保存位置', default,
                                              'CSV 表格 (*.csv);;Excel 表格 (*.xlsx)')
        if path:
            self.edit_out.setText(path)

    def on_run(self):
        try:
            self.btn_run.setEnabled(False)
            self.progress.setValue(0)
            QApplication.processEvents()
            self._save_session()
            self._do_query()
        except Exception as exc:
            import traceback; traceback.print_exc()
            QMessageBox.critical(self, '查询失败', '执行查询时发生异常:\n%s' % str(exc))
        finally:
            self.progress.setValue(self.progress.maximum())
            QApplication.processEvents()
            self.btn_run.setEnabled(True)

    # ============================ 查询 ============================
    def _do_query(self):
        src_layers = self._checked_layers(self.lw_src)
        area_layers = self._checked_layers(self.lw_area)
        if not src_layers:
            QMessageBox.warning(self, '提示', '请至少勾选一个"需查询图层"。')
            return
        if not area_layers:
            QMessageBox.warning(self, '提示', '请至少勾选一个"区域图层"（面图层）。')
            return

        self.lbl_status.setText('正在读取 %d 个区域图层的图形...' % len(area_layers))
        QApplication.processEvents()
        areas = []
        for ly in area_layers:
            try:
                a = _AreaLayer(ly)
            except Exception as exc:
                QMessageBox.warning(self, '区域图层读取失败', '%s\n%s' % (ly.name(), exc))
                continue
            if not a.geoms:
                QgsMessageLog.logMessage('区域图层无有效面要素，已跳过: %s' % ly.name(),
                                         'Shape_Layer_Tools', Qgis.MessageLevel.Warning)
                continue
            areas.append(a)
        if not areas:
            QMessageBox.warning(self, '提示', '所选区域图层中没有有效的面要素。')
            return
        self.lbl_status.setText('区域图形已加载：%s' % '、'.join(
            '%s(%d个)' % (a.name, len(a.geoms)) for a in areas))
        QApplication.processEvents()

        total = 0
        for ly in src_layers:
            try:
                total += int(ly.featureCount() or 0)
            except Exception as exc:
                # 个别数据源（WFS/部分驱动）不支持计数：只影响进度条的精度，记录后继续
                QgsMessageLog.logMessage('读取图层 "%s" 要素数失败，进度条改用不确定模式: %s'
                                         % (ly.name(), exc),
                                         'Shape_Layer_Tools', Qgis.MessageLevel.Info)
        if total > 0:
            self.progress.setRange(0, total)
        else:
            self.progress.setRange(0, 0)
        self.progress.setValue(0)

        done = [0]
        add_area_col = len(areas) > 1
        results = []     # [(src_layer, rows, extra_cols, area_col, out_layer)]

        def _tick():
            done[0] += 1
            if total > 0:
                self.progress.setValue(done[0])
            if done[0] % 200 == 0:
                QApplication.processEvents()

        for i, src_layer in enumerate(src_layers):
            self.lbl_status.setText('正在查询：%s（%d/%d）' % (src_layer.name(), i + 1, len(src_layers)))
            QApplication.processEvents()
            rows = self._query_one(src_layer, areas, _tick)
            if not rows:
                QgsMessageLog.logMessage('源图层没有可查询的要素: %s' % src_layer.name(),
                                         'Shape_Layer_Tools', Qgis.MessageLevel.Warning)
                continue
            extra_cols = self._extra_columns(src_layer, areas) \
                if self.chk_area_attrs.isChecked() else []
            out_layer, feats_all, feats_layer, fields = self._build_output(
                src_layer, rows, extra_cols, add_area_col)
            results.append((src_layer, extra_cols, add_area_col, out_layer,
                            feats_all, feats_layer, fields))

        if not results:
            QMessageBox.information(self, '查询完成', '未找到任何符合条件的要素。')
            return

        self._export_and_report(results)

    def _query_one(self, src_layer, areas, tick):
        """对单个源图层执行包含查询，返回结果行列表。"""
        ctx = QgsProject.instance().transformContext()
        pairs = []
        for a in areas:
            try:
                pairs.append((a, QgsCoordinateTransform(src_layer.crs(), a.crs, ctx)))
            except Exception as exc:
                QgsMessageLog.logMessage('坐标系转换创建失败(%s): %s' % (a.name, exc),
                                         'Shape_Layer_Tools', Qgis.MessageLevel.Warning)
        if not pairs:
            QMessageBox.warning(self, '提示', '源图层与区域图层之间无法建立坐标转换。')
            return []

        match_nearest = self.chk_nearest.isChecked()
        max_dist = float(self.spin_dist.value())
        one_to_one = self.rb_single.isChecked()
        use_pct = self.chk_pct.isChecked()
        pct_thr = self.spin_pct.value() / 100.0

        rows = []    # (点序号, 源几何, 区域图层名, 区域属性, 是否在图形内, 面积, 源属性)
        n_pt = 0
        xf_failed = set()   # 坐标转换失败的区域图层名，只提示一次，避免逐要素刷屏
        for feat in src_layer.getFeatures():
            tick()
            g = feat.geometry()
            if g is None or g.isEmpty():
                continue
            src_attrs = list(feat.attributes())
            matched = False
            nearest = None      # (区域图层名, 区域属性, 面积, 距离米)
            for a, xf in pairs:
                p_g = QgsGeometry(g)
                try:
                    p_g.transform(xf)
                except Exception as exc:
                    if a.name not in xf_failed:
                        xf_failed.add(a.name)
                        QgsMessageLog.logMessage(
                            '要素坐标转换到区域图层 "%s" 失败，该区域图层本次跳过: %s'
                            % (a.name, exc),
                            'Shape_Layer_Tools', Qgis.MessageLevel.Warning)
                    continue
                if p_g.isEmpty():
                    continue
                # 以要素的"起始点经纬度"判断是否落在区域内；
                # 同时支持"图形面积占比"匹配（默认图形有 50% 面积在区域内即算匹配）。
                start = _first_point(p_g)
                if start is None:
                    cen = p_g.centroid()
                    if cen is None or cen.isEmpty():
                        continue
                    cpt = cen.asPoint()
                    start = QgsPointXY(cpt.x(), cpt.y())
                start_g = QgsGeometry.fromPointXY(start)
                bbox = p_g.boundingBox()
                total_area = p_g.area()
                has_geom_area = total_area > 1e-12
                hit = False
                for fid in sorted(a.candidates(bbox)):
                    pg = a.geoms.get(fid)
                    if pg is None:
                        continue
                    is_hit = False
                    if pg.contains(start_g):
                        is_hit = True
                    elif use_pct and has_geom_area:
                        try:
                            inter = pg.intersection(p_g)
                        except Exception:
                            inter = None
                        if (inter is not None and not inter.isEmpty()
                                and (inter.area() / total_area) >= pct_thr):
                            is_hit = True
                    if is_hit:
                        hit = True
                        rows.append((n_pt, g, a.name, a.attrs.get(fid), '是',
                                     a.area(pg), src_attrs))
                if hit:
                    matched = True
                elif match_nearest:
                    buf = a.buffer_deg(max_dist)
                    exp = QgsRectangle(bbox.xMinimum() - buf, bbox.yMinimum() - buf,
                                       bbox.xMaximum() + buf, bbox.yMaximum() + buf)
                    for fid in a.candidates(exp):
                        pg = a.geoms.get(fid)
                        if pg is None:
                            continue
                        try:
                            d = a.to_meters(pg.distance(start_g))
                        except Exception as exc:
                            QgsMessageLog.logMessage('计算距离失败, 跳过该候选面: %s' % exc,
                                                     'Shape_Layer_Tools', Qgis.MessageLevel.Warning)
                            continue
                        if d <= max_dist and (nearest is None or d < nearest[3]):
                            nearest = (a.name, a.attrs.get(fid), a.area(pg), d)

            if not matched:
                if nearest is not None:
                    rows.append((n_pt, g, nearest[0], nearest[1], '否', nearest[2], src_attrs))
                else:
                    rows.append((n_pt, g, '', None, '否', None, src_attrs))
            n_pt += 1

        # 1对1：按原始点取"优先在范围内、其次面积最小"的那条
        if one_to_one:
            def _key(r):
                within_rank = 0 if r[4] == '是' else 1
                area = r[5] if r[5] is not None else float('inf')
                return (within_rank, area)
            grouped = {}
            for row in rows:
                cur = grouped.get(row[0])
                if cur is None or _key(row) < _key(cur):
                    grouped[row[0]] = row
            rows = [grouped[k] for k in sorted(grouped)]
        return rows

    @staticmethod
    def _extra_columns(src_layer, areas):
        """结果表格中"区域图层属性"列：多图层字段重名时自动加图层名前缀。"""
        used = set(src_layer.fields().names())
        extra = []      # (输出列名, 区域图层名, 区域字段序号)
        for a in areas:
            for i, fname in enumerate(a.field_names):
                out = fname
                if out in used:
                    out = '%s_%s' % (a.name, fname)
                    k = 2
                    while out in used:
                        out = '%s_%s_%d' % (a.name, fname, k)
                        k += 1
                used.add(out)
                extra.append((out, a.name, i))
        return extra

    def _build_output(self, src_layer, rows, extra_cols, add_area_col):
        """按源图层几何类型/坐标系生成结果内存图层；返回图层与要素集合。"""
        src_fields = list(src_layer.fields())
        fields = QgsFields()
        for f in src_fields:
            fields.append(QgsField(f.name(), QVariant.String))
        fields.append(QgsField('是否在图形内', QVariant.String))
        fields.append(QgsField('面图层面积(平方米)', QVariant.String))
        if add_area_col:
            fields.append(QgsField('匹配区域图层', QVariant.String))
        for out_name, _a, _i in extra_cols:
            fields.append(QgsField(out_name, QVariant.String))

        crs_authid = src_layer.crs().authid() or 'EPSG:4326'
        src_has_geom = src_layer.wkbType() != QgsWkbTypes.Type.NoGeometry
        if src_has_geom:
            geom_token = QgsWkbTypes.displayString(src_layer.wkbType()) or 'Point'
            uri = '%s?crs=%s' % (geom_token, crs_authid)
        else:
            uri = 'NoGeometry?crs=%s' % crs_authid
        out_layer = QgsVectorLayer(uri, self._unique_name(self._result_name(src_layer)), 'memory')
        provider = out_layer.dataProvider()
        provider.addAttributes(fields)
        out_layer.updateFields()

        feats_all = []
        feats_layer = []
        for _idx, geom, area_name, area_attrs, within, area_m2, vals in rows:
            f = QgsFeature()
            f.setFields(fields)
            if src_has_geom:
                f.setGeometry(geom)
            av = ['' if v is None else str(v) for v in vals]
            av.append(within)
            av.append('' if area_m2 is None else ('%.2f' % area_m2))
            if add_area_col:
                av.append(area_name or '')
            for _out, a_name, i in extra_cols:
                if area_attrs is not None and a_name == area_name and i < len(area_attrs):
                    v = area_attrs[i]
                    av.append('' if v is None else str(v))
                else:
                    av.append('')
            f.setAttributes(av)
            feats_all.append(f)
            if within == '是':
                feats_layer.append(f)

        # 仅把"在范围内"的结果写入图层，不在范围内的不显示在地图上
        if feats_layer:
            provider.addFeatures(feats_layer)
            out_layer.updateExtents()
        QgsProject.instance().addMapLayer(out_layer, True)
        try:
            canvas = self.iface.mapCanvas()
            if canvas and not out_layer.extent().isEmpty():
                canvas.setExtent(out_layer.extent())
            canvas.refresh()
            self.iface.layerTreeView().refreshLayerSymbology(out_layer.id())
        except Exception as e:
            QgsMessageLog.logMessage('导出后刷新画布/符号失败: %s' % e, 'Shape_Layer_Tools', Qgis.MessageLevel.Warning)
        return out_layer, feats_all, feats_layer, fields

    @staticmethod
    def _result_name(src_layer):
        return '%s-包含查询结果' % src_layer.name()

    def _export_and_report(self, results):
        out_path = self.edit_out.text().strip()
        if not out_path:
            out_path = os.path.expanduser('~/contain_query_result.csv')
            self.edit_out.setText(out_path)
        multi = len(results) > 1
        base, ext = os.path.splitext(out_path)
        if not ext:
            ext = '.csv'

        lines = []
        failures = []
        for src_layer, _extra, _ac, out_layer, feats_all, feats_layer, fields in results:
            path = out_path
            if multi:
                safe = self._safe_name(src_layer.name())
                path = '%s_%s%s' % (base, safe, ext)
            try:
                if path.lower().endswith('.xlsx'):
                    self._write_xlsx(path, fields, feats_all)
                else:
                    self._write_csv(path, fields, feats_all)
                lines.append('· %s：处理 %d 条，其中 %d 条在范围内，已生成图层 "%s"，表格 → %s' % (
                    src_layer.name(), len(feats_all), len(feats_layer), out_layer.name(), path))
            except Exception as exc:
                failures.append('%s：%s' % (src_layer.name(), exc))
                lines.append('· %s：图层已生成 "%s"，但表格导出失败（%s）' % (
                    src_layer.name(), out_layer.name(), exc))

        msg = '查询完成！\n\n%s\n\n说明：结果图层仅显示"在范围内"的要素，导出表格包含全部记录。' % '\n'.join(lines)
        if failures:
            QMessageBox.warning(self, '部分导出失败', msg)
        else:
            QMessageBox.information(self, '查询完成', msg)

    @staticmethod
    def _safe_name(name):
        bad = '\\/:*?"<>|'
        out = ''.join('_' if c in bad else c for c in str(name)).strip()
        return out or 'layer'

    def _write_csv(self, path, fields, feats):
        names = [f.name() for f in fields]
        with open(path, 'w', newline='', encoding='utf-8-sig') as fh:
            w = csv.writer(fh)
            w.writerow(names)
            for feat in feats:
                w.writerow([feat.attribute(i) if feat.attribute(i) is not None else ''
                            for i in range(len(names))])

    def _write_xlsx(self, path, fields, feats):
        try:
            import openpyxl
        except ImportError:
            raise ImportError('导出 Excel 需要安装 openpyxl: pip install openpyxl')
        wb = openpyxl.Workbook(); ws = wb.active
        ws.append([f.name() for f in fields])
        for feat in feats:
            ws.append([feat.attribute(i) if feat.attribute(i) is not None else ''
                       for i in range(len(fields))])
        wb.save(path)

    def _unique_name(self, base):
        existing = {ly.name() for ly in QgsProject.instance().mapLayers().values()}
        if base not in existing:
            return base
        i = 2
        while ('%s-%d' % (base, i)) in existing:
            i += 1
        return '%s-%d' % (base, i)
