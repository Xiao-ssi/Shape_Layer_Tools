# 网优图层工具 (Shape Layer Tools)

Pluggable tools for network optimization (net-op) work in QGIS, providing common vector-layer operations.

A QGIS plugin with layer tools for network optimization: generate sector shapes from an Excel table, run layer containment queries, buffer/shrink point, line and polygon layers, and export to SHP/TAB/KML/KMZ/xlsx.

## 功能 / Features

- **制作图层**：无需固定模板，Excel 表中含经纬度、方位角、尺寸字段，即可生成圆形、扇形、刀形等不同形状的矢量图层。
- **导出图层**：将已加载的矢量图层导出为 SHP / TAB / KML / KMZ / xlsx 等格式。
- **搜索数据**：在图层中检索数据并跳转到对应位置、闪烁定位。
- **图层包含查询**：查找指定区域图层中包含了哪些点/线/面要素，支持按起始点坐标或面积占比判断，可导出查询结果。
- **缓冲膨胀缩小**：对点、线、面图层沿边界向外/向内扩大或缩小指定距离，可生成单侧缓冲。

## 安装

1. 下载本插件源码（或从 QGIS 官方插件仓库搜索 “Shape Layer Tools” 安装）。
2. 解压后，将 `Shape_Layer_Tools` 文件夹放入 QGIS 插件目录：
   - Windows：`%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\`
   - Linux：`~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`
3. 打开 QGIS，点击「插件 → 管理并安装插件」，在“已安装”中勾选启用在顶部工具栏和菜单里即可看到「网优图层工具」。

## 使用说明

- 顶部「网优图层工具」工具栏提供每个功能对应的图标按钮，点击即可运行。

## 许可协议

本插件以 **GNU GPL v3** 协议开源发布。

## 作者

- 肖司（xiaossi@139.com）