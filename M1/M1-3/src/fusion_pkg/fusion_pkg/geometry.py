"""M1-3 纯计算层:相机模型、激光点投影、按距离着色、零依赖 PNG 写出。

与 rclpy 完全解耦,所以可以在没有 ROS 环境的机器上单独跑测试
(tools/check_geometry.py 就是干这个的)。

坐标系约定
----------
base_link : x 向前, y 向左, z 向上(ROS 惯例)
camera_link: x 向右, y 向下, z 向前(光学惯例)
laser_link : x 向前, y 向左, z 向上(2D 激光在 z=0 平面扫描)

base_link 到 camera_link 的旋转,把 base 的三个轴映射到 camera:
  base +x(前) -> camera +z(前)
  base +y(左) -> camera -x(左即右的反向)
  base +z(上) -> camera -y(上即下的反向)
因此 camera 系下的坐标 = R_CAM_FROM_BASE @ (p_base - t_camera)。
"""

import struct
import zlib

import numpy as np

# 把 base_link 向量转到 camera 光学系的旋转矩阵(正交,det=+1)
R_CAM_FROM_BASE = np.array([
    [0.0, -1.0, 0.0],
    [0.0, 0.0, -1.0],
    [1.0, 0.0, 0.0],
])

# 默认相机内参:640x480,水平视场约 77 度
DEFAULT_INTRINSICS = {"fx": 400.0, "fy": 400.0, "cx": 320.0, "cy": 240.0}

# ---------------------------------------------------------------- 外参
# 全项目唯一的定义处。static_tf 发布、baggen 生成、projector 校验都用它。
LASER_IN_BASE = (0.20, 0.0, 0.20)     # 激光:车头前 0.2m,离地 0.2m
CAMERA_IN_BASE = (0.05, 0.0, 0.40)    # 相机:车头前 0.05m,离地 0.4m
CAMERA_QUAT = (-0.5, 0.5, -0.5, 0.5)  # base->camera 旋转,x y z w

# ---------------------------------------------------------------- 合成场景
# 矩形房间 + 地面,只给 baggen 和离线自检使用
ROOM = {"x": (-8.0, 8.0), "y": (-6.0, 6.0), "wall_h": 2.5}

WALLS_2D = [
    (0, ROOM["x"][1], ROOM["y"]),     # 前墙 x=+8
    (0, ROOM["x"][0], ROOM["y"]),     # 后墙 x=-8
    (1, ROOM["y"][1], ROOM["x"]),     # 左墙 y=+6
    (1, ROOM["y"][0], ROOM["x"]),     # 右墙 y=-6
]


# ---------------------------------------------------------------- TF 定义


def tf_chain():
    """整棵静态坐标树,[(parent, child, xyz, quat), ...]。

    static_tf 节点只负责把这张表发到 /tf_static,数值定义仍在本文件,
    这样离线自检脚本能直接验证"广播出去的四元数"和"几何层用的旋转"
    是同一个东西 —— 这是 M1-3 最容易出错的一处。
    """
    return [
        ("map", "odom", (0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0)),
        ("odom", "base_link", (0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0)),
        ("base_link", "laser_link", LASER_IN_BASE, (0.0, 0.0, 0.0, 1.0)),
        ("base_link", "camera_link", CAMERA_IN_BASE, CAMERA_QUAT),
    ]


def cam_from_laser():
    """按 TF 语义算出 camera_link <- laser_link 的 (R, t)。

    p_cam = R @ p_laser + t
    推导:T_base<-camera = (t_cam, R_cam_from_base),T_base<-laser = (t_laser, I)
          camera<-laser = T_base<-camera^-1 * T_base<-laser
          => R = R_cam_from_base^T, t = R_cam_from_base^T @ (t_laser - t_cam)
    """
    trans = {child: xyz for _, child, xyz, _ in tf_chain()}
    quats = {child: q for _, child, _, q in tf_chain()}
    r = quat_to_matrix(*quats["camera_link"]).T
    t = r @ (np.array(trans["laser_link"]) - np.array(trans["camera_link"]))
    return r, t


# ---------------------------------------------------------------- 几何


