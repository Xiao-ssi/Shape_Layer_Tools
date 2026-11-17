# -*- coding: utf-8 -*-
"""
形状几何生成模块：根据经纬度、方位角、尺寸生成不同形状的多边形顶点。
所有尺寸(半径/边长/长度/宽度)均以"米"为单位，自动换算为经纬度偏移。
方位角为罗盘方位角，0=正北，顺时针增加（90=正东）。
"""
import math

# 每纬度度的米数（近似）
M_PER_DEG_LAT = 111320.0


def _degree_offset(lat_ref_deg, east_m, north_m):
    """将(东向米, 北向米)在参考纬度处换算为经纬度偏移(dlon, dlat)。"""
    m_per_deg_lon = M_PER_DEG_LAT * math.cos(math.radians(lat_ref_deg))
    if abs(m_per_deg_lon) < 1e-9:
        m_per_deg_lon = M_PER_DEG_LAT
    dlon = east_m / m_per_deg_lon
    dlat = north_m / M_PER_DEG_LAT
    return dlon, dlat


def _local_offset(lat_ref_deg, f, p, bearing_deg):
    """将局部坐标系(f:沿方位角方向, p:垂直于其右侧)的点换算为(dlon, dlat)。"""
    a = math.radians(bearing_deg)
    east = f * math.sin(a) + p * math.cos(a)
    north = f * math.cos(a) - p * math.sin(a)
    return _degree_offset(lat_ref_deg, east, north)


def circle_polygon(lon, lat, radius_m, num_points=48):
    """圆：以(经纬)为中心、radius为半径的多边形。"""
    pts = []
    for i in range(num_points):
        ang = 360.0 * i / num_points
        a = math.radians(ang)
        east = radius_m * math.sin(a)
        north = radius_m * math.cos(a)
        dlon, dlat = _degree_offset(lat, east, north)
        pts.append((lon + dlon, lat + dlat))
    pts.append(pts[0])
    return pts


def square_polygon(lon, lat, side_m, bearing_deg):
    """方形：边长 side，正面(第一条边)朝向方位角 direction。"""
    a = math.radians(bearing_deg)
    fx, fy = math.sin(a), math.cos(a)      # 前向单位向量(东,北)
    px, py = math.cos(a), -math.sin(a)     # 右侧单位向量
    half = side_m / 2.0
    # 顺序：前右、前左、后左、后右（闭合外环）
    corners = [
        (half, half), (half, -half), (-half, -half), (-half, half),
    ]
    pts = []
    for f, p in corners:
        east = fx * f + px * p
        north = fy * f + py * p
        dlon, dlat = _degree_offset(lat, east, north)
        pts.append((lon + dlon, lat + dlat))
    pts.append(pts[0])
    return pts


def sector_polygon(lon, lat, radius_m, bearing_deg, width_deg, num_arc=24):
    """扇形：以(经纬)为顶点，半径 radius，中心方位角朝向 bearing，张角为 width(度)。"""
    pts = [(lon, lat)]  # 顶点
    half = width_deg / 2.0
    count = max(2, int(num_arc))
    for i in range(count + 1):
        ang = (bearing_deg - half) + width_deg * i / float(count)
        a = math.radians(ang)
        east = radius_m * math.sin(a)
        north = radius_m * math.cos(a)
        dlon, dlat = _degree_offset(lat, east, north)
        pts.append((lon + dlon, lat + dlat))
    pts.append((lon, lat))  # 回到顶点闭合
    return pts


def sword_polygon(lon, lat, length_m, width_m, bearing_deg):
    """剑形(箭头/匕首)：正向沿方位角。length为总长，width为刀身宽。"""
    w = max(width_m, length_m * 0.05)
    ln = length_m
    # 局部点 (f沿前向, p沿右侧)
    local = [
        (ln, 0.0),            # 剑尖
        (ln * 0.15, w / 2.0), # 右肩
        (0.0, w / 2.0),      # 右尾
        (-ln * 0.15, 0.0),    # 尾部凹槽
        (0.0, -w / 2.0),     # 左尾
        (ln * 0.15, -w / 2.0),# 左肩
        (ln, 0.0),            # 回到剑尖
    ]
    pts = []
    for f, p in local:
        dlon, dlat = _local_offset(lat, f, p, bearing_deg)
        pts.append((lon + dlon, lat + dlat))
    return pts


