# -*- coding: utf-8 -*-
"""搜索数据组件：选择图层+字段，模糊/精确搜索，双击定位并闪烁。

性能策略（百万行数据）：
1. 优先使用本地 SQLite 索引（search_index.LayerIndex）—— 首次建立后查询毫秒级；
2. 未建索引时，把过滤条件交给 QGIS 表达式在 C++ 层筛完，只回传命中行；
3. 仅当以上方式都不可用时，才退回逐要素遍历。

既用于主对话框的"搜索数据"标签页，也用于独立的"搜索数据"窗口。
本模块独立，可单独修改而不影响其他功能模块。
"""

import time

from qgis.core import (
    Qgis,
    QgsProject,
    QgsGeometry,
    QgsRectangle,
    QgsExpression,
    QgsFeatureRequest,
    QgsCoordinateTransform,
    QgsMessageLog,
)
from qgis.PyQt.QtCore import Qt, QObject, QSettings, QTimer
from qgis.gui import QgsRubberBand
from qgis.PyQt.QtGui import QColor, QKeySequence
try:                     # Qt5 里 QShortcut 在 QtWidgets，Qt6 移到了 QtGui
    from qgis.PyQt.QtGui import QShortcut
except ImportError:
    from qgis.PyQt.QtWidgets import QShortcut
from .utils import session_get, session_set
from .search_index import LayerIndex, IndexCancelled
from qgis.PyQt.QtWidgets import (
    QWidget,
    QDialog,
    QDockWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QComboBox,
    QPushButton,
    QCheckBox,
    QMessageBox,
    QDialogButtonBox,
    QTableWidget,
    QTableWidgetItem,
    QListWidget,
    QListWidgetItem,
    QProgressDialog,
    QSizePolicy,
    QStyleOptionComboBox,
    QStylePainter,
    QStyle,
    QApplication,
)

SEARCH_SHORTCUT = 'Ctrl+F'

# 窄面板用：去掉 Qt 给按钮的 80px 默认最小宽度，让文字自己决定大小
COMPACT_QSS = ('QPushButton { padding: 3px 5px; min-width: 0px; }'
               'QComboBox { min-width: 0px; }')


def _in_attribute_table(widget, depth=6):
    """判断控件是否在属性表里——属性表自己用 Ctrl+F 做过滤，别抢它的快捷键。"""
    node = widget
    for _ in range(depth):
        if node is None:
            return False
        name = type(node).__name__
        if 'AttributeTable' in name or 'DualView' in name:
            return True
        node = node.parentWidget()
    return False

INDEX_MIN_ROWS = 20000   # 超过该行数才主动建议建立索引
RESULT_CAP = 2000        # 结果条数上限（与原行为一致）


def _quote_ident(name):
    return '"%s"' % str(name).replace('"', '""')


def _quote_str(text):
    return "'%s'" % str(text).replace('\\', '\\\\').replace("'", "''")


def _escape_like(text):
    return (str(text).replace('\\', '\\\\')
                     .replace('%', '\\%')
                     .replace('_', '\\_')
                     .replace("'", "''"))


def build_filter_expression(field, keyword, exact):
    """构造 QGIS 过滤表达式：交给 QGIS 在 C++ 层筛选，避免逐行进入 Python。"""
    fld = _quote_ident(field)
    if exact:
        return 'to_string(%s) = %s' % (fld, _quote_str(keyword))
    return "lower(to_string(%s)) LIKE '%%%s%%'" % (fld, _escape_like(keyword.lower()))


class ElidedComboBox(QComboBox):
    """当前项文字超出宽度时用「…」截断。

    有些样式（Windows / Fusion）画下拉框当前项时只是硬裁掉，不留省略号，
    窄面板里看起来像断字。这里自己画一次：只把显示的文本换成省略后的版本，
    下拉列表里仍是完整文字，鼠标悬停也能看到全称。
    """

    def paintEvent(self, event):
        if self.isEditable():
            super().paintEvent(event)
            return
        try:
            opt = QStyleOptionComboBox()
            self.initStyleOption(opt)
            full = opt.currentText
            if full:
                field = self.style().subControlRect(
                    QStyle.ComplexControl.CC_ComboBox, opt,
                    QStyle.SubControl.SC_ComboBoxEditField, self)
                elided = self.fontMetrics().elidedText(
                    full, Qt.TextElideMode.ElideRight, max(field.width() - 2, 0))
                if elided != full:
                    opt.currentText = elided
            painter = QStylePainter(self)
            painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, opt)
            painter.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, opt)
        except Exception:
            super().paintEvent(event)


