"""Crea el paquete portable con poca memoria y compresión rápida."""

import os
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "dist" / "Descargador2"
OUTPUT = ROOT / "CorneterosStudio_Winlator_x64.zip"
TEMP_OUTPUT = OUTPUT.with_suffix(".zip.tmp")


def main():
    if not SOURCE.is_dir():
        raise SystemExit(f"No se encontró el cliente Winlator: {SOURCE}")
    files = sorted(path for path in SOURCE.rglob("*") if path.is_file())
    if not files:
        raise SystemExit("El cliente Winlator no contiene archivos para empaquetar.")
    try:
        with zipfile.ZipFile(
            TEMP_OUTPUT, "w", compression=zipfile.ZIP_DEFLATED,
            compresslevel=1, allowZip64=True
        ) as archive:
            for path in files:
                archive.write(path, path.relative_to(SOURCE).as_posix())
        os.replace(TEMP_OUTPUT, OUTPUT)
    except Exception:
        try:
            TEMP_OUTPUT.unlink()
        except OSError:
            pass
        raise
    size_mib = OUTPUT.stat().st_size / (1024 * 1024)
    print(f"Paquete Winlator listo: {OUTPUT} ({size_mib:.1f} MiB, {len(files)} archivos)")


if __name__ == "__main__":
    main()
