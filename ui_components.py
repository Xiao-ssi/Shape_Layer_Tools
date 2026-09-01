# -*- coding: utf-8 -*-
"""通用 UI 组件：颜色选择相关的小部件与“分类设置”弹出窗口。
被“生成图层”主对话框使用；本模块独立，可单独修改而不影响其他功能模块。"""

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QPushButton,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QSlider,
    QSpinBox,
    QDialogButtonBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QComboBox,
    QLineEdit,
)

from .utils import _to_float, SHAPE_LABELS, SHAPE_LIST_ORDER


class ColorButton(QPushButton):
    def __init__(self, color, parent=None):
        super().__init__(parent)
        self.setColor(QColor(color))
        self.setFixedWidth(70)
        self.clicked.connect(self._pick)

    def setColor(self, color):
        self.color = QColor(color)
        self.setStyleSheet('background-color:%s; border:1px solid #888;' % self.color.name())
        self.setText(self.color.name())

    def _pick(self):
        dlg = ColorPickDialog(self, self.color)
        if dlg.exec_():
            self.setColor(dlg.result_color())


PRESET_COLORS = [
    '#000000', '#ffffff', '#808080', '#c0c0c0', '#800000', '#ff0000', '#ff8000', '#ffff00',
    '#808000', '#00ff00', '#008000', '#00ffff', '#008080', '#0000ff', '#000080', '#ff00ff',
    '#800080', '#00b050', '#2e75b6', '#1f4e78', '#ed7d31', '#c00000', '#ffc000', '#a6a6a6',
    '#70ad47', '#5b9bd5', '#7030a0', '#953735', '#d99694', '#9dc3e6', '#c6e0b4', '#ffe699',
]


class _SwatchButton(QPushButton):
    """色块按钮：单击预览，双击直接选取并关闭。"""

    previewed = pyqtSignal()
    picked = pyqtSignal()

    def __init__(self, color, parent=None):
        super().__init__(parent)
        self._color = QColor(color)
        self.setFixedSize(30, 30)
        self._apply_style()
        self.setToolTip('%s（单击预览，双击确定）' % self._color.name())
        self.clicked.connect(self.previewed.emit)

    def _apply_style(self):
        self.setStyleSheet(
            'background-color:%s; border:1px solid #888;' % self._color.name())

    def color(self):
        return QColor(self._color)

    def mouseDoubleClickEvent(self, event):
        self.picked.emit()
        event.accept()


class ColorPickDialog(QDialog):
    """自定义取色窗口：背景固定为白色、支持透明度、双击色块快速选取、确定按钮加长。"""

    def __init__(self, parent, initial):
        super().__init__(parent)
        self.setWindowTitle('选择颜色')
        self.setStyleSheet('QDialog,QDialog *{ background:#ffffff; }')
        self._result = QColor(initial)
        lay = QVBoxLayout(self)

        grid = QGridLayout(); grid.setSpacing(4)
        self._btns = []
        for i, hexs in enumerate(PRESET_COLORS):
            b = _SwatchButton(hexs)
            b.previewed.connect(self._preview_current)
            b.picked.connect(self.accept)  # 双击：快速确定并关闭
            grid.addWidget(b, i // 8, i % 8)
            self._btns.append(b)
        lay.addLayout(grid)

        hrow = QHBoxLayout()
        self.preview = QLabel(); self.preview.setFixedSize(60, 34)
        self.lbl_hex = QLabel('')
        hrow.addWidget(self.preview, 1)
        hrow.addWidget(self.lbl_hex, 2)
        lay.addLayout(hrow)

        # 透明度百分比：0%=不透明，100%=完全透明
        arow = QHBoxLayout()
        arow.addWidget(QLabel('透明度(%):'))
        self.pct = 100 - int(round(initial.alpha() / 255.0 * 100))
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 100); self.slider.setValue(self.pct)
        self.spin = QSpinBox(); self.spin.setRange(0, 100); self.spin.setSuffix('%')
        self.spin.setValue(self.pct)
        self.slider.valueChanged.connect(self.spin.setValue)
        self.spin.valueChanged.connect(self.slider.setValue)
        self.slider.valueChanged.connect(lambda _: self._refresh_preview())
        arow.addWidget(self.slider, 1)
        arow.addWidget(self.spin)
        lay.addLayout(arow)
        aura = QLabel('100%为完全透明，0%为不透明；线条设为100%则不绘制线条。')
        aura.setStyleSheet('color:#888;'); aura.setWordWrap(True)
        lay.addWidget(aura)

        blay = QHBoxLayout()
        self.btn_ok = QPushButton('确定'); self.btn_ok.setMinimumSize(120, 38)
        self.btn_ok.clicked.connect(self.accept)
        btn_cancel = QPushButton('取消'); btn_cancel.setMinimumSize(120, 38)
        btn_cancel.clicked.connect(self.reject)
        blay.addStretch(); blay.addWidget(self.btn_ok); blay.addSpacing(12); blay.addWidget(btn_cancel)
        lay.addLayout(blay)
        self._refresh_preview()

    def _preview_current(self):
        if hasattr(self.sender(), 'color'):
            self._result = self.sender().color()
        self._refresh_preview()

    def _refresh_preview(self):
        c = QColor(self._result)
        c.setAlpha(int(round(255 * (100 - self.slider.value()) / 100.0)))
        self._result = c
        name = c.name().upper()
        if c.alpha() < 255:
            name += ' (透明度 %d%%)' % self.slider.value()
        self.preview.setStyleSheet('background-color:%s; border:1px solid #888;' % c.name())
        self.lbl_hex.setText(name)

    def result_color(self):
        return QColor(self._result)


