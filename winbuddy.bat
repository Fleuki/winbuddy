@echo off
rem ============================================================
rem  winbuddy — запуск двойным кликом (без терминала)
rem  Активирует venv и запускает Electron. Node должен быть в PATH.
rem ============================================================

rem Папка, где лежит этот .bat (корень проекта winbuddy)
set "PROJECT=%~dp0"

rem Активируем venv, чтобы Electron нашёл нужный python
call "%PROJECT%.venv\Scripts\activate.bat"

rem Переходим в папку electron и запускаем приложение.
rem start "" /b — запуск без отдельного окна консоли.
cd /d "%PROJECT%electron"
start "" /b cmd /c "npm start"

rem Даём процессу стартовать и закрываем это окно.
exit
