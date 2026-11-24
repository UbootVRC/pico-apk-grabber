@echo off
rem Builds dist\PicoApkGrabber.exe (same settings as auto-py-to-exe-config.json)
cd /d "%~dp0"
py -m pip install --upgrade pyinstaller || goto :err
py -m PyInstaller --noconfirm --onefile --windowed --name PicoApkGrabber pico_apk_grabber.py || goto :err
echo.
echo Done: %~dp0dist\PicoApkGrabber.exe
pause
exit /b 0
:err
echo Build failed. Is Python 3.10+ installed from python.org (with the "py" launcher)?
pause
exit /b 1
