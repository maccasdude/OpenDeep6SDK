@echo off
rem d6edit on Windows: creates a Python environment in .venv on the first run.
rem Needs Python 3.10 or newer from python.org (with "Add to PATH").
setlocal
set HERE=%~dp0
if not exist "%HERE%.venv\Scripts\python.exe" (
    echo Setting up the Python environment, this takes a minute...
    py -3 -m venv "%HERE%.venv" || python -m venv "%HERE%.venv" || goto :nopython
    "%HERE%.venv\Scripts\python.exe" -m pip install --upgrade pip
    "%HERE%.venv\Scripts\python.exe" -m pip install -r "%HERE%requirements.txt" || goto :fail
)
"%HERE%.venv\Scripts\python.exe" "%HERE%editor\d6edit.py" %*
goto :eof
:nopython
echo Python 3 was not found. Install it from https://www.python.org/downloads/ and run d6edit.bat again.
pause
goto :eof
:fail
echo Installing the requirements failed (see above).
pause
