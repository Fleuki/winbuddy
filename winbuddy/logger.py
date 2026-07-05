"""
Логирование winbuddy.

Два раздельных потока:
  1. APP_LOG   — обычная работа (что происходит, ошибки, шаги агента).
  2. AUDIT_LOG — ТОЛЬКО реальные write-действия (переместил/удалил/переименовал).
                 Это юридически-важный след: что агент сделал с файлами.
                 Пишется отдельно, чтобы его нельзя было потерять в шуме.

Аудит-лог — часть модели безопасности. Каждое опасное действие обязано
оставить в нём запись ДО выполнения.
"""

from __future__ import annotations

import logging
from datetime import datetime

from . import config


def _ensure_log_dir() -> None:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)


def get_logger(name: str = "winbuddy") -> logging.Logger:
    """Обычный логгер приложения. Пишет в консоль и в APP_LOG."""
    _ensure_log_dir()
    logger = logging.getLogger(name)
    if logger.handlers:  # уже настроен
        return logger

    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s", "%H:%M:%S")

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)

    file_handler = logging.FileHandler(config.APP_LOG, encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    return logger


def audit(action: str, detail: str) -> None:
    """
    Записать РЕАЛЬНОЕ write-действие в аудит-лог.

    Вызывается слоем безопасности непосредственно перед выполнением опасной
    операции. Формат намеренно простой и человекочитаемый.

    Пример:
        audit("MOVE", "d:\\dump\\a.txt -> d:\\dump\\images\\a.txt")
    """
    _ensure_log_dir()
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{stamp}  {action:<8}  {detail}\n"
    with open(config.AUDIT_LOG, "a", encoding="utf-8") as f:
        f.write(line)
