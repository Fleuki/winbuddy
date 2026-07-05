"""
Ядро агента, управляемое callback'ами.

Одна реализация агент-цикла для двух интерфейсов (CLI и Electron). Различия
интерфейсов вынесены в две функции обратного вызова:

  emit(event: dict)         — как показывать шаги (печать в консоль / отправка
                              в окно по websocket). Типы событий см. ниже.
  confirm(name, plan) -> bool — как спрашивать подтверждение на write-действие
                              (ввод с клавиатуры / диалог в окне).

Ключевое свойство безопасности сохраняется: dry_run/выполнение задаёт ЭТОТ код,
а не модель; confirm обязателен перед любым write.

Типы событий emit:
  {"type": "text",      "text": str}                 — реплика/размышление агента
  {"type": "tool_call", "name": str, "input": dict}  — вызов read-инструмента
  {"type": "plan",      "name": str, "plan": dict}   — план write-действия (до confirm)
  {"type": "rejected",  "name": str}                 — пользователь отклонил
  {"type": "result",    "name": str, "result": dict} — write-действие выполнено
  {"type": "final",     "text": str}                 — финальный ответ
  {"type": "error",     "text": str}                 — ошибка
"""

from __future__ import annotations

import json

from .. import config
from ..logger import get_logger
from ..tools import registry

log = get_logger()

SYSTEM_PROMPT = None  # заполняется из loop.py, чтобы не дублировать текст


def _build_client():
    """Собрать клиент Anthropic (с учётом aitunnel base_url и Bearer-заголовка)."""
    from anthropic import Anthropic
    client_kwargs = {"api_key": config.ANTHROPIC_API_KEY}
    if config.API_BASE_URL:
        client_kwargs["base_url"] = config.API_BASE_URL
        client_kwargs["default_headers"] = {
            "Authorization": f"Bearer {config.ANTHROPIC_API_KEY}"
        }
    return Anthropic(**client_kwargs)


def run_agent_core(user_task, history, emit, confirm, system_prompt):
    """
    Прогнать задачу через агент-цикл. Возвращает (финальный_текст, история).

    emit    — callback для событий (см. модульную доку).
    confirm — callback (name, plan) -> bool для подтверждения write-действий.
    """
    try:
        from anthropic import Anthropic  # noqa: F401
    except ImportError:
        msg = "Ошибка: не установлен пакет 'anthropic'."
        emit({"type": "error", "text": msg})
        return msg, (history or [])

    if not config.ANTHROPIC_API_KEY:
        msg = ("Ошибка: не задан ANTHROPIC_API_KEY. Создай .env и вставь ключ "
               "(для aitunnel — sk-aitunnel-...).")
        emit({"type": "error", "text": msg})
        return msg, (history or [])

    client = _build_client()
    messages = history if history is not None else []
    messages.append({"role": "user", "content": user_task})

    for _step in range(1, config.MAX_AGENT_STEPS + 1):
        try:
            response = client.messages.create(
                model=config.MODEL,
                max_tokens=2000,
                system=system_prompt,
                messages=messages,
                tools=registry.TOOL_SCHEMAS,
            )
        except Exception as e:
            log.error("сбой запроса к API: %s", e)
            msg = f"Ошибка обращения к Claude API: {e}"
            emit({"type": "error", "text": msg})
            return msg, messages

        assistant_content = []
        tool_uses = []
        text_blocks = []            # копим текст, эмитим ПОСЛЕ (см. ниже)
        for block in response.content:
            if block.type == "text":
                assistant_content.append({"type": "text", "text": block.text})
                if block.text.strip():
                    text_blocks.append(block.text.strip())
            elif block.type == "tool_use":
                assistant_content.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })
                tool_uses.append(block)

        messages.append({"role": "assistant", "content": assistant_content})

        if response.stop_reason != "tool_use":
            # Финальный шаг: весь текст идёт как ОДИН 'final', не дублируем в 'text'.
            final = "\n".join(text_blocks) or "(пустой ответ)"
            emit({"type": "final", "text": final})
            return final, messages

        # Промежуточный шаг: текст агента (размышление перед вызовом инструмента)
        # эмитим как 'text' — это не финальный ответ.
        for t in text_blocks:
            emit({"type": "text", "text": t})

        tool_results = []
        for tu in tool_uses:
            if tu.name in registry.WRITE_TOOLS:
                result_json = _handle_write_tool(tu.name, tu.input, emit, confirm)
            else:
                emit({"type": "tool_call", "name": tu.name, "input": tu.input})
                result_json = registry.dispatch(tu.name, tu.input)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": result_json,
            })

        messages.append({"role": "user", "content": tool_results})

    msg = "Достигнут лимит шагов агента — уточни запрос."
    emit({"type": "final", "text": msg})
    return msg, messages


def _handle_write_tool(name, args, emit, confirm):
    """
    Провести write-инструмент через подтверждение.

    Подтверждение — вне контроля модели: dry_run/выполнение решает этот код,
    а решение да/нет принимает человек через callback confirm().
    """
    # 1. План (dry-run).
    plan_json = registry.dispatch(name, {**args, "dry_run": True})
    plan = json.loads(plan_json)

    if "error" in plan:
        emit({"type": "error", "text": plan["error"]})
        return plan_json

    # 2. Показываем план и спрашиваем человека.
    emit({"type": "plan", "name": name, "plan": plan})
    approved = confirm(name, plan)

    if not approved:
        emit({"type": "rejected", "name": name})
        return json.dumps(
            {"status": "отклонено пользователем", "note": "ничего не изменено"},
            ensure_ascii=False,
        )

    # 3. Выполняем.
    result_json = registry.dispatch(name, {**args, "dry_run": False})
    result = json.loads(result_json)
    emit({"type": "result", "name": name, "result": result})
    return result_json
