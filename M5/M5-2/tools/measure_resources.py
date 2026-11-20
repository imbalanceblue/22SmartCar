#!/usr/bin/env python3
"""量进程资源占用:CPU% 与 RSS 内存,给"持续 N 秒的平均值"。

为什么不用"看一眼任务管理器":题面要求可复现的采样口径,
瞬时值会被 import 阶段和 GC 抖动污染,所以取窗口内多次采样的均值。

用法:
    python tools/measure_resources.py --pid 12345 --seconds 60
    python tools/measure_resources.py --pid 12345 --seconds 60 --interval 0.5

无 psutil 时自动退化到 /proc/<pid>/stat(仅 Linux),两者口径一致:
- CPU%  : 进程累计 CPU 时间增量 / 墙钟增量 / 核数
- RSS   : 常驻内存当前值,取窗口内最大值与均值
"""
import argparse
import os
import time


def _read_proc(pid):
    """从 /proc 读 (累计 CPU 秒数, RSS 字节)。Linux 无 psutil 时的兜底。"""
    with open(f"/proc/{pid}/stat", "r", encoding="utf-8") as f:
        parts = f.read().split()
    ticks = int(parts[13]) + int(parts[14])            # utime + stime
    hz = os.sysconf("SC_CLK_TCK")
    rss_pages = int(parts[23])
    return ticks / hz, rss_pages * os.sysconf("SC_PAGE_SIZE")


def _probe(pid, proc_mod):
    """返回 (累计 CPU 秒, RSS 字节)。优先 psutil,失败退化 /proc。"""
    if proc_mod is not None:
        p = proc_mod.Process(pid)
        with p.oneshot():
            cpu = sum(p.cpu_times()[:2])               # user + system
            return cpu, p.memory_info().rss
    return _read_proc(pid)


def measure(pid, seconds=60.0, interval=1.0):
    """按固定间隔采样,返回均值与峰值。"""
    try:
        import psutil as proc_mod
    except ImportError:
        proc_mod = None
        print("[提示] 未装 psutil,改用 /proc 采样(结果口径相同)")

    cpu0, rss0 = _probe(pid, proc_mod)
    t0 = time.monotonic()
    samples = []
    while True:
        time.sleep(interval)
        elapsed = time.monotonic() - t0
        cpu_t, rss = _probe(pid, proc_mod)
        cpu_pct = (cpu_t - cpu0) / elapsed * 100.0 / (os.cpu_count() or 1)
        samples.append((cpu_pct, rss))
        if elapsed >= seconds:
            break

    cpus = [s[0] for s in samples]
    rsss = [s[1] for s in samples]
    return {
        "pid": pid,
        "window_s": round(seconds, 1),
        "samples": len(samples),
        "cpu_avg_pct": round(sum(cpus) / len(cpus), 2),
        "cpu_peak_pct": round(max(cpus), 2),
        "rss_avg_mb": round(sum(rsss) / len(rsss) / 1024 / 1024, 1),
        "rss_peak_mb": round(max(rsss) / 1024 / 1024, 1),
    }


def main():
    ap = argparse.ArgumentParser(description="测量进程 CPU/内存(窗口平均)")
    ap.add_argument("--pid", type=int, required=True, help="目标进程 PID")
    ap.add_argument("--seconds", type=float, default=60.0, help="采样窗口(默认 60s)")
    ap.add_argument("--interval", type=float, default=1.0, help="采样间隔(默认 1s)")
    args = ap.parse_args()

    try:
        r = measure(args.pid, args.seconds, args.interval)
    except FileNotFoundError:
        print(f"[错误] 进程 {args.pid} 不存在")
        return 1
    except PermissionError:
        print(f"[错误] 无权读取进程 {args.pid}(试试 sudo)")
        return 1

    print(f"进程 {r['pid']}  |  {r['window_s']}s 内 {r['samples']} 次采样")
    print(f"  CPU  平均 {r['cpu_avg_pct']}%   峰值 {r['cpu_peak_pct']}%")
    print(f"  内存 平均 {r['rss_avg_mb']}MB  峰值 {r['rss_peak_mb']}MB")
    print("  题面阈值:小车端 CPU<2% / 内存<50MB;主机端 CPU<5% / 内存<200MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
