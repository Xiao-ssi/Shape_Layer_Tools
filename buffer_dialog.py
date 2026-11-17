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
                    if bg.type() != QgsWkbTypes.GeometryType.PolygonGeometry:
                        bg = self._polygonal(bg)
                return bg
            else:
                wg = QgsGeometry(g)
                bg = self._single_side_buffer(wg, dist, side == 'left') if is_line \
                    else wg.buffer(dist, 24)
                if bg is not None and not bg.isEmpty() \
                        and bg.type() != QgsWkbTypes.GeometryType.PolygonGeometry:
                    bg = self._polygonal(bg)
                return bg
        except Exception:
            return None

    def _single_side_buffer(self, line_wg, dist, keep_left):
        """生成线的一侧完整距离(dist)的条带：内侧贴着原线，外侧按法向偏移 dist，
        首尾按线段方向直线截断(无半圆、无对侧残留)。返回几何或 None。"""
        b = self._build_side_band(line_wg, dist, keep_left)
        if b is not None and not b.isEmpty():
            return b
        # 兜底：对称缓冲（宁可两侧都有，也不要空结果）
        try:
            buf = line_wg.buffer(dist, 8)
            if buf and not buf.isEmpty():
                QgsMessageLog.logMessage('单边条带构建失败，已退回对称缓冲。',
                                         'Shape_Layer_Tools', Qgis.MessageLevel.Warning)
                return buf
        except Exception as e:
            QgsMessageLog.logMessage('对称缓冲兜底失败: %s' % e, 'Shape_Layer_Tools', Qgis.MessageLevel.Critical)
        return None

    def _build_side_band(self, line_wg, dist, side_left):
        """构建线的一侧条带：逐段生成四边形，转角处补斜接（miter），最后整体求并集。

        两个要点：
        1) 逐段求并集而不是拼环后 makeValid：GEOS 的 makeValid 按奇偶规则修补自交环，
           线条折返/重叠处会被"挖洞"，地图上出现空白；
        2) 转角补的是斜接而不是弦：弦把外侧尖角切平，切点到两条边的垂直距离只有
           d·cos(θ/2)（θ=120° 时正好一半），看起来就像膨胀距离缩水。"""
        try:
            plines = []
            if line_wg.isMultipart():
                m = line_wg.asMultiPolyline()
                plines = [list(p) for p in m] if m else []
            else:
                s = line_wg.asPolyline()
                plines = [list(s)] if s else []

            side_sign = 1.0 if side_left else -1.0
            pieces = []
            for pl in plines:
                if len(pl) < 2:
                    continue
                # 1) 每一段一个四边形：内侧=线段本身，外侧=该段法向偏移
                segs = []      # (b点, 单位方向, 起点外侧点, 终点外侧点)
                for i in range(len(pl) - 1):
                    a, b = pl[i], pl[i + 1]
                    dxy = self._seg_dir(a, b, side_left)
                    if dxy is None:
                        continue
                    nx, ny, ux, uy = dxy
                    oa = QgsPointXY(a.x() + nx * dist, a.y() + ny * dist)
                    ob = QgsPointXY(b.x() + nx * dist, b.y() + ny * dist)
                    segs.append((b, (ux, uy), oa, ob))
                    quad = [QgsPointXY(a.x(), a.y()), QgsPointXY(b.x(), b.y()), ob, oa]
                    q = QgsGeometry.fromPolygonXY([quad])
                    if q is not None and not q.isEmpty():
                        pieces.append(q)
                # 2) 外凸转角补斜接/圆弧；内凹转角已被两个四边形覆盖
                for i in range(len(segs) - 1):
                    v = segs[i][0]
                    joint = self._corner_piece(
                        v, segs[i][3], segs[i][1], segs[i + 1][2], segs[i + 1][1],
                        dist, side_sign)
                    if joint is not None:
                        pieces.append(joint)

            if not pieces:
                return None
            return self._union_all(pieces)
        except Exception as exc:
            QgsMessageLog.logMessage('构建单边条带失败: %s' % exc,
                                     'Shape_Layer_Tools', Qgis.MessageLevel.Warning)
            return None

    @staticmethod
    def _corner_piece(v, o_prev, u_in, o_cur, u_out, dist, side_sign, miter_limit=4.0):
        """外凸转角的面片：优先斜接(miter)，斜接长度超限时改用半径 dist 的圆弧。

        o_prev/o_cur: 前一段终点、后一段起点的外侧偏移点；u_in/u_out: 单位方向。
        内凹转角返回 None（相邻两个四边形已经覆盖）。"""
        cross = u_in[0] * u_out[1] - u_in[1] * u_out[0]
        # 内凹（转向朝着偏移侧）：无需补面
        if cross * side_sign > 1e-12:
            return None
        vx, vy = v.x(), v.y()
        if abs(cross) < 1e-12:
            # 平行：同向无事；180° 原路折返补半圆，避免尖端只剩一条边
            dot = u_in[0] * u_out[0] + u_in[1] * u_out[1]
            if dot > 0:
                return None
            return BufferDialog._arc_piece(v, o_prev, o_cur, dist)
        # 斜接点：两条偏移线的交点
        dx, dy = o_cur.x() - o_prev.x(), o_cur.y() - o_prev.y()
        t = (dx * u_out[1] - dy * u_out[0]) / cross
        mx = o_prev.x() + u_in[0] * t
        my = o_prev.y() + u_in[1] * t
        if math.hypot(mx - vx, my - vy) <= dist * miter_limit:
            ring = [QgsPointXY(vx, vy),
                    QgsPointXY(o_prev.x(), o_prev.y()),
                    QgsPointXY(mx, my),
                    QgsPointXY(o_cur.x(), o_cur.y())]
            g = QgsGeometry.fromPolygonXY([ring])
            if g is not None and not g.isEmpty():
                return g
        return BufferDialog._arc_piece(v, o_prev, o_cur, dist)

    @staticmethod
    def _arc_piece(v, o_prev, o_cur, dist, step=math.pi / 16.0):
        """以顶点为圆心、半径 dist 的扇形，用来补过锐转角（保证转角处仍是完整宽度）。"""
        if dist <= 0:
            return None
        a0 = math.atan2(o_prev.y() - v.y(), o_prev.x() - v.x())
        a1 = math.atan2(o_cur.y() - v.y(), o_cur.x() - v.x())
        delta = a1 - a0
        while delta > math.pi:
            delta -= 2 * math.pi
        while delta < -math.pi:
            delta += 2 * math.pi
        if abs(delta) < 1e-9:      # 两个偏移点重合，没有可补的面积
            return None
        steps = max(2, int(math.ceil(abs(delta) / step)))
        ring = [QgsPointXY(v.x(), v.y())]
        for k in range(steps + 1):
            ang = a0 + delta * k / steps
            ring.append(QgsPointXY(v.x() + math.cos(ang) * dist,
                                   v.y() + math.sin(ang) * dist))
        g = QgsGeometry.fromPolygonXY([ring])
        return g if g is not None and not g.isEmpty() else None

    @staticmethod
    def _seg_dir(a, b, side_left):
        """线段 a→b 的单位法向与单位方向 (nx, ny, ux, uy)；零长度返回 None。"""
        dx, dy = b.x() - a.x(), b.y() - a.y()
        length = math.hypot(dx, dy)
        if length < 1e-12:
            return None
        ux, uy = dx / length, dy / length
        if side_left:
            return -uy, ux, ux, uy
        return uy, -ux, ux, uy

    def _union_all(self, pieces, chunk=2000):
        """对多边形列表求并集，返回面几何（Polygon/MultiPolygon）或 None。"""
        geoms = [g for g in pieces if g is not None and not g.isEmpty()]
        if not geoms:
            return None
        # 分块归并，避免一次把上万个面丢给 GEOS
        while len(geoms) > chunk:
            nxt = []
            for i in range(0, len(geoms), chunk):
                merged = self._union_chunk(geoms[i:i + chunk])
                if merged is not None and not merged.isEmpty():
                    nxt.append(merged)
            if not nxt:
                return None
            geoms = nxt
        return self._union_chunk(geoms)

    @staticmethod
    def _union_chunk(geoms):
        """单个块求并：优先 unaryUnion，取不到就逐块 combine。"""
        if not geoms:
            return None
        if len(geoms) == 1:
            return BufferDialog._polygonal(geoms[0])
        merged = None
        try:
            merged = QgsGeometry.unaryUnion(geoms)
        except Exception as exc:
            QgsMessageLog.logMessage('unaryUnion 不可用，改用逐块合并: %s' % exc,
                                     'Shape_Layer_Tools', Qgis.MessageLevel.Info)
            merged = None
        if merged is None or merged.isEmpty():
            merged = None
            for g in geoms:
                if merged is None:
                    merged = g
                    continue
                try:
                    combined = merged.combine(g)
                except Exception:
                    combined = None
                if combined is not None and not combined.isEmpty():
                    merged = combined
        return BufferDialog._polygonal(merged)

    @staticmethod
    def _polygonal(geom):
        """从任意几何中抽取面部分，返回 Polygon/MultiPolygon；没有面则返回 None。"""
        if geom is None or geom.isEmpty():
            return None
        poly_type = QgsWkbTypes.GeometryType.PolygonGeometry
        if geom.type() == poly_type:
            return geom
        rings = []

        def _walk(g):
            if g is None or g.isEmpty():
                return
            if g.type() == poly_type:
                if g.isMultipart():
                    multi = g.asMultiPolygon()
                    if multi:
                        rings.extend(multi)
                else:
                    single = g.asPolygon()
                    if single:
                        rings.append(single)
                return
            # 只有多部件/集合才继续下钻，避免对线、点反复递归
            if g.isMultipart():
                for i in range(g.geometryCount()):
                    _walk(g.getGeometry(i))

        _walk(geom)
        if not rings:
            return None
        return QgsGeometry.fromMultiPolygonXY(rings)

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
        # 用 MultiPolygon：单边膨胀遇到线条折返/重叠时结果是多块，Polygon 类型会被截断
        fields = QgsFields()
        for f in src.fields():
            fields.append(QgsField(f.name(), QVariant.String))
        out_layer = QgsVectorLayer(
            'MultiPolygon?crs=%s' % (crs.authid() or 'EPSG:4326'),
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
            # QGIS 3 的 makeValid() 返回新几何、不改自身，之前直接丢弃了修复结果
            repaired = g.makeValid()
            if repaired is None or repaired.isEmpty():
                _skip('源几何修复失败')
                skipped += 1
                continue
            g = repaired
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