def quat_to_matrix(x, y, z, w):
    """四元数 -> 3x3 旋转矩阵。用于把 TF 查到的朝向转成矩阵。"""
    n = x * x + y * y + z * z + w * w
    if n < 1e-12:
        return np.eye(3)
    s = 2.0 / n
    return np.array([
        [1 - s * (y * y + z * z), s * (x * y - z * w), s * (x * z + y * w)],
        [s * (x * y + z * w), 1 - s * (x * x + z * z), s * (y * z - x * w)],
        [s * (x * z - y * w), s * (y * z + x * w), 1 - s * (x * x + y * y)],
    ])


def scan_to_xy(ranges, angle_min, angle_inc, range_min, range_max):
    """LaserScan 的极坐标 -> laser_link 平面笛卡尔点,同时剔除无效值。

    返回 (xy, angles, dists),xy 形状 (N,2)。
    """
    r = np.asarray(ranges, dtype=float)
    ang = angle_min + np.arange(r.size) * angle_inc
    ok = np.isfinite(r) & (r >= range_min) & (r <= range_max)
    dist = r[ok]
    a = ang[ok]
    return np.stack([dist * np.cos(a), dist * np.sin(a)], axis=1), a, dist


def laser_xy_to_camera(xy, laser_xyz, cam_xyz):
    """laser_link 平面点(z=0) -> camera 系 3D 点。

    laser_xyz / cam_xyz 是两者在 base_link 中的位置。
    """
    p = np.empty((xy.shape[0], 3))
    p[:, 0] = xy[:, 0] + (laser_xyz[0] - cam_xyz[0])
    p[:, 1] = xy[:, 1] + (laser_xyz[1] - cam_xyz[1])
    p[:, 2] = 0.0 + (laser_xyz[2] - cam_xyz[2])
    return p @ R_CAM_FROM_BASE.T


def project(points_cam, intr, min_depth=0.2):
    """camera 系 3D 点 -> 像素坐标。返回 (uv, valid_mask, depth)。

    只保留相机前方(min_depth 以上)的点,否则除法会翻转到画面外。
    """
    z = points_cam[:, 2]
    valid = z > min_depth
    uv = np.full((points_cam.shape[0], 2), -1.0)
    uv[valid, 0] = intr["fx"] * points_cam[valid, 0] / z[valid] + intr["cx"]
    uv[valid, 1] = intr["fy"] * points_cam[valid, 1] / z[valid] + intr["cy"]
    return uv, valid, z


def color_by_range(dists, range_min=0.3, range_max=8.0):
    """近红远蓝:距离越近越红,越远越蓝。返回 (N,3) uint8。"""
    t = np.clip((np.asarray(dists, dtype=float) - range_min)
                / max(range_max - range_min, 1e-6), 0.0, 1.0)
    r = (255.0 * (1.0 - t)).astype(np.uint8)
    b = (255.0 * t).astype(np.uint8)
    g = np.full(r.shape, 40, dtype=np.uint8)
    return np.stack([r, g, b], axis=1)


# ---------------------------------------------------------------- 画布


def blank_image(width, height):
    """上浅下深的灰度渐变,当作"墙 + 地面"的底图。"""
    img = np.zeros((height, width, 3), dtype=np.uint8)
    for y in range(height):
        v = int(190 - 120 * (y / max(height - 1, 1)))
        img[y, :, :] = (v, v, v)
    return img


def draw_point(img, u, v, color, radius=2):
    """画一个实心方块。越界自动裁剪,不会抛异常。"""
    h, w = img.shape[:2]
    x0, x1 = max(int(u) - radius, 0), min(int(u) + radius + 1, w)
    y0, y1 = max(int(v) - radius, 0), min(int(v) + radius + 1, h)
    if x0 < x1 and y0 < y1:
        img[y0:y1, x0:x1, :] = color
    return img


def draw_line(img, p0, p1, color, thickness=1):
    """两点间画线,线性插值(针孔模型下直线投影后仍是直线)。"""
    n = int(max(abs(p1[0] - p0[0]), abs(p1[1] - p0[1]))) + 1
    n = min(max(n, 2), 2000)
    xs = np.linspace(p0[0], p1[0], n)
    ys = np.linspace(p0[1], p1[1], n)
    for x, y in zip(xs, ys):
        draw_point(img, x, y, color, radius=max(thickness - 1, 0))
    return img


# ---------------------------------------------------------------- 输出