class ClassifyConfigDialog(QDialog):
    """弹出窗口：针对分类字段的每个取值，分别设置其样式/半径/长度/顶角/颜色。"""

    def __init__(self, parent, headers, values, cfg, defaults):
        super().__init__(parent)
        self.setWindowTitle('分类设置 - 各分类图形单独配置')
        self.resize(800, 500)
        self._headers = list(headers)
        self._values = list(values)
        self._cfg = {k: dict(v) for k, v in cfg.items()}
        self._defaults = defaults

        lay = QVBoxLayout(self)
        tip = QLabel('为下方每个"分类值"分别设置其图形形状、尺寸、顶角及颜色。'
                     '其中尺寸可输入数字，也可选择列标题；留空表示沿用全局设置。')
        tip.setWordWrap(True); tip.setStyleSheet('color:#666;')
        lay.addWidget(tip)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ['分类值', '样式', '尺寸(米)', '顶角/剑宽', '填充', '边框'])
        self.table.verticalHeader().setVisible(False)
        lay.addWidget(self.table)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._populate()

    def _dim_combo(self, value, default):
        cmb = QComboBox(); cmb.setEditable(True); cmb.clear(); cmb.addItems(self._headers)
        # 空=沿用全局；预填当前值便于微调
        if value is not None:
            cmb.setCurrentText(str(value))
        else:
            cmb.setCurrentText('' if default is None else str(default))
        return cmb

    def _populate(self):
        self.table.setRowCount(len(self._values))
        for i, v in enumerate(self._values):
            cfg = self._cfg.get(v, {})
            self.table.setItem(i, 0, QTableWidgetItem(str(v)))
            cmb = QComboBox()
            for k in SHAPE_LIST_ORDER:
                cmb.addItem(SHAPE_LABELS[k], k)
            shape = cfg.get('shape') or self._defaults['shape']
            cmb.setCurrentIndex(cmb.findData(shape))
            self.table.setCellWidget(i, 1, cmb)
            self.table.setCellWidget(i, 2, self._dim_combo(cfg.get('size'), self._defaults['size']))
            w = cfg.get('width')
            self.table.setCellWidget(i, 3, QLineEdit('' if w is None else str(w)))
            fill = cfg.get('fill') or QColor('#00b050')
            line = cfg.get('line') or QColor(0, 0, 0, 0)  # 默认无线条边框（透明）
            self.table.setCellWidget(i, 4, ColorButton(fill))
            self.table.setCellWidget(i, 5, ColorButton(line))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setColumnWidth(0, 120)

    def result_config(self):
        cfg = {}
        for i in range(self.table.rowCount()):
            val = self._values[i]
            cfg[val] = {
                'shape': self.table.cellWidget(i, 1).currentData(),
                'size': _to_float(self.table.cellWidget(i, 2).currentText()),
                'width': _to_float(self.table.cellWidget(i, 3).text()),
                'fill': self.table.cellWidget(i, 4).color,
                'line': self.table.cellWidget(i, 5).color,
            }
        return cfg