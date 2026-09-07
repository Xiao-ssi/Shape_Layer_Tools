# -*- coding: utf-8 -*-
"""缓冲期(膨胀)生成：对所选图层的几何沿边缘向外/向内/两边扩展指定距离(米)生成新图层。
本模块独立，可单独修改而不影响其他功能模块。"""
import math

from qgis.core import (
    Qgis,
    QgsProject,
    QgsVectorLayer,
    QgsField,
    QgsFeature,
    QgsGeometry,
    QgsWkbTypes,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFields,
    QgsPointXY,
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
    QDoubleSpinBox,
    QRadioButton,
    QButtonGroup,
    QPushButton,
    QMessageBox,
    QApplication,
)
from .utils import session_get, session_set


class BufferDialog(QDialog):
    """缓冲膨胀：选择图层，按边界向外/向内/两边扩展指定米数，生成"原图层名-膨胀"新图层。"""

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.setWindowTitle('图层缓冲膨胀缩小')
        self.resize(460, 320)
        self._build_ui()
        self._restore_session()
        self.refresh_layers()

    def _build_ui(self):
        lay = QVBoxLayout(self)

        gb_layer = QGroupBox('选择图层')
        f_ly = QFormLayout(gb_layer)
        self.cmb_layer = QComboBox()
        self.cmb_layer.currentIndexChanged.connect(self._on_layer_changed)
        f_ly.addRow('图层：', self.cmb_layer)
        self.lbl_hint = QLabel('')
        self.lbl_hint.setStyleSheet('color:#888;')
        f_ly.addRow('说明：', self.lbl_hint)
        lay.addWidget(gb_layer)

        gb_opt = QGroupBox('缓冲（膨胀）设置')
        f_ot = QFormLayout(gb_opt)
        self.edit_dist = QDoubleSpinBox()
        self.edit_dist.setRange(0.1, 10000000.0)
        self.edit_dist.setDecimals(1)
        self.edit_dist.setValue(50.0)
        self.edit_dist.setSuffix(' 米')
        self.edit_dist.setSingleStep(10.0)
        f_ot.addRow('扩展距离：', self.edit_dist)

        # 膨胀方向
        self.rb_out = QRadioButton('向外扩大（或线左侧扩大）')
        self.rb_in = QRadioButton('缩小区域（或线右侧扩大）')
        self.rb_both = QRadioButton('两边扩大')
        self.rb_out.setChecked(True)
        g_dir = QButtonGroup(self)
        g_dir.addButton(self.rb_out, 0)
        g_dir.addButton(self.rb_in, 1)
        g_dir.addButton(self.rb_both, 2)
        h_dir = QHBoxLayout()
        h_dir.addWidget(self.rb_out); h_dir.addWidget(self.rb_in); h_dir.addWidget(self.rb_both)
        h_dir.addStretch()
        f_ot.addRow('扩大方向：', h_dir)
        lay.addWidget(gb_opt)

        # 状态
        self.lbl_status = QLabel('请选择图层并设置扩展距离。')
        self.lbl_status.setStyleSheet('color:#666;')
        lay.addWidget(self.lbl_status)

        hb = QHBoxLayout()
        self.btn_run = QPushButton('生成膨胀图层'); self.btn_run.clicked.connect(self.on_run)
        btn_close = QPushButton('关闭'); btn_close.clicked.connect(self.reject)
        hb.addStretch(); hb.addWidget(self.btn_run); hb.addWidget(btn_close)
        lay.addLayout(hb)

    def refresh_layers(self):
        items = []
        for lid, ly in QgsProject.instance().mapLayers().items():
            if not isinstance(ly, QgsVectorLayer) or ly.geometryType() == QgsWkbTypes.Type.NoGeometry:
                continue
            items.append((ly.name(), lid))
        self.cmb_layer.blockSignals(True)
        self.cmb_layer.clear()
        if not items:
            self.cmb_layer.addItem('（无几何图层面）', '')
        for name, lid in items:
            self.cmb_layer.addItem(name, lid)
        # 恢复上次选择的图层
        prev = session_get('buffer', 'layer_id')
        if prev:
            idx = self.cmb_layer.findData(prev)
            if idx >= 0:
                self.cmb_layer.setCurrentIndex(idx)
        self.cmb_layer.blockSignals(False)
        self._on_layer_changed()

    def _restore_session(self):
        dist = session_get('buffer', 'dist', self.edit_dist.value())
        try:
            self.edit_dist.setValue(float(dist))
        except (TypeError, ValueError, AttributeError) as e:
            QgsMessageLog.logMessage('恢复扩展距离设置失败: %s' % e, 'Shape_Layer_Tools', Qgis.MessageLevel.Warning)
        dir_idx = session_get('buffer', 'dir', 0)
        (self.rb_out if dir_idx == 0 else self.rb_in if dir_idx == 1 else self.rb_both).setChecked(True)

    def _save_session(self):
        session_set('buffer', 'layer_id', self.cmb_layer.currentData())
        session_set('buffer', 'dist', self.edit_dist.value())
        dir_idx = 0 if self.rb_out.isChecked() else 1 if self.rb_in.isChecked() else 2
        session_set('buffer', 'dir', dir_idx)

    def _current_layer(self):
        lid = self.cmb_layer.currentData()
        if not lid:
            return None
        return QgsProject.instance().mapLayer(lid)

    def _on_layer_changed(self):
        layer = self._current_layer()
        if layer is None:
            self.lbl_hint.setText('')
            return
        t = layer.geometryType()
        if t == QgsWkbTypes.GeometryType.PointGeometry:
            self.lbl_hint.setText('点图层：向四周膨胀。')
        elif t == QgsWkbTypes.GeometryType.LineGeometry:
            self.lbl_hint.setText('线图层：向外扩大=线一侧、缩小区域=另一侧、两边=两侧；均按完整距离，方向垂直于线（任意走向适用）。')
        else:
            self.lbl_hint.setText('面图层：向外=整体外扩（含原区域）、向内=向内收缩、两边=边界向内外各扩一半。')

    def _unique_name(self, base):
        existing = {ly.name() for ly in QgsProject.instance().mapLayers().values()}
        if base not in existing:
            return base
        i = 2
        while ('%s-%d' % (base, i)) in existing:
            i += 1
        return '%s-%d' % (base, i)

    @staticmethod
    def _resolve_direction(geom_type, dir_idx, dist):
        """返回 (buff_dist, side)。side: None对称缓冲 / 'left'左侧 / 'right'右侧。
        dir_idx: 0向外 1向内 2两边。点无内部，内扩按外扩处理。"""
        if geom_type == QgsWkbTypes.GeometryType.LineGeometry:
            if dir_idx == 0:       # 向外扩大 → 向线左侧扩大
                return  dist, 'left'
            if dir_idx == 1:       # 向内扩大 → 向线右侧扩大
                return  dist, 'right'
            return  dist, None     # 两边扩大 → 两侧同时扩大
        if geom_type == QgsWkbTypes.GeometryType.PointGeometry:
            return  dist, None     # 点：向四周
        # 面图层
        if dir_idx == 1:
            return -dist, None     # 向内收缩
        if dir_idx == 2:
            return  dist / 2.0, None  # 两边：边界向内外各扩一半
        return  dist, None         # 向外（含原区域）

    def _buffer_geometry(self, g, crs, dist, side=None):
        """将几何 g(所在crs) 缓冲 dist(米) 生成新几何，返回同crs的结果，失败返回 None。
        side: None对称缓冲 / 'left'一侧 / 'right'另一侧(仅线图层，方向垂直于线)。
        地理坐标系(如WGS84)先转到 EPSG:3857 按米缓冲，并做纬度比例校正后转回。"""
        try:
            is_line = side in ('left', 'right') and g is not None \
                and g.type() == QgsWkbTypes.GeometryType.LineGeometry
            if crs.isGeographic():
                work = QgsCoordinateReferenceSystem('EPSG:3857')
                x_to = QgsCoordinateTransform(crs, work, QgsProject.instance().transformContext())
                x_back = QgsCoordinateTransform(work, crs, QgsProject.instance().transformContext())
                c = g.centroid().asPoint()
                lat = c.y()
                cosv = math.cos(math.radians(lat))
                scale = (1.0 / cosv) if abs(cosv) > 1e-6 else 100.0
                wg = QgsGeometry(g)
                wg.transform(x_to)
                bg = self._single_side_buffer(wg, dist * scale, side == 'left') if is_line \
                    else wg.buffer(dist * scale, 24)
                if bg and not bg.isEmpty():
                    bg.transform(x_back)
                return bg
            else:
                wg = QgsGeometry(g)
                bg = self._single_side_buffer(wg, dist, side == 'left') if is_line \
                    else wg.buffer(dist, 24)
                return bg
        except Exception:
            return None

    def _single_side_buffer(self, line_wg, dist, keep_left):
        """生成线的一侧完整距离(dist)的矩形条带：内侧=原线，外侧=沿线各顶点
        垂直偏移 dist，首尾按线段方向直线截断(无半圆、无对侧残留、无空白)。
        返回几何或 None。"""
        b = self._build_side_band(line_wg, dist, keep_left)
        if b is not None and not b.isEmpty():
            return b
        # 兜底：对称缓冲（避免空结果）
        try:
            buf = line_wg.buffer(dist, 8)
            if buf and not buf.isEmpty():
                return buf
        except Exception as e:
            QgsMessageLog.logMessage('对称缓冲兜底失败: %s' % e, 'Shape_Layer_Tools', Qgis.MessageLevel.Critical)
        return None

    def _build_side_band(self, line_wg, dist, side_left):
        """构建线的一侧条带多边形：内侧=原线，外侧=各顶点沿垂直到该侧偏移 dist。
        跟随线的走向，适用于曲线。返回几何或 None。"""
        try:
            plines = []
            if line_wg.isMultipart():
                m = line_wg.asMultiPolyline()
                plines = [list(p) for p in m] if m else []
            else:
                s = line_wg.asPolyline()
                plines = [list(s)] if s else []
            polys = []
            for pl in plines:
                if len(pl) < 2:
                    continue
                off = []
                for i in range(len(pl)):
                    if i == 0:
                        a, b = pl[0], pl[1]
                    elif i == len(pl) - 1:
                        a, b = pl[-2], pl[-1]
                    else:
                        a, b = pl[i - 1], pl[i + 1]
                    dx, dy = b.x() - a.x(), b.y() - a.y()
                    L = math.hypot(dx, dy)
                    if L < 1e-12:
                        off.append(QgsPointXY(pl[i].x(), pl[i].y()))
                        continue
                    nx, ny = (-dy / L, dx / L) if side_left else (dy / L, -dx / L)
                    off.append(QgsPointXY(pl[i].x() + nx * dist, pl[i].y() + ny * dist))
                if len(off) < 2:
                    continue
                ring = [QgsPointXY(p.x(), p.y()) for p in pl]
                ring.extend(off[::-1])
                poly = QgsGeometry.fromPolygonXY([ring])
                if poly is None or poly.isEmpty():
                    continue
                poly.makeValid()
                polys.append(poly)
            if not polys:
                return None
            if len(polys) == 1:
                return polys[0]
            m = None
            for p in polys:
                m = p if m is None else m.combine(p)
            if m is not None:
                m.makeValid()
            return m
        except Exception:
            return None

    def on_run(self):
        try:
            self.btn_run.setEnabled(False)
            self._do_buff()
        except Exception as exc:
            import traceback; traceback.print_exc()
            QMessageBox.critical(self, '生成失败', '生成膨胀图层时发生异常:\n%s' % str(exc))
        finally:
            self.btn_run.setEnabled(True)

    def _do_buff(self):
        self._save_session()
        src = self._current_layer()
        if src is None:
            QMessageBox.warning(self, '提示', '请先选择一个要膨胀的图层。')
            return
        dist_m = self.edit_dist.value()
        if dist_m <= 0:
            QMessageBox.warning(self, '提示', '扩展距离必须大于 0。')
            return

        geom_type = src.geometryType()
        dir_idx = [self.rb_out, self.rb_in, self.rb_both].index(
            next((r for r in (self.rb_out, self.rb_in, self.rb_both) if r.isChecked()), self.rb_out))
        buff_dist, side = self._resolve_direction(geom_type, dir_idx, dist_m)

        crs = src.crs()
        if not crs.isValid():
            crs = QgsCoordinateReferenceSystem('EPSG:4326')

        # 新图层：几何为面，字段继承源字段，命名为 "源图层名-膨胀"
        fields = QgsFields()
        for f in src.fields():
            fields.append(QgsField(f.name(), QVariant.String))
        out_layer = QgsVectorLayer(
            'Polygon?crs=%s' % (crs.authid() or 'EPSG:4326'),
            self._unique_name(src.name() + '-膨胀'), 'memory')
        provider = out_layer.dataProvider()
        provider.addAttributes(fields)
        out_layer.updateFields()

        total = src.featureCount()
        done = 0
        ok = 0
        skipped = 0
        reasons = {}
        def _skip(r):
            reasons[r] = reasons.get(r, 0) + 1
        for feat in src.getFeatures():
            done += 1
            if total and total > 0:
                self.lbl_status.setText('正在处理 %d/%d ...' % (done, total))
            else:
                self.lbl_status.setText('正在处理 %d 个要素 ...' % done)
            QApplication.processEvents()

            g = feat.geometry()
            if g is None:
                _skip('源要素无几何')
                skipped += 1
                continue
            if g.isEmpty():
                _skip('源要素空几何')
                skipped += 1
                continue
            if not g.makeValid():
                skipped += 1
                _skip('源几何修复失败')
                continue
            bg = self._buffer_geometry(g, crs, buff_dist, side)
            if bg is None:
                _skip('缓冲生成失败(_buffer_geometry返回None)')
                skipped += 1
                continue
            if bg.isEmpty():
                _skip('缓冲几何为空')
                skipped += 1
                continue

            nf = QgsFeature()
            nf.setFields(fields)
            nf.setGeometry(bg)
            for f in src.fields():
                nf.setAttribute(f.name(), feat[f.name()] if feat[f.name()] is not None else '')
            try:
                okadd = provider.addFeature(nf)
            except Exception as e:
                okadd = False
                _skip('addFeature异常:%s' % e)
            if not okadd:
                _skip('addFeature返回失败')
                skipped += 1
            else:
                ok += 1

        out_layer.updateExtents()

        if ok == 0:
            detail = '；'.join('%s: %s个' % (k, v) for k, v in reasons.items())
            QMessageBox.information(self, '生成完成',
                                    '未能生成任何有效膨胀要素。\n跳过明细: %s' % detail)
            return

        QgsProject.instance().addMapLayer(out_layer, True)
        try:
            canvas = self.iface.mapCanvas()
            if canvas and not out_layer.extent().isEmpty():
                canvas.setExtent(out_layer.extent())
            canvas.refresh()
            self.iface.layerTreeView().refreshLayerSymbology(out_layer.id())
        except Exception as e:
            QgsMessageLog.logMessage('生成后刷新画布/符号失败: %s' % e, 'Shape_Layer_Tools', Qgis.MessageLevel.Warning)

        self.lbl_status.setText('完成。')
        QMessageBox.information(
            self, '生成完成',
            '已为图层"%s"生成膨胀图层"%s"：共处理 %d 个要素，成功 %d 个，跳过 %d 个。\n'
            '线图层：向外扩大=线一侧、缩小区域=另一侧、两边=两侧（均为完整距离）；'
            '面图层"向外/两边"已包含原图层区域。'
            % (src.name(), out_layer.name(), total if total and total > 0 else done, ok, skipped))