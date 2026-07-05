"""
Инструменты анализа (read-only).

Каждая функция:
  - делает ОДНУ вещь;
  - возвращает компактный dict (экономим токены — не дампим всё дерево);
  - уважает слой безопасности (forbidden-пути пропускаются молча);
  - НИЧЕГО не меняет на диске. Это Фаза 1.

Код кроссплатформенный (pathlib/os.scandir), но целится в Windows.
"""

from __future__ import annotations

import os
from pathlib import Path

from .. import safety

# --------------------------------------------------------------------------
# Вспомогательное
# --------------------------------------------------------------------------

def _human(size: int) -> str:
    """Байты -> человекочитаемо (1.5 GB)."""
    s = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if s < 1024 or unit == "TB":
            return f"{s:.1f} {unit}" if unit != "B" else f"{int(s)} B"
        s /= 1024
    return f"{s:.1f} TB"


# Что считаем кандидатом в мусор (только КАНДИДАТЫ — ничего не удаляем).
_JUNK_EXTS = {".tmp", ".temp", ".log", ".bak", ".old", ".dmp",
              ".crdownload", ".part", ".chk"}
_JUNK_NAMES = {"thumbs.db", "desktop.ini", ".ds_store"}
_JUNK_DIR_HINTS = ("cache", "temp", "tmp", "__pycache__")


def _walk_safe(root: Path):
    """
    Обход дерева с уважением к безопасности: forbidden-ветки не открываем,
    недоступные папки пропускаем без падения.
    """
    stack = [root]
    while stack:
        current = stack.pop()
        if safety.is_forbidden(current):
            continue
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if not safety.is_forbidden(entry.path):
                                stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            yield Path(entry.path), entry
                    except OSError:
                        continue
        except (PermissionError, OSError):
            continue


# --------------------------------------------------------------------------
# Инструменты
# --------------------------------------------------------------------------

