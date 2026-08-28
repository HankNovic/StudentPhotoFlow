@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "APP_EXE=%~dp0PhotoExporter.exe"

rem StudentPhotoFlow is distributed only as a complete portable package.
if not exist "%APP_EXE%" goto PORTABLE_BROKEN

:CHECK_PORTABLE
if not exist "%~dp0_internal" goto PORTABLE_BROKEN
if not exist "%~dp0_internal\python312.dll" goto PORTABLE_BROKEN
if not exist "%~dp0_internal\_tkinter.pyd" goto PORTABLE_BROKEN
if not exist "%~dp0_internal\models\u2netp\u2netp.onnx" goto PORTABLE_BROKEN
if not exist "%~dp0_internal\models\yunet\face_detection_yunet_2023mar.onnx" goto PORTABLE_BROKEN
start "" /wait "%APP_EXE%" %*
set "APP_CODE=%ERRORLEVEL%"
if "%APP_CODE%"=="0" exit /b 0
goto RUN_FAILED

:PORTABLE_BROKEN
echo.
echo ERROR: The portable package is incomplete.
echo Extract the entire ZIP and keep PhotoExporter.exe, this BAT, and _internal together.
pause
exit /b 2

:RUN_FAILED
echo.
echo ERROR: The application exited with code %APP_CODE%.
echo Check the error dialog and README.md for details.
pause
exit /b %APP_CODE%
