@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto paketi
set "PY312=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if exist "%PY312%" goto neposredno
py -3.12 -V >nul 2>&1
if not errorlevel 1 goto zaganjalnik
echo Namescam Python 3.12 za poslusalca ...
winget install --id Python.Python.3.12 --exact --source winget --scope user --architecture x64 --accept-source-agreements --accept-package-agreements
if exist "%PY312%" goto neposredno
py -3.12 -V >nul 2>&1
if errorlevel 1 goto python_error
:zaganjalnik
py -3.12 -m venv .venv
goto preveri
:neposredno
"%PY312%" -m venv .venv
:preveri
if errorlevel 1 goto konec
:paketi
".venv\Scripts\python.exe" -c "from faster_whisper import WhisperModel; import av; assert av.__version__ == '16.1.0'" >nul 2>&1
if errorlevel 1 (
    echo Namescam knjiznice. Prvi zagon potrebuje internet.
    ".venv\Scripts\python.exe" -m pip install faster-whisper==1.2.1 av==16.1.0
    if errorlevel 1 goto konec
)
".venv\Scripts\python.exe" poslusalec.py
goto konec
:python_error
echo Namesti 64-bitni Python 3.12 s python.org in ponovno odpri ZAZENI.
:konec
pause
