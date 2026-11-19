"""M5-3 感知看门狗:相机断了多久 -> 判定视觉降级 -> 通知降级 + 语音。

为什么要做"持续 N 秒"而不是"一断就报":
单帧丢失/瞬时抖动是常态,误报会让状态机频繁走 fallback,反而更慢。
用时间去抖(debounce)是工程上最便宜也最稳的做法。
"""


class Watchdog:
    def __init__(self, degrade_after=1.0):
        self.degrade_after = float(degrade_after)
        self.camera_ok = True
        self._bad_since = None

    def feed(self, camera_bad, now):
        """喂一次"相机是否异常";返回事件(None 表示无状态变化)。"""
        if camera_bad:
            if self._bad_since is None:
                self._bad_since = now
            if self.camera_ok and (now - self._bad_since) >= self.degrade_after:
                self.camera_ok = False
                return {"event": "DEGRADED",
                        "text": "视觉降级，切纯激光导航",
                        "after_s": round(now - self._bad_since, 2)}
        else:
            self._bad_since = None
            if not self.camera_ok:
                self.camera_ok = True
                return {"event": "RECOVERED", "text": "视觉恢复"}
        return None

    def state(self):
        return {"camera_ok": self.camera_ok,
                "degrade_after": self.degrade_after}
