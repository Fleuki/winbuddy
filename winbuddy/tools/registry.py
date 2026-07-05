"""
Реестр инструментов.

Две вещи:
  1. TOOL_SCHEMAS — описания инструментов в формате Claude tool use. Именно их
     Claude видит и по ним решает, что вызвать. Описания на русском, чтобы
     модель точнее понимала намерение пользователя.
  2. dispatch() — берёт имя инструмента и аргументы от Claude, вызывает
     реальную Python-функцию, возвращает результат.

На Фазе 1 все инструменты read-only. Когда добавим write-инструменты (Фаза 2),
они пройдут через слой подтверждения, а не напрямую сюда.
"""

from __future__ import annotations

import json

from .analysis import (
    scan_directory,
    find_large_files,
    find_junk_candidates,
    list_files,
    get_system_info,
    propose_organization,
)
from .actions import organize_into_folders, clean_junk
from .security_tools import scan_for_viruses, get_defender_status

# Схемы для Claude. input_schema — JSON Schema аргументов.
TOOL_SCHEMAS = [
    {
        "name": "scan_directory",
        "description": (
            "Обзор папки: суммарный размер, количество файлов, топ самых "
            "тяжёлых подпапок и файлов. Используй для общего вопроса "
            "'что занимает место' или 'проанализируй папку/диск'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Путь к папке, напр. 'D:\\\\'"},
                "top_n": {"type": "integer", "description": "Сколько крупнейших элементов показать (по умолч. 10)"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "find_large_files",
        "description": (
            "Найти самые крупные файлы в дереве папки от заданного размера. "
            "Используй, когда нужно 'что весит больше всего' или найти, "
            "что можно удалить для освобождения места."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Путь к папке"},
                "min_size_mb": {"type": "integer", "description": "Порог в МБ (по умолч. 100)"},
                "limit": {"type": "integer", "description": "Сколько файлов показать (по умолч. 15)"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "find_junk_candidates",
        "description": (
            "Найти КАНДИДАТОВ в мусор: временные файлы, кэши, .tmp/.log/.bak, "
            "thumbs.db и т.п. Только предложение к рассмотрению — ничего не "
            "удаляется. Используй для 'найди мусор' / 'что можно почистить'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Путь к папке"},
                "limit": {"type": "integer", "description": "Сколько кандидатов показать (по умолч. 30)"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "list_files",
        "description": (
            "Точный поимённый список файлов в папке (с размерами и подпапками). "
            "В отличие от scan_directory показывает КОНКРЕТНЫЕ имена файлов. "
            "Используй перед любым действием (раскладка/удаление), чтобы "
            "показать пользователю, что именно будет затронуто."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Путь к папке"},
                "limit": {"type": "integer", "description": "Макс. файлов в списке (по умолч. 100)"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "organize_into_folders",
        "description": (
            "РАЗЛОЖИТЬ файлы папки по подпапкам-категориям (Изображения, "
            "Документы, Видео, Архивы и т.д.). Это перемещение внутри той же "
            "папки, НЕ удаление. Используй для 'разложи по папкам', 'наведи "
            "порядок'. Действие будет показано пользователю как план и "
            "выполнено только после его подтверждения."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Путь к папке, которую нужно разложить"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "clean_junk",
        "description": (
            "УДАЛИТЬ мусорные файлы В КОРЗИНУ (обратимо, не безвозвратно). "
            "По умолчанию удаляет только безопасный мусор (temp, .old, .bak, "
            "thumbs.db). Кэши живых приложений НЕ трогает, если не попросить "
            "явно 'включая кэши приложений'. Используй для 'почисти мусор', "
            "'удали временные файлы'. Показывается как план и выполняется "
            "только после подтверждения человеком."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Путь к папке для чистки"},
                "include_risky": {
                    "type": "boolean",
                    "description": "Включить кэши приложений (рискованно). По умолчанию false.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "scan_for_viruses",
        "description": (
            "Просканировать компьютер или папку на вирусы встроенным Windows "
            "Defender. Без path — системное сканирование (quick=true быстрое, "
            "false полное и долгое). С path — сканирует конкретную папку/файл. "
            "Ничего не удаляет без ведома Defender, только проверяет. Используй "
            "для 'проверь на вирусы', 'просканируй диск'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Папка/файл для сканирования (опционально)"},
                "quick": {"type": "boolean", "description": "Быстрое (true) или полное (false) сканирование"},
            },
        },
    },
    {
        "name": "get_defender_status",
        "description": (
            "Проверить состояние Windows Defender: включён ли антивирус, "
            "работает ли защита в реальном времени, когда обновлялись базы. "
            "Используй для 'работает ли антивирус', 'статус защиты'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_system_info",
        "description": (
            "Информация о дисках (объём, свободно, занято) и памяти. "
            "Аргументов не требует. Используй для 'сколько места на дисках', "
            "'состояние системы'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "propose_organization",
        "description": (
            "Предложить схему раскладки файлов папки по категориям "
            "(Изображения, Документы, Видео и т.д.). ТОЛЬКО предложение — "
            "ничего не перемещается. Используй для 'разложи по папкам', "
            "'наведи порядок' (на этой фазе — только план)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Путь к папке"},
            },
            "required": ["path"],
        },
    },
]

# Имя -> функция.
_DISPATCH = {
    "scan_directory": scan_directory,
    "find_large_files": find_large_files,
    "find_junk_candidates": find_junk_candidates,
    "list_files": list_files,
    "get_system_info": get_system_info,
    "propose_organization": propose_organization,
    "organize_into_folders": organize_into_folders,
    "clean_junk": clean_junk,
    "scan_for_viruses": scan_for_viruses,
    "get_defender_status": get_defender_status,
}

# WRITE-инструменты (ОПАСНЫЕ). Цикл обязан провести их через подтверждение
# человеком и режим dry-run, а не вызывать напрямую. Слой безопасности.
WRITE_TOOLS = {"organize_into_folders", "clean_junk"}


def dispatch(name: str, args: dict) -> str:
    """
    Выполнить инструмент по имени. Возвращает JSON-строку (её получит Claude).
    Никогда не бросает наружу — ошибки заворачиваются в JSON.
    """
    func = _DISPATCH.get(name)
    if func is None:
        return json.dumps({"error": f"неизвестный инструмент: {name}"}, ensure_ascii=False)
    try:
        result = func(**args)
    except TypeError as e:
        result = {"error": f"неверные аргументы для {name}: {e}"}
    except Exception as e:  # инструмент не должен ронять агента
        result = {"error": f"сбой инструмента {name}: {e}"}
    return json.dumps(result, ensure_ascii=False)
