"""
Локальный сервер-мост между агентом (Python) и окном Electron (JS).

Архитектура:
    Electron (окно)  <--websocket-->  этот сервер  -->  agent.core

Зачем websocket: агент работает пошагово и в середине может запросить
подтверждение. Обычный HTTP «запрос-ответ» так не умеет — нужен двусторонний
живой канал, чтобы стримить шаги в окно и получать «да/нет» обратно.

Сложность: агент синхронный (SDK anthropic sync) и блокирующий, а websocket
асинхронный. Поэтому агент крутится в отдельном ПОТОКЕ, а общение с ним идёт
через потокобезопасные очереди:
    out_queue  — события агента -> отправляем в окно
    confirm_box — ответ пользователя на подтверждение <- из окна

Сервер слушает только localhost — наружу порт не торчит.
"""

from __future__ import annotations

import asyncio
import json
import queue
import threading

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from ..logger import get_logger
from .core import run_agent_core
from .loop import SYSTEM_PROMPT

log = get_logger()
app = FastAPI(title="winbuddy")

# Electron-фронтенд ходит с file:// или localhost — разрешаем локальные источники.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # только localhost-сервер, наружу не торчит
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    """Проверка, что сервер жив (Electron пингует при старте)."""
    return {"status": "ok"}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    log.info("Electron подключился по websocket")

    # История диалога живёт на всё соединение (память сессии).
    history: list = []

    try:
        while True:
            # Ждём сообщение пользователя из окна.
            raw = await ws.receive_text()
            data = json.loads(raw)

            if data.get("type") == "reset":
                history = []
                await ws.send_text(json.dumps({"type": "reset_ok"}))
                continue

            user_task = data.get("text", "").strip()
            if not user_task:
                continue

            history = await _run_turn(ws, user_task, history)

    except WebSocketDisconnect:
        log.info("Electron отключился")
    except Exception as e:
        log.error("ошибка websocket: %s", e)
        try:
            await ws.send_text(json.dumps({"type": "error", "text": str(e)}))
        except Exception:
            pass


async def _run_turn(ws: WebSocket, user_task: str, history: list) -> list:
    """
    Прогнать один ход агента в потоке, стримя события в окно и обрабатывая
    подтверждение через websocket. Возвращает обновлённую историю.
    """
    loop = asyncio.get_event_loop()
    out_queue: queue.Queue = queue.Queue()          # события агента -> окно
    confirm_box: dict = {"event": threading.Event(), "answer": False}
    result_box: dict = {"history": history}

    def emit(event: dict):
        """Вызывается из потока агента — просто кладём событие в очередь."""
        out_queue.put(event)

    def confirm(name: str, plan: dict) -> bool:
        """
        Вызывается из потока агента. Кладём запрос подтверждения в очередь и
        БЛОКИРУЕМ поток агента, пока async-сторона не получит ответ из окна.
        """
        out_queue.put({"type": "confirm_request", "name": name, "plan": plan})
        confirm_box["event"].wait()          # ждём ответа пользователя
        confirm_box["event"].clear()
        return confirm_box["answer"]

    def agent_thread():
        try:
            _final, new_history = run_agent_core(
                user_task, history, emit, confirm, SYSTEM_PROMPT
            )
            result_box["history"] = new_history
        finally:
            out_queue.put({"type": "__done__"})   # сигнал завершения хода

    # Запускаем агента в фоне.
    threading.Thread(target=agent_thread, daemon=True).start()

    # Перекачиваем события из очереди в websocket, пока ход не завершится.
    while True:
        try:
            event = await loop.run_in_executor(None, out_queue.get, True, 0.1)
        except queue.Empty:
            continue

        if event.get("type") == "__done__":
            break

        if event.get("type") == "confirm_request":
            # Отправляем запрос в окно и ждём ответа именно на него.
            await ws.send_text(json.dumps(event, ensure_ascii=False))
            reply_raw = await ws.receive_text()
            reply = json.loads(reply_raw)
            confirm_box["answer"] = bool(reply.get("approved", False))
            confirm_box["event"].set()          # разблокируем поток агента
            continue

        # Обычное событие — просто в окно.
        await ws.send_text(json.dumps(event, ensure_ascii=False))

    return result_box["history"]


def run_server(host: str = "127.0.0.1", port: int = 8756):
    """Запустить сервер. Слушает только localhost."""
    import uvicorn
    log.info("winbuddy server: http://%s:%d", host, port)
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    run_server()
