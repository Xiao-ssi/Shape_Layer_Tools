# -*- coding: utf-8 -*-
"""主对话框：读取 Excel/CSV(快速仅标题) → 生成形状图层 → 导出 SHP/TAB/KML/KMZ。
本模块独立，可单独修改而不影响其他功能模块的正常运行。"""
import os
import math

from qgis.core import (
    Qgis,
    QgsProject,
    QgsVectorLayer,
    QgsField,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsFields,
    QgsVectorFileWriter,
    QgsWkbTypes,
    QgsCoordinateTransformContext,
    QgsFillSymbol,
    QgsCategorizedSymbolRenderer,
    QgsRendererCategory,
    QgsSingleSymbolRenderer,
    QgsMessageLog,
)
from qgis.PyQt.QtCore import Qt, QVariant
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QComboBox,
    QPushButton,
    QFileDialog,
    QMessageBox,
    QTabWidget,
    QWidget,
    QGridLayout,
    QDialogButtonBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
)

from . import shapes
from .utils import (_to_float, _distinct_colors, SHAPE_LABELS, SHAPE_LIST_ORDER,
                    WRITER_ERROR_TEXT)
from .sources import open_source
from .ui_components import ColorButton, ClassifyConfigDialog
from .search_dialog import SearchWidget

EXPORT_FORMATS = [
    ('SHP（ESRI Shapefile）', 'ESRI Shapefile', '.shp'),
    ('TAB（MapInfo）', 'MapInfo File', '.tab'),
    ('KML（Google Earth）', 'KML', '.kml'),
    ('KMZ（Google Earth 压缩）', 'LIBKML', '.kmz'),
]

SIZE_UNIT_TIP = '尺寸用于圆点/扇形时表示半径(米)，用于方形/剑形时表示边长/长度(米)。方位角、尺寸可输入数字或选择列标题。'
FILE_FILTER = '表格文件 (*.csv *.xlsx *.xlsm *.xls *.xlsb);;所有文件 (*.*)'