def scan_directory(path: str, top_n: int = 10) -> dict:
    """
    Обзор папки: суммарный размер, число файлов/папок, топ самых тяжёлых
    подпапок и файлов первого уровня.
    """
    root = Path(path)
    if not root.exists():
        return {"error": f"путь не существует: {path}"}
    if not safety.is_read_allowed(root):
        return {"error": f"путь недоступен для чтения (защита): {path}"}

    total_size = 0
    file_count = 0
    # размеры по элементам первого уровня
    first_level: dict[str, int] = {}

    try:
        entries = list(os.scandir(root))
    except (PermissionError, OSError) as e:
        return {"error": f"нет доступа к {path}: {e}"}

    for entry in entries:
        if safety.is_forbidden(entry.path):
            continue
        try:
            if entry.is_file(follow_symlinks=False):
                sz = entry.stat().st_size
                total_size += sz
                file_count += 1
                first_level[entry.name] = sz
            elif entry.is_dir(follow_symlinks=False):
                sub = 0
                for _fp, fe in _walk_safe(Path(entry.path)):
                    try:
                        sub += fe.stat().st_size
                        file_count += 1
                    except OSError:
                        continue
                total_size += sub
                first_level[entry.name + "\\"] = sub
        except OSError:
            continue

    top = sorted(first_level.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
    return {
        "path": str(root),
        "total_size": _human(total_size),
        "total_size_bytes": total_size,
        "file_count": file_count,
        "top_items": [{"name": n, "size": _human(s)} for n, s in top],
    }


def find_large_files(path: str, min_size_mb: int = 100, limit: int = 15) -> dict:
    """Найти самые крупные файлы в дереве (от min_size_mb и выше)."""
    root = Path(path)
    if not root.exists():
        return {"error": f"путь не существует: {path}"}
    if not safety.is_read_allowed(root):
        return {"error": f"путь недоступен для чтения (защита): {path}"}

    threshold = min_size_mb * 1024 * 1024
    found: list[tuple[str, int]] = []
    for fp, fe in _walk_safe(root):
        try:
            sz = fe.stat().st_size
        except OSError:
            continue
        if sz >= threshold:
            found.append((str(fp), sz))

    found.sort(key=lambda kv: kv[1], reverse=True)
    found = found[:limit]
    return {
        "path": str(root),
        "min_size_mb": min_size_mb,
        "count": len(found),
        "files": [{"path": p, "size": _human(s)} for p, s in found],
    }


def find_junk_candidates(path: str, limit: int = 30) -> dict:
    """
    Найти КАНДИДАТОВ в мусор (временные файлы, кэши, .tmp/.log/.bak и т.п.).
    Только предложение к рассмотрению — ничего не удаляется.
    """
    root = Path(path)
    if not root.exists():
        return {"error": f"путь не существует: {path}"}
    if not safety.is_read_allowed(root):
        return {"error": f"путь недоступен для чтения (защита): {path}"}

    items: list[tuple[str, int, str]] = []  # (path, size, reason)
    total = 0
    for fp, fe in _walk_safe(root):
        name = fp.name.lower()
        ext = fp.suffix.lower()
        reason = ""
        if name in _JUNK_NAMES:
            reason = "системный мусорный файл"
        elif ext in _JUNK_EXTS:
            reason = f"временный/кэш ({ext})"
        else:
            parent = fp.parent.name.lower()
            if any(h in parent for h in _JUNK_DIR_HINTS):
                reason = f"в папке-кэше ({fp.parent.name})"
        if reason:
            try:
                sz = fe.stat().st_size
            except OSError:
                sz = 0
            items.append((str(fp), sz, reason))
            total += sz

    items.sort(key=lambda t: t[1], reverse=True)
    shown = items[:limit]
    return {
        "path": str(root),
        "candidate_count": len(items),
        "total_size": _human(total),
        "note": "ТОЛЬКО кандидаты. Ничего не удалено. Требует подтверждения человеком.",
        "candidates": [
            {"path": p, "size": _human(s), "reason": r} for p, s, r in shown
        ],
    }


def list_files(path: str, limit: int = 100) -> dict:
    """
    Точный поимённый список файлов первого уровня папки (с размерами).
    В отличие от scan_directory (агрегаты), показывает КОНКРЕТНЫЕ файлы —
    нужно, чтобы человек глазами увидел, что именно будет затронуто действием.
    """
    root = Path(path)
    if not root.exists():
        return {"error": f"путь не существует: {path}"}
    if not safety.is_read_allowed(root):
        return {"error": f"путь недоступен для чтения (защита): {path}"}

    files: list[tuple[str, int]] = []
    dirs: list[str] = []
    try:
        entries = list(os.scandir(root))
    except (PermissionError, OSError) as e:
        return {"error": f"нет доступа к {path}: {e}"}

    for entry in entries:
        if safety.is_forbidden(entry.path):
            continue
        try:
            if entry.is_file(follow_symlinks=False):
                files.append((entry.name, entry.stat().st_size))
            elif entry.is_dir(follow_symlinks=False):
                dirs.append(entry.name + "\\")
        except OSError:
            continue

    files.sort(key=lambda kv: kv[1], reverse=True)
    return {
        "path": str(root),
        "file_count": len(files),
        "dir_count": len(dirs),
        "files": [{"name": n, "size": _human(s)} for n, s in files[:limit]],
        "subfolders": sorted(dirs)[:limit],
        "truncated": len(files) > limit,
    }


def get_system_info() -> dict:
    """Инфо о дисках и памяти. Пути не нужны."""
    try:
        import psutil
    except ImportError:
        return {"error": "psutil не установлен (pip install psutil)"}

    disks = []
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
            disks.append({
                "drive": part.device,
                "fstype": part.fstype,
                "total": _human(usage.total),
                "used": _human(usage.used),
                "free": _human(usage.free),
                "percent_used": f"{usage.percent}%",
            })
        except (PermissionError, OSError):
            continue

    mem = psutil.virtual_memory()
    return {
        "disks": disks,
        "memory": {
            "total": _human(mem.total),
            "available": _human(mem.available),
            "percent_used": f"{mem.percent}%",
        },
    }


# Типы файлов -> имя папки для раскладки.
_ORG_MAP = {
    "Изображения": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".heic"},
    "Видео": {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm", ".flv"},
    "Аудио": {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a"},
    "Документы": {".pdf", ".doc", ".docx", ".txt", ".rtf", ".odt", ".md"},
    "Таблицы": {".xls", ".xlsx", ".csv", ".ods"},
    "Презентации": {".ppt", ".pptx", ".odp"},
    "Архивы": {".zip", ".rar", ".7z", ".tar", ".gz"},
    "Установщики": {".exe", ".msi"},
    "Код": {".py", ".js", ".html", ".css", ".json", ".c", ".cpp", ".java"},
}


def propose_organization(path: str) -> dict:
    """
    Предложить схему раскладки файлов первого уровня папки по категориям.
    ТОЛЬКО предложение — раскладка выполняется на Фазе 2 с подтверждением.
    """
    root = Path(path)
    if not root.exists():
        return {"error": f"путь не существует: {path}"}
    if not safety.is_read_allowed(root):
        return {"error": f"путь недоступен для чтения (защита): {path}"}

    ext_to_folder = {ext: folder for folder, exts in _ORG_MAP.items() for ext in exts}
    plan: dict[str, list[str]] = {}
    unmatched = 0

    try:
        entries = list(os.scandir(root))
    except (PermissionError, OSError) as e:
        return {"error": f"нет доступа к {path}: {e}"}

    for entry in entries:
        if not entry.is_file(follow_symlinks=False):
            continue
        ext = Path(entry.name).suffix.lower()
        folder = ext_to_folder.get(ext)
        if folder:
            plan.setdefault(folder, []).append(entry.name)
        else:
            unmatched += 1

    return {
        "path": str(root),
        "note": "ТОЛЬКО предложение. Ничего не перемещено. Выполнение — на след. фазе с подтверждением.",
        "proposed_folders": {
            folder: {"count": len(files), "examples": files[:3]}
            for folder, files in sorted(plan.items())
        },
        "unmatched_files": unmatched,
    }
