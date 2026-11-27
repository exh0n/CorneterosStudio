"""Extrae los recursos Tcl/Tk incluidos con Python 3.14 para PyInstaller."""

from pathlib import Path
import sys
import zipfile


ROOT = Path(__file__).resolve().parent
SOURCE = Path(sys.base_prefix) / "tcl"
DESTINATION = ROOT / "build_tcl_runtime"


def extract_library(archive_name, destination_name, required_file):
    archive = SOURCE / archive_name
    target = DESTINATION / destination_name
    if not archive.is_file():
        raise SystemExit(f"Falta el recurso Tcl/Tk de Python: {archive}")
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as package:
        root = target.resolve()
        for member in package.infolist():
            output = (target / member.filename).resolve()
            if output != root and root not in output.parents:
                raise SystemExit(f"Ruta inválida dentro del archivo Tcl/Tk: {member.filename}")
        package.extractall(target)
    library = target / required_file
    if not library.is_file():
        raise SystemExit(f"El recurso {archive_name} no contiene {required_file}.")
    return library.parent


def main():
    tcl_dir = extract_library("libtcl9.0.4.zip", "tcl", "tcl_library/init.tcl")
    tk_dir = extract_library("libtk9.0.4.zip", "tk", "tk_library/tk.tcl")
    print(f"TCL_LIBRARY={tcl_dir}")
    print(f"TK_LIBRARY={tk_dir}")


if __name__ == "__main__":
    main()
