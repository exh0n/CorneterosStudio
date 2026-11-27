"""Helper separado para instalar Windows o reemplazar el paquete Winlator."""

import argparse
import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import tkinter as tk
import zipfile
from pathlib import Path, PurePosixPath
from tkinter import ttk


APP_NAME = "Corneteros Studio"
BLACK = "#000000"
PANEL = "#111114"
WHITE = "#f4f4f5"
MUTED = "#a1a1aa"
ACCENT = "#5b8cff"
MAX_ARCHIVE_BYTES = 750 * 1024 * 1024
MAX_UNPACKED_BYTES = 2 * 1024 * 1024 * 1024


def wait_for_process(process_id):
    if os.name != "nt":
        return
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(0x00100000, False, int(process_id))
    if not handle:
        return
    try:
        kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)
    finally:
        kernel32.CloseHandle(handle)


class UpdateWindow:
    def __init__(self, title, version=""):
        self.root = tk.Tk()
        self.root.title(title)
        self.root.configure(bg=BLACK)
        self.root.geometry("480x220")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", lambda: None)
        try:
            self.root.iconbitmap(str(resource_path("app.ico")))
        except (OSError, tk.TclError):
            pass

        outer = tk.Frame(self.root, bg=BLACK)
        outer.pack(fill="both", expand=True, padx=24, pady=22)
        header = tk.Frame(outer, bg=BLACK)
        header.pack(fill="x")
        try:
            self.logo = tk.PhotoImage(file=str(resource_path("logo_cs_fondo_negro.png")))
            ratio = max(1, self.logo.width() // 54)
            self.logo = self.logo.subsample(ratio, ratio)
            tk.Label(header, image=self.logo, bg=BLACK).pack(side="left", padx=(0, 12))
        except (OSError, tk.TclError):
            self.logo = None
        label = f"{APP_NAME}  ·  {version}" if version else APP_NAME
        tk.Label(
            header, text=label, bg=BLACK, fg=WHITE,
            font=("Segoe UI", 14, "bold"), anchor="w"
        ).pack(side="left", fill="x", expand=True)

        self.status = tk.Label(
            outer, text="Preparando la actualización…", bg=BLACK, fg=WHITE,
            font=("Segoe UI", 10), anchor="w"
        )
        self.status.pack(fill="x", pady=(18, 9))
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Update.Horizontal.TProgressbar", troughcolor=PANEL,
            background=ACCENT, bordercolor=PANEL, lightcolor=ACCENT,
            darkcolor=ACCENT, thickness=12
        )
        self.progress = ttk.Progressbar(
            outer, mode="determinate", maximum=100,
            style="Update.Horizontal.TProgressbar"
        )
        self.progress.pack(fill="x")
        self.detail = tk.Label(
            outer, text="No cierres la aplicación mientras se instala.",
            bg=BLACK, fg=MUTED, font=("Segoe UI", 9), anchor="w"
        )
        self.detail.pack(fill="x", pady=(10, 0))
        self.error_button = tk.Button(
            outer, text="Cerrar", command=self.root.destroy,
            bg="#242428", fg=WHITE, activebackground="#34343a",
            activeforeground=WHITE, relief="flat", padx=18, pady=5
        )

    def update(self, message, fraction=None, detail=None):
        def apply():
            if not self.root.winfo_exists():
                return
            self.status.configure(text=message)
            if fraction is not None:
                self.progress.configure(value=max(0, min(100, fraction * 100)))
            if detail:
                self.detail.configure(text=detail)
        self.root.after(0, apply)

    def fail(self, message):
        def apply():
            if not self.root.winfo_exists():
                return
            self.status.configure(text="No se pudo completar la actualización.")
            self.detail.configure(text=str(message)[:260], fg="#ff8585")
            self.error_button.pack(anchor="e", pady=(16, 0))
        self.root.after(0, apply)

    def finish(self):
        self.root.after(0, self.root.destroy)

    def run(self):
        self.root.mainloop()


def resource_path(name):
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / name


def safe_relative_path(member_name):
    normalized = str(member_name).replace("\\", "/")
    relative = PurePosixPath(normalized)
    if (not normalized or relative.is_absolute() or ".." in relative.parts
            or any(":" in part for part in relative.parts)):
        raise ValueError(f"Ruta no válida dentro del ZIP: {member_name}")
    return Path(*relative.parts)


