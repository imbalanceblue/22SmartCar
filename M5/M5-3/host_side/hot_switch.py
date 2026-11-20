"""M5-3 参数/模型/场景热切换。

考核点:切换必须"热"——不重启感知/导航节点,切换后立刻生效并留痕。
真实环境走 ros2 param load / ros2 param set;本实现把"真实命令"
一并生成并写进 switch_log.csv 的 note 列,答辩时可对照演示。

switch_log.csv 列:ts,type,old_value,new_value,operator,exp_name,note
"""
import csv
import os
import threading
from collections import deque
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))

SCENES = ["scene_race.yaml", "scene_nav_speed_0.9.yaml",
          "scene_lidar_only.yaml", "scene_night.yaml"]

MODELS = {
    "yolov8n_fp32": {"fps": 12.0, "size_mb": 12.8, "map": 0.850},
    "yolov8s_fp16": {"fps": 18.0, "size_mb": 22.4, "map": 0.872},
    "yolov8n_int8": {"fps": 32.0, "size_mb": 6.2, "map": 0.832},
}

PARAMS = {
    "nav_speed": {"min": 0.2, "max": 1.5, "step": 0.1, "value": 0.8,
                  "unit": "m/s", "node": "/nav_controller",
                  "ros_name": "max_linear_speed"},
    "perception_hz": {"min": 5.0, "max": 30.0, "step": 1.0, "value": 10.0,
                      "unit": "Hz", "node": "/perception",
                      "ros_name": "publish_rate"},
    "conf_threshold": {"min": 0.3, "max": 0.95, "step": 0.05, "value": 0.7,
                       "unit": "", "node": "/perception",
                       "ros_name": "conf_threshold"},
}

CSV_HEADER = ["ts", "type", "old_value", "new_value", "operator",
              "exp_name", "note"]


class HotSwapManager:
    """持有一份"在线参数",apply() 模拟热加载并落盘日志。

    pid 全程不变 + reloads 递增 = "节点没有重启,只重载了参数/模型"的证明。
    """

    def __init__(self, log_path=None, node_pid=None):
        self.log_path = log_path or os.path.join(_HERE, "switch_log.csv")
        self.pid = node_pid or os.getpid()
        self.reloads = 0
        self.scene = SCENES[0]
        self.model = "yolov8n_fp32"
        self.params = {k: dict(v) for k, v in PARAMS.items()}
        self._lock = threading.Lock()
        self._ensure_header()

    def _ensure_header(self):
        if os.path.exists(self.log_path):
            return
        with open(self.log_path, "w", encoding="utf-8", newline="") as f:
            csv.writer(f).writerow(CSV_HEADER)

    def _record(self, kind, old, new, operator, exp_name, note):
        row = {
            "ts": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "type": kind,
            "old_value": old,
            "new_value": new,
            "operator": operator,
            "exp_name": exp_name or "",
            "note": note,
        }
        with open(self.log_path, "a", encoding="utf-8", newline="") as f:
            csv.DictWriter(f, fieldnames=CSV_HEADER).writerow(row)
        return row

    def apply(self, kind, name, value, operator="operator", exp_name=""):
        """kind: scene | model | param。返回切换结果(含真实 ros2 命令)。"""
        with self._lock:
            if kind == "scene":
                if value not in SCENES:
                    raise ValueError(f"未知场景: {value}(可选: {SCENES})")
                old = self.scene
                self.scene = value
                cmd = f"ros2 param load /perception {value}"
            elif kind == "model":
                if value not in MODELS:
                    raise ValueError(f"未知模型: {value}"
                                     f"(可选: {sorted(MODELS)})")
                old = self.model
                self.model = value
                cmd = f"ros2 param set /perception model_path models/{value}.onnx"
            elif kind == "param":
                if name not in self.params:
                    raise ValueError(f"未知参数: {name}"
                                     f"(可选: {sorted(self.params)})")
                spec = self.params[name]
                try:
                    val = float(value)
                except (TypeError, ValueError):
                    raise ValueError(f"参数 {name} 需要数字,收到: {value!r}")
                if not (spec["min"] <= val <= spec["max"]):
                    raise ValueError(f"{name} 超出范围 "
                                     f"[{spec['min']}, {spec['max']}]: {val}")
                old = spec["value"]
                spec["value"] = val
                cmd = (f"ros2 param set {spec['node']} {spec['ros_name']} {val}")
                value = val
            else:
                raise ValueError(f"未知切换类型: {kind}")

            self.reloads += 1         # 重载次数 +1,进程 pid 不变
            row = self._record(kind, old, value, operator, exp_name, cmd)
            return {"kind": kind, "name": name, "old": old, "new": value,
                    "ros2_cmd": cmd, "reloads": self.reloads,
                    "pid": self.pid, "log": row}

    def state(self):
        with self._lock:
            model = MODELS[self.model]
            return {
                "scene": self.scene,
                "scenes": list(SCENES),
                "model": self.model,
                "models": {k: dict(v) for k, v in MODELS.items()},
                "fps": model["fps"],
                "map": model["map"],
                "size_mb": model["size_mb"],
                "params": {k: dict(v) for k, v in self.params.items()},
                "pid": self.pid,
                "reloads": self.reloads,
                "switch_log": self.log_tail(8),
            }

    def log_tail(self, n=10):
        if not os.path.exists(self.log_path):
            return []
        rows = deque(maxlen=n)
        with open(self.log_path, "r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                rows.append(row)
        return list(rows)
