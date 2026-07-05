"""
Оркестрация встроенного Windows Defender (внешняя утилита MpCmdRun.exe).

Мы НЕ пишем антивирус — мы вызываем штатный Defender и разбираем его вывод.
Это read-операция по духу: сканирование само по себе ничего не удаляет,
только докладывает. Поэтому подтверждение человеком здесь не требуется.

Тонкости, заложенные в код:
  - Путь к MpCmdRun.exe содержит версию платформы, которая меняется после
    обновлений. Ищем актуальную версию, а не хардкодим.
  - Сканирование долгое (минуты). Ставим таймаут, чтобы не висеть вечно.
  - Defender может быть отключён / заменён сторонним антивирусом / требовать
    прав. Все эти случаи ловим и честно сообщаем, а не падаем.

Работает только на Windows. На других ОС инструмент вернёт понятную ошибку.
"""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path

from ..logger import get_logger

log = get_logger()

# Где Defender держит MpCmdRun.exe. Версия в пути (Platform\<версия>) меняется,
# поэтому берём самую свежую подпапку.
_DEFENDER_PLATFORM_DIR = Path(
    r"C:\ProgramData\Microsoft\Windows Defender\Platform"
)
# Запасной путь (старые системы) — без версии.
_DEFENDER_FALLBACK = Path(r"C:\Program Files\Windows Defender\MpCmdRun.exe")


def _find_mpcmdrun() -> Path | None:
    """Найти актуальный MpCmdRun.exe. None, если не найден."""
    if _DEFENDER_PLATFORM_DIR.is_dir():
        # подпапки — версии платформы; берём самую свежую по имени (версии
        # сортируются лексикографически близко к семантике, для наших целей ок)
        versions = sorted(
            (d for d in _DEFENDER_PLATFORM_DIR.iterdir() if d.is_dir()),
            reverse=True,
        )
        for v in versions:
            candidate = v / "MpCmdRun.exe"
            if candidate.is_file():
                return candidate
    if _DEFENDER_FALLBACK.is_file():
        return _DEFENDER_FALLBACK
    return None


def scan_for_viruses(path: str | None = None, quick: bool = True,
                     timeout_sec: int = 900) -> dict:
    """
    Запустить сканирование Windows Defender.

    path  — что сканировать (папка/файл). Если None — системное сканирование.
    quick — True: быстрое сканирование; False: полное (может занять десятки минут).
    timeout_sec — предохранитель от вечного зависания (по умолч. 15 минут).

    Возвращает структурированный результат: найдены ли угрозы, сырой вывод.
    """
    if platform.system() != "Windows":
        return {"error": "сканирование Defender доступно только на Windows"}

    mpcmd = _find_mpcmdrun()
    if mpcmd is None:
        return {
            "error": "MpCmdRun.exe не найден. Возможно, Defender отключён или "
                     "заменён сторонним антивирусом."
        }

    # Собираем команду. -Scan -ScanType 1 = быстрое, 2 = полное, 3 = папка/файл.
    if path:
        target = Path(path)
        if not target.exists():
            return {"error": f"путь не существует: {path}"}
        cmd = [str(mpcmd), "-Scan", "-ScanType", "3", "-File", str(target)]
        scan_desc = f"папка/файл: {path}"
    else:
        scan_type = "1" if quick else "2"
        cmd = [str(mpcmd), "-Scan", "-ScanType", scan_type]
        scan_desc = "быстрое (системное)" if quick else "полное (системное)"

    log.info("Defender: запуск сканирования (%s)…", scan_desc)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return {
            "error": f"сканирование превысило таймаут {timeout_sec} с. "
                     "Для полного сканирования увеличь timeout или запусти вручную."
        }
    except (OSError, subprocess.SubprocessError) as e:
        return {"error": f"не удалось запустить Defender: {e}"}

    output = (proc.stdout or "") + (proc.stderr or "")
    # Определяем угрозы по КОДУ ВОЗВРАТА, а не по тексту: MpCmdRun возвращает
    # 0 = чисто, 2 = найдены угрозы. Текстовый парсинг ненадёжен — фраза
    # "found no threats" содержит и "found", и "threat", но означает ЧИСТО.
    if proc.returncode == 0:
        threats_found = False
    elif proc.returncode == 2:
        threats_found = True
    else:
        # неизвестный код — не утверждаем ни то, ни другое, помечаем как неясно
        threats_found = None

    # Обрезаем сырой вывод, чтобы не раздувать контекст агента.
    trimmed = output.strip()
    if len(trimmed) > 2000:
        trimmed = trimmed[:1000] + "\n…\n" + trimmed[-1000:]

    if threats_found is True:
        summary = "Обнаружены угрозы — проверь детали ниже."
    elif threats_found is False:
        summary = "Угроз не обнаружено."
    else:
        summary = f"Сканирование завершилось с кодом {proc.returncode} (статус неясен)."

    return {
        "scan_type": scan_desc,
        "return_code": proc.returncode,
        "threats_found": threats_found,
        "summary": summary,
        "raw_output": trimmed,
    }


def get_defender_status(timeout_sec: int = 30) -> dict:
    """
    Быстрая проверка состояния Defender (включён ли, когда обновлялись базы).
    Использует PowerShell Get-MpComputerStatus — не требует MpCmdRun.exe.
    """
    if platform.system() != "Windows":
        return {"error": "доступно только на Windows"}

    ps_cmd = (
        "Get-MpComputerStatus | Select-Object "
        "AntivirusEnabled,RealTimeProtectionEnabled,"
        "AntivirusSignatureLastUpdated | Format-List"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=timeout_sec,
            encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.SubprocessError) as e:
        return {"error": f"не удалось получить статус Defender: {e}"}

    out = (proc.stdout or "").strip()
    if not out:
        return {"error": "Defender не ответил (возможно, отключён или заменён)."}
    return {"status_raw": out}
