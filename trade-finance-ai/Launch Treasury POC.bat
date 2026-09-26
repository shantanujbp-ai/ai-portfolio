@echo off
REM Double-click this file to launch the Treasury & Trade POC chat window.
REM It activates the project's virtual environment and starts Streamlit
REM from whatever folder this .bat file itself lives in.

cd /d "%~dp0"

if not exist "venv\Scripts\activate.bat" (
    echo Could not find the virtual environment ^(venv\^) in this folder.
    echo Make sure this file stays inside the Treasury-Trade-POC folder,
    echo and that you've already run the setup steps in README.md.
    pause
    exit /b 1
)

call venv\Scripts\activate.bat
streamlit run app.py

REM If Streamlit exits or errors immediately, keep the window open so you
REM can actually read what happened instead of it vanishing.
pause
