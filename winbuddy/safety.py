"""
Слой безопасности: проверка путей.

Единственный источник правды о том, что агенту можно трогать. Инструменты
ОБЯЗАНЫ спрашивать разрешение здесь, а не решать сами.

Логика:
  - is_forbidden(path)      -> путь внутри системного/запретного корня? Тогда НИКОГДА.
  - is_read_allowed(path)   -> можно ли читать/анализировать.
  - is_write_allowed(path)  -> можно ли перемещать/удалять/переименовывать.

Дефолт максимально трусливый: если сомневаемся — запрещаем.
"""

from __future__ import annotations

import ntpath
from pathlib import Path

from . import config


def _normalize(path: str | Path) -> str:
    """
    Привести Windows-путь к каноничной форме для сравнения.

    Используем ntpath (а не pathlib), чтобы обратные слеши и логика Windows
    работали одинаково на любой ОС — включая машину разработки на Linux.
    ntpath.normpath ещё и схлопывает '..' — это защита от обхода allowlist
    трюком вида  d:\\dump\\..\\..\\windows.
    """
    s = str(path)
    s = ntpath.normpath(s)          # схлопывает .. и приводит разделители к \
    s = ntpath.normcase(s)          # нижний регистр + / -> \ (Windows-семантика)
    return s.rstrip("\\/")


def _is_within(path_norm: str, root: str) -> bool:
    """path_norm лежит внутри root (или равен ему)?"""
    root_norm = _normalize(root)
    return path_norm == root_norm or path_norm.startswith(root_norm + "\\")


def is_forbidden(path: str | Path) -> bool:
    """
    True, если путь трогать нельзя. Два случая:
      1. путь внутри одного из FORBIDDEN_ROOTS (напр. c:\\windows);
      2. в пути встречается запрещённая на любом диске папка
         (корзина, System Volume Information и т.п.) — на d:, e: и т.д.
    """
    p = _normalize(path)
    if any(_is_within(p, root) for root in config.FORBIDDEN_ROOTS):
        return True
    # разбиваем путь на сегменты и ищем запрещённые имена папок
    segments = p.split("\\")
    forbidden_names = {name.lower() for name in config.FORBIDDEN_ANY_DRIVE}
    return any(seg in forbidden_names for seg in segments)


def is_read_allowed(path: str | Path) -> bool:
    """
    Можно ли читать/анализировать путь.

    Правило: нельзя в FORBIDDEN. Если ALLOWED_READ_ROOTS пуст — разрешено везде
    (кроме forbidden). Если задан — только внутри него.
    """
    if is_forbidden(path):
        return False
    if not config.ALLOWED_READ_ROOTS:
        return True
    p = _normalize(path)
    return any(_is_within(p, root) for root in config.ALLOWED_READ_ROOTS)


def is_write_allowed(path: str | Path) -> bool:
    """
    Можно ли выполнять ОПАСНУЮ операцию (move/delete/rename) над путём.

    Правило: нельзя в FORBIDDEN. Разрешено ТОЛЬКО внутри ALLOWED_WRITE_ROOTS.
    Пустой allowlist => write запрещён везде (безопасный дефолт до Фазы 2).
    """
    if is_forbidden(path):
        return False
    if not config.ALLOWED_WRITE_ROOTS:
        return False
    p = _normalize(path)
    return any(_is_within(p, root) for root in config.ALLOWED_WRITE_ROOTS)


def explain(path: str | Path) -> str:
    """Человекочитаемый статус пути — удобно для отладки и вывода пользователю."""
    if is_forbidden(path):
        return "ЗАПРЕЩЁН (системный/защищённый путь)"
    read = "чтение: да" if is_read_allowed(path) else "чтение: нет"
    write = "запись: да" if is_write_allowed(path) else "запись: нет"
    return f"{read}, {write}"
