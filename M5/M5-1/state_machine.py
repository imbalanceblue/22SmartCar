"""M5-1 任务状态机核心:节点管执行,状态机管调度,TaskContext 管传数据。"""
import json
import sys
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field

# ---------- 状态常量 ----------
IDLE, RUNNING, RETRYING = "IDLE", "RUNNING", "RETRYING"
SUCCESS, FAILED, SKIPPED, TIMEOUT = "SUCCESS", "FAILED", "SKIPPED", "TIMEOUT"

_COLORS = {"INFO": "\033[95m", "RUNNING": "\033[96m", "RETRYING": "\033[94m",
           "SUCCESS": "\033[92m", "FAILED": "\033[91m", "TIMEOUT": "\033[93m",
           "SKIPPED": "\033[90m"}
_RESET = "\033[0m"


def log(tag, msg):
    """彩色日志;非终端(重定向/CI)时退化为纯文本。"""
    line = f"[{tag:>8}] {msg}"
    print(f"{_COLORS.get(tag, '')}{line}{_RESET}" if sys.stdout.isatty() else line)


@dataclass
class TaskContext:
    """任务上下文:一次任务全程共享的黑板。"""
    mission_id: str = "mission"
    deadline: float | None = None        # 截止时刻(time.monotonic 基准)
    shared: dict = field(default_factory=dict)      # 共享数据字典
    retry_budget: int = 10               # 全局重试预算
    fallback_used: dict = field(default_factory=dict)  # 节点名 -> 降级目标
    metadata: dict = field(default_factory=dict)


class TaskNode(ABC):
    """节点基类:子类只实现 execute();重试/超时/降级归状态机(分权)。"""

    def __init__(self, name, *, timeout=None, retry=0, retry_interval=0.5,
                 fallback=None, fail_times=0, duration=0.0):
        self.name, self.timeout = name, timeout         # 单次执行超时,None=不限
        self.retry, self.retry_interval = retry, retry_interval
        self.fallback = fallback        # 降级目标节点名
        self.fail_times, self.duration = fail_times, duration   # mock 专用
        self.attempts = 0               # 已执行次数(含重试)

    @abstractmethod
    def execute(self, ctx: TaskContext) -> str:
        """执行一次。只返回 SUCCESS / FAILED,超时由框架判定。"""

    def run_once(self, ctx: TaskContext) -> str:
        """mock 公共逻辑:计时 + 前 N 次故意失败。"""
        self.attempts += 1
        time.sleep(self.duration)
        return FAILED if self.attempts <= self.fail_times else SUCCESS

    def run_in_thread(self, ctx: TaskContext) -> str:
        """子线程执行并强制超时(超时后无法强杀,只能不再等它)。"""
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            return pool.submit(self.execute, ctx).result(timeout=self.timeout)
        except FutureTimeout:
            return TIMEOUT
        finally:
            pool.shutdown(wait=False)


class Parallel(TaskNode):
    """并发分支:全部成功才算成功(wait-all,不半途取消其余分支)。
    超时分层:分支归 run_in_thread,整体归 StateMachine 外层,本层不重复设防。"""

    def __init__(self, name, branches, **kwargs):
        super().__init__(name, **kwargs)
        self.branches = branches        # list[TaskNode]

    def execute(self, ctx: TaskContext) -> str:
        results = {}
        with ThreadPoolExecutor(max_workers=len(self.branches)) as pool:
            futures = {b.name: pool.submit(b.run_in_thread, ctx)
                       for b in self.branches}
            for name, future in futures.items():
                results[name] = future.result()
        for name, status in results.items():
            log("INFO", f"  [{self.name}] 分支 {name} -> {status}")
        return SUCCESS if all(v == SUCCESS for v in results.values()) else FAILED