def apply_archive(archive_path, app_path, window):
    app_dir = Path(app_path).resolve().parent
    if Path(archive_path).stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("El paquete supera el tamaño máximo permitido.")

    staging_root = Path(tempfile.mkdtemp(prefix=".corneteros_update_", dir=app_dir))
    staged = staging_root / "new"
    backup = staging_root / "backup"
    staged.mkdir()
    replaced = []
    backed_up = []
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            members = [item for item in archive.infolist() if not item.is_dir()]
            total_bytes = sum(item.file_size for item in members)
            if total_bytes > MAX_UNPACKED_BYTES:
                raise ValueError("El paquete descomprimido supera el límite de seguridad.")
            copied = 0
            for number, item in enumerate(members, 1):
                relative = safe_relative_path(item.filename)
                if (item.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError("El ZIP incluye un enlace simbólico que no se permite.")
                target = staged / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(item, "r") as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)
                copied += item.file_size
                window.update(
                    "Preparando archivos de Winlator…",
                    copied / max(total_bytes, 1),
                    f"{number}/{len(members)} archivos preparados",
                )

        files = [path for path in staged.rglob("*") if path.is_file()]
        total_files = len(files)
        for number, source in enumerate(files, 1):
            relative = source.relative_to(staged)
            # El helper se mantiene vivo hasta que termine esta rutina.
            if relative.name.casefold() == "corneterosupdater.exe":
                continue
            target = app_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                old = backup / relative
                old.parent.mkdir(parents=True, exist_ok=True)
                os.replace(target, old)
                backed_up.append((old, target))
            try:
                os.replace(source, target)
                replaced.append(target)
            except Exception:
                if backed_up and backed_up[-1][1] == target:
                    old, destination = backed_up.pop()
                    os.replace(old, destination)
                raise
            window.update(
                "Instalando la actualización de Winlator…",
                number / max(total_files, 1),
                f"{number}/{total_files} archivos instalados",
            )
    except Exception:
        for target in reversed(replaced):
            try:
                if target.is_file():
                    target.unlink()
            except OSError:
                pass
        for old, target in reversed(backed_up):
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                if old.exists():
                    os.replace(old, target)
            except OSError:
                pass
        raise
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


def install_windows(installer_path):
    from ctypes import wintypes

    class ShellExecuteInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG),
            ("hwnd", wintypes.HWND), ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE), ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    info = ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x00000040
    info.lpVerb = "runas"
    info.lpFile = installer_path
    info.lpParameters = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-"
    info.nShow = 0
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(ShellExecuteInfo)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    try:
        kernel32.WaitForSingleObject(info.hProcess, 0xFFFFFFFF)
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(exit_code)):
            raise ctypes.WinError(ctypes.get_last_error())
        if exit_code.value != 0:
            raise RuntimeError(f"El instalador terminó con código {exit_code.value}.")
    finally:
        kernel32.CloseHandle(info.hProcess)


def main():
    parser = argparse.ArgumentParser(add_help=False)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--installer", dest="action", action="store_const", const="installer")
    action.add_argument("--archive", dest="action", action="store_const", const="archive")
    parser.add_argument("package")
    parser.add_argument("parent_pid", type=int)
    parser.add_argument("app_path")
    args = parser.parse_args()

    window = UpdateWindow(
        "Actualización de Corneteros Studio",
        "Winlator" if args.action == "archive" else "Windows",
    )

    def worker():
        try:
            window.update("Esperando que se cierre la aplicación…")
            wait_for_process(args.parent_pid)
            if args.action == "installer":
                window.update("Instalando la versión nueva…", 0.15)
                install_windows(args.package)
            else:
                apply_archive(args.package, args.app_path, window)
            window.update("Actualización completada. Abriendo Corneteros Studio…", 1.0)
            try:
                subprocess.Popen([args.app_path], cwd=os.path.dirname(args.app_path), close_fds=True)
            except OSError:
                pass
            try:
                os.remove(args.package)
            except OSError:
                pass
            window.finish()
        except Exception as exc:
            try:
                subprocess.Popen([args.app_path], cwd=os.path.dirname(args.app_path), close_fds=True)
            except OSError:
                pass
            window.fail(exc)

    import threading
    threading.Thread(target=worker, name="update-apply", daemon=True).start()
    window.run()


if __name__ == "__main__":
    main()
