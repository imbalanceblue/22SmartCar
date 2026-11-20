"""分层急停:一条零速度不够,四层都要兜住。

题面点名的最容易翻车处:只发一条 /cmd_vel 零速度就完事。
底盘可能因为控制器超时恢复、或另一路指令源覆盖而继续走。

四层职责:
  ① 指令层  发零速度 /cmd_vel          —— 常规停车
  ② 话题层  发 /emergency_stop          —— 让底盘自己进入急停
  ③ 控制器层 置使能位为 0               —— 防止控制器超时后恢复
  ④ 记录层  黑匣子 + 审计日志           —— 事后追责/复盘

本模块只负责「按顺序触发这四层」,不持有状态;
解除急停必须人工复位(sim.emergency_stop 置位后不会自动清)。
"""


def layered_estop(sim, bridge, car, referee, audit, announce,
                  source="operator", reason="manual"):
    """执行四层急停,返回零速度指令(供界面回显)。

    参数都是调用方的组件,本函数不做任何 import,
    这样它既能被终端复用,也能被测试单独调用。
    """
    # ① 仿真侧锁死:estopped=True 后 tick 不再推进运动
    sim.emergency_stop()

    # ② 状态机原子暂停:只在节点边界生效,不切断正在跑的节点
    bridge.pause()

    # ③ 控制器层:car 侧发零速度、置使能位 0、写黑匣子
    cmd = car.on_emergency_stop(source=source, reason=reason)

    # ④ 记录层:裁判端置 FAULT + 审计留痕(哈希链)
    referee.set_fault("紧急停车")
    audit.append("EMERGENCY_STOP",
                 {"zero_velocity": True,
                  "cmd_vel": [cmd["linear"], cmd["angular"]],
                  "reason": reason},
                 source=source)

    announce("紧急停车，已发零速度指令")
    return cmd
