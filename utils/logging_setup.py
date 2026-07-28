# -*- coding: utf-8 -*-
"""
集中式日志落盘配置。

设计目标：
1. 幂等 —— 同一进程内多次调用 setup_logging() 不会重复添加 handler，
   也不会清掉 Streamlit / 第三方库已注册的 handler。
2. 不影响原有行为 —— 不修改任何已有 logger / 不替换任何已有的 handler；
   仅在 root logger 上挂一个按天轮转的 FileHandler，让既有的
   `logger = logging.getLogger(__name__)` 自动通过根 Logger 落盘。
3. 输出位置 —— 项目根目录下 ./logs/aiagents-YYYY-MM-DD.log，保留 14 天。

调用方式（在入口脚本顶部调用一次即可）：
    from utils.logging_setup import setup_logging
    setup_logging()

历史 logger.info / logger.warning / logger.error 调用无需任何修改即可落盘。
"""
from __future__ import annotations

import logging
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Optional, Union

# 项目根目录 = utils/ 的上一级
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG_DIR = _PROJECT_ROOT / "logs"
_HANDLER_NAME = "aiagents_file_log"
_DEFAULT_LEVEL = logging.INFO
_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_RETENTION_DAYS = 14


def _build_file_handler(log_file: Path) -> TimedRotatingFileHandler:
    """构造一个按天轮转、保留 14 天的 FileHandler。"""
    handler = TimedRotatingFileHandler(
        filename=str(log_file),
        when="midnight",
        interval=1,
        backupCount=_RETENTION_DAYS,
        encoding="utf-8",
        utc=False,
    )
    # 文件名后缀按本地日期滚动
    handler.suffix = "%Y-%m-%d"
    handler.setLevel(_DEFAULT_LEVEL)
    handler.setFormatter(logging.Formatter(fmt=_LOG_FORMAT, datefmt=_DATE_FORMAT))
    handler.set_name(_HANDLER_NAME)
    return handler


def setup_logging(
    log_dir: Optional[Union[str, Path]] = None,
    level: int = _DEFAULT_LEVEL,
) -> Path:
    """
    配置 root logger，挂载按天轮转的文件 handler。

    参数:
        log_dir: 自定义日志目录；None 时使用项目根下的 ./logs/。
        level:   root logger 的最低记录级别，默认 INFO。

    返回:
        实际写入日志文件的目录（Path 对象）。
    """
    resolved_dir = Path(log_dir) if log_dir else DEFAULT_LOG_DIR
    resolved_dir.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # 幂等：已有同名 handler 则不再挂载
    for existing in root_logger.handlers:
        if existing.get_name() == _HANDLER_NAME:
            return resolved_dir

    today = datetime.now().strftime("%Y-%m-%d")
    log_file = resolved_dir / f"aiagents-{today}.log"
    handler = _build_file_handler(log_file)
    root_logger.addHandler(handler)

    # 用项目专用 logger 写一条启动行，便于排查"日志文件是不是被覆盖/轮转了"
    startup_logger = logging.getLogger("utils.logging_setup")
    startup_logger.info("日志系统已就绪 → %s", log_file)

    return resolved_dir


if __name__ == "__main__":
    # 独立运行本文件可做冒烟测试：
    #   python -m utils.logging_setup
    out = setup_logging()
    logging.getLogger("smoke_test").info("hello from logging_setup smoke test")
    print(f"[OK] 日志目录: {out}")