def write_png(path, rgb):
    """(H,W,3) uint8 -> 8bit RGB PNG。只依赖标准库 zlib,不需要 opencv。"""
    arr = np.ascontiguousarray(rgb, dtype=np.uint8)
    h, w = arr.shape[:2]
    raw = b"".join(b"\x00" + arr[y].tobytes() for y in range(h))

    def chunk(tag, data):
        body = tag + data
        return (struct.pack(">I", len(data)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    blob = (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 6))
            + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(blob)
    return path


def rgb_to_bytes(img):
    """(H,W,3) uint8 -> 行优先字节流,用于填进 sensor_msgs/Image.data。"""
    return np.ascontiguousarray(img, dtype=np.uint8).tobytes()


# ---------------------------------------------------------------- 场景渲染


def raycast_2d(ox, oy, angles):
    """从 (ox, oy) 向各角度发射射线,返回打到房间墙面的距离。"""
    dx, dy = np.cos(angles), np.sin(angles)
    best = np.full(angles.size, np.inf)
    for axis, value, (lo, hi) in WALLS_2D:
        d = dx if axis == 0 else dy
        o = ox if axis == 0 else oy
        with np.errstate(divide="ignore", invalid="ignore"):
            t = (value - o) / d
        hit = oy + t * dy if axis == 0 else ox + t * dx
        ok = (t > 0.0) & (hit >= lo) & (hit <= hi)
        best = np.where(ok & (t < best), t, best)
    best = np.where(np.isfinite(best), best, 10.0)
    return np.clip(best, 0.1, 10.0)


def render(cam_xyz, width, height, intr, range_max=10.0):
    """从相机位置逐像素光线投射,返回 (H, W, 3) 图像。

    与 project() 用同一套针孔模型,所以激光点投到这张图上
    会精确落在对应的墙面上 —— 这正是自检脚本要验证的闭环。
    """
    uu, vv = np.meshgrid(np.arange(width), np.arange(height))
    d_cam = np.stack([(uu - intr["cx"]) / intr["fx"],
                      (vv - intr["cy"]) / intr["fy"],
                      np.ones(uu.shape)], axis=-1)
    dirs = d_cam @ R_CAM_FROM_BASE        # camera 方向 -> base 方向
    origin = np.array(cam_xyz, dtype=float)

    planes = [
        (0, ROOM["x"][1], {1: ROOM["y"], 2: (0.0, ROOM["wall_h"])}),
        (0, ROOM["x"][0], {1: ROOM["y"], 2: (0.0, ROOM["wall_h"])}),
        (1, ROOM["y"][1], {0: ROOM["x"], 2: (0.0, ROOM["wall_h"])}),
        (1, ROOM["y"][0], {0: ROOM["x"], 2: (0.0, ROOM["wall_h"])}),
        (2, 0.0, {0: ROOM["x"], 1: ROOM["y"]}),        # 地面
    ]

    dist = np.full(uu.shape, np.inf)
    kind = np.zeros(uu.shape, dtype=np.uint8)          # 1=墙 2=地面
    for axis, value, bounds in planes:
        den = dirs[..., axis]
        with np.errstate(divide="ignore", invalid="ignore"):
            t = (value - origin[axis]) / den
        t = np.where(np.abs(den) < 1e-9, np.inf, t)
        ok = t > 0.05
        # t=inf(射线与该平面平行)时 inf*0 会出 nan,这里静默掉;
        # 这些像素随后会被 ok 与 np.isfinite 滤掉,不影响成像。
        with np.errstate(invalid="ignore"):
            pt = origin + t[..., None] * dirs
        for ax, (lo, hi) in bounds.items():
            ok &= (pt[..., ax] >= lo) & (pt[..., ax] <= hi)
        better = ok & (t < dist)
        dist = np.where(better, t, dist)
        kind = np.where(better, 1 if axis != 2 else 2, kind)

    # 按距离做明暗衰减,墙面偏暖、地面偏冷
    shade = np.clip(
        1.0 / (1.0 + 0.10 * np.where(np.isfinite(dist), dist, range_max * 5)),
        0.06, 1.0)
    img = np.empty((height, width, 3))
    img[..., 0] = 205.0 * shade
    img[..., 1] = 205.0 * shade
    img[..., 2] = 200.0 * shade
    floor = kind == 2
    img[floor, 0] *= 0.80
    img[floor, 1] *= 0.85
    img[floor, 2] *= 0.95
    img[~np.isfinite(dist)] = (12.0, 14.0, 20.0)   # 高于墙顶的视线
    return np.clip(img, 0, 255).astype(np.uint8)
