"""DSL 加载 + type -> 节点类 的注册表 + 节点工厂。

新增节点只需在 REGISTRY 里加一行,不用改调度内核 —— 这是"改 YAML 不改代码"
能成立的前提。

并发分支支持两种写法,都进同一个 Parallel 类:
    - name: x
      type: Parallel
      branches: [...]
    - name: x
      parallel:
        fail_policy: any_fail
        branches: [...]
"""
import json

from .core import Parallel
from .nodes import (DetectTarget, ExecuteGrasp, NavigateTo, SpeakTTS,
                    WaitForTrigger)

REGISTRY = {
    "WaitForTrigger": WaitForTrigger,
    "NavigateTo": NavigateTo,
    "DetectTarget": DetectTarget,
    "ExecuteGrasp": ExecuteGrasp,
    "SpeakTTS": SpeakTTS,
    "Parallel": Parallel,
}

# 框架参数由 TaskNode 基类消化,其余字段透传给具体节点
BASE_PARAMS = {"timeout", "retry", "retry_interval", "fallback", "on_fail",
               "fail_times", "duration", "branches", "fail_policy"}


def load_config(path):
    """按后缀解析 YAML/JSON,并做基础校验。返回 (tasks, mission_id, deadline)。"""
    with open(path, "r", encoding="utf-8") as f:
        suffix = path.rsplit(".", 1)[-1].lower()
        if suffix == "yaml":
            import yaml              # 懒加载:JSON 配置无需 pyyaml
            try:
                cfg = yaml.safe_load(f)
            except yaml.YAMLError as e:
                raise ValueError(f"YAML 解析失败: {e}")
        elif suffix == "json":
            cfg = json.load(f)
        else:
            raise ValueError(f"不支持的配置格式: {path}(只支持 .yaml/.json)")
    if not isinstance(cfg, dict) or not cfg.get("tasks"):
        raise ValueError("配置缺少 tasks 列表")
    if not isinstance(cfg["tasks"], list):
        raise ValueError("tasks 必须是列表")
    return cfg["tasks"], cfg.get("mission_id", "mission"), cfg.get("deadline")


def build_node(spec):
    """把一个任务字典实例化成 TaskNode 对象(Parallel 递归处理分支)。"""
    if not isinstance(spec, dict):
        raise ValueError(f"任务必须是字典,收到 {type(spec).__name__}")
    if "name" not in spec:
        raise ValueError("任务缺少 name 字段")
    base = {k: spec[k] for k in BASE_PARAMS if k in spec}
    base.pop("fail_policy", None)
    base.pop("branches", None)          # 分支单独构造,避免和位置参数冲突

    if "parallel" in spec:              # 写法二:parallel 子键(官方 README 示例)
        cfg = spec["parallel"] or {}
        branches = cfg.get("branches")
        if not branches or not isinstance(branches, list):
            raise ValueError(f"Parallel 节点 {spec['name']} 缺少 branches 列表")
        return Parallel(spec["name"], [build_node(b) for b in branches],
                        fail_policy=cfg.get("fail_policy", "any_fail"), **base)

    t = spec.get("type")
    if t not in REGISTRY:
        raise ValueError(f"未知节点类型: {t}(可选: {sorted(REGISTRY)})")
    cls = REGISTRY[t]
    extra = {k: v for k, v in spec.items()
             if k not in BASE_PARAMS and k not in ("name", "type", "parallel")}
    if cls is Parallel:                 # 写法一:type: Parallel + branches
        branches = spec.get("branches")
        if not branches or not isinstance(branches, list):
            raise ValueError(f"Parallel 节点 {spec['name']} 缺少 branches 列表")
        return Parallel(spec["name"], [build_node(b) for b in branches],
                        fail_policy=spec.get("fail_policy", "any_fail"), **base)
    return cls(spec["name"], **base, **extra)
