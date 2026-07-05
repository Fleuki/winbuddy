"""
Write-инструменты (ОПАСНЫЕ — меняют файловую систему).

Ключевые принципы, общие для всех инструментов этого файла:
  1. Двойной режим: dry_run=True возвращает ПЛАН (что будет сделано), не трогая
     диск. dry_run=False реально выполняет. Режимом управляет обвязка (agent-цикл),
     а НЕ модель — модель не может сама себе разрешить выполнение.
  2. Только внутри ALLOWED_WRITE_ROOTS и никогда в forbidden-путях.
  3. Никогда не перезаписываем существующие файлы — при коллизии имени пропускаем.
  4. Каждое реальное действие пишется в аудит-лог ДО выполнения.

Первый инструмент — organize_into_folders: раскладывает файлы папки по
подпапкам-категориям (Изображения, Документы и т.д.). Это перемещение, не
удаление; файлы остаются внутри той же папки, просто раскладываются.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .. import safety
from ..logger import audit, get_logger
from .analysis import _ORG_MAP, _human, _walk_safe  # переиспользуем из анализа

# --------------------------------------------------------------------------
# Классификация мусора по риску (для clean_junk)
# --------------------------------------------------------------------------
# БЕЗОПАСНО удалять: временные файлы, старые версии, системный мусор.
_SAFE_JUNK_EXTS = {".tmp", ".temp", ".bak", ".old", ".dmp", ".crdownload", ".part", ".chk"}
_SAFE_JUNK_NAMES = {"thumbs.db", "desktop.ini", ".ds_store"}

# РИСКОВАННО: кэши живых приложений. Пересоздадутся, но по умолчанию НЕ трогаем.
_RISKY_DIR_HINTS = ("cache", "webcache", "__pycache__", "gpucache", "code cache")


def _classify_junk(fp: Path) -> str | None:
    """Вернуть 'safe' / 'risky' / None (не мусор) для файла."""
    name = fp.name.lower()
    ext = fp.suffix.lower()
    if name in _SAFE_JUNK_NAMES or ext in _SAFE_JUNK_EXTS:
        return "safe"
    parent = fp.parent.name.lower()
    if any(h in parent for h in _RISKY_DIR_HINTS):
        return "risky"
    if ext == ".log":  # логи рискованные — приложение может писать в них сейчас
        return "risky"
    return None

log = get_logger()


def organize_into_folders(path: str, dry_run: bool = True) -> dict:
    """
    Разложить файлы первого уровня папки по подпапкам-категориям.

    dry_run=True  -> вернуть план (какой файл в какую папку), ничего не двигая.
    dry_run=False -> реально создать подпапки и переместить файлы.
    """
    root = Path(path)
    if not root.exists():
        return {"error": f"путь не существует: {path}"}
    if not safety.is_read_allowed(root):
        return {"error": f"путь недоступен (защита): {path}"}

    # Для РЕАЛЬНОГО выполнения нужен write-доступ. Для плана (dry_run) — нет.
    if not dry_run and not safety.is_write_allowed(root):
        return {
            "error": (
                f"запись в {path} запрещена. Добавь корень в WINBUDDY_WRITE_ROOTS "
                f"в .env, если действительно хочешь разрешить."
            )
        }

    ext_to_folder = {ext: folder for folder, exts in _ORG_MAP.items() for ext in exts}

    # Строим план: файл -> категория.
    plan: list[tuple[Path, str]] = []  # (файл, имя_папки_категории)
    skipped_no_category = 0
    try:
        entries = list(root.iterdir())
    except (PermissionError, OSError) as e:
        return {"error": f"нет доступа к {path}: {e}"}

    for entry in entries:
        if not entry.is_file():
            continue  # папки не трогаем
        folder = ext_to_folder.get(entry.suffix.lower())
        if folder:
            plan.append((entry, folder))
        else:
            skipped_no_category += 1

    # Группируем для читаемого вывода.
    by_folder: dict[str, list[str]] = {}
    for f, folder in plan:
        by_folder.setdefault(folder, []).append(f.name)

    if dry_run:
        return {
            "mode": "план (dry-run)",
            "path": str(root),
            "note": "Ничего не перемещено. Требуется подтверждение человеком.",
            "will_move_count": len(plan),
            "plan": {folder: names for folder, names in sorted(by_folder.items())},
            "skipped_no_category": skipped_no_category,
        }

    # --- реальное выполнение ---
    moved = 0
    skipped_collision: list[str] = []
    errors: list[str] = []

    for f, folder in plan:
        dest_dir = root / folder
        dest = dest_dir / f.name

        # Никогда не перезаписываем: если файл с таким именем уже есть — пропуск.
        if dest.exists():
            skipped_collision.append(f.name)
            continue

        # Двойная проверка безопасности источника и назначения перед КАЖДЫМ move.
        if safety.is_forbidden(f) or safety.is_forbidden(dest):
            errors.append(f"{f.name}: путь защищён, пропущен")
            continue

        try:
            dest_dir.mkdir(exist_ok=True)
            audit("MOVE", f"{f} -> {dest}")   # пишем в аудит ДО перемещения
            shutil.move(str(f), str(dest))
            moved += 1
        except (OSError, shutil.Error) as e:
            errors.append(f"{f.name}: {e}")

    log.info("organize: перемещено %d, коллизий %d, ошибок %d",
             moved, len(skipped_collision), len(errors))

    return {
        "mode": "выполнено",
        "path": str(root),
        "moved_count": moved,
        "skipped_collision": skipped_collision,
        "errors": errors,
        "note": "Файлы перемещены в подпапки. Отменить можно вручную или через историю.",
    }


def clean_junk(path: str, include_risky: bool = False,
               max_files: int = 500, dry_run: bool = True) -> dict:
    """
    Удалить мусорные файлы В КОРЗИНУ (send2trash, не безвозвратно).

    По умолчанию — только БЕЗОПАСНЫЙ мусор (temp, .old, thumbs.db, .bak).
    include_risky=True добавляет кэши приложений (по явному запросу).

    Снимок списка: файлы фиксируются детерминированно (сортировка по пути),
    поэтому dry-run показывает ровно то, что удалится — без рассинхрона.
    """
    root = Path(path)
    if not root.exists():
        return {"error": f"путь не существует: {path}"}
    if not safety.is_read_allowed(root):
        return {"error": f"путь недоступен (защита): {path}"}
    if not dry_run and not safety.is_write_allowed(root):
        return {
            "error": (
                f"удаление в {path} запрещено. Добавь корень в WINBUDDY_WRITE_ROOTS "
                f"в .env, если действительно хочешь разрешить."
            )
        }

    safe: list[tuple[str, int]] = []
    risky: list[tuple[str, int]] = []
    for fp, fe in _walk_safe(root):
        if safety.is_forbidden(fp):
            continue
        kind = _classify_junk(fp)
        if kind is None:
            continue
        try:
            sz = fe.stat().st_size
        except OSError:
            sz = 0
        (safe if kind == "safe" else risky).append((str(fp), sz))

    safe.sort()
    risky.sort()

    to_delete = list(safe)
    if include_risky:
        to_delete += risky
    to_delete = to_delete[:max_files]
    total_size = sum(s for _p, s in to_delete)

    if dry_run:
        return {
            "mode": "план (dry-run)",
            "path": str(root),
            "note": "Удаление ТОЛЬКО в корзину (обратимо). Требуется подтверждение.",
            "will_delete_count": len(to_delete),
            "will_delete_size": _human(total_size),
            "safe_junk_count": len(safe),
            "risky_cache_count": len(risky),
            "including_risky": include_risky,
            "sample": [{"path": p, "size": _human(s)} for p, s in to_delete[:20]],
            "hint": ("Кэши приложений НЕ включены (рискованно). Чтобы включить — "
                     "попроси явно 'включая кэши приложений'." if not include_risky
                     else "Включены кэши приложений — пересоздадутся при запуске."),
        }

    # --- реальное удаление в корзину ---
    try:
        from send2trash import send2trash
    except ImportError:
        return {"error": "не установлен send2trash (pip install send2trash)"}

    trashed = 0
    errors: list[str] = []
    for p, _s in to_delete:
        if safety.is_forbidden(p):     # финальная проверка перед каждым удалением
            errors.append(f"{p}: защищён, пропущен")
            continue
        try:
            audit("TRASH", p)          # аудит ДО удаления
            send2trash(p)
            trashed += 1
        except Exception as e:
            errors.append(f"{p}: {e}")

    log.info("clean_junk: в корзину %d, ошибок %d", trashed, len(errors))
    return {
        "mode": "выполнено",
        "path": str(root),
        "trashed_count": trashed,
        "freed_size": _human(total_size),
        "errors": errors,
        "note": "Файлы перемещены в КОРЗИНУ — можно восстановить оттуда.",
    }
