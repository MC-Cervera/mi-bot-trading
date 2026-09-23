@echo off
REM Abre el panel en http://localhost:8501
cd /d "%~dp0..\.."
call .venv\Scripts\activate.bat
streamlit run panel\app.py --server.address 127.0.0.1
