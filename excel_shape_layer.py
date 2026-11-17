# -*- coding: utf-8 -*-
"""QGIS 插件入口：在顶部菜单栏添加"网优图层工具"菜单及其下拉操作。"""

import os

from qgis.core import Qgis, QgsMessageLog
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QMenu

from .excel_shape_layer_dialog import ExcelShapeLayerDialog
from .search_dialog import SearchDialog, SearchDock, SearchShortcut
from .contain_query_dialog import ContainQueryDialog
from .buffer_dialog import BufferDialog
from .overlap_dialog import OverlapDialog


class ExcelShapeLayer(object):
    def __init__(self, iface):
        self.iface = iface
        self.actions = []
        self.menu = None
        self.toolbar = None
        self.dlg = None
        self.search_dlg = None
        self.search_dock = None
        self.search_shortcut = None
        self.contain_dlg = None
        self.buffer_dlg = None
        self.overlap_dlg = None

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
        action_search = self._new_action('icon_search.svg', '搜索数据 (Ctrl+F)', self.run_search)
        action_contain = self._new_action('icon_contain.svg', '图层包含查询', self.run_contain_query)
        action_buffer = self._new_action('icon_buffer.svg', '缓冲膨胀缩小', self.run_buffer_query)
        action_overlap = self._new_action('icon_overlap.svg', '同站同覆盖分析', self.run_overlap_query)
        for a in (action_make, action_export, action_search, action_contain, action_buffer, action_overlap):
            self.toolbar.addAction(a)

        # 同时保留顶层菜单"网优图层工具"
        self.menu = QMenu('网优图层工具', self.iface.mainWindow())
        self.menu.addAction(action_make)
        self.menu.addAction(action_export)
        self.menu.addSeparator()
        self.menu.addAction(action_search)
        self.menu.addAction(action_contain)
        self.menu.addAction(action_buffer)
        self.menu.addAction(action_overlap)
        self.iface.mainWindow().menuBar().addMenu(self.menu)

        # 搜索面板：左侧常驻的窄版面板；Ctrl+F / 工具栏 / 菜单打开的是功能完整的搜索窗口
        self._ensure_search_dock()
        if self.search_shortcut is None:
            self.search_shortcut = SearchShortcut(self.iface, self.run_search)

    def _ensure_search_dock(self):
        """创建（或取回）左侧"搜索数据"停靠面板。"""
        if self.search_dock is None:
            self.search_dock = SearchDock(self.iface)
            # 走 QgisInterface：顺带注册到「视图 → 面板」里，可随时勾选显示/隐藏
            self.iface.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.search_dock)
            # 首次创建时给一个窄宽度，之后由 QGIS 记住用户调整的结果
            try:
                self.iface.mainWindow().resizeDocks(
                    [self.search_dock], [SearchDock.MIN_WIDTH + 40],
                    Qt.Orientation.Horizontal)
            except Exception as e:
                # 个别版本的 QgisInterface 不提供 resizeDocks：宽度退回默认值即可，不影响功能
                QgsMessageLog.logMessage('设置搜索面板初始宽度失败: %s' % e,
                                         'Shape_Layer_Tools', Qgis.MessageLevel.Info)
            self.search_dock.show()
        return self.search_dock

    def unload(self):
        if self.search_shortcut is not None:
            self.search_shortcut.release()
            self.search_shortcut = None
        if self.search_dlg is not None:
            try:
                self.search_dlg.widget.hide()
                self.search_dlg.close()
                self.search_dlg.deleteLater()
            except Exception as e:
                QgsMessageLog.logMessage('关闭搜索窗口失败: %s' % e,
                                         'Shape_Layer_Tools', Qgis.MessageLevel.Warning)
            self.search_dlg = None
        if self.search_dock is not None:
            try:
                self.search_dock.widget.hide()      # 触发 hideEvent，清掉画布上的闪烁带
                self.search_dock.dispose()
                self.iface.mainWindow().removeDockWidget(self.search_dock)
                self.search_dock.deleteLater()
            except Exception as e:
                QgsMessageLog.logMessage('卸载搜索面板失败: %s' % e,
                                         'Shape_Layer_Tools', Qgis.MessageLevel.Warning)
            self.search_dock = None
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
        """弹出搜索窗口（Ctrl+F、工具栏按钮、菜单都走这里）。"""
        if self.search_dlg is None:
            self.search_dlg = SearchDialog(self.iface, self.iface.mainWindow())
        dlg = self.search_dlg
        if dlg.isVisible():
            dlg.refresh()           # 已打开：刷新图层并回到搜索框
        else:
            dlg.show()              # 首次显示：showEvent 里刷新并聚焦
        dlg.raise_()
        dlg.activateWindow()

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

    def run_overlap_query(self):
        if self.overlap_dlg is None:
            self.overlap_dlg = OverlapDialog(self.iface, self.iface.mainWindow())
        self.overlap_dlg.refresh_layers()
        self.overlap_dlg.show()
        self.overlap_dlg.raise_()
        self.overlap_dlg.activateWindow()