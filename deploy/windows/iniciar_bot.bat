@echo off
REM Arranca el bot en paper trading. Úsalo con el Programador de tareas (ver README) o con doble clic.
cd /d "%~dp0..\.."
call .venv\Scripts\activate.bat
python scripts\bot.py
