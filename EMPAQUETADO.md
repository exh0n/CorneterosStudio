# Compilar e instalar Corneteros Studio

Esta carpeta incluye `descargador2.py` y los recursos visuales de la app. Conserva `assets/corneteros_cs_source.png`, que es el original del monograma CS.

1. Instala Python 3.14 de 64 bits e Inno Setup en la computadora de desarrollo. El compilado queda fijado en Python 3.14 para funcionar en Wine/Winlator: Python 3.15 usa `%OB/%Ob` al cargar `calendar`, y Wine todavía devuelve `ValueError: Invalid format string`.
2. Ejecuta `compilar.bat`. El script prepara FFmpeg, instala las dependencias, genera los logos, los iconos y las imágenes del asistente, prepara `dist\Descargador2`, crea el ZIP de Winlator y compila el instalador de Windows si encuentra Inno Setup.
3. Para Winlator, extrae el ZIP completo en una carpeta y abre `Descargador2.exe`; conserva juntos el ejecutable, `_internal` y `ffmpeg`. El ZIP es portátil y se actualiza reemplazándolo por el nuevo ZIP. Para Windows, entrega `installer\Instalador_Corneteros_Studio_2.1.0.exe`. Incluye el monograma CS en el icono y en el asistente.

`logo_cs_fondo_negro.png` usa fondo negro y `logo_cs_transparente.png` conserva transparencia. `app_icon.png` y `app.ico` adaptan el monograma a un icono circular en escala de grises.

El programa busca primero las herramientas junto al ejecutable, bajo `ffmpeg\bin`, y después en el `PATH` de Windows. No necesita que cada usuario instale FFmpeg por separado.

## Actualizaciones de Windows

El cliente consulta el manifiesto al abrirse y cada 10 minutos mientras esté abierto. Cuando aparece una versión nueva ofrece instalarla, muestra el progreso de descarga, comprueba el SHA-256, cierra la app, inicia el instalador y vuelve a abrirla. Windows puede mostrar el permiso de administrador del instalador. En Wine/Winlator este actualizador se desactiva: allí se distribuye un ZIP portátil nuevo.

El cliente ya apunta al manifiesto estable de GitHub Releases: `https://github.com/exh0n/CorneterosStudio/releases/latest/download/latest.json`. Para cada versión subí el instalador y un archivo `latest.json` como assets de un Release público. El JSON debe tener este formato:

```json
{
  "version": "2.2.0",
  "installer_url": "https://github.com/exh0n/CorneterosStudio/releases/download/v2.2.0/Instalador_Corneteros_Studio_2.2.0.exe",
  "sha256": "64-caracteres-hexadecimales-del-instalador",
  "notes": "Novedades de esta versión"
}
```

`compilar.bat` deja `latest.json` listo con el hash del instalador. Para cada versión nueva, actualiza `APP_VERSION` en `descargador2.py` y `AppVersion` en `Descargador.iss`, compila, crea el Release con etiqueta `v<versión>` y adjunta el instalador y `latest.json`. El repositorio y sus Releases deben ser públicos para que la app pueda consultar y descargar versiones sin pedir credenciales. Las instalaciones anteriores a 2.1.0 necesitan una instalación manual inicial; después el cliente puede buscar versiones nuevas.

Las claves actualmente se validan contra una lista publicada en Pastebin y el vínculo con el equipo se guarda localmente. Ese diseño evita que una MAC cambiante cause falsos bloqueos después de reiniciar, pero no puede imponer una activación única en varios equipos: para eso hace falta un servicio de licencias que guarde el vínculo en el servidor.
