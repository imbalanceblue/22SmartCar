"""离线自检:不需要 ROS 环境,直接验证 M1-1 的数学逻辑。

用法:
    cd ~/22SmartCar/M1/M1-1
    python3 tools/sim_check.py

它检查四件事:
    1. 传感器公式在 10Hz 下的距离范围,是否真的会穿过 0.3m 阈值;
    2. 低通滤波是否真的"平滑"(alpha 越小抖动越小);
    3. 无效状态(1/2)是否被正确判定为需要丢弃;
    4. 报警触发次数是否被冷却时间约束,不会刷屏。
"""

import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "my_pkg"))

from my_pkg.core import is_invalid, low_pass, sim_distance  # noqa: E402

RATE, PERIOD = 10.0, 6.0
CENTER, AMP, NOISE = 0.55, 0.35, 0.05
THRESHOLD, COOLDOWN = 0.30, 2.0


def main() -> int:
    fails = []

    # ---- 1. 传感器:60 秒 @10Hz(与 sensor_node 同款:种子固定 + 高斯噪声) ----
    rng = random.Random(0)
    series = [sim_distance(i, RATE, PERIOD, CENTER, AMP,
                           noise=rng.gauss(0.0, NOISE))
              for i in range(1, int(RATE * 60) + 1)]
    lo, hi = min(series), max(series)
    crossings = sum(1 for v in series if v < THRESHOLD)
    print(f"[1] 距离范围 {lo:.3f} ~ {hi:.3f}m,低于阈值样本 {crossings} 个")
    if not (lo < THRESHOLD and hi > 0.8):
        fails.append("距离没有覆盖报警阈值两侧,演示时不会触发报警")
    if crossings == 0:
        fails.append("60 秒内从未低于 0.3m")

    # ---- 2. 滤波:alpha 越小越平滑 ----
    def jitter(alpha):
        prev, total = None, 0.0
        for raw in series:
            y = low_pass(prev, raw, alpha)
            if prev is not None:
                total += abs(y - prev)
            prev = y
        return total / (len(series) - 1)

    raw_jitter = jitter(1.0)      # alpha=1 等于不滤波
    soft, hard = jitter(0.1), jitter(0.9)
    print(f"[2] 抖动 原始={raw_jitter:.4f}  alpha=0.9 时={hard:.4f}  "
          f"alpha=0.1 时={soft:.4f}")
    if not (soft < hard < raw_jitter):
        fails.append("alpha 未起到'越小越平滑'的作用")

    # ---- 3. 无效状态判定 ----
    dropped = [s for s in (0, 1, 2) if is_invalid(s)]
    print(f"[3] is_invalid(1)={is_invalid(1)}, is_invalid(2)={is_invalid(2)}, "
          f"is_invalid(0)={is_invalid(0)}")
    if dropped != [1, 2]:
        fails.append("status 判定错误")

    # 无效帧不影响滤波状态
    before = low_pass(None, 0.42, 0.3)
    after = before if is_invalid(2) else low_pass(before, -1.0, 0.3)
    if after != before:
        fails.append("无效数据污染了滤波状态")
    print(f"[3] 无效帧后滤波值保持 {before:.3f} -> {after:.3f}")

    # ---- 4. 报警次数受冷却约束 ----
    alarms, last = 0, -1e9
    for i, v in enumerate(series):
        t = i / RATE
        if v < THRESHOLD and t - last >= COOLDOWN:
            alarms += 1
            last = t
    expect_max = int(RATE * 60 / COOLDOWN) + 1
    print(f"[4] 60 秒内报警 {alarms} 次(冷却 {COOLDOWN}s,上限 {expect_max})")
    if alarms == 0 or alarms > expect_max:
        fails.append("报警次数异常")

    print()
    if fails:
        print("FAIL: " + "; ".join(fails))
        return 1
    print("PASS: 4 项逻辑自检全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
