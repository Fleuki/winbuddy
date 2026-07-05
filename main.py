"""
Точка входа winbuddy.

Режимы:
    python main.py                    — интерактивный агент (нужен ANTHROPIC_API_KEY)
    python main.py --test-tools PATH  — прогнать инструменты по пути БЕЗ API (бесплатно)
    python main.py --self-check       — проверка скелета и слоя безопасности (Фаза 0)

Совет: перед первым запуском агента прогони --test-tools на реальной папке,
чтобы убедиться, что инструменты видят твой диск правильно. Это не тратит токены.
"""

from __future__ import annotations

import argparse
import json

# .env подхватываем автоматически, если есть python-dotenv
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from winbuddy import config
from winbuddy.logger import get_logger
from winbuddy import safety

log = get_logger()


def self_check() -> None:
    log.info("winbuddy self-check")
    log.info("Модель агента: %s", config.MODEL)
    log.info("dry-run по умолчанию: %s", config.DRY_RUN_DEFAULT)
    key = "задан" if config.ANTHROPIC_API_KEY else "НЕ задан"
    log.info("ANTHROPIC_API_KEY: %s", key)
    log.info("--- проверка слоя безопасности ---")
    for s in [r"c:\windows\system32", r"c:\program files\app",
              r"d:\dump\photo.jpg", r"c:\users\artemiy\downloads"]:
        log.info("  %-32s -> %s", s, safety.explain(s))
    log.info("--- ok ---")


def test_tools(path: str) -> None:
    """Прогнать все read-инструменты по пути без обращения к API."""
    from winbuddy.tools import registry
    log.info("Тест инструментов на пути: %s (без API)", path)
    calls = [
        ("get_system_info", {}),
        ("scan_directory", {"path": path}),
        ("find_large_files", {"path": path, "min_size_mb": 50}),
        ("find_junk_candidates", {"path": path}),
        ("propose_organization", {"path": path}),
    ]
    for name, args in calls:
        print(f"\n=== {name}({args}) ===")
        result = registry.dispatch(name, args)
        # красиво печатаем JSON
        print(json.dumps(json.loads(result), ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="winbuddy — ИИ-помощник для Windows")
    parser.add_argument("--self-check", action="store_true", help="проверка скелета")
    parser.add_argument("--test-tools", metavar="PATH", help="прогнать инструменты по пути без API")
    args = parser.parse_args()

    if args.self_check:
        self_check()
    elif args.test_tools:
        test_tools(args.test_tools)
    else:
        from winbuddy.agent.loop import interactive
        interactive()


if __name__ == "__main__":
    main()
