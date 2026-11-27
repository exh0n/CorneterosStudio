# Corneteros Studio

Aplicación de Windows para buscar y descargar audio, con editor y efectos de sonido. También incluye un paquete portable para Winlator.

## Compilar Windows y Winlator

1. Instalá Python 3.14 x64, Inno Setup 6.6 o posterior y FFmpeg.
2. Copiá `ffmpeg.exe`, `ffplay.exe` y `ffprobe.exe` dentro de `ffmpeg/bin/`.
3. Ejecutá `compilar.bat` desde esta carpeta.

El proceso genera el instalador de Windows, `CorneterosStudio_Winlator_x64.zip` y `latest.json`. El instalador usa la interfaz oscura y negra de Corneteros Studio.

## Publicar actualizaciones

Abrí `gestor_actualizaciones.py` con el Python del entorno `.venv-winlator` (por ejemplo, `.venv-winlator\Scripts\python.exe gestor_actualizaciones.py`). La herramienta sincroniza la versión del cliente y el instalador, compila ambos paquetes, sube el código fuente y publica los archivos como un GitHub Release. El token se escribe en la ventana y no se guarda en disco.

Para el token, creá un Fine-grained personal access token limitado a `exh0n/CorneterosStudio` con permiso `Contents: Read and write`. La carga inicial del código y cada publicación se confirman desde la ventana antes de enviarse a GitHub.

Cuando se publica una versión, el cliente Windows descarga el instalador; Winlator descarga y aplica el ZIP portable. Ambos verifican SHA-256 antes de instalar. Para recibir actualizaciones automáticas, los clientes necesitan la versión 2.2.0 o posterior; las instalaciones Winlator anteriores deben instalar una vez el nuevo ZIP manualmente.

## Archivos que no se suben al repositorio

Los paquetes compilados y FFmpeg se excluyen del commit fuente. El gestor los sube como archivos del Release, y el manifiesto `latest.json` lleva los enlaces y hashes para la actualización automática.