class StateMachine:
    """调度器:按顺序跑节点,负责重试/超时/降级/连坐。"""

    def __init__(self, nodes, mission_id="mission", deadline=None):
        self.nodes = nodes
        self.ctx = TaskContext(mission_id=mission_id, deadline=deadline)
        self.records = {}               # 节点名 -> 执行记录
        self.final = SUCCESS            # 整个任务流程的最终状态
        self._executed_via_fallback = set()   # 已随降级执行过的节点名
        self._validate()

    def _validate(self):
        """名字唯一 + fallback 目标必须存在(配置错误在启动时就报)。"""
        names = [n.name for n in self.nodes]
        if len(names) != len(set(names)):
            raise ValueError(f"节点名重复: {names}")
        for node in self.nodes:
            if node.fallback and node.fallback not in names:
                raise ValueError(f"{node.name} 的 fallback 指向不存在的节点: "
                                 f"{node.fallback}")

    def _by_name(self, name):
        for node in self.nodes:
            if node.name == name:
                return node
        raise ValueError(f"未找到节点: {name}")

    def _record(self, node, **kwargs):
        rec = self.records.setdefault(node.name, {
            "type": type(node).__name__, "status": None, "attempts": 0,
            "retries": 0, "duration": 0.0, "fallback": None})
        rec.update(kwargs)

    def _execute_once(self, node):
        start = time.monotonic()
        status = node.run_in_thread(self.ctx)
        self._record(node, duration=round(time.monotonic() - start, 3))
        return status

    def _run_with_retry(self, node):
        """带重试的执行。超时不重试(直接交由降级),失败按次数重试。"""
        retries = 0                     # 本轮真实发生的重试次数
        for attempt in range(1, node.retry + 2):
            self._record(node, status=RUNNING)
            log("RUNNING", f"{node.name} 第 {attempt} 次尝试")
            status = self._execute_once(node)
            self._record(node, status=status, attempts=node.attempts)
            if status == SUCCESS:
                return SUCCESS
            if status == TIMEOUT:
                return TIMEOUT          # 超时 -> 不重试,交给降级
            if attempt <= node.retry:
                if self.ctx.retry_budget <= 0:
                    log("INFO", f"全局重试预算耗尽,{node.name} 判定失败")
                    return FAILED
                self.ctx.retry_budget -= 1
                retries += 1
                self._record(node, status=RETRYING, retries=retries)
                log("RETRYING", f"{node.name} 将在 {node.retry_interval}s 后重试")
                time.sleep(node.retry_interval)
        return FAILED

    def run(self):
        log("INFO", f"任务 {self.ctx.mission_id} 开始,共 {len(self.nodes)} 个节点")
        final = SUCCESS
        for node in self.nodes:
            if final != SUCCESS:
                self._record(node, status=SKIPPED)
                log("SKIPPED", f"{node.name} 因上游失败被跳过")
                continue
            if node.name in self._executed_via_fallback:
                self._record(node, status=SUCCESS)
                log("INFO", f"{node.name} 已随降级执行过,不重复执行")
                continue
            if self.ctx.deadline and time.monotonic() > self.ctx.deadline:
                log("TIMEOUT", f"已超全局截止时间,{node.name} 及后续跳过")
                final = FAILED
                continue
            status = self._run_with_retry(node)
            if status == SUCCESS:
                log("SUCCESS", f"{node.name} 完成")
                continue
            if node.fallback:           # 降级:跑指定节点,成功则继续主流程
                log("INFO", f"{node.name} 失败({status}),降级到 {node.fallback}")
                fb_status = self._run_with_retry(self._by_name(node.fallback))
                if fb_status == SUCCESS:
                    self.ctx.fallback_used[node.name] = node.fallback
                    self._executed_via_fallback.add(node.fallback)
                    self._record(node, status=FAILED, fallback=node.fallback)
                    log("SUCCESS", f"降级 {node.fallback} 成功,继续主流程")
                    continue
            self._record(node, status=FAILED)
            log("FAILED", f"{node.name} 最终失败")
            final = FAILED
        log("INFO" if final == SUCCESS else "FAILED", f"任务结束: {final}")
        self.final = final
        return final

    def report(self):
        return {"mission_id": self.ctx.mission_id, "final": self.final,
                "tasks": self.records, "fallback_used": self.ctx.fallback_used,
                "retry_budget_left": self.ctx.retry_budget,
                "final_context": self.ctx.shared}

    def save_report(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.report(), f, ensure_ascii=False, indent=2)
        log("INFO", f"报告已写入 {path}")
