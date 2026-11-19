#!/usr/bin/env python3
"""M1-3 离线自检:不需要 ROS,只靠 numpy 验证整条几何链路。

为什么需要这个
--------------
projector 跑起来只会告诉你"处理了 N 帧",不会告诉你"投影到底对不对"。
本脚本把同一套几何独立复算一遍,用 7 个可判定的条件卡住最容易错的环节:

  1. CAMERA_QUAT 转出来的矩阵 == 几何层用的 R_CAM_FROM_BASE
  2. tf_chain() 推出的 camera<-laser 变换 == laser_xy_to_camera()
  3. 投影后再反投影能回到原相机系坐标(针孔模型自洽)
  4. 正前方的激光点落在图像主点正下方(可解析预测)
  5. 车后方的激光点被判为不可见(深度裁剪生效)
  6. 着色函数近红远蓝且单调
  7. 投影点落在渲染图的墙面上,而不是天空(端到端几何闭环)

第 1、2 条最容易出错:外参同时被 TF 和几何两处使用,
四元数写错一个符号,投影结果就会整体偏移几十像素,但程序不报任何错。

用法
----
  python3 tools/check_geometry.py
退出码 0 = 全过,1 = 有失败项。
"""

import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src", "fusion_pkg"))

from fusion_pkg import geometry as G   # noqa: E402

FAILED = []


def check(name, ok, detail=""):
    """记录一项检查结果,返回 ok 方便串联。"""
    tag = "PASS" if ok else "FAIL"
    print("  [%s] %s%s" % (tag, name, ("  (%s)" % detail) if detail else ""))
    if not ok:
        FAILED.append(name)
    return ok


def laser_points(n_beams=360, x=0.0):
    """模拟一帧激光:从 x 处扫一圈,返回 laser_link 系 3D 点与距离。"""
    angles = np.linspace(-np.pi, np.pi, n_beams, endpoint=False)
    ranges = G.raycast_2d(x + G.LASER_IN_BASE[0], 0.0, angles)
    xy, _, dist = G.scan_to_xy(
        ranges, -np.pi, 2.0 * np.pi / n_beams, 0.1, 10.0)
    return np.hstack([xy, np.zeros((xy.shape[0], 1))]), dist


