"""
日志工具 —— 把运行过程中的信息记到文件里，出了事能翻记录。

为什么要日志？
    改造前，程序出错只会 print 到黑窗口。窗口一关，线索全没了；
    而且原来的代码会把一大段英文报错（含本机路径）直接显示在网页界面上，
    对使用者毫无帮助。
    现在改成：界面上只说人话（"第 3 句配音失败"），详细的技术细节写进日志文件。

日志文件位置：outputs/logs/app_年月日.log
"""

import logging
import sys
from logging.handlers import RotatingFileHandler

import config

# 只初始化一次，避免重复导入时日志被打印两遍
_initialized = False


def _make_console_utf8() -> None:
    """
    让控制台能正常显示中文和符号。

    Windows 的命令行窗口默认用 GBK 编码，遇到 "✅" 这种符号会直接抛异常
    （UnicodeEncodeError）。这里把输出流强制改成 UTF-8，并用 errors="replace"
    兜底——万一还有个别打不出来的字符，显示成问号总比让程序崩掉强。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            # 极少数环境下输出流不支持重新配置，忽略即可，不影响主功能
            pass


def setup_logging(level: str = "INFO") -> None:
    """配置日志：同时写到文件（详细）和控制台（简洁）。程序启动时调用一次。"""
    global _initialized
    if _initialized:
        return

    _make_console_utf8()

    # 从环境变量读日志级别；设成 DEBUG 才会看到那些 [DEBUG] 细节
    log_level = getattr(logging, level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(log_level)

    # 统一的格式：时间 | 级别 | 哪个模块 | 说了什么
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)-20s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # —— 输出到文件 ——
    # RotatingFileHandler = 写到 2MB 就自动开新文件，最多保留 5 个备份。
    # 这样日志不会越积越大把硬盘塞满。encoding 必须写 utf-8，否则中文是乱码。
    file_handler = RotatingFileHandler(
        config.LOG_DIR / "app.log",
        maxBytes=2 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    # —— 输出到控制台 ——
    # 控制台只看 INFO 以上的信息，避免刷屏（答辩演示时界面清爽）。
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    # 这几个第三方库话太多，压掉它们的输出，免得淹没我们自己的日志
    for noisy in ("httpx", "httpcore", "urllib3", "openai", "gradio", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _initialized = True


def get_logger(name: str) -> logging.Logger:
    """取一个日志记录器。用法：logger = get_logger(__name__)，然后 logger.info("...")。"""
    return logging.getLogger(name)
