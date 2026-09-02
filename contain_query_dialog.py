# -*- coding: utf-8 -*-
"""图层包含查询：判断点图层中的点是否落在所选面图层内，生成结果图层并导出表格。
本模块独立，可单独修改而不影响其他功能模块。"""
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
    QgsFeatureRequest,
    QgsDistanceArea,
    QgsRectangle,
    QgsPointXY,
    QgsFields,
    QgsMessageLog,
)
from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QGroupBox,
    QLabel,
    QComboBox,
    QPushButton,
    QCheckBox,
    QSpinBox,
    QRadioButton,
    QButtonGroup,
    QLineEdit,
    QFileDialog,
    QMessageBox,
    QProgressBar,
    QApplication,
)


def _first_point(qg):
    """返回几何的起始点(首点) QgsPointXY，用于以经纬度起点判断是否在区域内；
    取不到首点时返回 None。"""
    try:
        p = qg.vertexAt(0)
        return QgsPointXY(p.x(), p.y())
    except Exception as e:
        QgsMessageLog.logMessage('取起点坐标失败: %s' % e, 'Shape_Layer_Tools', Qgis.Warning)
    try:
        if qg.type() == QgsWkbTypes.GeometryType.PointGeometry:
            pt = qg.asPoint()
            return QgsPointXY(pt.x(), pt.y())
    except Exception as e:
        QgsMessageLog.logMessage('取点几何坐标失败: %s' % e, 'Shape_Layer_Tools', Qgis.Warning)
    return None


