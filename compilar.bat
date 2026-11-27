@echo off
setlocal
cd /d "%~dp0"

if not exist "descargador2.py" (
  echo No se encontro descargador2.py en esta carpeta.
  pause
  exit /b 1
)

rem Python 3.15 usa un formato nuevo de meses al importar calendar.
rem Wine/Winlator no lo acepta y rompe el import de yt-dlp al iniciar la app.
rem Compilar con 3.14 evita ese fallo y mantiene compatibilidad con Wine.
set "PYTHON314="
where py >nul 2>nul
if not errorlevel 1 for /f "delims=" %%P in ('py -3.14 -c "import sys;print(sys.executable)" 2^>nul') do set "PYTHON314=%%P"
if not defined PYTHON314 if exist "%LocalAppData%\Programs\Python\Python314\python.exe" set "PYTHON314=%LocalAppData%\Programs\Python\Python314\python.exe"
if not defined PYTHON314 (
  echo Hace falta Python 3.14 de 64 bits para generar una version compatible con Winlator.
  echo Instala Python 3.14 x64 y vuelve a ejecutar este archivo.
  pause
  exit /b 1
)

"%PYTHON314%" -c "import struct,sys; raise SystemExit(0 if sys.version_info[:2] == (3,14) and struct.calcsize('P') == 8 else 1)" >nul 2>nul
if errorlevel 1 (
  echo Hace falta Python 3.14 de 64 bits para generar una version compatible con Winlator.
  echo Instala Python 3.14 x64 y vuelve a ejecutar este archivo.
  pause
  exit /b 1
)

if not exist "ffmpeg\bin\ffmpeg.exe" call preparar_ffmpeg.bat
if errorlevel 1 goto error

if not exist "ffmpeg\bin\ffmpeg.exe" (
  echo Falta ffmpeg\bin\ffmpeg.exe
  echo Copia dentro de ffmpeg\bin los archivos del directorio bin de FFmpeg:
  echo ffmpeg.exe, ffplay.exe, ffprobe.exe y los archivos de licencia.
  pause
  exit /b 1
)
if not exist "ffmpeg\bin\ffplay.exe" (
  echo Falta ffmpeg\bin\ffplay.exe
  pause
  exit /b 1
)
if not exist "ffmpeg\bin\ffprobe.exe" (
  echo Falta ffmpeg\bin\ffprobe.exe
  pause
  exit /b 1
)

if not exist ".venv-winlator\Scripts\python.exe" "%PYTHON314%" -m venv .venv-winlator
if errorlevel 1 goto error

".venv-winlator\Scripts\python.exe" -c "import struct,sys; raise SystemExit(0 if sys.version_info[:2] == (3,14) and struct.calcsize('P') == 8 else 1)" >nul 2>nul
if errorlevel 1 (
  echo El entorno .venv-winlator existente no usa Python 3.14 x64.
  echo Eliminalo y vuelve a ejecutar este archivo.
  pause
  exit /b 1
)

if "%SKIP_PIP_INSTALL%"=="1" (
  echo Usando las dependencias ya instaladas en .venv-winlator.
) else (
  ".venv-winlator\Scripts\python.exe" -m pip install --upgrade pip
  if errorlevel 1 goto error
  ".venv-winlator\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 goto error
)

".venv-winlator\Scripts\python.exe" preparar_logos.py
if errorlevel 1 goto error
".venv-winlator\Scripts\python.exe" preparar_tcl_runtime.py
if errorlevel 1 goto error
set "TCL_LIBRARY=%CD%\build_tcl_runtime\tcl\tcl_library"
set "TK_LIBRARY=%CD%\build_tcl_runtime\tk\tk_library"
".venv-winlator\Scripts\python.exe" -c "import tkinter; t=tkinter.Tcl(); assert t.eval('info library'); print('Tcl/Tk listo')"
if errorlevel 1 goto error

if "%SKIP_APP_BUILD%"=="1" (
  echo Reutilizando el cliente ya compilado.
) else (
  ".venv-winlator\Scripts\python.exe" -m PyInstaller --noconfirm --clean --windowed --onedir --name Descargador2 --icon app.ico --add-data "app.ico;." --add-data "app_icon.png;." --add-data "logo_cs_fondo_negro.png;." --add-data "logo_cs_transparente.png;." --add-data "update_config.json;." --collect-all customtkinter --collect-all yt_dlp --collect-all yt_dlp_ejs --collect-submodules sounddevice descargador2.py
  if errorlevel 1 goto error
)

if "%SKIP_UPDATER_BUILD%"=="1" (
  echo Reutilizando CorneterosUpdater.exe ya compilado.
) else (
  ".venv-winlator\Scripts\python.exe" -m PyInstaller --noconfirm --onefile --windowed --name CorneterosUpdater --icon "%CD%\app.ico" --add-data "%CD%\app.ico;." --add-data "%CD%\logo_cs_fondo_negro.png;." --distpath "build\updater_dist" --workpath "build\updater_work" --specpath "build\updater_spec" actualizador.py
  if errorlevel 1 goto error
)
copy /Y "build\updater_dist\CorneterosUpdater.exe" "dist\Descargador2\CorneterosUpdater.exe" >nul
if errorlevel 1 goto error

xcopy "ffmpeg" "dist\Descargador2\ffmpeg\" /E /I /Y >nul
if errorlevel 1 goto error
copy /Y "LEEME_Winlator.txt" "dist\Descargador2\LEEME_Winlator.txt" >nul
if errorlevel 1 goto error
".venv-winlator\Scripts\python.exe" preparar_winlator_zip.py
if errorlevel 1 goto error
set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC for /f "delims=" %%P in ('where ISCC.exe 2^>nul') do if not defined ISCC set "ISCC=%%P"
if defined ISCC (
  "%ISCC%" Descargador.iss
  if errorlevel 1 goto error
  ".venv-winlator\Scripts\python.exe" generar_manifest_release.py
  if errorlevel 1 goto error
) else (
  echo No se encontro Inno Setup; se omitio el instalador de Windows.
)

echo.
echo Compilacion lista: dist\Descargador2\Descargador2.exe
echo Paquete Winlator listo: CorneterosStudio_Winlator_x64.zip
if defined ISCC echo Instalador de Windows listo en la carpeta installer.
if defined ISCC echo latest.json contiene enlaces y SHA-256 para Windows y Winlator.
if not "%CI%"=="1" pause
exit /b 0

:error
echo.
echo Hubo un error al preparar o compilar la app.
if not "%CI%"=="1" pause
exit /b 1
