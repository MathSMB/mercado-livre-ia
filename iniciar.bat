@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Criando ambiente Python pela primeira vez...
  py -3 -m venv .venv 2>nul || python -m venv .venv
  if not exist ".venv\Scripts\python.exe" (
    echo Nao encontrei o Python. Instale em https://www.python.org/downloads/ e marque "Add Python to PATH".
    pause
    exit /b 1
  )
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || (echo Falha ao instalar dependencias. & pause & exit /b 1)
)
echo Abrindo o painel em http://localhost:8501 ...
".venv\Scripts\python.exe" -m streamlit run painel.py
pause
