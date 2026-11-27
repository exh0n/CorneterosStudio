"""Genera el latest.json que se adjunta a cada GitHub Release."""

import hashlib
import json
import os
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "descargador2.py"
OUTPUT = ROOT / "latest.json"
REPOSITORY = "exh0n/CorneterosStudio"


def current_version():
    source = SOURCE.read_text(encoding="utf-8")
    match = re.search(r'^APP_VERSION\s*=\s*"([0-9]+(?:\.[0-9]+){1,3})"', source, re.MULTILINE)
    if not match:
        raise SystemExit("No se pudo leer APP_VERSION de descargador2.py.")
    return match.group(1)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    version = current_version()
    installer = Path(sys.argv[1]) if len(sys.argv) > 1 else (
        ROOT / "installer" / f"Instalador_Corneteros_Studio_{version}.exe"
    )
    if not installer.is_absolute():
        installer = ROOT / installer
    installer = installer.resolve()
    if not installer.is_file():
        raise SystemExit(f"No se encontró el instalador: {installer}")
    winlator = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "CorneterosStudio_Winlator_x64.zip"
    if not winlator.is_absolute():
        winlator = ROOT / winlator
    winlator = winlator.resolve()
    if not winlator.is_file():
        raise SystemExit(f"No se encontró el paquete Winlator: {winlator}")

    installer_hash = sha256(installer)
    manifest = {
        "version": version,
        "installer_url": (
            f"https://github.com/{REPOSITORY}/releases/download/v{version}/"
            f"Instalador_Corneteros_Studio_{version}.exe"
        ),
        "installer_sha256": installer_hash,
        "sha256": installer_hash,
        "winlator_url": (
            f"https://github.com/{REPOSITORY}/releases/download/v{version}/"
            "CorneterosStudio_Winlator_x64.zip"
        ),
        "winlator_sha256": sha256(winlator),
        "notes": os.environ.get("RELEASE_NOTES", "Actualización de Corneteros Studio.").strip(),
    }
    OUTPUT.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Manifiesto listo para GitHub Releases: {OUTPUT}")
    print(f"SHA-256 instalador: {manifest['installer_sha256']}")
    print(f"SHA-256 Winlator: {manifest['winlator_sha256']}")


if __name__ == "__main__":
    main()
