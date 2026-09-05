# -*- coding: utf-8 -*-
"""共享工具函数与常量：被多个功能模块复用，避免各模块之间循环依赖。
修改本文件不会影响其余功能模块的正常运行；其余模块仅在需要时从本文件导入。"""
import random

from qgis.PyQt.QtGui import QColor


# 会话级设置记忆：仅在本次 QGIS 运行期间生效，重启 QGIS（Python 进程销毁）后自动重置。
# 结构: {"对话框名": {"项名": 值}}，仅缓存可序列化的简单类型(str/int/float/bool)。
_SESSION = {}


def session_get(dialog, key, default=None):
    """读取会话记忆中的设置值。"""
    return _SESSION.setdefault(dialog, {}).get(key, default)


def session_set(dialog, key, value):
    """写入会话记忆中的设置值。"""
    _SESSION.setdefault(dialog, {})[key] = value


def _to_float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# 直观易辨认的基础色板（红/黄/绿/蓝/橙/紫/青/品红等）
_BASE_COLORS = [
    '#e6194b', '#ffe119', '#3cb44b', '#4363d8',   # 红  黄  绿  蓝
    '#f58231', '#911eb4', '#46f0f0', '#f032e6',   # 橙  紫  青  品红
    '#008080', '#9a6324', '#00b0ff', '#000075',   # 青绿 棕  天蓝 深蓝
]


def _distinct_colors(count):
    """从直观基础色板中随机分配 count 个互不重复的颜色（一眼可辨，不超过色板数则不重复）。"""
    if count <= 0:
        return []
    palette = list(_BASE_COLORS)
    random.shuffle(palette)           # 随机打乱，保证每次顺序不同
    return [QColor(palette[i % len(palette)]) for i in range(count)]


# 形状类型标签与顺序（供“生成图层”与“分类设置”共用）
SHAPE_LABELS = {'circle': '圆点', 'square': '方形', 'sector': '扇形', 'sword': '剑形'}
SHAPE_LIST_ORDER = ['circle', 'square', 'sector', 'sword']


# QgsVectorFileWriter.WriterError 错误码 → 中文说明，便于定位
WRITER_ERROR_TEXT = {
    0: '成功',
    1: '未找到 OGR 驱动（ErrDriverNotFound）',
    2: '创建数据源失败（ErrCreateDataSource，多为输出目录不存在或没有写入权限）',
    3: '创建图层失败（ErrCreateLayer）',
    4: '属性类型不受支持（ErrAttributeTypeUnsupported）',
    5: '属性创建失败（ErrAttributeCreationFailed，字段名截断后可能重名）',
    6: '投影/坐标转换失败（ErrProjection）',
    7: '要素写入失败（ErrFeatureWriteFailed，多为几何含 NaN/Inf 等非法坐标）',
    8: '无效图层（ErrInvalidLayer）',
}