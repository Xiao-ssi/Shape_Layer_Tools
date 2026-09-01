# -*- coding: utf-8 -*-

def classFactory(iface):
    """从 QGIS 加载插件时调用。"""
    from .excel_shape_layer import ExcelShapeLayer
    return ExcelShapeLayer(iface)