class ContainQueryDialog(QDialog):
    """图层包含查询：判断点图层中的点是否落在所选面图层内，生成结果图层并导出表格。"""

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.setWindowTitle('图层包含查询')
        self.resize(560, 420)
        self._build_ui()
        self.refresh_layers()

    def _build_ui(self):
        lay = QVBoxLayout(self)

        grp = QGroupBox('图层选择')
        form = QFormLayout(grp)
        self.cmb_pt = QComboBox(); self.cmb_pt.currentIndexChanged.connect(self._on_layer_changed)
        form.addRow('需查询图层:', self.cmb_pt)
        self.cmb_poly = QComboBox(); self.cmb_poly.currentIndexChanged.connect(self._on_layer_changed)
        form.addRow('区域图层（范围内）:', self.cmb_poly)
        btn_refresh = QPushButton('刷新图层列表'); btn_refresh.clicked.connect(self.refresh_layers)
        form.addRow('', btn_refresh)
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
        self.edit_out = QLineEdit(); self.edit_out.setReadOnly(True)
        self.edit_out.setPlaceholderText('默认保存到面图层所在目录')
        btn_out = QPushButton('另存为...'); btn_out.clicked.connect(self.on_browse_out)
        r3 = QHBoxLayout(); r3.addWidget(self.edit_out); r3.addWidget(btn_out)
        fout.addRow('保存路径:', r3)
        lay.addWidget(out)

        self.lbl_status = QLabel('请选择点图层和面图层')
        self.lbl_status.setStyleSheet('color:#666;')
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

    def refresh_layers(self):
        src_layers, poly_layers = [], []
        for lid, ly in QgsProject.instance().mapLayers().items():
            if not isinstance(ly, QgsVectorLayer):
                continue
            # 需查询图层可以是任意矢量图层（点/线/面，甚至无几何）
            src_layers.append((ly.name(), lid))
            if ly.geometryType() == QgsWkbTypes.GeometryType.PolygonGeometry:
                poly_layers.append((ly.name(), lid))
        if not poly_layers:
            poly_layers = [('（无面图层面）', '')]
        for cmb, layers in ((self.cmb_pt, src_layers), (self.cmb_poly, poly_layers)):
            cmb.blockSignals(True)
            cmb.clear()
            for name, lid in layers:
                cmb.addItem(name, lid)
            cmb.blockSignals(False)
        self._on_layer_changed()

    def _on_layer_changed(self):
        if not self.edit_out.text():
            self._default_out_path()

    def _default_out_path(self):
        poly = self._current_layer(self.cmb_poly)
        base = None
        if poly is not None:
            base = os.path.splitext(poly.source())[0] if os.path.exists(poly.source()) else None
        if not base and self._current_layer(self.cmb_pt) is not None:
            pt = self._current_layer(self.cmb_pt)
            base = os.path.splitext(pt.source())[0] if os.path.exists(pt.source()) else None
        if not base:
            base = os.path.expanduser('~/contain_query_result')
        self.edit_out.setText(base + '_查询结果.csv')
        self._out_path = None

    @staticmethod
    def _current_layer(cmb):
        lid = cmb.currentData()
        return QgsProject.instance().mapLayer(lid) if lid else None

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
            self._do_query()
        except Exception as exc:
            import traceback; traceback.print_exc()
            QMessageBox.critical(self, '查询失败', '执行查询时发生异常:\n%s' % str(exc))
        finally:
            self.progress.setValue(self.progress.maximum())
            QApplication.processEvents()
            self.btn_run.setEnabled(True)

    def _do_query(self):
        pt_layer = self._current_layer(self.cmb_pt)
        poly_layer = self._current_layer(self.cmb_poly)
        if pt_layer is None or poly_layer is None:
            QMessageBox.warning(self, '提示', '请选择点图层和面图层。')
            return

        # 点几何统一转换到面图层坐标系
        xform = QgsCoordinateTransform(
            pt_layer.crs(), poly_layer.crs(),
            QgsProject.instance().transformContext())
        # 面要素(几何/属性)一次性读入内存
        poly_fields = [f.name() for f in poly_layer.fields()]
        polys = []
        for ft in poly_layer.getFeatures():
            g = ft.geometry()
            if g is not None and not g.isEmpty():
                polys.append((g, ft.attributes()))

        # 面积计算（米²）
        da = QgsDistanceArea()
        da.setSourceCrs(poly_layer.crs(), QgsProject.instance().transformContext())
        da.setEllipsoid(QgsProject.instance().ellipsoid())

        src_fields = [f for f in pt_layer.fields()]
        used = list(pt_layer.fields().names())
        # 输出字段：源字段 + 是否在图形内 + 面积 + 面图层字段（重名则加前缀）
        extra = []
        for name in poly_fields:
            fname = name if name not in used else '%s_%s' % (poly_layer.name(), name)
            extra.append(fname)
            used.append(fname)

        match_nearest = self.chk_nearest.isChecked()
        max_dist = self.spin_dist.value()
        one_to_one = self.rb_single.isChecked()

        # 记录原始点顺序，用于1对1去重
        result_rows = []  # (idx, feature, poly_attrs | None, within, area, attrs_vals)
        n_pt = 0
        # 初始化查询进度条
        total = pt_layer.featureCount()
        done = 0
        if total and total > 0:
            self.progress.setRange(0, total)
        else:
            self.progress.setRange(0, 0)  # 不确定模式
        self.progress.setValue(0)
        for feat in pt_layer.getFeatures():
            done += 1
            if total and total > 0:
                self.progress.setValue(done)
            else:
                self.progress.setValue(0)
            QApplication.processEvents()
            g = feat.geometry()
            if g is None or g.isEmpty():
                continue
            pt_g = QgsGeometry(g)  # 拷贝后原地转换，避免 QgsGeometry 重载缺失问题
            pt_g.transform(xform)
            # 以要素的"起始点经纬度"判断是否落在区域内：只要该经纬度点在区域内即算匹配；
            # 同时支持"图形面积占比"匹配（默认图形有 50% 面积在区域内即算匹配）。
            start = _first_point(pt_g)
            if start is None:
                cen = pt_g.centroid()
                start = QgsPointXY(cen.x(), cen.y()) if cen is not None else None
            if start is None:
                continue
            start_g = QgsGeometry.fromPointXY(start)
            bbox = pt_g.boundingBox()
            cands = []
            for pf in poly_layer.getFeatures(QgsFeatureRequest().setFilterRect(bbox)):
                pg = pf.geometry()
                if pg is None or pg.isEmpty():
                    continue
                cands.append((pg, pf.attributes()))
            use_pct = self.chk_pct.isChecked()
            pct_thr = self.spin_pct.value() / 100.0
            total_area = pt_g.area()
            has_area = total_area > 1e-12
            matches = []
            for pg, pa in cands:
                if pg.contains(start_g):
                    matches.append((pg, pa))
                    continue
                if use_pct and has_area:
                    inter = pg.intersection(pt_g)
                    if not inter.isEmpty() and (inter.area() / total_area) >= pct_thr:
                        matches.append((pg, pa))

            if matches:
                for pg, pa in matches:
                    result_rows.append((n_pt, pt_g, pa, '是', da.measureArea(pg),
                                       list(feat.attributes())))
            elif match_nearest:
                closest = None; cdist = None
                buf = max_dist / 111319.9
                exp = QgsRectangle(bbox.xMinimum() - buf, bbox.yMinimum() - buf,
                                   bbox.xMaximum() + buf, bbox.yMaximum() + buf)
                for pf in poly_layer.getFeatures(QgsFeatureRequest().setFilterRect(exp)):
                    pg = pf.geometry()
                    if pg is None or pg.isEmpty():
                        continue
                    try:
                        d = pg.distance(start_g) * 111319.9
                    except Exception as e:
                        QgsMessageLog.logMessage('计算距离失败, 跳过该候选面: %s' % e,
                                                  'Shape_Layer_Tools', Qgis.Warning)
                        continue
                    if closest is None or d < cdist:
                        closest, cdist = pg, d
                if closest is not None and cdist <= max_dist:
                    pa = polys[[p[0] for p in polys].index(closest)] if closest in [p[0] for p in polys] else None
                    result_rows.append((n_pt, pt_g, pa, '否', da.measureArea(closest),
                                        list(feat.attributes())))
                else:
                    result_rows.append((n_pt, pt_g, None, '否', None, list(feat.attributes())))
            else:
                result_rows.append((n_pt, pt_g, None, '否', None, list(feat.attributes())))
            n_pt += 1

        # 1对1：按原始点取面积最小（含面积优先按填‘是’的）的那条
        if one_to_one:
            grouped = {}
            for row in result_rows:
                idx = row[0]
                cur = grouped.get(idx)
                if cur is None or row[4] is not None and (cur[4] is None or row[4] < cur[4]):
                    grouped[idx] = row
            result_rows = [grouped[k] for k in sorted(grouped)]

        if not result_rows:
            QMessageBox.information(self, '查询完成', '未找到任何符合条件的要素。')
            return

        # 构建结果内存图层：几何类型跟随"需查询图层"
        fields = QgsFields()
        for f in src_fields:
            fields.append(QgsField(f.name(), QVariant.String))
        fields.append(QgsField('是否在图形内', QVariant.String))
        fields.append(QgsField('面图层面积(平方米)', QVariant.String))
        for fname in extra:
            fields.append(QgsField(fname, QVariant.String))

        crs_authid = poly_layer.crs().authid() or 'EPSG:4326'
        src_has_geom = pt_layer.wkbType() != QgsWkbTypes.Type.NoGeometry
        if src_has_geom:
            geom_token = QgsWkbTypes.displayString(pt_layer.wkbType()) or 'Point'
            uri = '%s?crs=%s' % (geom_token, crs_authid)
        else:
            uri = 'NoGeometry?crs=%s' % crs_authid
        out_layer = QgsVectorLayer(
            uri, self._unique_name('包含查询结果'), 'memory')
        provider = out_layer.dataProvider()
        provider.addAttributes(fields)
        out_layer.updateFields()

        # 导出表格需要全部匹配结果；图层仅显示"在范围内"(within=='是')的结果
        feats_all = []
        feats_layer = []
        for idx, pt_g, pa, within, area, vals in result_rows:
            f = QgsFeature()
            f.setFields(fields)
            if src_has_geom:
                f.setGeometry(pt_g)
            av = ['' if v is None else str(v) for v in vals]
            av.append(within)
            av.append('' if area is None else ('%.2f' % area))
            if pa is None:
                av += [''] * len(extra)
            else:
                av += ['' if pa[i] is None else str(pa[i]) for i in range(min(len(poly_fields), len(pa)))]
                av += [''] * (len(extra) - min(len(poly_fields), len(pa)))
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
            QgsMessageLog.logMessage('导出后刷新画布/符号失败: %s' % e, 'Shape_Layer_Tools', Qgis.Warning)

        # 导出表格（保留全部匹配结果）
        out_path = self.edit_out.text().strip()
        if not out_path:
            out_path = self.edit_out.text = os.path.expanduser('~/contain_query_result.csv')
        try:
            if out_path.lower().endswith('.xlsx'):
                self._write_xlsx(out_path, fields, feats_all)
            else:
                self._write_csv(out_path, fields, feats_all)
            msg = ('查询完成：共处理 %d 条记录，其中 %d 条在范围内（图层仅显示这些）。\n'
                   '已新增图层 "%s"，并将全部 %d 条匹配结果导出表格到：\n%s') % (
                len(feats_all), len(feats_layer), out_layer.name(), len(feats_all), out_path)
            QMessageBox.information(self, '查询完成', msg)
        except Exception as exc:
            QMessageBox.warning(self, '导出失败', '图层已生成，但表格导出失败：%s' % str(exc))

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