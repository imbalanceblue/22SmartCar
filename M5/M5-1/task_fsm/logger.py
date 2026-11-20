"""彩色日志:状态转移/重试/降级/超时各用不同颜色。

非终端(重定向到文件或 CI)时自动退化为纯文本,避免输出 ANSI 转义码。
"""
import sys

_COLORS = {
    "INFO": "\033[95m",      # 品红:一般信息
    "RUNNING": "\033[96m",   # 青:正在执行
    "RETRYING": "\033[94m",  # 蓝:重试中
    "SUCCESS": "\033[92m",   # 绿:成功
    "FAILED": "\033[91m",    # 红:失败
    "TIMEOUT": "\033[93m",   # 黄:超时
    "SKIPPED": "\033[90m",   # 灰:跳过
}
_RESET = "\033[0m"


def log(tag, msg):
    """打印一行带标签的彩色日志。tag 决定颜色。"""
    line = f"[{tag:>8}] {msg}"
    if sys.stdout.isatty():
        print(f"{_COLORS.get(tag, '')}{line}{_RESET}")
    else:
        print(line)
