@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "APP_EXE=%~dp0PhotoExporter.exe"
set "INSTALL_LOG=%~dp0dependency_install.log"

rem Portable mode: Python, GUI, OpenCV, and AI dependencies are bundled.
if exist "%APP_EXE%" goto CHECK_PORTABLE
goto SOURCE_MODE

:CHECK_PORTABLE
if not exist "%~dp0_internal" goto PORTABLE_BROKEN
if not exist "%~dp0_internal\python312.dll" goto PORTABLE_BROKEN
if not exist "%~dp0_internal\_tkinter.pyd" goto PORTABLE_BROKEN
if not exist "%~dp0_internal\models\u2netp\u2netp.onnx" (
  echo WARNING: The bundled AI model is missing.
  echo Original export, quick background replacement, and face detection are still available.
)
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

:SOURCE_MODE
rem Source mode: detect Python and install all base/AI dependencies when missing.
where py >nul 2>nul
if not errorlevel 1 (
  set "PYTHON_CMD=py -3"
  goto CHECK_SOURCE_FILES
)
where python >nul 2>nul
if not errorlevel 1 (
  set "PYTHON_CMD=python"
  goto CHECK_SOURCE_FILES
)
echo.
echo ERROR: No portable runtime or system Python was found.
echo Please use the complete portable ZIP package.
pause
exit /b 3

:CHECK_SOURCE_FILES
if not exist "%~dp0photo_exporter.py" (
  echo ERROR: photo_exporter.py is missing.
  pause
  exit /b 4
)
%PYTHON_CMD% -c "import PIL,numpy,cv2,rembg,onnxruntime" >nul 2>nul
if not errorlevel 1 goto RUN_SOURCE
echo.
echo First run: installing image and AI dependencies. Keep the network connected.
echo Installation log: %INSTALL_LOG%
%PYTHON_CMD% -m pip install -r "%~dp0requirements-ai.txt" >>"%INSTALL_LOG%" 2>&1
if errorlevel 1 (
  echo.
  echo ERROR: Automatic dependency installation failed. See dependency_install.log.
  pause
  exit /b 5
)

:RUN_SOURCE
%PYTHON_CMD% "%~dp0photo_exporter.py" %*
set "APP_CODE=%ERRORLEVEL%"
if "%APP_CODE%"=="0" exit /b 0

:RUN_FAILED
echo.
echo ERROR: The application exited with code %APP_CODE%.
echo Check the error dialog and README.md for details.
pause
exit /b %APP_CODE%
