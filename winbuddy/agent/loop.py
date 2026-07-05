"""
CLI-интерфейс агента (консоль).

Тонкая обёртка вокруг agent.core: подставляет консольные callback'и для вывода
шагов (rich) и подтверждения (ввод с клавиатуры). Вся логика агент-цикла и
безопасности живёт в core.py и переиспользуется сервером Electron.
"""

from __future__ import annotations

import json

from rich.console import Console
from rich.panel import Panel

from ..logger import get_logger
from .core import run_agent_core

log = get_logger()
console = Console()

SYSTEM_PROMPT = """Ты — winbuddy, помощник для анализа Windows-компьютера.

Твоя задача: понять запрос пользователя на обычном языке, разложить его на шаги
и выполнить через доступные инструменты, а затем дать понятный ОТЧЁТ на русском.

Важные правила:
- Ты умеешь АНАЛИЗИРОВАТЬ (read) свободно и умеешь РАЗЛОЖИТЬ файлы по папкам
  (organize_into_folders) — но любое изменение файлов проходит через
  обязательное подтверждение человеком, которое выполняет программа, не ты.
  Ты просто вызываешь инструмент; система сама покажет план и спросит человека.
- Ты умеешь чистить мусор (clean_junk) — но удаление ТОЛЬКО в корзину
  (обратимо) и тоже через подтверждение человеком. По умолчанию трогай лишь
  безопасный мусор; кэши приложений включай (include_risky=true) только если
  пользователь явно попросил. Всегда предупреждай, что это удаление.
- Ты умеешь проверять компьютер на вирусы через встроенный Windows Defender
  (scan_for_viruses) и смотреть его статус (get_defender_status). Быстрое
  сканирование занимает минуты, полное — десятки минут; предупреждай об этом.
  Если Defender недоступен (отключён/заменён) — честно скажи об этом.
- Ты пока НЕ умеешь переименовывать файлы — это появится позже.
- Перед раскладкой полезно вызвать list_files или propose_organization, чтобы
  показать человеку, что именно будет затронуто.
- Системные папки (Windows, Program Files, корзина) защищены — это нормально.
- Вызывай инструменты по мере необходимости; можешь вызвать несколько подряд.
- Финальный ответ — краткий, по делу, с конкретными путями и размерами. Не
  выдумывай данные, опирайся только на результаты инструментов.
"""


def _console_emit(event: dict) -> None:
    """Показать событие агента в консоли (rich)."""
    t = event.get("type")
    if t == "text":
        console.print(event["text"], style="dim")
    elif t == "tool_call":
        console.print(f"  🔍 {event['name']}({event['input']})", style="cyan")
    elif t == "plan":
        console.print(Panel(
            json.dumps(event["plan"], ensure_ascii=False, indent=2),
            title="[bold yellow]ПЛАН ДЕЙСТВИЯ (пока ничего не сделано)[/bold yellow]",
            style="yellow",
        ))
    elif t == "rejected":
        console.print("  ❌ Отменено пользователем. Ничего не изменено.", style="dim")
    elif t == "result":
        console.print(Panel(
            json.dumps(event["result"], ensure_ascii=False, indent=2),
            title="[bold green]РЕЗУЛЬТАТ[/bold green]",
            style="green",
        ))
    elif t == "error":
        console.print(f"  ⚠️ {event['text']}", style="red")
    # 'final' печатается вызывающим кодом в Panel, здесь не дублируем


def _console_confirm(name: str, plan: dict) -> bool:
    """Спросить подтверждение с клавиатуры."""
    try:
        answer = console.input(
            "[bold red]Выполнить это действие? Введи 'да' для подтверждения: [/bold red]"
        ).strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = ""
    return answer in {"да", "yes", "y", "д"}


def run_agent(user_task: str, history: list | None = None) -> tuple[str, list]:
    """Прогнать задачу через агент с консольным выводом и подтверждением."""
    return run_agent_core(
        user_task, history,
        emit=_console_emit,
        confirm=_console_confirm,
        system_prompt=SYSTEM_PROMPT,
    )


def interactive() -> None:
    """Интерактивный диалог с памятью в рамках сессии."""
    console.print(Panel.fit(
        "winbuddy — анализ, раскладка, чистка, антивирус\n"
        "Опиши задачу обычным языком. Напр.: «проанализируй диск D».\n"
        "Действия с файлами — с подтверждением. Удаление — только в корзину.\n"
        "Команды: reset — забыть контекст, exit — выход.",
        style="bold green",
    ))
    history: list = []
    while True:
        try:
            task = console.input("\n[bold]Ты:[/bold] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\nПока!")
            break
        if not task or task.lower() in {"exit", "quit", "выход"}:
            console.print("Пока!")
            break
        if task.lower() in {"reset", "сброс"}:
            history = []
            console.print("[dim]Контекст очищен.[/dim]")
            continue
        console.print("[bold]winbuddy:[/bold]")
        answer, history = run_agent(task, history)
        console.print(Panel(answer, style="green", title="Отчёт"))
