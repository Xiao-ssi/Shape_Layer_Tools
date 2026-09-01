# -*- coding: utf-8 -*-
"""QGIS 插件入口：在顶部菜单栏添加"网优图层工具"菜单及其下拉操作。"""

import os

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QMenu

from .excel_shape_layer_dialog import (
    ExcelShapeLayerDialog,
    SearchDialog,
    ContainQueryDialog,
)
from .buffer_dialog import BufferDialog


class ExcelShapeLayer(object):
    def __init__(self, iface):
        self.iface = iface
        self.actions = []
        self.menu = None
        self.toolbar = None
        self.dlg = None
        self.search_dlg = None
        self.contain_dlg = None
        self.buffer_dlg = None

    @staticmethod
    def _icon_path(name='icon.svg'):
        base = os.path.dirname(os.path.abspath(__file__))
        p = os.path.join(base, 'icons', name)
        return p if os.path.exists(p) else None

    def _new_action(self, icon_name, text, slot):
        p = self._icon_path(icon_name)
        icon = QIcon(p) if p else QIcon()
        action = QAction(icon, text, self.iface.mainWindow())
        action.setToolTip(text)
        action.triggered.connect(slot)
        self.actions.append(action)
        return action

    def _show_dialog(self, tab_index):
        if self.dlg is None:
            self.dlg = ExcelShapeLayerDialog(self.iface, self.iface.mainWindow())
        self.dlg.switch_tab(tab_index)

    def initGui(self):
        # 顶部面板：新增"网优图层工具"工具栏，每个功能一个独立图标，点击即可运行
        self.toolbar = self.iface.addToolBar('网优图层工具')
        self.toolbar.setObjectName('excel_shape_layer_toolbar')
        action_make = self._new_action('icon_make.svg', '制作图层', lambda: self._show_dialog(0))
        action_export = self._new_action('icon_export.svg', '导出图层', lambda: self._show_dialog(1))
        action_search = self._new_action('icon_search.svg', '搜索数据', self.run_search)
        action_contain = self._new_action('icon_contain.svg', '图层包含查询', self.run_contain_query)
        action_buffer = self._new_action('icon_buffer.svg', '缓冲膨胀缩小', self.run_buffer_query)
        for a in (action_make, action_export, action_search, action_contain, action_buffer):
            self.toolbar.addAction(a)

        # 同时保留顶层菜单"网优图层工具"
        self.menu = QMenu('网优图层工具', self.iface.mainWindow())
        self.menu.addAction(action_make)
        self.menu.addAction(action_export)
        self.menu.addSeparator()
        self.menu.addAction(action_search)
        self.menu.addAction(action_contain)
        self.menu.addAction(action_buffer)
        self.iface.mainWindow().menuBar().addMenu(self.menu)

    def unload(self):
        if self.toolbar is not None:
            self.iface.mainWindow().removeToolBar(self.toolbar)
            self.toolbar.deleteLater()
            self.toolbar = None
        if self.menu is not None:
            self.iface.mainWindow().menuBar().removeAction(self.menu.menuAction())
            self.menu.deleteLater()
            self.menu = None
        for action in self.actions:
            action.deleteLater()
        self.actions = []

    def run_search(self):
        if self.search_dlg is None:
            self.search_dlg = SearchDialog(self.iface, self.iface.mainWindow())
        self.search_dlg.show()
        self.search_dlg.raise_()
        self.search_dlg.activateWindow()

    def run_contain_query(self):
        if self.contain_dlg is None:
            self.contain_dlg = ContainQueryDialog(self.iface, self.iface.mainWindow())
        self.contain_dlg.refresh_layers()
        self.contain_dlg.show()
        self.contain_dlg.raise_()
        self.contain_dlg.activateWindow()

    def run_buffer_query(self):
        if self.buffer_dlg is None:
            self.buffer_dlg = BufferDialog(self.iface, self.iface.mainWindow())
        self.buffer_dlg.refresh_layers()
        self.buffer_dlg.show()
        self.buffer_dlg.raise_()
        self.buffer_dlg.activateWindow()