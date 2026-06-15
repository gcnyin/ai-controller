"""日志系统 —— 双输出 logger（控制台带颜色 + 文件纯文本）。"""

import sys
import logging
from pathlib import Path
from typing import Optional

LOG_FILE = "AI-CHANGELOG.md"
LOGGER_FILE = "ai-controller.log"

_logger: Optional[logging.Logger] = None


class ColoredFormatter(logging.Formatter):
    """带 ANSI 颜色的控制台日志格式化器。

    根据日志级别自动添加颜色：DEBUG=青色, INFO=绿色,
    WARNING=黄色, ERROR=红色, CRITICAL=粗体红色。
    文件输出使用无颜色的纯文本。

    注意：使用 copy() 创建 record 副本以避免 ANSI 颜色码
    泄漏到后续的 handler（如 FileHandler）。
    """
    COLORS = {
        logging.DEBUG: "\033[36m",          # CYAN
        logging.INFO: "\033[32m",           # GREEN
        logging.WARNING: "\033[33m",        # YELLOW
        logging.ERROR: "\033[31m",          # RED
        logging.CRITICAL: "\033[1m\033[31m", # BOLD RED
    }
    RESET = "\033[0m"

    def format(self, record):
        color = self.COLORS.get(record.levelno, "")
        if color:
            # 复制 record 避免颜色码泄漏到其他 handler
            record = logging.makeLogRecord(record.__dict__)
            record.levelname = f"{color}{record.levelname}{self.RESET}"
            record.msg = f"{color}{record.msg}{self.RESET}"
        return super().format(record)


def setup_logger(target_dir: str) -> logging.Logger:
    """配置双输出 logger：控制台（带颜色）+ 文件（纯文本）。

    控制台 handler：INFO 及以上级别，带 ANSI 颜色。
    文件 handler：DEBUG 及以上级别，无颜色，写入 ai-controller.log。

    Args:
        target_dir: 目标目录，日志文件将写入该目录下的 ai-controller.log
    Returns:
        配置好的 logger 实例
    """
    global _logger
    logger = logging.getLogger("ai-controller")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    # 控制台 handler — INFO 及以上，带颜色
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(ColoredFormatter("%(message)s"))
    logger.addHandler(ch)

    # 文件 handler — DEBUG 及以上，纯文本
    log_path = Path(target_dir) / LOGGER_FILE
    fh = logging.FileHandler(str(log_path), encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(fh)

    _logger = logger
    return logger


def get_logger() -> logging.Logger:
    """获取全局 logger（未初始化时返回一个基础 console logger）。"""
    global _logger
    if _logger is not None:
        return _logger
    # 回退：基础 console logger
    logger = logging.getLogger("ai-controller")
    if not logger.handlers:
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        logger.addHandler(ch)
    logger.setLevel(logging.INFO)
    return logger
