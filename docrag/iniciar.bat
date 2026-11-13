@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
if not exist "dados\entrada" mkdir "dados\entrada"
if not exist ".venv\Scripts\python.exe" (
  echo Criando ambiente Python pela primeira vez. A instalacao do Docling e do Marker demora alguns minutos...
  py -3.12 -m venv .venv 2>nul || py -3 -m venv .venv 2>nul || python -m venv .venv
  if not exist ".venv\Scripts\python.exe" (
    echo Nao encontrei o Python. Instale o Python 3.12 em https://www.python.org/downloads/ e marque "Add Python to PATH".
    pause
    exit /b 1
  )
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || (echo Falha ao instalar dependencias. & rmdir /s /q .venv & pause & exit /b 1)
)
echo Abrindo o painel em http://localhost:8501 ...
start "" /min cmd /c "timeout /t 5 >nul & start http://localhost:8501"
".venv\Scripts\python.exe" -m streamlit run painel.py --server.headless true
pause