class SearchWidget(QWidget):
    """搜索界面。

    compact=True 时按左侧窄面板排布：不放「设置显示字段」「跳转到该位置」按钮，
    下拉框与表格都不随内容撑宽，超长文字用省略号截断（悬停可看全称）。
    """

    def __init__(self, iface, parent=None, compact=False):
        super().__init__(parent)
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.compact = bool(compact)
        self._display_fields = self._read_display_fields()
        if self.compact:
            # 窄面板：去掉 Qt 给按钮的 80px 默认最小宽度，让文字自己决定大小
            self.setStyleSheet(COMPACT_QSS)
        self._search_items = []     # [(fid, [字段值...]), ...]
        self._search_layer = None
        self._search_pos = 0
        self._current_row = -1
        self._selected_geom = None
        self._selected_layer = None
        self._flash_band = None
        self._flash_timer = None
        self._indexes = {}          # layer.id() -> LayerIndex
        self._no_index = set()      # 本次运行中用户已拒绝建索引的图层
        self._layer_ids = None      # 上次刷新时的图层列表，用于按需刷新
        self._build_ui()
        # 恢复本次运行期间上次的搜索设置
        txt = session_get('search', 'text')
        if txt:
            self.edit_s_text.setText(txt)
        md = session_get('search', 'mode')
        if md is not None:
            self.cmb_s_mode.setCurrentIndex(md)

    @staticmethod
    def _read_display_fields():
        """显示字段设置（与弹窗共用同一份 QSettings，谁改了都生效）。"""
        saved = QSettings().value('excel_shape/search_display_fields', '')
        return [x for x in str(saved).split(',') if x] if saved else None

    @staticmethod
    def _tame_combo(combo, chars=8):
        """下拉框不随最长选项撑宽：超出部分省略显示，悬停看全称。"""
        try:
            combo.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(chars)
            combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            combo.view().setTextElideMode(Qt.TextElideMode.ElideRight)
            combo.setMinimumWidth(0)
            combo.currentTextChanged.connect(lambda t, c=combo: c.setToolTip(t))
        except Exception as exc:
            QgsMessageLog.logMessage('设置下拉框宽度策略失败: %s' % exc,
                                     'Shape_Layer_Tools', Qgis.MessageLevel.Info)

    def _build_ui(self):
        root = QVBoxLayout(self)
        if self.compact:
            root.setContentsMargins(6, 6, 6, 6)
            root.setSpacing(5)

        grp = QGroupBox('搜索条件')
        if self.compact:
            # QGroupBox 里的按钮拿不到父控件样式表（Qt 的 polish 时机问题），单独设一次
            grp.setStyleSheet(COMPACT_QSS)
        grp_v = QVBoxLayout(grp)
        if self.compact:
            grp_v.setContentsMargins(8, 6, 8, 6)
            grp_v.setSpacing(4)
        form = QFormLayout()
        grp_v.addLayout(form)
        if self.compact:
            form.setContentsMargins(0, 0, 0, 0)
            form.setHorizontalSpacing(6)
            form.setVerticalSpacing(4)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        lab = (lambda full, short: short) if self.compact else (lambda full, short: full)

        self.cmb_s_layer = ElidedComboBox()
        self._tame_combo(self.cmb_s_layer, 4 if self.compact else 10)
        self.cmb_s_layer.currentIndexChanged.connect(self.on_s_layer_changed)
        form.addRow(lab('选择图层:', '图层:'), self.cmb_s_layer)
        self.cmb_s_field = ElidedComboBox()
        self._tame_combo(self.cmb_s_field, 4 if self.compact else 10)
        form.addRow(lab('选择字段:', '字段:'), self.cmb_s_field)
        self.edit_s_text = QLineEdit()
        self.edit_s_text.setPlaceholderText('输入搜索内容，回车即搜索')
        self.edit_s_text.returnPressed.connect(self.on_search)
        form.addRow(lab('搜索内容:', '内容:'), self.edit_s_text)
        self.cmb_s_mode = ElidedComboBox()
        self.cmb_s_mode.addItem('模糊搜索（包含）', 'fuzzy')
        self.cmb_s_mode.addItem('精确搜索（完全一致）', 'exact')
        self._tame_combo(self.cmb_s_mode, 5 if self.compact else 12)
        form.addRow(lab('匹配方式:', '方式:'), self.cmb_s_mode)

        # 索引加速
        self.chk_index = QCheckBox('索引加速' if self.compact else '数据库索引加速')
        self.chk_index.setChecked(True)
        self.chk_index.setToolTip(
            '勾选后：已建索引的图层直接用 sqlite 查询（百万行毫秒级）；\n'
            '未建索引且数据量较大时会询问是否建立。\n'
            '不勾选则使用图层的原生过滤能力（比逐行遍历快很多）。')
        self.btn_index_build = QPushButton('建立索引' if self.compact else '建立/重建索引')
        self.btn_index_build.setToolTip('为当前图层的属性字段建立本地 SQLite 查询索引')
        self.btn_index_build.clicked.connect(self.on_build_index)
        self.btn_index_drop = QPushButton('删除索引')
        self.btn_index_drop.setToolTip('删除当前图层的本地索引文件（不影响原始数据）')
        self.btn_index_drop.clicked.connect(self.on_drop_index)
        if self.compact:
            # 窄面板里放不下一行：勾选框占一行，两个按钮单独一行（放在表单外面，
            # 否则 QFormLayout 会按默认 80px 按钮宽度把面板顶宽）
            form.addRow(lab('查询加速:', '加速:'), self.chk_index)
            row_idx_btn = QHBoxLayout()
            row_idx_btn.setSpacing(4)
            row_idx_btn.addWidget(self.btn_index_build)
            row_idx_btn.addWidget(self.btn_index_drop)
            grp_v.addLayout(row_idx_btn)
        else:
            r_idx = QHBoxLayout()
            r_idx.addWidget(self.chk_index, 1)
            r_idx.addWidget(self.btn_index_build)
            r_idx.addWidget(self.btn_index_drop)
            form.addRow('查询加速:', r_idx)
        root.addWidget(grp)

        self.lbl_index = QLabel('索引状态：-')
        self.lbl_index.setStyleSheet('color:#888;')
        self.lbl_index.setWordWrap(True)
        root.addWidget(self.lbl_index)

        bar = QHBoxLayout()
        if self.compact:
            bar.setSpacing(4)
        self.btn_search = QPushButton('搜索')
        self.btn_search.clicked.connect(self.on_search)
        self.btn_refresh = QPushButton('刷新' if self.compact else '刷新图层列表')
        self.btn_refresh.setToolTip('重新读取工程中的图层列表')
        self.btn_refresh.clicked.connect(self.refresh_layers)
        self.btn_flash = QPushButton('闪烁')
        self.btn_flash.setToolTip('在地图上闪烁显示选中的要素')
        self.btn_flash.clicked.connect(self.flash_selected)
        if self.compact:
            # 窄面板：三个按钮等分铺满整行，随面板宽度一起伸缩
            for btn in (self.btn_search, self.btn_refresh, self.btn_flash):
                btn.setSizePolicy(QSizePolicy.Policy.Expanding,
                                  QSizePolicy.Policy.Fixed)
                bar.addWidget(btn, 1)
        else:
            bar.addWidget(self.btn_search)
            bar.addWidget(self.btn_refresh)
            btn_cols = QPushButton('设置显示字段...')
            btn_cols.clicked.connect(self.on_set_display_fields)
            bar.addWidget(btn_cols)
            bar.addWidget(self.btn_flash)
            self.btn_goto = QPushButton('跳转到该位置')
            self.btn_goto.clicked.connect(self.goto_selected)
            bar.addSpacing(8)
            bar.addWidget(self.btn_goto)
            bar.addStretch()
        root.addLayout(bar)

        self.table_search = QTableWidget(0, 0)
        self.table_search.verticalHeader().setVisible(False)
        self.table_search.setWordWrap(False)
        self.table_search.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table_search.horizontalHeader().setDefaultSectionSize(
            110 if self.compact else 150)
        self.table_search.setMinimumWidth(0)
        self.table_search.cellClicked.connect(self.on_search_click)
        self.table_search.cellDoubleClicked.connect(self.on_search_double_click)
        root.addWidget(self.table_search, 1)
        self.lbl_search = QLabel('')
        self.lbl_search.setStyleSheet('color:#666;')
        self.lbl_search.setWordWrap(True)
        root.addWidget(self.lbl_search)

    def _current_layer(self):
        lid = self.cmb_s_layer.currentData()
        return QgsProject.instance().mapLayer(lid) if lid else None

    def hideEvent(self, event):
        # 窗口/标签页隐藏时清理画布上的闪烁橡皮带，避免残留
        self._stop_flash()
        super().hideEvent(event)

    def refresh_layers(self):
        self._layer_ids = list(QgsProject.instance().mapLayers().keys())
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

    def refresh_layers_if_changed(self):
        """仅在图层增删时刷新，避免面板每次显示都重置已选字段。"""
        self._display_fields = self._read_display_fields()   # 与弹窗保持同步
        ids = list(QgsProject.instance().mapLayers().keys())
        if ids != self._layer_ids:
            self.refresh_layers()
        else:
            self._refresh_index_label()

    def on_s_layer_changed(self):
        self.cmb_s_field.clear()
        layer = self._current_layer()
        self._refresh_index_label()
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

    # ==================== 索引管理 ====================
    def _index_for(self, layer):
        idx = self._indexes.get(layer.id())
        if idx is None or idx.layer is not layer:
            idx = LayerIndex(layer)
            self._indexes[layer.id()] = idx
        return idx

    def _refresh_index_label(self):
        layer = self._current_layer()
        if layer is None:
            self.lbl_index.setText('索引状态：未选择图层')
            return
        idx = self._index_for(layer)
        if idx.is_ready():
            meta = idx.meta() or {}
            if self.compact:      # 窄面板里不显示长路径，鼠标悬停看全
                self.lbl_index.setText(
                    '索引状态：已就绪 — %s 行，%.0f MB' % (
                        format(int(meta.get('count', 0)), ','),
                        float(meta.get('size_mb', 0.0))))
                self.lbl_index.setToolTip('建立于 %s\n%s' % (
                    meta.get('built_at', '未知'), meta.get('path', '')))
            else:
                self.lbl_index.setText(
                    '索引状态：已就绪 — %s 行，%.0f MB，建立于 %s（%s）' % (
                        format(int(meta.get('count', 0)), ','),
                        float(meta.get('size_mb', 0.0)),
                        meta.get('built_at', '未知'),
                        meta.get('path', '')))
                self.lbl_index.setToolTip('')
        elif idx.exists():
            self.lbl_index.setText('索引状态：已过期（数据有变化），建议重新建立索引。')
        else:
            try:
                n = layer.featureCount()
            except Exception:
                n = 0
            txt = '索引状态：未建立'
            if n:
                txt += '（图层共 %s 条记录' % format(int(n), ',')
                if n >= INDEX_MIN_ROWS:
                    txt += '，建议建立索引以获得毫秒级查询'
                txt += '）'
            self.lbl_index.setText(txt)

    def on_build_index(self):
        layer = self._current_layer()
        if layer is None:
            QMessageBox.warning(self, '提示', '请先选择一个图层')
            return
        self._build_index(layer)

    def _build_index(self, layer, ask=True):
        """为图层建立索引，成功返回 True。"""
        idx = self._index_for(layer)
        try:
            total = int(layer.featureCount() or 0)
        except Exception:
            total = 0
        if ask and total >= 200000:
            ans = QMessageBox.question(
                self, '建立索引',
                '图层 "%s" 共 %s 条记录，建立索引需要遍历全部要素，\n'
                '请耐心等待（可随时取消），完成后查询将大幅提速。\n\n是否继续？'
                % (layer.name(), format(total, ',')),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes)
            if ans != QMessageBox.StandardButton.Yes:
                return False
        dlg = QProgressDialog('正在建立查询索引：%s' % layer.name(), '取消',
                              0, max(total, 1), self)
        dlg.setWindowTitle('建立索引')
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)

        def _progress(done, tot):
            dlg.setMaximum(max(int(tot or 0), 1))
            dlg.setValue(done)
            QApplication.processEvents()

        def _cancelled():
            return dlg.wasCanceled()

        try:
            n, secs = idx.build(_progress, _cancelled)
        except IndexCancelled:
            dlg.close(); dlg.deleteLater()
            QMessageBox.information(self, '已取消', '索引建立已取消，未写入任何数据。')
            self._refresh_index_label()
            return False
        except Exception as exc:
            dlg.close(); dlg.deleteLater()
            QgsMessageLog.logMessage('建立索引失败: %r' % exc, 'Shape_Layer_Tools',
                                     Qgis.MessageLevel.Critical)
            QMessageBox.critical(
                self, '建立索引失败',
                '%s\n\nQGIS 版本：%s\n索引文件：%s\n\n若反复失败，请把 QGIS 的'
                '"Python 错误"面板内容发我。' % (exc, Qgis.QGIS_VERSION, idx.path))
            self._refresh_index_label()
            return False
        dlg.close(); dlg.deleteLater()
        QMessageBox.information(
            self, '索引已建立',
            '图层 "%s" 索引建立完成：\n%d 行，用时 %.1f 秒，文件 %.0f MB\n%s' % (
                layer.name(), n, secs, idx.size_mb(), idx.path))
        self._refresh_index_label()
        return True

    def on_drop_index(self):
        layer = self._current_layer()
        if layer is None:
            QMessageBox.warning(self, '提示', '请先选择一个图层')
            return
        idx = self._index_for(layer)
        if not idx.exists():
            QMessageBox.information(self, '提示', '该图层还没有索引文件。')
            return
        if QMessageBox.question(self, '删除索引', '确定删除该图层的索引文件吗？\n%s' % idx.path,
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) \
                != QMessageBox.StandardButton.Yes:
            return
        idx.drop()
        self._refresh_index_label()

    def _ensure_index(self, layer):
        """确认图层索引可用；必要时询问并建立。返回 LayerIndex 或 None。"""
        idx = self._index_for(layer)
        if idx.is_ready():
            return idx
        if layer.id() in self._no_index:
            return None
        try:
            n = int(layer.featureCount() or 0)
        except Exception:
            n = 0
        if n < INDEX_MIN_ROWS:
            return None
        if idx.exists():
            ans = QMessageBox.question(
                self, '索引已过期',
                '图层 "%s" 的索引与当前数据不一致（数据可能被修改过）。\n是否重新建立索引？'
                % layer.name(),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes)
            if ans != QMessageBox.StandardButton.Yes:
                self._no_index.add(layer.id())
                return None
        else:
            ans = QMessageBox.question(
                self, '建议建立索引',
                '图层 "%s" 共 %s 条记录。\n建立本地查询索引后，可把十几秒的查找缩短到毫秒级；\n'
                '索引只是缓存文件，不会改动原始数据。\n\n现在建立索引吗？'
                '（选择"否"将使用快速过滤方式搜索，同样比逐行遍历快）'
                % (layer.name(), format(n, ',')),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes)
            if ans != QMessageBox.StandardButton.Yes:
                self._no_index.add(layer.id())
                return None
        ok = self._build_index(layer, ask=False)
        if not ok:
            self._no_index.add(layer.id())
        return idx if ok else None

    # ==================== 搜索 ====================
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

        self.btn_search.setEnabled(False)
        self.lbl_search.setText('正在搜索...')
        QApplication.processEvents()
        t0 = time.time()
        try:
            matches, via = self._run_search(layer, field_name, kw, mode)
        except Exception as exc:
            self.btn_search.setEnabled(True)
            QMessageBox.critical(self, '搜索失败', '%s' % exc)
            self.lbl_search.setText('搜索失败')
            return
        elapsed = time.time() - t0
        self.btn_search.setEnabled(True)

        self._search_layer = layer
        self._search_items = matches
        self._search_pos = 0
        self._current_row = -1
        self._selected_geom = None
        self.table_search.setUpdatesEnabled(False)
        self.table_search.setColumnCount(len(names))
        self.table_search.setHorizontalHeaderLabels(list(names))
        self.table_search.setRowCount(0)
        self.table_search.setRowCount(len(matches))
        col_idx = [layer.fields().indexOf(nm) for nm in names]
        for i, (_fid, attrs) in enumerate(matches):
            for j, k in enumerate(col_idx):
                val = attrs[k] if 0 <= k < len(attrs) else ''
                self.table_search.setItem(
                    i, j, QTableWidgetItem('' if val is None else str(val)))
        self.table_search.setUpdatesEnabled(True)
        # 结果很多时自动列宽会很慢，改为固定宽度，保证搜索响应速度；
        # 窄面板一律固定列宽，让超长内容自己省略，不把面板撑宽
        if len(matches) <= 300 and not self.compact:
            self.table_search.resizeColumnsToContents()
        else:
            self.table_search.horizontalHeader().setDefaultSectionSize(
                110 if self.compact else 150)
        self.lbl_search.setText(
            '共匹配到 %d 条记录%s，用时 %.2f 秒（%s）。（双击某行可在地图中定位并闪烁）' % (
                len(matches), '（已截断）' if len(matches) >= RESULT_CAP else '',
                elapsed, via))
        self._refresh_index_label()

    def _run_search(self, layer, field_name, kw, mode):
        """返回 (matches, 方式说明)。matches 元素为 (fid, 属性列表)。"""
        if self.chk_index.isChecked():
            idx = self._ensure_index(layer)
            if idx is not None:
                try:
                    rows = idx.search(field_name, kw, mode == 'exact', RESULT_CAP)
                    return rows, '数据库索引'
                except Exception as exc:
                    # 索引损坏/被占用等情况：不让搜索失败，退回图层过滤
                    QgsMessageLog.logMessage('索引查询失败，改用图层过滤: %s' % exc,
                                             'Shape_Layer_Tools', Qgis.MessageLevel.Warning)
        # 无索引：把过滤条件下推到图层（C++ 层筛选），只回传命中行
        rows = self._search_by_filter(layer, field_name, kw, mode)
        if rows is not None:
            return rows, '图层过滤'
        return self._search_by_scan(layer, field_name, kw, mode), '逐行遍历'

    @staticmethod
    def _search_by_filter(layer, field_name, kw, mode, cap=RESULT_CAP):
        """用 QGIS 表达式过滤：不命中的要素不会进入 Python，速度快一个量级。"""
        expr = build_filter_expression(field_name, kw, mode == 'exact')
        try:
            if QgsExpression(expr).hasParserError():
                return None
        except Exception:
            return None
        out = []
        try:
            for feat in layer.getFeatures(QgsFeatureRequest().setFilterExpression(expr)):
                out.append((feat.id(), list(feat.attributes())))
                if len(out) >= cap:
                    break
        except Exception as exc:
            QgsMessageLog.logMessage('表达式过滤失败，回退逐行遍历: %s' % exc,
                                     'Shape_Layer_Tools', Qgis.MessageLevel.Warning)
            return None
        return out

    @staticmethod
    def _search_by_scan(layer, field_name, kw, mode, cap=RESULT_CAP):
        """兜底：逐要素遍历（内存图层等不支持过滤的场景）。"""
        idx = layer.fields().indexOf(field_name)
        out = []
        kwl = kw.lower()
        for feat in layer.getFeatures():
            attrs = feat.attributes()
            val = attrs[idx] if 0 <= idx < len(attrs) else None
            sv = '' if val is None else str(val)
            ok = (sv == kw) if mode == 'exact' else (kwl in sv.lower())
            if ok:
                out.append((feat.id(), list(attrs)))
                if len(out) >= cap:
                    break
        return out

    # ==================== 结果交互 ====================
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
        """记录选中行，几何体在需要时按 fid 从图层取，避免一次性加载全部图形。"""
        if not (0 <= row < len(self._search_items)):
            return
        self._current_row = row
        self._selected_geom = None
        self._selected_layer = self._search_layer or self._current_layer()

    def _selected_geometry(self):
        """按需取回当前行的几何体（懒加载）。"""
        if self._selected_geom is not None:
            return self._selected_geom
        if not (0 <= self._current_row < len(self._search_items)):
            return None
        layer = self._selected_layer
        if layer is None:
            return None
        fid = self._search_items[self._current_row][0]
        try:
            feat = layer.getFeature(fid)
        except Exception as exc:
            QgsMessageLog.logMessage('取要素失败(fid=%s): %s' % (fid, exc),
                                     'Shape_Layer_Tools', Qgis.MessageLevel.Warning)
            return None
        if feat is None or not feat.hasGeometry():
            return None
        self._selected_geom = feat.geometry()
        return self._selected_geom

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
        geom = self._canvas_geometry(self._selected_geometry())
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
        geom = self._canvas_geometry(self._selected_geometry())
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
    """浮动的"搜索数据"窗口（Ctrl+F / 工具栏 / 菜单打开）。

    功能完整：含「设置显示字段」「跳转到该位置」，不受窄面板限制。
    """

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.setWindowTitle('搜索数据')
        self.setObjectName('shape_layer_tools_search_dialog')
        self.resize(720, 520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        self.widget = SearchWidget(iface, self)
        lay.addWidget(self.widget, 1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def refresh(self, focus=True):
        self.widget.refresh_layers_if_changed()
        if focus:
            edit = self.widget.edit_s_text
            edit.setFocus()
            edit.selectAll()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()


class SearchDock(QDockWidget):
    """停靠在 QGIS 主窗口左侧的窄版"搜索数据"面板。

    只保留最常用的操作（搜索 / 刷新 / 闪烁），尽量少占宽度：
    - 「设置显示字段」「跳转到该位置」只在 Ctrl+F 打开的搜索窗口里；
    - 下拉框、结果表不随长文本撑宽，超出部分用省略号截断（悬停看全称）；
    - 双击结果行同样可以定位并闪烁；
    - 位置/显隐由 QGIS 主窗口状态记忆，可在「视图 → 面板」里勾选。
    """

    MIN_WIDTH = 240

    def __init__(self, iface, parent=None):
        super().__init__('搜索数据', parent if parent is not None else iface.mainWindow())
        self.iface = iface
        self.setObjectName('shape_layer_tools_search_dock')
        self.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea
                             | Qt.DockWidgetArea.RightDockWidgetArea)
        self.setMinimumWidth(self.MIN_WIDTH)
        self.widget = SearchWidget(iface, self, compact=True)
        self.widget.setMinimumWidth(0)
        self.setWidget(self.widget)
        # 工程图层增删时自动刷新下拉（合并短时间内的多次变更，避免工程加载时反复刷新）
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self.widget.refresh_layers_if_changed)
        project = QgsProject.instance()
        project.layersAdded.connect(self._on_layers_changed)
        project.layersRemoved.connect(self._on_layers_changed)

    def _on_layers_changed(self, *_args):
        if self.isVisible():
            self._refresh_timer.start(300)

    def release_project_signals(self):
        if self._refresh_timer is not None:
            self._refresh_timer.stop()

    def showEvent(self, event):
        super().showEvent(event)
        self.widget.refresh_layers_if_changed()

    def dispose(self):
        """插件卸载时清理信号。"""
        self.release_project_signals()
        try:
            project = QgsProject.instance()
            project.layersAdded.disconnect(self._on_layers_changed)
            project.layersRemoved.disconnect(self._on_layers_changed)
        except (TypeError, RuntimeError):
            pass


