@echo off
REM ============================================================
REM  PDF Translate - one-click packaging script (Windows)
REM  Double-click this file. It builds a single-file .exe with
REM  PyInstaller and copies it to your Desktop.
REM ============================================================
chcp 65001 >nul
cd /d "%~dp0"

echo [1/4] Activating virtual environment...
if exist ".venv\Scripts\activate.bat" (
    call ".venv\Scripts\activate.bat"
) else (
    echo   .venv not found, using system Python.
)

echo [2/4] Installing PyInstaller (and deps)...
python -m pip install --upgrade pyinstaller >nul
python -m pip install -r requirements.txt >nul

echo [3/4] Building PDFTranslate.exe (this can take a few minutes)...
python -m PyInstaller --noconfirm --onefile --windowed ^
    --name "PDFTranslate" ^
    --collect-all keyring ^
    main.py
if errorlevel 1 (
    echo.
    echo  BUILD FAILED. Scroll up for the error and send it to me.
    pause
    exit /b 1
)

echo [4/4] Copying to Desktop...
set "DESK=%USERPROFILE%\Desktop"
if exist "%ONEDRIVE%\Desktop" set "DESK=%ONEDRIVE%\Desktop"
copy /Y "dist\PDFTranslate.exe" "%DESK%\PDFTranslate.exe" >nul
if errorlevel 1 (
    echo   Could not copy to Desktop. The exe is in the "dist" folder.
) else (
    echo   Done!  ->  %DESK%\PDFTranslate.exe
)

echo.
echo  First run: open the app, go to Settings, enter your DeepSeek API key.
echo  (cache.db and error.log are created next to the .exe.)
pause
