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
    l = length_m
    # 局部点 (f沿前向, p沿右侧)
    local = [
        (l, 0.0),            # 剑尖
        (l * 0.15, w / 2.0), # 右肩
        (0.0, w / 2.0),      # 右尾
        (-l * 0.15, 0.0),    # 尾部凹槽
        (0.0, -w / 2.0),     # 左尾
        (l * 0.15, -w / 2.0),# 左肩
        (l, 0.0),            # 回到剑尖
    ]
    pts = []
    for f, p in local:
        dlon, dlat = _local_offset(lat, f, p, bearing_deg)
        pts.append((lon + dlon, lat + dlat))
    return pts


POLYGON_BUILDERS = {
    'circle': circle_polygon,
    'square': square_polygon,
    'sector': sector_polygon,
    'sword': sword_polygon,
}


def build_polygon(shape, lon, lat, size_m, bearing_deg, width_deg=None, sword_width_m=None):
    """统一入口：返回多边形顶点列表 [(lon,lat), ...]，带闭合。
    shape: 'circle' | 'square' | 'sector' | 'sword'
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
    raise ValueError('未知形状类型: %s' % shape)