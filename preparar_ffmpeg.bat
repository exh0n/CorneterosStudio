@echo off
setlocal
cd /d "%~dp0"

echo Descargando el paquete Essentials de FFmpeg para incluirlo en el instalador...
where curl.exe >nul 2>nul
if errorlevel 1 (
  echo No se encontro curl.exe. Instala las actualizaciones de Windows o prepara FFmpeg manualmente.
  exit /b 1
)
curl.exe -L --fail --retry 8 --retry-all-errors --retry-delay 2 --continue-at - "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" -o "%TEMP%\descargador-ffmpeg-essentials.zip"
if errorlevel 1 (
  echo No se pudo descargar FFmpeg. Revisa tu conexion y vuelve a intentarlo.
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $zip=Join-Path $env:TEMP 'descargador-ffmpeg-essentials.zip'; $unpack=Join-Path $env:TEMP ('descargador-ffmpeg-' + [guid]::NewGuid().ToString('N')); Expand-Archive -LiteralPath $zip -DestinationPath $unpack -Force; $source=Get-ChildItem -LiteralPath $unpack -Directory | Select-Object -First 1; if (-not $source) { throw 'No se encontró la carpeta de FFmpeg extraída.' }; New-Item -ItemType Directory -Force -Path 'ffmpeg' | Out-Null; Copy-Item -Path (Join-Path $source.FullName '*') -Destination 'ffmpeg' -Recurse -Force; if (-not (Test-Path 'ffmpeg\bin\ffmpeg.exe')) { throw 'No se encontró ffmpeg.exe después de extraer el paquete.' }; Remove-Item -LiteralPath $unpack -Recurse -Force; Remove-Item -LiteralPath $zip -Force"
if errorlevel 1 (
  echo No se pudo descargar o preparar FFmpeg. Revisa tu conexion y vuelve a intentarlo.
  exit /b 1
)

echo FFmpeg quedo preparado en ffmpeg\bin.
exit /b 0
