"""小车端唯一新增的订阅者:/emergency_stop -> 零速度 + 黑匣子。

题面要求"终端跑在主机上,小车端只增加 /emergency_stop 订阅者,零额外进程"。
本文件就是这个订阅者的 mock 版本:
- 收到急停 -> 立刻发布 /cmd_vel = (0, 0) 并落黑匣子;
- 不参与调度、不做决策,只执行 —— 与题面的职责划分一致。

单独运行可查看黑匣子:python3 car_side.py --tail 20
"""
import argparse
import json
import os
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))


class CarSideStub:
    """模拟小车端的急停订阅者(零速度 + 黑匣子记录)。"""

    def __init__(self, blackbox_path=None):
        self.blackbox_path = blackbox_path or os.path.join(
            _HERE, "blackbox.jsonl")
        self._lock = threading.Lock()
        self.estop_count = 0

    def on_emergency_stop(self, source="operator", reason="manual"):
        """收到 /emergency_stop:发布零速度,写黑匣子,返回指令内容。"""
        with self._lock:
            self.estop_count += 1
            cmd = {
                "ts": int(time.time() * 1000),
                "topic": "/cmd_vel",
                "linear": 0.0,
                "angular": 0.0,
                "reason": reason,
                "source": source,
                "seq": self.estop_count,
            }
            with open(self.blackbox_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(cmd, ensure_ascii=False) + "\n")
            return cmd

    def blackbox_tail(self, n=10):
        if not os.path.exists(self.blackbox_path):
            return []
        rows = []
        with open(self.blackbox_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        continue
        return rows[-n:]


def main():
    parser = argparse.ArgumentParser(description="小车端急停订阅者(mock)")
    parser.add_argument("--tail", type=int, default=0,
                        help="打印最近 N 条黑匣子记录")
    args = parser.parse_args()
    stub = CarSideStub()
    if args.tail:
        for row in stub.blackbox_tail(args.tail):
            print(json.dumps(row, ensure_ascii=False))
    else:
        print("[car_side] 已就绪:订阅 /emergency_stop(由终端触发)")


if __name__ == "__main__":
    main()
