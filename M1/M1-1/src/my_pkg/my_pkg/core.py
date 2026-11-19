"""纯计算逻辑(不 import rclpy),便于离线单测与答辩演示。

抽出来的理由:传感器公式、滤波公式属于"数学",与 ROS 通信无关;
解耦后可以在没装 ROS 的机器上直接跑 tools/sim_check.py 验证。
"""

import math


def clamp_alpha(alpha: float) -> float:
    """把滤波系数夹到 [0,1]:0=完全不动,1=完全跟随原始值。"""
    return min(max(float(alpha), 0.0), 1.0)


def low_pass(prev, raw: float, alpha: float) -> float:
    """一阶低通滤波 y[n] = alpha*x[n] + (1-alpha)*y[n-1]。

    prev 为 None 时直接用 raw 作为初值(避免从 0 爬升造成假报警)。
    """
    alpha = clamp_alpha(alpha)
    if prev is None:
        return float(raw)
    return alpha * float(raw) + (1.0 - alpha) * float(prev)


def sim_distance(seq: int, rate: float, period: float,
                 center: float, amplitude: float, noise: float = 0.0) -> float:
    """模拟超声波距离:正弦扫过阈值 + 外部给定的噪声样本。

    center=0.55, amplitude=0.35 时距离在 0.20~0.90m 之间往复,
    保证会穿过 0.3m 报警阈值,演示时不用等太久。
    噪声由调用方(节点)用 random.Random(seed) 生成,保证可复现。
    """
    t = seq / max(float(rate), 0.1)
    base = float(center) + float(amplitude) * math.sin(
        2 * math.pi * t / max(float(period), 1e-6))
    return max(base + float(noise), 0.0)


def is_invalid(status: int) -> bool:
    """status: 0=正常, 1=超出量程, 2=传感器错误。非 0 一律不参与滤波。"""
    return int(status) != 0