def main():
    intr = dict(G.DEFAULT_INTRINSICS)
    print("=" * 62)
    print("M1-3 几何链路离线自检")
    print("  相机内参 fx=%.0f fy=%.0f cx=%.0f cy=%.0f"
          % (intr["fx"], intr["fy"], intr["cx"], intr["cy"]))
    print("  相机位置 base=(%.2f, %.2f, %.2f)"
          % G.CAMERA_IN_BASE)
    print("  激光位置 base=(%.2f, %.2f, %.2f)"
          % G.LASER_IN_BASE)
    print("=" * 62)

    # ---------------------------------------------------------- 1
    print("\n[1] 四元数 -> 旋转矩阵")
    # TF 的 rotation 表示"child 在 parent 中的朝向",即 p_parent = R @ p_child + t。
    # 所以 base_link->camera_link 的四元数转出来是 R_base_cam(camera 轴在 base 里的表示),
    # 而几何层用的 R_CAM_FROM_BASE 是它的转置(camera<-base)。两者必须互为转置。
    r_base_cam = G.quat_to_matrix(*G.CAMERA_QUAT)
    err = float(np.abs(r_base_cam.T - G.R_CAM_FROM_BASE).max())
    check("CAMERA_QUAT 转置后 == R_CAM_FROM_BASE", err < 1e-12,
          "max|Δ|=%.2e" % err)
    det = float(np.linalg.det(r_base_cam))
    check("旋转矩阵行列式为 +1(不是镜像)", abs(det - 1.0) < 1e-9,
          "det=%.6f" % det)
    # 直接验轴重排语义:base 的 +x(前) 应落在 camera 的 +z(前)
    fwd = G.R_CAM_FROM_BASE @ np.array([1.0, 0.0, 0.0])
    up = G.R_CAM_FROM_BASE @ np.array([0.0, 0.0, 1.0])
    check("base 前方(+) 映射到相机 z 轴", np.allclose(fwd, [0, 0, 1]),
          "R@[1,0,0]=(%.0f,%.0f,%.0f)" % tuple(fwd))
    check("base 上方(z) 映射到相机 -y 轴", np.allclose(up, [0, -1, 0]),
          "R@[0,0,1]=(%.0f,%.0f,%.0f)" % tuple(up))

    # ---------------------------------------------------------- 2
    print("\n[2] TF 链路 vs 几何层外参")
    r_tf, t_tf = G.cam_from_laser()
    pts, dist = laser_points()
    p_geo = G.laser_xy_to_camera(pts[:, :2], G.LASER_IN_BASE,
                                 G.CAMERA_IN_BASE)
    p_tf = pts @ r_tf.T + t_tf
    err = float(np.abs(p_geo - p_tf).max())
    check("两种算法得到的相机系坐标一致", err < 1e-9,
          "max|Δ|=%.2e m" % err)
    print("      camera<-laser 平移 t=(%.3f, %.3f, %.3f)"
          % tuple(t_tf))

    # ---------------------------------------------------------- 3
    print("\n[3] 针孔投影自洽性")
    uv, valid, depth = G.project(p_tf, intr)
    z = depth[valid]
    back = np.stack([
        (uv[valid, 0] - intr["cx"]) / intr["fx"] * z,
        (uv[valid, 1] - intr["cy"]) / intr["fy"] * z,
        z,
    ], axis=1)
    err = float(np.abs(back - p_tf[valid]).max())
    check("投影 -> 反投影回到原点", err < 1e-9, "max|Δ|=%.2e m" % err)
    check("可见点数约占一半(前半球)", valid.sum() > 0.35 * valid.size,
          "可见 %d/%d" % (int(valid.sum()), valid.size))

    # ---------------------------------------------------------- 4
    print("\n[4] 激光系正前方 3m 的点(可解析预测)")
    probe = np.array([[3.0, 0.0, 0.0]])
    pc = G.laser_xy_to_camera(probe[:, :2], G.LASER_IN_BASE, G.CAMERA_IN_BASE)
    puv, pvalid, pdepth = G.project(pc, intr)
    # 该点在 base 系是 (3.2, 0, 0.2),相机在 (0.05, 0, 0.4):
    #   前向距离 = 3.2 - 0.05 = 3.15,高度差 = 0.4 - 0.2 = 0.2
    # 相机看向水平前方,所以该点必然投影在主点"下方" fy*0.2/3.15 像素处
    fwd = 3.0 + G.LASER_IN_BASE[0] - G.CAMERA_IN_BASE[0]
    dz = G.CAMERA_IN_BASE[2] - G.LASER_IN_BASE[2]
    exp_v = intr["cy"] + intr["fy"] * dz / fwd
    check("该点可见", bool(pvalid[0]))
    check("u 落在主点上(左右不偏)", abs(puv[0, 0] - intr["cx"]) < 1e-6,
          "u=%.3f, cx=%.0f" % (puv[0, 0], intr["cx"]))
    check("v 在上式计算位置", abs(puv[0, 1] - exp_v) < 1.0,
          "v=%.1f, 期望=%.1f" % (puv[0, 1], exp_v))
    check("深度等于前向距离", abs(pdepth[0] - fwd) < 0.01,
          "z=%.3f, 期望=%.2f" % (pdepth[0], fwd))

    # ---------------------------------------------------------- 5
    print("\n[5] 正后方 3m 的点")
    behind = np.array([[-3.0, 0.0, 0.0]])
    pb = G.laser_xy_to_camera(behind[:, :2], G.LASER_IN_BASE,
                              G.CAMERA_IN_BASE)
    _, bvalid, bdepth = G.project(pb, intr)
    check("被判为不可见(深度裁剪)", not bool(bvalid[0]),
          "z=%.3f" % bdepth[0])

    # ---------------------------------------------------------- 6
    print("\n[6] 距离着色")
    cols = G.color_by_range(np.array([0.3, 2.0, 4.0, 8.0]), 0.3, 8.0)
    check("最近点偏红", cols[0, 0] > 240 and cols[0, 2] < 20,
          "rgb=(%d,%d,%d)" % tuple(int(c) for c in cols[0]))
    check("最远点偏蓝", cols[-1, 2] > 240 and cols[-1, 0] < 20,
          "rgb=(%d,%d,%d)" % tuple(int(c) for c in cols[-1]))
    check("红色分量单调递减", bool(np.all(np.diff(cols[:, 0].astype(int)) <= 0)))

    # ---------------------------------------------------------- 7
    print("\n[7] 端到端:投影点是否落在墙面上")
    img = G.render(G.CAMERA_IN_BASE, 640, 480, intr)
    us = uv[valid, 0].astype(int)
    vs = uv[valid, 1].astype(int)
    inside = (us >= 0) & (us < 640) & (vs >= 0) & (vs < 480)
    # 画面顶部是"高于墙顶的天空",像素值只有 12~20;墙面最暗也有 100 左右
    bright = img[vs[inside], us[inside]].mean(axis=1)
    dark = int((bright < 40).sum())
    check("画布内点数足够做判断", int(inside.sum()) > 50,
          "画布内 %d 点" % int(inside.sum()))
    check("没有点落在天空上", dark == 0,
          "%d 个点亮度<40, 最低 %.0f" % (dark, bright.min() if bright.size else -1))

    # ---------------------------------------------------------- 8
    print("\n[8] 反向验证:这组检查真的能抓到错")
    # 把相机四元数误写成单位四元数(= 忘了 base->optical 的轴重排)
    wrong = G.quat_to_matrix(0.0, 0.0, 0.0, 1.0)
    wrong_r = wrong.T
    wrong_t = wrong_r @ (np.array(G.LASER_IN_BASE)
                         - np.array(G.CAMERA_IN_BASE))
    p_wrong = pts @ wrong_r.T + wrong_t
    _, wvalid, _ = G.project(p_wrong, intr)
    ratio = float(wvalid.sum()) / float(wvalid.size)
    check("外参写错时可见点数骤降(检验有效)", ratio < 0.3,
          "错误外参可见比例 %.0f%%" % (ratio * 100))

    # ---------------------------------------------------------- 灵敏度
    print("\n[外参灵敏度] 答辩用数字")
    fwd = 3.0 + G.LASER_IN_BASE[0] - G.CAMERA_IN_BASE[0]
    for dx, label in ((0.05, "平移 5cm"), (0.10, "平移 10cm")):
        print("  %s -> 3m 处偏差 %.1f px" % (label, intr["fx"] * dx / fwd))
    print("  旋转 1 度 -> 任意距离都偏 %.1f px"
          % (intr["fx"] * np.tan(np.deg2rad(1.0))))

    # ---------------------------------------------------------- 汇总
    print("\n" + "=" * 62)
    if FAILED:
        print("结果: FAIL %d 项 -> %s" % (len(FAILED), ", ".join(FAILED)))
        print("=" * 62)
        return 1
    print("结果: 全部通过")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
