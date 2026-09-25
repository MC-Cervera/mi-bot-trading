@echo off
REM Doble clic: arranca el bot (si no estaba en marcha) y abre el panel en el navegador.
chcp 65001 >nul
title Mi bot de trading - panel
cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
    echo No se encuentra el entorno .venv en %CD%
    echo Crea el entorno siguiendo el README ^(paso "Instalacion"^) y vuelve a intentarlo.
    pause
    exit /b 1
)
call .venv\Scripts\activate.bat

python scripts\arrancar_bot.py

echo.
echo Abriendo el panel en http://localhost:8501 ...
echo NO cierres esta ventana mientras uses el panel. El bot sigue funcionando aunque la cierres.
echo.
start "" /b cmd /c "timeout /t 5 /nobreak >nul & start "" http://localhost:8501"
streamlit run panel\app.py

echo.
echo El panel se cerro. El bot sigue funcionando en segundo plano ^(detenlo desde el panel^).
pause
