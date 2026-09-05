# -*- coding: utf-8 -*-
"""搜索数据组件：选择图层+字段，模糊/精确搜索，双击定位并闪烁。
既用于主对话框的"搜索数据"标签页，也用于独立的"搜索数据"窗口。
本模块独立，可单独修改而不影响其他功能模块。"""

from qgis.core import (
    Qgis,
    QgsProject,
    QgsGeometry,
    QgsRectangle,
    QgsCoordinateTransform,
    QgsMessageLog,
)
from qgis.PyQt.QtCore import Qt, QSettings, QTimer
from qgis.gui import QgsRubberBand
from qgis.PyQt.QtGui import QColor
from .utils import session_get, session_set
from qgis.PyQt.QtWidgets import (
    QWidget,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QComboBox,
    QPushButton,
    QMessageBox,
    QDialogButtonBox,
    QTableWidget,
    QTableWidgetItem,
    QListWidget,
    QListWidgetItem,
)


class SearchWidget(QWidget):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.canvas = iface.mapCanvas()
        s = QSettings()
        saved = s.value('excel_shape/search_display_fields', '')
        self._display_fields = [x for x in str(saved).split(',') if x] if saved else None
        self._search_items = []
        self._search_pos = 0
        self._selected_geom = None
        self._selected_layer = None
        self._flash_band = None
        self._flash_timer = None
        self._build_ui()
        # 恢复本次运行期间上次的搜索设置
        txt = session_get('search', 'text')
        if txt:
            self.edit_s_text.setText(txt)
        md = session_get('search', 'mode')
        if md is not None:
            self.cmb_s_mode.setCurrentIndex(md)

    def _build_ui(self):
        root = QVBoxLayout(self)
        grp = QGroupBox('搜索条件')
        form = QFormLayout(grp)
        self.cmb_s_layer = QComboBox()
        self.cmb_s_layer.currentIndexChanged.connect(self.on_s_layer_changed)
        form.addRow('选择图层:', self.cmb_s_layer)
        self.cmb_s_field = QComboBox()
        form.addRow('选择字段:', self.cmb_s_field)
        self.edit_s_text = QLineEdit()
        self.edit_s_text.setPlaceholderText('输入搜索内容，回车即搜索')
        self.edit_s_text.returnPressed.connect(self.on_search)
        form.addRow('搜索内容:', self.edit_s_text)
        self.cmb_s_mode = QComboBox()
        self.cmb_s_mode.addItem('模糊搜索（包含）', 'fuzzy')
        self.cmb_s_mode.addItem('精确搜索（完全一致）', 'exact')
        form.addRow('匹配方式:', self.cmb_s_mode)
        root.addWidget(grp)
        bar = QHBoxLayout()
        self.btn_search = QPushButton('搜索'); self.btn_search.clicked.connect(self.on_search)
        btn_refresh = QPushButton('刷新图层列表'); btn_refresh.clicked.connect(self.refresh_layers)
        btn_cols = QPushButton('设置显示字段...'); btn_cols.clicked.connect(self.on_set_display_fields)
        self.btn_flash = QPushButton('闪烁'); self.btn_flash.clicked.connect(self.flash_selected)
        self.btn_goto = QPushButton('跳转到该位置'); self.btn_goto.clicked.connect(self.goto_selected)
        bar.addWidget(self.btn_search); bar.addWidget(btn_refresh); bar.addWidget(btn_cols)
        bar.addSpacing(8); bar.addWidget(self.btn_flash); bar.addWidget(self.btn_goto)
        bar.addStretch()
        root.addLayout(bar)
        self.table_search = QTableWidget(0, 0)
        self.table_search.verticalHeader().setVisible(False)
        self.table_search.cellClicked.connect(self.on_search_click)
        self.table_search.cellDoubleClicked.connect(self.on_search_double_click)
        root.addWidget(self.table_search)
        self.lbl_search = QLabel('')
        self.lbl_search.setStyleSheet('color:#666;')
        root.addWidget(self.lbl_search)

    def _current_layer(self):
        lid = self.cmb_s_layer.currentData()
        return QgsProject.instance().mapLayer(lid) if lid else None

    def hideEvent(self, event):
        # 窗口/标签页隐藏时清理画布上的闪烁橡皮带，避免残留
        self._stop_flash()
        super().hideEvent(event)

    def refresh_layers(self):
        self.cmb_s_layer.blockSignals(True)
        self.cmb_s_layer.clear()
        for lid, ly in QgsProject.instance().mapLayers().items():
            self.cmb_s_layer.addItem(ly.name(), lid)
        prev = session_get('search', 'layer_id')
        if prev:
            idx = self.cmb_s_layer.findData(prev)
            if idx >= 0:
                self.cmb_s_layer.setCurrentIndex(idx)
        self.cmb_s_layer.blockSignals(False)
        self.on_s_layer_changed()

    def on_s_layer_changed(self):
        self.cmb_s_field.clear()
        layer = self._current_layer()
        if layer is None:
            return
        names = [f.name() for f in layer.fields()]
        for n in names:
            self.cmb_s_field.addItem(n)
        # 默认选择：优先字段名含"CGI"→其次含"名称"→否则第 1 个字段
        low = [x.lower() for x in names]
        def _pick(keys):
            for i, ln in enumerate(low):
                if any(k in ln for k in keys):
                    return i
            return -1
        idx = _pick(['cgi'])
        if idx < 0:
            idx = _pick(['名称', 'name'])
        if idx < 0:
            idx = 0
        self.cmb_s_field.setCurrentIndex(max(0, min(idx, self.cmb_s_field.count() - 1)))
        # 若本次运行已保存该图层的字段选择，则覆盖默认值
        saved_field = session_get('search', 'field')
        if saved_field:
            fidx = self.cmb_s_field.findText(saved_field)
            if fidx >= 0:
                self.cmb_s_field.setCurrentIndex(fidx)

    def on_search(self):
        layer = self._current_layer()
        if layer is None:
            QMessageBox.warning(self, '提示', '请先选择一个图层'); return
        session_set('search', 'layer_id', layer.id())
        session_set('search', 'field', self.cmb_s_field.currentText())
        session_set('search', 'text', self.edit_s_text.text())
        session_set('search', 'mode', self.cmb_s_mode.currentIndex())
        field_name = self.cmb_s_field.currentText()
        idx = layer.fields().indexOf(field_name)
        if idx < 0:
            QMessageBox.warning(self, '提示', '请选择要搜索的字段'); return
        kw = self.edit_s_text.text().strip()
        if not kw:
            QMessageBox.warning(self, '提示', '请输入搜索内容'); return
        mode = self.cmb_s_mode.currentData()
        if self._display_fields:
            names = [n for n in self._display_fields if n in layer.fields().names()]
        else:
            # 默认仅呈现"选择字段"的内容
            sel = self.cmb_s_field.currentText()
            all_names = layer.fields().names()
            if sel and sel in all_names:
                names = [sel]
            elif all_names:
                names = all_names[:1]
            else:
                names = []
        matches = []
        cap = 2000
        for feat in layer.getFeatures():
            attrs = feat.attributes()
            val = attrs[idx] if idx < len(attrs) else None
            sv = '' if val is None else str(val)
            ok = (sv == kw) if mode == 'exact' else (kw.lower() in sv.lower())
            if ok:
                matches.append((feat.geometry(), attrs))
            if len(matches) >= cap:
                break
        self._search_items = matches
        self._search_pos = 0
        self.table_search.setColumnCount(len(names))
        self.table_search.setHorizontalHeaderLabels(list(names))
        self.table_search.setRowCount(0); self.table_search.setRowCount(len(matches))
        for i, (geom, attrs) in enumerate(matches):
            for j, nm in enumerate(names):
                k = layer.fields().indexOf(nm)
                self.table_search.setItem(
                    i, j, QTableWidgetItem(str(attrs[k]) if k < len(attrs) else ''))
        self.table_search.resizeColumnsToContents()
        self.lbl_search.setText('共匹配到 %d 条记录%s（双击某行可在地图中定位并闪烁）' % (
            len(matches), '（已截断）' if len(matches) >= cap else ''))

    def on_search_click(self, row, col):
        """单击任意单元格即选中该行，方便仅用按钮操作。"""
        self._select_row(row)

    def on_search_double_click(self, row, col):
        if not self._search_items or not (0 <= row < len(self._search_items)):
            return
        self._select_row(row)
        # 双击：先移动到该位置，再闪烁 5 秒
        self.goto_selected()
        self.flash_selected()

    def _select_row(self, row):
        """记录选中行对应的几何体与所在图层，供闪烁/定位复用。
        不加 setCurrentCell，保留用户实际点中的单元格。"""
        self._current_row = row
        geom, _attrs = self._search_items[row]
        self._selected_geom = geom
        self._selected_layer = self._current_layer()

    def _canvas_geometry(self, geom):
        """把图层坐标系的几何体转换到画布坐标系，避免坐标歪斜/闪烁到错误位置。"""
        layer = self._selected_layer
        if layer is None or geom is None:
            return geom
        try:
            xform = QgsCoordinateTransform(
                layer.crs(),
                self.canvas.mapSettings().destinationCrs(),
                QgsProject.instance().transformContext())
            g = QgsGeometry(geom)  # 拷贝后原地转换，避免 QgsGeometry 重载缺失问题
            g.transform(xform)
            return g
        except Exception:
            return geom

    def flash_selected(self):
        """用橡皮带叠加闪烁 5 秒：红色/白色每 500ms 交叉变化，结束后移除并恢复图层原色。"""
        geom = self._canvas_geometry(self._selected_geom)
        if geom is None or geom.isEmpty():
            QMessageBox.warning(self, '提示', '请先在结果列表中单击/双击选择一条数据。')
            return
        self._stop_flash()
        try:
            band = QgsRubberBand(self.canvas)
            band.setColor(QColor(255, 255, 255))
            band.setWidth(3)
            band.setFillColor(QColor(255, 0, 0))
            band.setToGeometry(geom, None)  # geom 已是画布坐标系，避免二次转换
            band.show()
            self._flash_band = band
            self._flash_count = 0
            self._flash_timer = QTimer(self)
            self._flash_timer.timeout.connect(self._flash_tick)
            self._flash_timer.start(500)
        except Exception:
            import traceback; traceback.print_exc()
            self._stop_flash()

    def _flash_tick(self):
        self._flash_count += 1
        red = bool(self._flash_count % 2)
        color = QColor(255, 0, 0) if red else QColor(255, 255, 255)
        band = self._flash_band
        if band is None:
            return
        band.setColor(color)
        band.setFillColor(color)
        band.update()
        if self._flash_count >= 10:  # 10 次 × 500ms = 5 秒
            self._stop_flash()

    def _stop_flash(self):
        if self._flash_timer is not None:
            self._flash_timer.stop()
            self._flash_timer.deleteLater()
            self._flash_timer = None
        band = self._flash_band
        if band is not None:
            try:
                self.canvas.scene().removeItem(band)
            except Exception as e:
                QgsMessageLog.logMessage('移除闪烁带失败: %s' % e, 'Shape_Layer_Tools', Qgis.MessageLevel.Warning)
            self._flash_band = None

    def goto_selected(self):
        """地图转移到数据所在位置，并把该几何体居中显示。"""
        geom = self._canvas_geometry(self._selected_geom)
        if geom is None or geom.isEmpty():
            QMessageBox.warning(self, '提示', '请先在结果列表中单击/双击选择一条数据。')
            return
        try:
            box = geom.boundingBox()
            w = (box.xMaximum() - box.xMinimum()) * 0.3
            h = (box.yMaximum() - box.yMinimum()) * 0.3
            if w < 1e-9:
                w = 1e-3
            if h < 1e-9:
                h = 1e-3
            box = QgsRectangle(box.xMinimum() - w, box.yMinimum() - h,
                               box.xMaximum() + w, box.yMaximum() + h)
            # setExtent 会把该范围居中显示
            self.canvas.setExtent(box)
            self.canvas.refresh()
        except Exception:
            import traceback; traceback.print_exc()

    def on_set_display_fields(self):
        layer = self._current_layer()
        field_names = list(layer.fields().names()) if layer else []
        if not field_names:
            QMessageBox.warning(self, '提示', '请先选择一个图层'); return
        dlg = QDialog(self)
        dlg.setWindowTitle('设置结果列（显示哪些字段）')
        v = QVBoxLayout(dlg)
        lw = QListWidget()
        current = self._display_fields if self._display_fields else field_names[:6]
        for f in field_names:
            it = QListWidgetItem(f)
            it.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            it.setCheckState(Qt.CheckState.Checked if f in current else Qt.CheckState.Unchecked)
            lw.addItem(it)
        v.addWidget(lw)
        b = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        b.accepted.connect(dlg.accept); b.rejected.connect(dlg.reject)
        v.addWidget(b)
        if not dlg.exec():
            return
        selected = [lw.item(i).text() for i in range(lw.count())
                    if lw.item(i).checkState() == Qt.CheckState.Checked]
        if not selected:
            QMessageBox.warning(self, '提示', '至少勾选一个字段'); return
        self._display_fields = selected
        QSettings().setValue('excel_shape/search_display_fields', ','.join(selected))
        self.lbl_search.setText('已更新显示字段：%s' % ','.join(selected))


class SearchDialog(QDialog):
    """工具栏"搜索数据"弹出的独立搜索窗口。"""

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.setWindowTitle('搜索数据')
        self.resize(560, 420)
        lay = QVBoxLayout(self)
        self.widget = SearchWidget(iface, self)
        lay.addWidget(self.widget)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def showEvent(self, event):
        super().showEvent(event)
        self.widget.refresh_layers()