class SearchShortcut(QObject):
    """在主窗口上注册 Ctrl+F 快捷键。

    - 作用域 WindowShortcut：地图、图层面板、面板里的控件都有效；
    - 焦点落在属性表里时自动让出该快捷键，交给属性表自己的 Ctrl+F 过滤；
    - 与 QGIS 自带快捷键重名时 Qt 只发 activatedAmbiguously，这里一并接住。
    """

    def __init__(self, iface, slot, parent=None):
        super().__init__(parent if parent is not None else iface.mainWindow())
        self.iface = iface
        self.slot = slot
        self._shortcut = None
        self._install()

    def _install(self):
        try:
            sc = QShortcut(QKeySequence(SEARCH_SHORTCUT), self.iface.mainWindow())
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(self._trigger)
            sc.activatedAmbiguously.connect(self._trigger)
            self._shortcut = sc
        except Exception as exc:
            QgsMessageLog.logMessage('注册 %s 快捷键失败: %s' % (SEARCH_SHORTCUT, exc),
                                     'Shape_Layer_Tools', Qgis.MessageLevel.Warning)
        app = QApplication.instance()
        if app is not None:
            app.focusChanged.connect(self._on_focus_changed)
            self._on_focus_changed(None, QApplication.focusWidget())

    def _trigger(self):
        if self.slot is not None:
            self.slot()

    def _on_focus_changed(self, _old, now):
        if self._shortcut is not None:
            self._shortcut.setEnabled(not _in_attribute_table(now))

    def release(self):
        app = QApplication.instance()
        if app is not None:
            try:
                app.focusChanged.disconnect(self._on_focus_changed)
            except (TypeError, RuntimeError):
                pass
        if self._shortcut is not None:
            self._shortcut.setEnabled(False)
            self._shortcut.deleteLater()
            self._shortcut = None