def star_polygon(lon, lat, size_m, bearing_deg, points=5, inner_factor=0.45):
    """星形：外半径为 size，内半径 = size*inner_factor，5 角中的一个角指向方位角。"""
    a0 = math.radians(bearing_deg)
    pts = []
    for i in range(points * 2):
        ang = a0 + i * math.pi / points
        r = size_m if i % 2 == 0 else size_m * inner_factor
        east = r * math.sin(ang)
        north = r * math.cos(ang)
        dlon, dlat = _degree_offset(lat, east, north)
        pts.append((lon + dlon, lat + dlat))
    pts.append(pts[0])
    return pts


def hexagon_polygon(lon, lat, size_m, bearing_deg):
    """正六边形：外接圆半径 size，一个角指向方位角。"""
    a0 = math.radians(bearing_deg)
    pts = []
    for i in range(6):
        ang = a0 + i * 2 * math.pi / 6
        east = size_m * math.sin(ang)
        north = size_m * math.cos(ang)
        dlon, dlat = _degree_offset(lat, east, north)
        pts.append((lon + dlon, lat + dlat))
    pts.append(pts[0])
    return pts


def cross_polygon(lon, lat, size_m, bearing_deg):
    """十字（加号）：总长约等于 size，臂宽较窄，正面沿方位角。"""
    a = size_m / 2.0
    b = size_m * 0.16
    local = [
        (a, b), (a, -b), (b, -b), (b, -a), (-b, -a), (-b, -b),
        (-a, -b), (-a, b), (-b, b), (-b, a), (b, a), (b, b),
    ]
    pts = []
    for f, p in local:
        dlon, dlat = _local_offset(lat, f, p, bearing_deg)
        pts.append((lon + dlon, lat + dlat))
    pts.append(pts[0])
    return pts


def plane_polygon(lon, lat, size_m, bearing_deg):
    """飞机：机头朝向方位角，翼展/机身按 size 比例缩放。"""
    scale = size_m / 4.0  # 飞机总长约等于 size
    local = [
        (4.0, 0.0),        # 机头
        (1.4, 2.8), (1.0, 2.8), (1.0, 0.9),   # 右翼
        (-0.1, 0.8), (-0.1, 1.7), (-1.8, 1.2), (-1.8, 0.4), (-0.7, 0.0),
        (-1.8, -0.4), (-1.8, -1.2), (-0.1, -1.7), (-0.1, -0.8),  # 左翼
        (1.0, -0.9), (1.0, -2.8), (1.4, -2.8),
    ]
    pts = []
    for f, p in local:
        dlon, dlat = _local_offset(lat, f * scale, p * scale, bearing_deg)
        pts.append((lon + dlon, lat + dlat))
    pts.append(pts[0])
    return pts


POLYGON_BUILDERS = {
    'circle': circle_polygon,
    'square': square_polygon,
    'sector': sector_polygon,
    'sword': sword_polygon,
    'star': star_polygon,
    'hexagon': hexagon_polygon,
    'cross': cross_polygon,
    'plane': plane_polygon,
}


def build_polygon(shape, lon, lat, size_m, bearing_deg, width_deg=None, sword_width_m=None):
    """统一入口：返回多边形顶点列表 [(lon,lat), ...]，带闭合。
    shape: 'circle' | 'square' | 'sector' | 'sword' | 'star' | 'hexagon' | 'cross' | 'plane'
    """
    shape = shape.lower()
    if shape == 'circle':
        return circle_polygon(lon, lat, size_m)
    if shape == 'square':
        return square_polygon(lon, lat, size_m, bearing_deg)
    if shape == 'sector':
        w = width_deg if width_deg not in (None, 0) else 60.0
        return sector_polygon(lon, lat, size_m, bearing_deg, w)
    if shape == 'sword':
        sw = sword_width_m if sword_width_m not in (None, 0) else size_m * 0.3
        return sword_polygon(lon, lat, size_m, sw, bearing_deg)
    if shape == 'star':
        return star_polygon(lon, lat, size_m, bearing_deg)
    if shape == 'hexagon':
        return hexagon_polygon(lon, lat, size_m, bearing_deg)
    if shape == 'cross':
        return cross_polygon(lon, lat, size_m, bearing_deg)
    if shape == 'plane':
        return plane_polygon(lon, lat, size_m, bearing_deg)
    raise ValueError('未知形状类型: %s' % shape)