class ExcelShapeLayerDialog(QDialog):
    def __init__(self, iface, parent=None, initial_tab=0):
        super().__init__(parent)
        self.iface = iface
        self.source = None
        self._initial_tab = initial_tab
        self.headers = []
        self._rows_cache = None
        self._distinct_cache = {}
        self.created_layers = []

        self.global_fill = QColor('#00b050')
        self.global_line = QColor(0, 0, 0, 0)  # 默认无线条边框（透明）
        self.classify_config = {}
        self._classify_field = None

        self.setWindowTitle('Excel 图形图层生成与导出')
        self.resize(640, 780)
        self._build_ui()
        # 工程中图层增删时同步刷新"导出图层"下拉（兼容旧版 QGIS 的 layersAdded/layersRemoved）
        project = QgsProject.instance()
        project.layersAdded.connect(self._refresh_layer_combo)
        project.layersRemoved.connect(self._refresh_layer_combo)
        self._refresh_layer_combo()
        self._connect()

    # ============================ 界面 ============================
    def _build_ui(self):
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.tab_search = self._build_search_tab()
        self.tabs.addTab(self._build_generate_tab(), '生成图层')
        self.tabs.addTab(self._build_export_tab(), '导出图层')
        self.tabs.addTab(self.tab_search, '搜索数据')
        layout.addWidget(self.tabs)
        tip = QLabel(SIZE_UNIT_TIP)
        tip.setStyleSheet('color:#666;'); tip.setWordWrap(True)
        layout.addWidget(tip)
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def _build_generate_tab(self):
        page = QWidget()
        root = QVBoxLayout(page)

        # ---- 数据来源 ----
        grp = QGroupBox('数据来源（快速加载标题）')
        form = QFormLayout(grp)
        self.edit_file = QLineEdit(); self.edit_file.setReadOnly(True)
        self.edit_file.setPlaceholderText('选择 CSV / Excel 文件')
        btn_file = QPushButton('浏览...'); btn_file.clicked.connect(self.on_browse_file)
        r = QHBoxLayout(); r.addWidget(self.edit_file); r.addWidget(btn_file)
        form.addRow('文件:', r)
        self.cmb_sheet = QComboBox()
        self.cmb_sheet.currentIndexChanged.connect(self.on_sheet_changed)
        form.addRow('工作表:', self.cmb_sheet)
        # 分类字段：选择后才展开分类设置
        self.cmb_classify = QComboBox()
        self.cmb_classify.addItem('（不使用分类）', '')
        self.cmb_classify.currentIndexChanged.connect(self.on_classify_changed)
        self.btn_classify_cfg = QPushButton('设置各类图形...')
        self.btn_classify_cfg.setEnabled(False)
        self.btn_classify_cfg.clicked.connect(self.on_edit_classify)
        r_cls = QHBoxLayout(); r_cls.addWidget(self.cmb_classify, 1); r_cls.addWidget(self.btn_classify_cfg)
        form.addRow('分类字段:', r_cls)
        g = QGridLayout()
        self.cmb_x = QComboBox(); self.cmb_y = QComboBox()
        g.addWidget(QLabel('经度列(X):'), 0, 0); g.addWidget(self.cmb_x, 0, 1)
        g.addWidget(QLabel('纬度列(Y):'), 1, 0); g.addWidget(self.cmb_y, 1, 1)
        form.addRow(g)
        root.addWidget(grp)
        self.lbl_status = QLabel('请选择文件')
        self.lbl_status.setStyleSheet('color:#666;')
        root.addWidget(self.lbl_status)

        # ---- 方向（始终显示） ----
        grp_dir = QGroupBox('方向')
        fdir = QFormLayout(grp_dir)
        self.cmb_bearing = self._editable_combo([], '0')
        fdir.addRow('方位角(度,0=北):', self.cmb_bearing)
        root.addWidget(grp_dir)

        # ---- 单要素全局设置（不使用分类时显示） ----
        grp_single = QGroupBox('全局设置（不使用分类时）')
        form = QFormLayout(grp_single)
        self.cmb_shape = QComboBox()
        for k in SHAPE_LIST_ORDER:
            self.cmb_shape.addItem(SHAPE_LABELS[k], k)
        self.cmb_shape.currentIndexChanged.connect(self.on_shape_changed)
        form.addRow('形状类型:', self.cmb_shape)
        self.cmb_size = self._editable_combo([], '100')
        form.addRow('尺寸(米):', self.cmb_size)
        self.lbl_width = QLabel('扇形顶角(度):')
        self.edit_width = QLineEdit('60')
        form.addRow(self.lbl_width, self.edit_width)
        self.lbl_width.hide(); self.edit_width.hide()
        self.btn_fill = ColorButton(self.global_fill)
        self.btn_line = ColorButton(self.global_line)
        hc = QHBoxLayout()
        hc.addWidget(QLabel('填充:')); hc.addWidget(self.btn_fill)
        hc.addSpacing(12); hc.addWidget(QLabel('边框:')); hc.addWidget(self.btn_line)
        hc.addStretch()
        form.addRow('颜色:', hc)
        self.grp_single = grp_single
        root.addWidget(grp_single)

        # ---- 分类设置（选择分类字段后展开） ----
        grp_cls = QGroupBox('分类设置（每类单独颜色/尺寸/样式）')
        self.grp_cls = grp_cls
        v = QVBoxLayout(grp_cls)
        self.table_class = QTableWidget(0, 6)
        self.table_class.setHorizontalHeaderLabels(
            ['分类值', '样式', '尺寸(米)', '顶角(度)/剑宽(米)', '填充', '边框'])
        self.table_class.verticalHeader().setVisible(False)
        v.addWidget(self.table_class)
        self.grp_cls.hide()
        root.addWidget(grp_cls)

        # ---- 图层设置 ----
        grp = QGroupBox('图层设置')
        form = QFormLayout(grp)
        self.edit_name = QLineEdit('Excel形状图层')
        form.addRow('图层名称:', self.edit_name)
        self.cmb_crs = QComboBox()
        self.cmb_crs.addItem('EPSG:4326 (WGS84 经纬度)', 'EPSG:4326')
        self.cmb_crs.addItem('EPSG:4490 (CGCS2000 经纬度)', 'EPSG:4490')
        form.addRow('坐标系:', self.cmb_crs)
        root.addWidget(grp)

        self.btn_create = QPushButton('创建图层')
        self.btn_create.clicked.connect(self.on_create_layer)
        root.addWidget(self.btn_create)
        root.addStretch()
        return page

    @staticmethod
    def _editable_combo(headers, default):
        cmb = QComboBox()
        cmb.setEditable(True); cmb.clear(); cmb.addItems(headers)
        cmb.setCurrentText(default)
        return cmb

    def _build_export_tab(self):
        page = QWidget(); root = QVBoxLayout(page)
        grp = QGroupBox('导出设置'); form = QFormLayout(grp)
        self.cmb_layer = QComboBox()
        self.cmb_layer.addItem('（请先在上方生成图层）', None)
        form.addRow('选择图层:', self.cmb_layer)
        self.cmb_format = QComboBox()
        for label, driver, ext in EXPORT_FORMATS:
            self.cmb_format.addItem(label, (driver, ext))
        form.addRow('导出格式:', self.cmb_format)
        self.edit_outdir = QLineEdit(); self.edit_outdir.setReadOnly(True)
        self.edit_outdir.setPlaceholderText('输出目录（不填则默认源文件目录）')
        btn_dir = QPushButton('选择目录...'); btn_dir.clicked.connect(self.on_browse_dir)
        r = QHBoxLayout(); r.addWidget(self.edit_outdir); r.addWidget(btn_dir)
        form.addRow('输出目录:', r)
        root.addWidget(grp)
        self.btn_export = QPushButton('导出到本地')
        self.btn_export.clicked.connect(self.on_export)
        root.addWidget(self.btn_export, alignment=Qt.AlignmentFlag.AlignLeft)
        root.addStretch()
        return page

    # ---- 搜索数据：复用 SearchWidget 组件 ----
    def _build_search_tab(self):
        page = QWidget(); root = QVBoxLayout(page)
        root.addWidget(SearchWidget(self.iface, page))
        return page

    def _connect(self):
        pass

    def showEvent(self, event):
        super().showEvent(event)
        # 定位到初始化时指定的标签页（0=生成图层，1=导出图层，2=搜索数据）
        if 0 <= self._initial_tab < self.tabs.count():
            self.tabs.setCurrentIndex(self._initial_tab)
        # 标签页内的搜索组件也随窗口刷新图层列表
        page = self.tabs.widget(self.tabs.indexOf(self.tab_search))
        if page:
            for w in page.findChildren(SearchWidget):
                w.refresh_layers()

    def switch_tab(self, index):
        """供菜单入口调用：显示窗口并切换到指定标签页。"""
        self._initial_tab = index
        self.show()
        self.raise_()
        self.activateWindow()

    # ============================ 数据 ============================
    def on_browse_file(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择表格文件', '', FILE_FILTER)
        if not path:
            return
        self.edit_file.setText(path)
        self.load_file(path)

    def load_file(self, path):
        src, err = open_source(path)
        if src is None:
            QMessageBox.warning(self, '读取失败', err)
            return
        self.source = src
        self.headers = []
        self._rows_cache = None
        self._distinct_cache = {}
        # 默认图层名称 = 文件名（不含扩展名）
        self.edit_name.setText(os.path.splitext(os.path.basename(path))[0])
        self.cmb_sheet.blockSignals(True)
        self.cmb_sheet.clear()
        self.cmb_sheet.addItems(src.names())
        self.cmb_sheet.blockSignals(False)
        self._load_current_sheet()

    def on_sheet_changed(self):
        self._load_current_sheet()

    def _load_current_sheet(self):
        if self.source is None:
            return
        sheet = self.cmb_sheet.currentText()
        if not sheet:
            return
        # 仅读取首行标题（快速）
        self.headers = self.source.header(sheet)
        self._rows_cache = None
        self._distinct_cache = {}
        self._populate_header_combos()
        self.lbl_status.setText('已加载工作表 "%s" 标题，共 %d 个字段' % (sheet, len(self.headers)))

    def _populate_header_combos(self):
        hdr = self.headers
        auto_x = next((c for c in ['lon', 'lng', 'longitude', '经度', 'Longitude', 'X', 'x', '经']
                       if c in hdr), hdr[0] if hdr else '')
        auto_y = next((c for c in ['lat', 'latitude', '纬度', 'Latitude', 'Y', 'y', '纬']
                       if c in hdr), hdr[1] if len(hdr) > 1 else '')
        for cmb in (self.cmb_x, self.cmb_y):
            cmb.blockSignals(True); cmb.clear(); cmb.addItems(hdr); cmb.blockSignals(False)
        self.cmb_classify.blockSignals(True)
        self.cmb_classify.clear()
        self.cmb_classify.addItem('（不使用分类）', '')
        for h in hdr:
            self.cmb_classify.addItem(h, h)  # data=字段名，供 on_classify_changed 识别
        self.cmb_classify.blockSignals(False)
        if auto_x in hdr:
            self.cmb_x.setCurrentText(auto_x)
        if auto_y in hdr:
            self.cmb_y.setCurrentText(auto_y)
        # 方位角字段自动匹配：优先匹配含"方位角"或"方向角"的列表头（模糊含其他字符）
        auto_bearing = next((c for c in hdr if ('方位角' in c) or ('方向角' in c)), None)
        for cmb, default in ((self.cmb_bearing, '0'), (self.cmb_size, '100')):
            cmb.blockSignals(True)
            cur = cmb.currentText()
            cmb.clear(); cmb.addItems(hdr)
            if cmb is self.cmb_bearing and auto_bearing:
                cmb.setCurrentText(auto_bearing)
            else:
                cmb.setCurrentText(cur if cur in hdr else default)
            cmb.blockSignals(False)

    def get_rows(self):
        if self._rows_cache is None:
            sheet = self.cmb_sheet.currentText()
            self._rows_cache = self.source.all_rows(sheet)
        return self._rows_cache

    def _distinct_values(self, field):
        key = (self.cmb_sheet.currentText(), field)
        if key not in self._distinct_cache:
            seen, order = set(), []
            for row in self.get_rows():
                v = row.get(field)
                if v is None or v == '':
                    continue
                s = str(v).strip()
                if not s:
                    continue
                if s not in seen:
                    seen.add(s); order.append(s)
            self._distinct_cache[key] = order
        return self._distinct_cache[key]

    # ============================ 联动 ============================
    def _default_class_shape(self):
        """分类样式的默认类型：若选择了方位角字段则默认扇形，否则取全局形状类型。"""
        if self.cmb_bearing.currentText() in self.headers:
            return 'sector'
        return self.cmb_shape.currentData()

    def on_shape_changed(self):
        shape = self.cmb_shape.currentData()
        is_sec = shape == 'sector'; is_swd = shape == 'sword'
        if shape in ('circle', 'sector'):
            self.lbl_width.setText('扇形顶角(度):'); self.edit_width.setText('60')
        else:
            self.lbl_width.setText('剑宽(米):'); self.edit_width.setText('30')
        self.lbl_width.setVisible(is_sec or is_swd)
        self.edit_width.setVisible(is_sec or is_swd)

    def on_classify_changed(self):
        field = self.cmb_classify.currentData()
        need_repopulate = (field != self._classify_field)
        self._classify_field = field
        self.btn_classify_cfg.setEnabled(bool(field))
        # 选择分类字段后才显示分类设置，否则显示全局设置
        if not field:
            self.grp_cls.hide()
            self.grp_single.show()
            return
        self.grp_single.hide()
        self.grp_cls.show()
        vals = self._distinct_values(field)
        if need_repopulate:
            self.classify_config = {}
            for v in vals:
                # 填充色不在此预设：由 _fill_classify_table 为每个分类值分配互不重复的随机颜色
                self.classify_config[v] = {
                    'shape': self._default_class_shape(),
                    'size': _to_float(self.cmb_size.currentText()),
                    'width': _to_float(self.edit_width.text()),
                    'line': QColor(self.global_line),
                }
        self._fill_classify_table(vals)

    def on_edit_classify(self):
        """点击"设置各类图形..."按钮：弹出窗口，对每个分类值单独设置。"""
        field = self._classify_field
        if not field:
            QMessageBox.warning(self, '提示', '请先选择分类字段。')
            return
        vals = self._distinct_values(field)
        defaults = {
            'shape': self._default_class_shape(),
            'size': _to_float(self.cmb_size.currentText()),
            'width': _to_float(self.edit_width.text()),
        }
        dlg = ClassifyConfigDialog(self, self.headers, vals, self.classify_config, defaults)
        if dlg.exec_():
            self.classify_config = dlg.result_config()
            self._fill_classify_table(vals)

    def _fill_classify_table(self, vals):
        self.table_class.setRowCount(0)
        self.table_class.setRowCount(len(vals))
        # 为尚未指定填充色的分类值分配互不重复的随机颜色，并写回配置
        missing = [v for v in vals if 'fill' not in self.classify_config.get(v, {})]
        palette = _distinct_colors(len(missing)) if missing else []
        idx = 0
        for v in missing:
            self.classify_config[v]['fill'] = palette[idx]
            idx += 1
        for i, v in enumerate(vals):
            cfg = self.classify_config.get(v, {})
            self.table_class.setItem(i, 0, QTableWidgetItem(str(v)))
            cmb = QComboBox()
            for k in SHAPE_LIST_ORDER:
                cmb.addItem(SHAPE_LABELS[k], k)
            cmb.setCurrentIndex(cmb.findData(cfg.get('shape', self._default_class_shape())))
            self.table_class.setCellWidget(i, 1, cmb)
            size = cfg.get('size')
            if size is None:
                size = _to_float(self.cmb_size.currentText())
            self.table_class.setItem(i, 2, QTableWidgetItem(str(size if size is not None else '')))
            width = cfg.get('width')
            if width is None:
                width = _to_float(self.edit_width.text())
            self.table_class.setItem(i, 3, QTableWidgetItem(str(width if width is not None else '')))
            self.table_class.setCellWidget(i, 4, ColorButton(cfg.get('fill', self.global_fill)))
            self.table_class.setCellWidget(i, 5, ColorButton(cfg.get('line', self.global_line)))
        self.table_class.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table_class.setColumnWidth(0, 120)

    def _collect_classify_config(self):
        ordered = self._distinct_values(self._classify_field) if self._classify_field else []
        cfg = {}
        for i in range(self.table_class.rowCount()):
            if i >= len(ordered):
                break
            val = ordered[i]
            cmb = self.table_class.cellWidget(i, 1)
            size = _to_float(self.table_class.item(i, 2).text())
            width = _to_float(self.table_class.item(i, 3).text())
            fill = self.table_class.cellWidget(i, 4).color
            line = self.table_class.cellWidget(i, 5).color
            cfg[val] = {'shape': cmb.currentData(), 'size': size,
                        'width': width, 'fill': fill, 'line': line}
        return cfg

    # ============================ 生成 ============================
    def _resolve_dim(self, combo, row, default):
        text = combo.currentText().strip()
        if text in self.headers:
            return _to_float(row.get(text), default)
        return _to_float(text, default)

    def on_create_layer(self):
        try:
            self._do_create_layer()
        except Exception as exc:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, '生成失败',
                                 '创建图层时发生异常:\n%s\n\n请把此窗口内容和 Python 控制台日志发我排查。' % str(exc))

    def _do_create_layer(self):
        if not self.edit_file.text() or not self.headers:
            QMessageBox.warning(self, '提示', '请先加载文件。')
            return
        rows = self.get_rows()
        shape = self.cmb_shape.currentData()
        xcol = self.cmb_x.currentText(); ycol = self.cmb_y.currentText()
        if not xcol or not ycol:
            QMessageBox.warning(self, '提示', '请选择经度列和纬度列。')
            return
        # 自动纠错：若所选 X/Y 列几乎全部无效而互换后有效，则自动交换经/纬度列
        swapped = self._auto_fix_lon_lat(rows, xcol, ycol)
        self._last_swapped = swapped
        if swapped:
            xcol, ycol = ycol, xcol
        width_val = _to_float(self.edit_width.text(), None)
        cls_field = self.cmb_classify.currentData()
        classify_cfg = self._collect_classify_config() if cls_field else {}

        fields = QgsFields()
        for name in self.headers:
            if not name:
                continue
            fields.append(QgsField(name, QVariant.String))
        fields.append(QgsField('FillColor', QVariant.String))
        fields.append(QgsField('LineColor', QVariant.String))

        # 参考 cyanlove：图层 URI 用字面量 "Polygon?crs=..."
        crs = self.cmb_crs.currentData() or 'EPSG:4326'
        base_name = self.edit_name.text().strip() or 'Excel形状图层'
        layer = QgsVectorLayer('Polygon?crs=' + crs, self._unique_layer_name(base_name), 'memory')
        provider = layer.dataProvider()
        provider.addAttributes(fields)
        layer.updateFields()
        # 参考 cyanlove：写要素前先设置符号渲染器
        self._apply_renderer(layer, cls_field, classify_cfg)

        feats = []; skipped = 0
        print('EXCEL_SHAPE 读取行=%d 经度列=%s 纬度列=%s' % (len(rows), xcol, ycol))
        for row in rows:
            lon = _to_float(row.get(xcol)); lat = _to_float(row.get(ycol))
            # 参考 cyanlove：校验经纬度范围，非法行直接跳过（防止坐标跑到球外不可见）
            if lon is None or lat is None:
                skipped += 1; continue
            if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                skipped += 1; continue
            s_shape, s_size, s_width = shape, None, width_val
            s_fill = QColor(self.global_fill); s_line = QColor(self.global_line)
            if cls_field:
                cc = classify_cfg.get(str(row.get(cls_field)).strip())
                if cc is not None:
                    s_shape = cc['shape'] or shape
                    s_size = cc['size']
                    s_width = cc['width'] if cc['width'] is not None else width_val
                    s_fill = cc['fill']; s_line = cc['line']
            size = s_size if s_size is not None else self._resolve_dim(self.cmb_size, row, 100.0)
            bearing = self._resolve_dim(self.cmb_bearing, row, 0.0)
            width = s_width
            # 过滤 NaN/Inf 等非常数值，避免写文件报 ErrFeatureWriteFailed(7)
            if not (math.isfinite(size) and math.isfinite(bearing) and
                    (width is None or math.isfinite(width))):
                skipped += 1; continue
            if size <= 0:
                skipped += 1; continue
            poly = shapes.build_polygon(
                s_shape, lon, lat, size, bearing, width,
                sword_width_m=width if s_shape == 'sword' else None)
            if not poly or not all(math.isfinite(x) and math.isfinite(y) for x, y in poly):
                skipped += 1; continue
            geom = QgsGeometry.fromPolygonXY([[QgsPointXY(x, y) for x, y in poly]])
            # 修复尖刺/自交等 GEOS 判为有效但 TAB 等驱动会拒绝的退化几何，避免导出报错码7
            geom = geom.makeValid()
            if geom.isEmpty():
                skipped += 1; continue
            # 多部分几何取面积最大部分，保持单部分 Polygon（图层类型为 Polygon）
            if geom.isMultipart():
                best, best_area = None, None
                for i in range(geom.geometryCount()):
                    part = geom.getGeometry(i)
                    if part is None or part.isEmpty():
                        continue
                    a = part.area()
                    if best_area is None or a > best_area:
                        best, best_area = part, a
                if best is None:
                    skipped += 1; continue
                geom = best
            if (geom.type() != QgsWkbTypes.GeometryType.PolygonGeometry
                    or not geom.isGeosValid() or not geom.area() > 0):
                skipped += 1; continue
            # 参考 cyanlove 栅格工具：QgsFeature() + setFields(fields) + 位置式 setAttributes
            feat = QgsFeature()
            feat.setFields(fields)
            feat.setGeometry(geom)
            vals = [str(row.get(name)) if row.get(name) is not None else '' for name in self.headers if name]
            vals.append(str(s_fill.name())); vals.append(str(s_line.name()))
            feat.setAttributes(vals)
            feats.append(feat)

        if not feats:
            QMessageBox.warning(self, '无有效要素',
                                '没有生成任何有效要素。请检查经度/纬度列选择及数值是否为有效经纬度（经度 -180~180，纬度 -90~90）。')
            return
        print('EXCEL_SHAPE 生成要素数=%d' % len(feats))
        if feats:
            print('EXCEL_SHAPE 首要素WKT=%s' % feats[0].geometry().asWkt()[:200])
        provider.addFeatures(feats)
        layer.updateExtents()
        # 校验要素是否真正写入 provider（属性表为空+无图形多为此处失败）
        n_added = layer.featureCount()
        print('EXCEL_SHAPE 图层实际要素数(featureCount)=%d' % n_added)
        if n_added == 0:
            QMessageBox.critical(self, '写入失败',
                                 '要素已生成(%d)但未能写入图层(featureCount=0)。请把 Python 控制台日志发我。' % len(feats))
            return
        self._apply_renderer(layer, cls_field, classify_cfg)
        # 参考 cyanlove：addMapLayer(layer, True) 并刷新画布，确保默认显示
        QgsProject.instance().addMapLayer(layer, True)
        self.created_layers.append((layer, layer.name()))
        self._refresh_layer_combo()

        try:
            canvas = self.iface.mapCanvas()
            if canvas and not layer.extent().isEmpty():
                canvas.setExtent(layer.extent())
            canvas.refresh()
            self.iface.layerTreeView().refreshLayerSymbology(layer.id())
            self.iface.layerTreeView().setCurrentLayer(layer)
        except Exception as e:
            QgsMessageLog.logMessage('生成后定位/刷新画布失败: %s' % e, 'Shape_Layer_Tools', Qgis.Warning)

        msg = '已生成图层 "%s"，共 %d 个要素。' % (layer.name(), len(feats))
        if skipped:
            msg += ' 跳过无效记录 %d 行。' % skipped
        if self._last_swapped:
            msg += ' 已自动交换经/纬度列（原选择经度=纬度、纬度=经度被修正）。'
        QMessageBox.information(self, '完成', msg)

    @staticmethod
    def _sample_value(rows, field):
        for row in rows:
            v = row.get(field)
            if v is not None and v != '':
                return v
        return None

    @staticmethod
    def _unique_layer_name(base):
        """若项目已有同名图层，自动追加 -2/-3... 避免重名。"""
        existing = {ly.name() for ly in QgsProject.instance().mapLayers().values()}
        if base not in existing:
            return base
        i = 2
        while ('%s-%d' % (base, i)) in existing:
            i += 1
        return '%s-%d' % (base, i)

    @staticmethod
    def _auto_fix_lon_lat(rows, xcol, ycol):
        """统计(X列→经度,Y列→纬度)与互换两种分配各自有效的行数，若互换明显更多则返回 True。"""
        def count_valid(xc, yc):
            n = 0
            for r in rows:
                x = _to_float(r.get(xc)); y = _to_float(r.get(yc))
                if x is not None and y is not None and -180 <= x <= 180 and -90 <= y <= 90:
                    n += 1
            return n
        a = count_valid(xcol, ycol)
        b = count_valid(ycol, xcol)
        # 仅当前分配大量无效、而互换后有效时才纠错，避免误判
        if b > a and a <= max(1, len(rows) * 0.1):
            return True
        return False

    @staticmethod
    def _fill_symbol_props(fill, line):
        props = {'color': fill.name(), 'outline_color': line.name()}
        # 线条 100% 透明 → 不绘制线条
        props['outline_width'] = '0' if line.alpha() == 0 else '0.6'
        if fill.alpha() < 255:
            props['color_alpha'] = fill.alpha()
        if line.alpha() < 255:
            props['outline_color_alpha'] = line.alpha()
        return props

    def _apply_renderer(self, layer, cls_field, classify_cfg):
        if cls_field and classify_cfg:
            cats = []
            ordered = self._distinct_values(cls_field)
            for v in ordered:
                cc = classify_cfg.get(v)
                if cc is None:
                    continue
                sym = QgsFillSymbol.createSimple(
                    self._fill_symbol_props(cc['fill'], cc['line']))
                cats.append(QgsRendererCategory(v, sym, str(v)))
            if cats:
                layer.setRenderer(QgsCategorizedSymbolRenderer(cls_field, cats))
                layer.triggerRepaint()
                return
        # 非分类（或分类无有效类别）：显式设置单一符号渲染器
        sym = QgsFillSymbol.createSimple(
            self._fill_symbol_props(self.global_fill, self.global_line))
        renderer = QgsSingleSymbolRenderer(sym)
        layer.setRenderer(renderer)
        layer.triggerRepaint()

    # ============================ 导出 ============================
    def _refresh_layer_combo(self):
        current = self.cmb_layer.currentData()
        self.cmb_layer.blockSignals(True); self.cmb_layer.clear()
        self.cmb_layer.addItem('（请选择要导出的图层）', None)
        seen = set()
        for layer in list(QgsProject.instance().mapLayers().values()) + self.created_layers:
            if not isinstance(layer, QgsVectorLayer):
                continue
            if id(layer) in seen:
                continue
            seen.add(id(layer))
            self.cmb_layer.addItem(layer.name(), layer)
        if current is not None:
            self.cmb_layer.setCurrentIndex(self.cmb_layer.findData(current))
        self.cmb_layer.blockSignals(False)

    def on_browse_dir(self):
        path = QFileDialog.getExistingDirectory(self, '选择输出目录')
        if path:
            self.edit_outdir.setText(path)

    def on_export(self):
        layer = self.cmb_layer.currentData()
        if layer is None:
            QMessageBox.warning(self, '提示', '请先选择要导出的图层。')
            return
        # 解析输出目录：优先手动指定，否则源文件目录，再否则用户主目录
        outdir = self.edit_outdir.text().strip()
        if not outdir:
            src_file = self.edit_file.text()
            outdir = os.path.dirname(src_file) if src_file else None
        if not outdir:
            QMessageBox.warning(self, '提示', '请选择输出目录。')
            return
        # 目录不存在则自动创建，避免 ErrCreateDataSource
        if not os.path.isdir(outdir):
            try:
                os.makedirs(outdir)
            except OSError as exc:
                QMessageBox.critical(self, '导出失败', '无法创建输出目录:\n%s' % exc)
                return
        driver, ext = self.cmb_format.currentData()
        out_path = os.path.join(outdir, layer.name()) + ext
        try:
            opts = QgsVectorFileWriter.SaveVectorOptions()
            opts.driverName = driver
            opts.fileEncoding = 'UTF-8'
            opts.destinationCrs = layer.crs()
            res = QgsVectorFileWriter.writeAsVectorFormatV3(
                layer, out_path, QgsCoordinateTransformContext(), opts)
            # 不同 QGIS 版本返回类型：元组 (err, msg, ...) 或直接错误码
            if isinstance(res, tuple):
                err, msg = res[0], (res[1] if len(res) > 1 else '')
            else:
                err, msg = res, ''
            if err != QgsVectorFileWriter.WriterError.NoError:
                detail = ('\n%s' % msg) if msg else ''
                hint = WRITER_ERROR_TEXT.get(err, '未知错误')
                QMessageBox.critical(self, '导出失败',
                                     '写入出错，错误码: %s（%s）%s' % (err, hint, detail))
            else:
                QMessageBox.information(self, '完成', '已导出到:\n' + out_path)
        except Exception as exc:
            QMessageBox.critical(self, '导出异常', str(exc))

    def closeEvent(self, event):
        event.accept()