# -*- coding: utf-8 -*-
"""兼容入口（门面）：
本文件已按"每个大功能一个独立 py 文件"拆分，各功能移入以下独立模块：
    - utils.py                  共享工具函数与常量
    - sources.py                表格数据源读取（CSV/XLSX/XLS/XLSB）
    - ui_components.py          通用 UI 组件（颜色选择、分类设置）
    - search_dialog.py          搜索数据（SearchWidget / SearchDialog）
    - contain_query_dialog.py   图层包含查询
    - main_dialog.py            制作图层 / 导出图层（ExcelShapeLayerDialog）

保留本文件仅为保持历史导入路径（如 excel_shape_layer.py 及外部脚本）不变，
不再修改本文件中的功能代码；后续新需求直接改各独立模块即可，互不影响。
"""

# 共享工具函数与常量
from .utils import (
    _to_float,
    _BASE_COLORS,
    _distinct_colors,
    SHAPE_LABELS,
    SHAPE_LIST_ORDER,
    WRITER_ERROR_TEXT,
)

# 数据源
from .sources import (
    _BaseSource,
    _OpenPyXlSource,
    _PandasSource,
    _XlsbSource,
    _CsvSource,
    open_source,
    VALID_EXTS,
    CSV_ENC,
)

# 通用 UI 组件
from .ui_components import (
    ColorButton,
    PRESET_COLORS,
    _SwatchButton,
    ColorPickDialog,
    ClassifyConfigDialog,
)

# 搜索数据
from .search_dialog import SearchWidget, SearchDialog

# 图层包含查询
from .contain_query_dialog import ContainQueryDialog

# 制作/导出图层
from .main_dialog import ExcelShapeLayerDialog, EXPORT_FORMATS, SIZE_UNIT_TIP, FILE_FILTER