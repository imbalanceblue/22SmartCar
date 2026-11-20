"""调度内核:状态 + TaskNode 基类 + Parallel + StateMachine(<200 行)。

分权:节点只管 execute(),重试/超时/降级归状态机;报告在 run_task.py。
"""
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

from .context import TaskContext
from .logger import log

IDLE, RUNNING, RETRYING = "IDLE", "RUNNING", "RETRYING"
SUCCESS, FAILED, SKIPPED = "SUCCESS", "FAILED", "SKIPPED"
TIMEOUT, ABORTED = "TIMEOUT", "ABORTED"


class TaskNode(ABC):
    """节点基类。契约:setup / execute / cancel / teardown。

    execute 只返回 SUCCESS/FAILED/SKIPPED;重试/超时归状态机。"""

    def __init__(self, name, *, timeout=None, retry=0, retry_interval=0.5,
                 fallback=None, on_fail="abort", fail_times=0, duration=0.0):
        self.name, self.timeout = name, timeout      # timeout=None 不限时
        self.retry, self.retry_interval = retry, retry_interval
        self.fallback = fallback                     # 降级目标节点名
        self.on_fail = on_fail                       # abort=整单失败 / skip=标 SKIPPED
        self.fail_times, self.duration = fail_times, duration
        self.attempts = 0
        self._cancelled = False

    def setup(self):
        """资源准备(真实节点在这里建 Action 客户端)。"""

    @abstractmethod
    def execute(self, ctx: TaskContext) -> str:
        """执行一次,只返回 SUCCESS / FAILED / SKIPPED。"""

    def cancel(self):
        """协作式取消:强杀线程不现实,靠标志位让 execute 尽快返回。"""
        self._cancelled = True

    def teardown(self):
        """释放资源。"""

    def run_in_thread(self, ctx: TaskContext) -> str:
        """子线程执行并强制超时;超时时主动 cancel(),让节点知道该收尾。"""
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            return pool.submit(self.execute, ctx).result(timeout=self.timeout)
        except FutureTimeout:
            self.cancel()
            return TIMEOUT
        finally:
            pool.shutdown(wait=False)


class Parallel(TaskNode):
    """并发分支。any_fail=任一失败即收掉其余;all_must_succeed=等全部完成。

    超时分层:分支超时归 run_in_thread,整体超时归状态机外层,本层不设防。"""

    def __init__(self, name, branches, fail_policy="any_fail", **kwargs):
        super().__init__(name, **kwargs)
        self.branches, self.fail_policy = branches, fail_policy

    def execute(self, ctx: TaskContext) -> str:
        with ThreadPoolExecutor(max_workers=len(self.branches)) as pool:
            futures = {b.name: pool.submit(b.run_in_thread, ctx)
                       for b in self.branches}
            results = {}
            for name, f in futures.items():
                results[name] = f.result()
                if self.fail_policy == "any_fail" and results[name] != SUCCESS:
                    for other in self.branches:      # 任一失败:收掉其余分支
                        if other.name != name:
                            other.cancel()
        for name, status in results.items():
            log("INFO", f"  [{self.name}] 分支 {name} -> {status}")
        return SUCCESS if all(v == SUCCESS for v in results.values()) else FAILED


class StateMachine:
    """调度器:按顺序跑节点,负责重试/超时/降级/连坐。"""

    def __init__(self, nodes, mission_id="mission", deadline=None):
        self.nodes = nodes
        self.ctx = TaskContext(mission_id=mission_id, deadline=deadline)
        self.records = {}                  # 节点名 -> 执行记录
        self.result = None
        self._via_fallback = set()         # 已随降级执行过的节点
        self._aborted = False
        self._validate()

    def _validate(self):    # 名字唯一 + fallback 存在:配置错误启动时就报
        names = [n.name for n in self.nodes]
        if len(names) != len(set(names)):
            raise ValueError(f"节点名重复: {names}")
        for node in self.nodes:
            if node.fallback and node.fallback not in names:
                raise ValueError(
                    f"{node.name} 的 fallback 指向不存在的节点: {node.fallback}")

    def abort(self):
        """外部中断(急停 / 裁判 STOP):标记中止并取消所有节点。"""
        self._aborted = True
        for node in self.nodes:
            node.cancel()
        log("FAILED", "收到中止请求,正在停止任务")

    def _by_name(self, name):
        for node in self.nodes:
            if node.name == name:
                return node
        raise ValueError(f"未找到节点: {name}")

    def _record(self, node, **kwargs):
        rec = self.records.setdefault(node.name, {
            "type": type(node).__name__, "status": None, "attempts": 0,
            "retries": 0, "duration": 0.0})
        rec.update(kwargs)

    def _execute_once(self, node):
        start = time.monotonic()
        status = node.run_in_thread(self.ctx)
        self._record(node, duration=round(time.monotonic() - start, 3))
        return status

    def _run_with_retry(self, node):
        """带重试执行:失败按次数重试,超时不重试(直接交降级)。"""
        retries = 0
        for attempt in range(1, node.retry + 2):
            if self._aborted:
                return ABORTED
            self._record(node, status=RUNNING)
            log("RUNNING", f"{node.name} 第 {attempt} 次尝试")
            status = self._execute_once(node)
            self._record(node, status=status, attempts=node.attempts)
            if status == SUCCESS:
                return SUCCESS
            if status == TIMEOUT:
                return TIMEOUT              # 超时不重试,交降级
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
            if self._aborted:
                final = ABORTED
                break
            if final != SUCCESS:
                self._record(node, status=SKIPPED)
                log("SKIPPED", f"{node.name} 因上游失败被跳过")
                continue
            if node.name in self._via_fallback:
                self._record(node, status=SUCCESS)
                log("INFO", f"{node.name} 已随降级执行过,不重复执行")
                continue
            if self.ctx.deadline and time.monotonic() > self.ctx.deadline:
                log("TIMEOUT", f"已超任务级截止时间,{node.name} 及后续跳过")
                self._record(node, status=TIMEOUT)
                final = TIMEOUT
                continue
            status = self._run_with_retry(node)
            if status == SUCCESS:
                log("SUCCESS", f"{node.name} 完成")
                continue
            if status == ABORTED:
                final = ABORTED
                break
            if node.fallback:               # 降级成功则继续主流程
                log("INFO", f"{node.name} 失败({status}),降级到 {node.fallback}")
                if self._run_with_retry(self._by_name(node.fallback)) == SUCCESS:
                    self.ctx.fallback_used[node.name] = node.fallback
                    self._via_fallback.add(node.fallback)
                    self._record(node, status=SKIPPED)
                    log("SUCCESS", f"降级 {node.fallback} 成功,继续主流程")
                    continue
            if node.on_fail == "skip":      # 失败但允许继续往下走
                self._record(node, status=SKIPPED)
                log("SKIPPED", f"{node.name} 失败,按 on_fail=skip 跳过")
                continue
            self._record(node, status=status if status == TIMEOUT else FAILED)
            log("FAILED", f"{node.name} 最终失败({status})")
            final = FAILED
        self.result = final
        log("INFO" if final == SUCCESS else "FAILED", f"任务结束: {final}")
        return final
