# 网优图层工具 (Shape Layer Tools)

Pluggable tools for network optimization (net-op) work in QGIS, providing common vector-layer operations.

A QGIS plugin with layer tools for network optimization: generate sector shapes from an Excel table, run layer containment queries, buffer/shrink point, line and polygon layers, and export to SHP/TAB/KML/KMZ/xlsx.网络优化常用图层工具，目前功能：无需固定模板制作图层，快速查询图层，支持导出为SHP/TAB/KML/KMZ谷歌地球文件，支持缓冲区制作，可扩大或缩小区域图层，单边扩大线条图层，支持同站同覆盖分析，可根据图形重叠度计算同覆盖（推荐用于2345G网络同覆盖评估、3CC组合评估）。

## 功能 / Features

- **制作图层**：无需固定模板，Excel 表中含经纬度、方位角、尺寸字段，即可生成圆形、扇形、刀形等不同形状的矢量图层。
- **导出图层**：将已加载的矢量图层导出为 SHP / TAB / KML / KMZ / xlsx 等格式，方便其他软件使用。
- **搜索数据**：在图层中检索数据并跳转到对应位置、闪烁定位。`Ctrl+F` 弹出搜索窗口，左侧另有常驻的窄版面板；百万行数据可建立本地 SQLite 查询索引，搜索从十几秒缩短到毫秒级。
- **图层包含查询**：查找指定区域图层中包含了哪些点/线/面要素，**支持同时勾选多个需查询图层与多个区域图层**，支持按起始点坐标或面积占比判断，可导出查询结果。
- **缓冲膨胀缩小**：对点、线、面图层沿边界向外/向内扩大或缩小指定距离，可生成单侧缓冲。
- **同站同覆盖分析**：分析多个图层中，根据设置距离和重叠度，判断是否存在同站同覆盖情况，支持导出分析结果。

## 大数据量搜索（索引加速）

「搜索数据」有两个入口，功能相同、设置互通：

- **搜索窗口**：按 **Ctrl+F**（或点工具栏/菜单的「搜索数据」）弹出，功能完整——含「设置显示字段」「跳转到该位置」，窗口大小可自由拖动。焦点在属性表里时会把 Ctrl+F 让给属性表自己的过滤。
- **左侧面板**：常驻的窄版面板，只保留搜索 / 刷新 / 闪烁，不占宽度；下拉框与结果列的超出文字用「…」截断（悬停看全称），双击结果行同样可定位并闪烁。面板位置与显隐由 QGIS 记住，也可在「视图 → 面板」里勾选。

搜索默认优先使用本地 SQLite 索引查询：

1. 选择图层后，若记录数较大（≥2 万行），首次搜索会提示是否建立索引；
2. 索引文件为 `<数据文件名>.sltidx.sqlite`，与数据文件放在同一目录；目录不可写、或数据位于 OneDrive 等同步盘时，改放 QGIS 配置目录的 `shape_layer_tools_cache` 下（避免大文件被反复上传）；
3. 索引只是缓存，**不改动任何原始数据**，删除该 sqlite 文件即可清除；
4. 数据文件被修改（大小/修改时间/要素数/字段变化）后索引自动判定为过期，可一键重建；
5. 也可点击「建立/重建索引」「删除索引」手动管理。

未建立索引时，搜索会把过滤条件下推到图层（由 QGIS 在底层筛选，只回传命中行），仍比逐行遍历快一个数量级。

## 安装

1. 下载本插件源码（或从 QGIS 官方插件仓库搜索 “Shape Layer Tools” 安装）。
2. 解压后，将 `Shape_Layer_Tools` 文件夹放入 QGIS 插件目录：
   - Windows：`%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\`
   - Linux：`~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`
3. 打开 QGIS，点击「插件 → 管理并安装插件」，在“已安装”中勾选启用在顶部工具栏和菜单里即可看到「网优图层工具」。

## 使用说明

- 顶部「网优图层工具」工具栏提供每个功能对应的图标按钮，点击即可运行。

## 提交到 QGIS 仓库前的自检

官方插件仓库会用 Bandit 做安全扫描，提交前建议本地先跑一遍（要求 0 告警）：

```bash
pip install bandit
bandit -r . -x ./.workbuddy
```

只要报表是 `Total issues: High 0 / Medium 0 / Low 0` 就不会被打回。
最容易命中的是 `B608`（把 SQL 用 `+`、`%`、`.format()`、f-string 拼出来）与 `B110`
（`except Exception: pass`）：前者应改为一律 `?` 参数绑定，语句里只保留内部列名；
后者应补日志后继续，而不是静默吞掉。

## 许可协议

本插件以 **GNU GPL v3** 协议开源发布。

## 作者

- 肖司（xiaossi@139.com）