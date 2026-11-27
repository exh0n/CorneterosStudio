import os
import sys
import ctypes
import hashlib
import json
import uuid
import threading
import subprocess
import webbrowser
import urllib.request
import urllib.parse
import importlib.util
import concurrent.futures
import re
import time
import copy
import math
import tempfile
import shutil
import io
import zipfile
from functools import lru_cache
from tkinter import filedialog, messagebox
import tkinter as tk
import customtkinter as ctk
import yt_dlp

try:
    from PIL import Image
except ImportError:
    Image = None

try:
    import numpy as np
except ImportError:
    np = None

try:
    import soundfile as sf
except ImportError:
    sf = None

try:
    from scipy import signal
except ImportError:
    signal = None

try:
    import sounddevice as sd
except ImportError:
    sd = None

# Forzar consola a UTF-8 en Windows
if sys.platform == "win32" and sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (OSError, ValueError):
        pass

CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".buscador_mp3_config.json")
LICENSE_FILE = os.path.join(os.path.expanduser("~"), ".buscador_mp3_license.json")
HISTORY_FILE = os.path.join(os.path.expanduser("~"), ".buscador_mp3_history.json")
LIKES_FILE = os.path.join(os.path.expanduser("~"), ".buscador_mp3_likes.json")
PROJECT_AUTOSAVE_FILE = os.path.join(os.path.expanduser("~"), ".corneteros_studio_autosave.csp")
AUDIO_SETTINGS_FILE = os.path.join(os.path.expanduser("~"), ".corneteros_studio_audio.json")
APP_VERSION = "2.2.0"
UPDATE_CONFIG_FILE = "update_config.json"
DEFAULT_CARPETA = os.path.join(os.path.expanduser("~"), "Desktop", "Descargas MP3")

# Tu enlace RAW actualizado de Pastebin
URL_KEYS_ONLINE = "https://pastebin.com/raw/HRv2HZRi"


def _memoria_fisica_gib():
    """Devuelve (RAM total, RAM disponible) en GiB, o (None, None)."""
    try:
        if os.name == "nt":
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            estado = MEMORYSTATUSEX()
            estado.dwLength = ctypes.sizeof(estado)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            if kernel32.GlobalMemoryStatusEx(ctypes.byref(estado)):
                gib = float(1024 ** 3)
                return estado.ullTotalPhys / gib, estado.ullAvailPhys / gib
        else:
            paginas = os.sysconf("SC_PHYS_PAGES")
            pagina_bytes = os.sysconf("SC_PAGE_SIZE")
            disponibles = os.sysconf("SC_AVPHYS_PAGES")
            gib = float(1024 ** 3)
            return paginas * pagina_bytes / gib, disponibles * pagina_bytes / gib
    except (AttributeError, OSError, TypeError, ValueError):
        pass
    return None, None


def perfil_descarga_adaptativo():
    """Ajusta el paralelismo para evitar paginación en equipos con poca RAM."""
    total_gib, libre_gib = _memoria_fisica_gib()
    if total_gib is None:
        trabajadores, fragmentos = 2, 3
    elif libre_gib is not None and libre_gib < 0.9:
        trabajadores, fragmentos = 1, 2
    elif total_gib <= 5.5 or (libre_gib is not None and libre_gib < 1.7):
        trabajadores, fragmentos = 2, 3
    elif total_gib <= 8 or (libre_gib is not None and libre_gib < 3.0):
        trabajadores, fragmentos = 3, 4
    else:
        trabajadores, fragmentos = 5, 8
    nucleos = max(1, os.cpu_count() or 2)
    return min(trabajadores, nucleos), fragmentos


def formato_audio_descarga(kbps):
    """Evita transferir una fuente mucho más pesada que la calidad elegida."""
    try:
        calidad = int(kbps)
    except (TypeError, ValueError):
        return "bestaudio/best"
    if calidad >= 320:
        return "bestaudio/best"
    return f"bestaudio[abr<={calidad}]/bestaudio/best"


def _version_tuple(version):
    """Convierte una versión numérica en una tupla comparable."""
    parts = str(version).strip().split(".")
    if not parts or any(not part.isdigit() for part in parts):
        raise ValueError("La versión debe contener solo números separados por puntos.")
    return tuple(int(part) for part in parts) + (0,) * max(0, 4 - len(parts))


def _is_wine_runtime():
    """Evita ofrecer el instalador de Windows dentro de Wine/Winlator."""
    if os.name != "nt":
        return False
    try:
        return hasattr(ctypes.WinDLL("ntdll"), "wine_get_version")
    except (AttributeError, OSError):
        return False


def _wait_for_windows_process(process_id):
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(0x00100000, False, int(process_id))  # SYNCHRONIZE
    if not handle:
        return
    try:
        kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)
    finally:
        kernel32.CloseHandle(handle)


def _run_installer_elevated(installer_path):
    """Ejecuta Inno Setup y espera su resultado, respetando el aviso de Windows."""
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
    info.fMask = 0x00000040  # SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = installer_path
    info.lpParameters = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-"
    info.nShow = 0
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(ShellExecuteInfo)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        error = ctypes.get_last_error()
        if error == 1223:  # ERROR_CANCELLED
            return 1223
        raise ctypes.WinError(error)
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.WaitForSingleObject(info.hProcess, 0xFFFFFFFF)
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(exit_code)):
            raise ctypes.WinError(ctypes.get_last_error())
        return int(exit_code.value)
    finally:
        ctypes.WinDLL("kernel32").CloseHandle(info.hProcess)


def _finish_update_helper(installer_path, parent_pid, app_path):
    """Modo auxiliar: espera a que cierre la app, instala y la vuelve a abrir."""
    if os.name != "nt":
        return 2
    _wait_for_windows_process(parent_pid)
    result = 1
    try:
        result = _run_installer_elevated(installer_path)
    except Exception as exc:
        try:
            ctypes.windll.user32.MessageBoxW(
                None, f"No se pudo instalar la actualización.\n{exc}",
                "Corneteros Studio", 0x10
            )
        except Exception:
            pass
    finally:
        try:
            if os.path.isfile(app_path):
                subprocess.Popen([app_path], close_fds=True)
        except OSError:
            pass
        try:
            os.remove(installer_path)
        except OSError:
            pass
    return 0 if result == 0 else 1

def obtener_id_hardware():
    # La MAC elegida por uuid.getnode() puede variar si Windows cambia el
    # adaptador de red preferido. MachineGuid se mantiene después de reiniciar.
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Cryptography"
            ) as clave:
                machine_guid = str(winreg.QueryValueEx(clave, "MachineGuid")[0]).strip().lower()
            if machine_guid:
                return f"windows:{machine_guid}"
        except (OSError, ImportError, ValueError):
            pass
    try:
        return f"legacy-mac:{uuid.getnode()}"
    except Exception:
        return "pc_desconocida"


def localizar_programa(nombre):
    """Busca una herramienta incluida con la app y luego en el PATH del sistema."""
    nombre_exe = nombre if nombre.lower().endswith(".exe") else f"{nombre}.exe"
    bases = []
    if getattr(sys, "_MEIPASS", None):
        bases.append(sys._MEIPASS)
    if getattr(sys, "frozen", False):
        bases.append(os.path.dirname(os.path.abspath(sys.executable)))
    bases.append(os.path.dirname(os.path.abspath(__file__)))
    for base in dict.fromkeys(bases):
        for carpeta in (base, os.path.join(base, "ffmpeg"),
                        os.path.join(base, "ffmpeg", "bin"),
                        os.path.join(base, "bin")):
            candidato = os.path.join(carpeta, nombre_exe)
            if os.path.isfile(candidato):
                return candidato
    return shutil.which(nombre) or shutil.which(nombre_exe)


def ruta_recurso(nombre):
    """Devuelve rutas tanto desde Python como desde el bundle de PyInstaller."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, nombre)


def cargar_logo_ui(tamano=40):
    if Image is None:
        return None
    ruta = ruta_recurso("app_icon.png")
    if not os.path.isfile(ruta):
        return None
    try:
        with Image.open(ruta) as imagen:
            logo = imagen.convert("RGBA")
        return ctk.CTkImage(light_image=logo, dark_image=logo, size=(tamano, tamano))
    except (OSError, RuntimeError):
        return None


def aplicar_icono_ventana(ventana):
    ruta = ruta_recurso("app.ico")
    if os.path.isfile(ruta):
        try:
            ventana.iconbitmap(default=ruta)
        except (tk.TclError, OSError):
            pass


def opciones_subproceso_oculto():
    """Evita que Windows abra una ventana de consola para FFmpeg/FFplay."""
    if os.name != "nt":
        return {}
    inicio = subprocess.STARTUPINFO()
    inicio.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    inicio.wShowWindow = subprocess.SW_HIDE
    return {
        "startupinfo": inicio,
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
    }

def cargar_configuracion():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                ruta = data.get("carpeta_destino")
                if ruta:
                    return ruta
        except Exception:
            pass
    return DEFAULT_CARPETA

def guardar_configuracion(ruta):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({"carpeta_destino": ruta}, f, indent=4)
    except Exception as e:
        print(f"Error guardando config: {e}")


def agregar_runtime_ytdlp(opciones):
    """Activa un runtime JS disponible y el solver EJS oficial de yt-dlp."""
    ffmpeg = localizar_programa("ffmpeg")
    if ffmpeg:
        opciones["ffmpeg_location"] = ffmpeg
    runtime = None
    deno = shutil.which("deno")
    node = shutil.which("node")
    if deno:
        # Deno es el runtime predeterminado de yt-dlp.
        runtime = None
    elif node:
        # Node debe habilitarse explícitamente; yt-dlp exige Node 22 o posterior.
        runtime = {"node": {"path": node}}
    else:
        qjs = shutil.which("qjs")
        if qjs:
            runtime = {"quickjs": {"path": qjs}}

    if runtime:
        opciones["js_runtimes"] = runtime

    if runtime or deno:
        try:
            ejs_instalado = importlib.util.find_spec("yt_dlp_ejs") is not None
        except (ImportError, ValueError):
            ejs_instalado = False
        if not ejs_instalado:
            # Scripts oficiales de resolución de desafíos JS, descargados por yt-dlp.
            opciones["remote_components"] = ["ejs:github"]
    return opciones

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("dark-blue")
_CTK_FONT_BASE = ctk.CTkFont


def fuente_ui(*args, **kwargs):
    """Usa Segoe UI y evita que las etiquetas secundarias queden diminutas."""
    kwargs.setdefault("family", "Segoe UI")
    size = kwargs.get("size")
    if isinstance(size, (int, float)) and 0 < size < 12:
        kwargs["size"] = int(round(size + 1))
    return _CTK_FONT_BASE(*args, **kwargs)


def aplicar_tema_grafito():
    """Neutraliza los controles CustomTkinter que usan colores por defecto."""
    paleta = {
        "CTk": {"fg_color": ["#f2f2f3", "#0a0a0b"]},
        "CTkToplevel": {"fg_color": ["#f2f2f3", "#0a0a0b"]},
        "CTkFrame": {
            "fg_color": ["#eeeeef", "#17171a"],
            "border_color": ["#d0d0d2", "#303034"],
        },
        "CTkLabel": {"text_color": ["#17171a", "#f4f4f5"]},
        "CTkButton": {
            "fg_color": ["#505057", "#37373d"],
            "hover_color": ["#414147", "#494950"],
            "border_color": ["#45454b", "#45454b"],
            "text_color": ["#ffffff", "#f4f4f5"],
        },
        "CTkEntry": {
            "fg_color": ["#ffffff", "#101012"],
            "border_color": ["#b8b8bc", "#404045"],
            "text_color": ["#111111", "#f4f4f5"],
            "placeholder_text_color": ["#77777c", "#88888e"],
        },
        "CTkTextbox": {
            "fg_color": ["#ffffff", "#101012"],
            "border_color": ["#b8b8bc", "#404045"],
            "text_color": ["#17171a", "#f4f4f5"],
            "scrollbar_button_color": ["#b0b0b4", "#414147"],
            "scrollbar_button_hover_color": ["#8e8e94", "#55555c"],
        },
        "CTkComboBox": {
            "fg_color": ["#ffffff", "#1b1b1f"],
            "border_color": ["#b8b8bc", "#404045"],
            "button_color": ["#d0d0d3", "#35353a"],
            "button_hover_color": ["#bcbcc0", "#494950"],
            "text_color": ["#111111", "#f4f4f5"],
        },
        "CTkOptionMenu": {
            "fg_color": ["#505057", "#37373d"],
            "button_color": ["#414147", "#2b2b30"],
            "button_hover_color": ["#35353a", "#494950"],
            "text_color": ["#ffffff", "#f4f4f5"],
        },
        "CTkSegmentedButton": {
            "fg_color": ["#dedee0", "#202024"],
            "selected_color": ["#45454b", "#45454b"],
            "selected_hover_color": ["#55555c", "#55555c"],
            "unselected_color": ["#dedee0", "#202024"],
            "unselected_hover_color": ["#cccccf", "#303034"],
            "text_color": ["#17171a", "#f4f4f5"],
        },
        "CTkTabview": {
            "fg_color": ["#eeeeef", "#111111"],
            "segmented_button_fg_color": ["#dedee0", "#202024"],
            "segmented_button_selected_color": ["#45454b", "#45454b"],
            "segmented_button_selected_hover_color": ["#55555c", "#55555c"],
        },
        "CTkScrollableFrame": {
            "fg_color": ["#eeeeef", "#111111"],
            "scrollbar_button_color": ["#b0b0b4", "#414147"],
            "scrollbar_button_hover_color": ["#8e8e94", "#55555c"],
        },
        "CTkCheckBox": {
            "fg_color": ["#68686f", "#494950"],
            "hover_color": ["#55555c", "#5b5b62"],
            "border_color": ["#88888e", "#68686f"],
        },
        "CTkRadioButton": {
            "fg_color": ["#68686f", "#494950"],
            "hover_color": ["#55555c", "#5b5b62"],
            "border_color": ["#88888e", "#68686f"],
        },
        "CTkSwitch": {
            "progress_color": ["#68686f", "#494950"],
            "button_color": ["#f4f4f5", "#eeeeef"],
            "button_hover_color": ["#dddddf", "#c9c9cd"],
        },
        "CTkSlider": {
            "progress_color": ["#68686f", "#494950"],
            "button_color": ["#68686f", "#68686f"],
            "button_hover_color": ["#55555c", "#7a7a82"],
        },
        "CTkProgressBar": {"progress_color": ["#68686f", "#494950"]},
        "CTkScrollbar": {
            "button_color": ["#b0b0b4", "#414147"],
            "button_hover_color": ["#8e8e94", "#55555c"],
        },
    }
    tema = getattr(ctk.ThemeManager, "theme", {})
    for componente, ajustes in paleta.items():
        if componente not in tema:
            continue
        for propiedad, colores in ajustes.items():
            if propiedad in tema[componente]:
                tema[componente][propiedad] = colores


aplicar_tema_grafito()

# --- VENTANA DE LOGIN OBLIGATORIA ---
class VentanaLogin(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Activación - Corneteros Studio")
        aplicar_icono_ventana(self)
        self.geometry("520x570")
        self.resizable(False, False)
        self.configure(fg_color="#0a0a0b")

        self.update_idletasks()
        x = (self.winfo_screenwidth() - self.winfo_width()) // 2
        y = (self.winfo_screenheight() - self.winfo_height()) // 2
        self.geometry(f"+{x}+{y}")

        # Tarjeta de activación con la misma paleta negra, gris y roja de la app.
        card = ctk.CTkFrame(
            self, fg_color="#18181b", corner_radius=16,
            border_width=1, border_color="#252525"
        )
        card.pack(fill="both", expand=True, padx=22, pady=22)

        brand = ctk.CTkFrame(card, fg_color="transparent")
        brand.pack(fill="x", padx=28, pady=(26, 20))
        emblem = ctk.CTkFrame(brand, width=48, height=48, fg_color="transparent", corner_radius=13)
        emblem.pack(side="left")
        emblem.pack_propagate(False)
        self.logo_login = cargar_logo_ui(46)
        if self.logo_login:
            ctk.CTkLabel(emblem, text="", image=self.logo_login, fg_color="transparent").pack(expand=True)
        else:
            ctk.CTkLabel(
                emblem, text="♪", font=fuente_ui(size=27, weight="bold"), text_color="#ffffff"
            ).pack(expand=True)
        brand_copy = ctk.CTkFrame(brand, fg_color="transparent")
        brand_copy.pack(side="left", padx=12)
        ctk.CTkLabel(
            brand_copy, text="CORNETEROS  /  STUDIO", anchor="w",
            font=fuente_ui(size=11, weight="bold"), text_color="#f4f4f5"
        ).pack(anchor="w")
        ctk.CTkLabel(
            brand_copy, text="BUSCADOR Y EDITOR DE AUDIO", anchor="w",
            font=fuente_ui(size=10), text_color="#f4f4f5"
        ).pack(anchor="w", pady=(3, 0))

        self.label_titulo = ctk.CTkLabel(
            card, text="Corneteros Studio",
            font=fuente_ui(size=27, weight="bold"), text_color="#ffffff"
        )
        self.label_titulo.pack(anchor="w", padx=30, pady=(2, 3))
        self.label_msg = ctk.CTkLabel(
            card, text="Descargá, mezclá y editá audio en un solo lugar.",
            font=fuente_ui(size=12), text_color="#f4f4f5", anchor="w"
        )
        self.label_msg.pack(anchor="w", padx=30, pady=(0, 21))

        ctk.CTkFrame(card, height=1, fg_color="#292929").pack(fill="x", padx=30, pady=(0, 20))
        ctk.CTkLabel(
            card, text="CLAVE DE ACCESO", font=fuente_ui(size=10, weight="bold"),
            text_color="#f4f4f5", anchor="w"
        ).pack(fill="x", padx=31, pady=(0, 7))
        self.entry_key = ctk.CTkEntry(
            card, placeholder_text="Pegá tu key acá", width=430, height=48,
            fg_color="#101012", border_color="#393939", border_width=1,
            text_color="#ffffff", placeholder_text_color="#a7a7ad",
            font=fuente_ui(size=14), corner_radius=10
        )
        self.entry_key.pack(fill="x", padx=30, pady=(0, 14))
        self.entry_key.bind("<Return>", lambda event: self.validar_key())
        self.entry_key.bind("<FocusIn>", lambda _event: self.entry_key.configure(border_color="#494950"))
        self.entry_key.bind("<FocusOut>", lambda _event: self.entry_key.configure(border_color="#393939"))

        self.btn_activar = ctk.CTkButton(
            card, text="Activar y entrar", height=46,
            fg_color="#494950", hover_color="#5b5b62",
            text_color="#ffffff", corner_radius=10,
            font=fuente_ui(size=13, weight="bold"),
            command=self.validar_key
        )
        self.btn_activar.pack(fill="x", padx=30, pady=(0, 13))

        self.label_estado = ctk.CTkLabel(
            card, text="●  Esperando activación",
            font=fuente_ui(size=11), text_color="#f4f4f5", anchor="w"
        )
        self.label_estado.pack(fill="x", padx=32, pady=(0, 15))

        contact = ctk.CTkFrame(card, fg_color="#171717", corner_radius=10)
        contact.pack(fill="x", padx=30, pady=(0, 13))
        ctk.CTkLabel(
            contact, text="¿Todavía no tenés una key?", font=fuente_ui(size=10),
            text_color="#f4f4f5"
        ).pack(anchor="w", padx=12, pady=(9, 0))
        ctk.CTkLabel(
            contact, text="Solicitá acceso por Discord  ·  exh0nukm",
            font=fuente_ui(size=11, weight="bold"), text_color="#f4f4f5"
        ).pack(anchor="w", padx=12, pady=(2, 9))

        ctk.CTkLabel(
            card, text="Licencia vinculada a este equipo",
            font=fuente_ui(size=9), text_color="#f4f4f5"
        ).pack(pady=(0, 10))
        self.entry_key.focus_set()

    def validar_key(self):
        key_ingresada = self.entry_key.get().strip()
        if not key_ingresada:
            self.entry_key.configure(border_color="#55555c")
            self.label_estado.configure(text="●  Pegá tu key para continuar.", text_color="#f4f4f5")
            return

        self.btn_activar.configure(state="disabled", text="Verificando...")
        self.entry_key.configure(border_color="#494950")
        self.label_estado.configure(text="●  Verificando la licencia…", text_color="#f4f4f5")

        threading.Thread(target=self._proceso_verificacion, args=(key_ingresada,), daemon=True).start()

    def _proceso_verificacion(self, key_ingresada):
        valida = False
        consulta_online_completada = False
        try:
            req = urllib.request.Request(
                URL_KEYS_ONLINE, 
                headers={'User-Agent': 'Mozilla/5.0'}
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                contenido = response.read().decode('utf-8')
                consulta_online_completada = True
                keys_permitidas = [line.strip().replace('\r', '') for line in contenido.splitlines() if line.strip()]
                
                if key_ingresada in keys_permitidas:
                    valida = True
        except Exception as e:
            print(f"Error conectando a Pastebin: {e}")

        # Si no hay conexión, permite revalidar una licencia ya activada en
        # este Windows. Si Pastebin respondió que la key no existe, no usa caché.
        if not valida and not consulta_online_completada:
            try:
                with open(LICENSE_FILE, "r", encoding="utf-8") as f:
                    licencia_local = json.load(f)
                if licencia_local.get("key") == key_ingresada:
                    id_guardado = licencia_local.get("machine_id")
                    valida = not id_guardado or id_guardado == obtener_id_hardware()
            except (OSError, ValueError, TypeError):
                pass

        # Respaldo de emergencia local
        if not valida and key_ingresada in ["EthanBajos2026", "GertrudisMix", "ParaVosRobaTemas"]:
            valida = True

        if valida:
            hwid_actual = obtener_id_hardware()
            pc_valida = True
            
            if os.path.exists(LICENSE_FILE):
                try:
                    with open(LICENSE_FILE, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if data.get("key") == key_ingresada:
                            id_guardado = data.get("machine_id")
                            if id_guardado:
                                pc_valida = id_guardado == hwid_actual
                            else:
                                # Migración desde versiones anteriores, que ataban
                                # la licencia a una MAC y podían cambiar tras reiniciar.
                                pc_valida = True
                except:
                    pass

            if pc_valida:
                try:
                    with open(LICENSE_FILE, "w", encoding="utf-8") as f:
                        json.dump({
                            "key": key_ingresada,
                            "hwid": hwid_actual,
                            "machine_id": hwid_actual,
                        }, f)
                except:
                    pass
                self.after(0, self.lanzar_app_principal)
            else:
                self.after(0, lambda: self.error_key("Esta Key ya fue activada en otro equipo."))
        else:
            self.after(0, lambda: self.error_key("No se pudo validar esa clave."))

    def error_key(self, mensaje):
        self.btn_activar.configure(state="normal", text="Activar y entrar")
        self.entry_key.configure(border_color="#55555c")
        self.label_estado.configure(text=f"●  {mensaje}", text_color="#f4f4f5")

    def lanzar_app_principal(self):
        self.withdraw()
        self._app_principal = AppMusic(self)


# --- APP PRINCIPAL ---
class AppMusic(ctk.CTkToplevel):
    def __init__(self, master):
        super().__init__(master)

        self.title("Corneteros Studio - Buscador y editor de audio")
        aplicar_icono_ventana(self)
        self.geometry("780x940")
        self.minsize(680, 720)
        self.configure(fg_color="#0a0a0b")

        self.carpeta_destino = cargar_configuracion()
        self.asegurar_carpeta(self.carpeta_destino)
        
        self.resultados_data = []
        self.likes_data = self._leer_likes()
        self._playlist_download_active = False
        self.pantalla_completa_estado = False
        self.proceso_audio = None
        self._resultado_preview_after = None
        self._resultado_preview_start = 0.0
        self._resultado_preview_base_position = 0.0
        self._resultado_preview_started_at = None
        self._resultado_preview_generation = 0
        self._resultado_preview_cache = {}
        self._is_result_seeking = False
        self._resultado_preview_duration = 0.0
        self._resultado_preview_slider = None
        self._resultado_preview_label = None
        self._resultado_preview_index = None
        self._resultado_preview_btn = None
        self._resultado_preview_item_override = None
        self._resultado_preview_final_label = None
        self._seek_after = None
        self.boton_reproduciendo_actual = None
        self._resultados_render_generation = 0
        self._resultados_render_after = None
        self._resultados_render_index = 0
        self._resultado_card_records = []
        self._filter_after = None
        self._queue_active = False
        self._queue_cancel_event = threading.Event()
        self._shutdown_event = threading.Event()
        self._queue_pause_event = threading.Event()
        self._queue_pause_event.set()
        self._queue_paused = False
        self._history_lock = threading.Lock()
        self._closing = False

        self.protocol("WM_DELETE_WINDOW", self.al_cerrar)

        # El espacio elástico va al área principal (y su lista de resultados),
        # no al pie; así se ven más canciones sin tener que achicar la ventana.
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # --- CABECERA ---
        self.frame_top = ctk.CTkFrame(self, fg_color="transparent")
        self.frame_top.grid(row=0, column=0, pady=(15, 5), padx=25, sticky="ew")

        self.logo_header = cargar_logo_ui(40)
        self.label_titulo = ctk.CTkLabel(
            self.frame_top, 
            text="CORNETEROS STUDIO", 
            image=self.logo_header, compound="left",
            font=fuente_ui(size=20, weight="bold"),
            text_color="#ffffff"
        )
        self.label_titulo.pack(side="left")

        self.btn_canal = ctk.CTkButton(
            self.frame_top,
            text="▶ Mi Canal",
            width=110,
            height=32,
            fg_color="#494950",
            hover_color="#5b5b62",
            font=fuente_ui(size=12, weight="bold"),
            command=self.abrir_canal
        )
        self.btn_canal.pack(side="right")

        # El acceso al editor permanece siempre visible en la ventana principal.
        self.btn_editor_audio = ctk.CTkButton(
            self.frame_top,
            text="♫ Editor de audio",
            width=145,
            height=32,
            fg_color="#29292e",
            hover_color="#333333",
            font=fuente_ui(size=12, weight="bold"),
            command=self.abrir_editor_audio
        )
        self.btn_editor_audio.pack(side="right", padx=(0, 8))

        self.btn_historial = ctk.CTkButton(
            self.frame_top, text="◷ Historial", width=110, height=32,
            fg_color="#29292e", hover_color="#333333",
            font=fuente_ui(size=12, weight="bold"), command=self.abrir_historial
        )
        self.btn_historial.pack(side="right", padx=(0, 8))

        # --- PESTAÑAS PRINCIPALES ---
        self.tabs = ctk.CTkTabview(
            self, fg_color="#0a0a0b", segmented_button_fg_color="#151515",
            segmented_button_selected_color="#45454b",
            segmented_button_selected_hover_color="#55555c",
            corner_radius=10
        )
        self.tabs.grid(row=1, column=0, sticky="nsew", padx=25, pady=5)
        try:
            self.tabs._segmented_button.configure(
                height=34, corner_radius=10,
                font=fuente_ui(size=12, weight="bold")
            )
        except (AttributeError, tk.TclError, TypeError):
            pass
        self.tabs.add("Buscar")
        self.tabs.add("Likes")
        self.tab_buscar = self.tabs.tab("Buscar")
        self.tab_likes = self.tabs.tab("Likes")
        self.tab_buscar.grid_rowconfigure(0, weight=1)
        self.tab_buscar.grid_columnconfigure(0, weight=1)
        self.tab_likes.grid_rowconfigure(2, weight=1)
        self.tab_likes.grid_columnconfigure(0, weight=1)

        # --- PANEL CENTRAL ---
        self.frame_main = ctk.CTkFrame(self.tab_buscar, fg_color="transparent")
        self.frame_main.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
        self.frame_main.grid_rowconfigure(5, weight=1)
        self.frame_main.grid_columnconfigure(0, weight=1)

        # --- PANEL DE RUTA ---
        self.frame_carpeta = ctk.CTkFrame(self.frame_main, fg_color="#1b1b1f", corner_radius=8)
        self.frame_carpeta.grid(row=0, column=0, sticky="ew", pady=(0, 6))

        self.label_ruta_titulo = ctk.CTkLabel(
            self.frame_carpeta,
            text="▰ Carpeta de destino actual:",
            font=fuente_ui(size=11, weight="bold"),
            text_color="#f4f4f5"
        )
        self.label_ruta_titulo.pack(anchor="w", padx=12, pady=(5, 0))

        self.frame_ruta_inner = ctk.CTkFrame(self.frame_carpeta, fg_color="transparent")
        self.frame_ruta_inner.pack(fill="x", padx=12, pady=(2, 6))

        self.label_ruta = ctk.CTkLabel(
            self.frame_ruta_inner,
            text=self.carpeta_destino,
            font=fuente_ui(size=12),
            text_color="#f4f4f5",
            anchor="w"
        )
        self.label_ruta.pack(side="left", fill="x", expand=True)

        self.btn_cambiar_carpeta = ctk.CTkButton(
            self.frame_ruta_inner,
            text="Cambiar",
            width=80,
            height=28,
            fg_color="#252529",
            hover_color="#333333",
            font=fuente_ui(size=11, weight="bold"),
            command=self.seleccionar_carpeta
        )
        self.btn_cambiar_carpeta.pack(side="right")

        self.btn_abrir_carpeta = ctk.CTkButton(
            self.frame_ruta_inner,
            text="Abrir carpeta",
            width=105,
            height=28,
            fg_color="#252529",
            hover_color="#333333",
            font=fuente_ui(size=11, weight="bold"),
            command=self.abrir_carpeta_destino
        )
        self.btn_abrir_carpeta.pack(side="right", padx=(0, 6))

        # --- PANEL DE CONFIGURACIÓN (CALIDAD Y CANTIDAD) ---
        self.frame_configs = ctk.CTkFrame(self.frame_main, fg_color="transparent")
        self.frame_configs.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        self.frame_configs.grid_columnconfigure(0, weight=1)
        self.frame_configs.grid_columnconfigure(1, weight=1)
        self.frame_configs.grid_columnconfigure(2, weight=1)

        # Selector de Calidad (Kbps)
        self.frame_calidad = ctk.CTkFrame(self.frame_configs, fg_color="#1b1b1f", corner_radius=8)
        self.frame_calidad.grid(row=0, column=0, sticky="ew", padx=(0, 4))

        self.label_calidad_titulo = ctk.CTkLabel(
            self.frame_calidad,
            text="♫ Calidad:",
            font=fuente_ui(size=11, weight="bold"),
            text_color="#f4f4f5"
        )
        self.label_calidad_titulo.pack(side="left", padx=8, pady=6)

        self.combo_kbps = ctk.CTkComboBox(
            self.frame_calidad,
            values=["128 kbps", "192 kbps", "320 kbps (Máxima)"],
            width=140,
            height=26,
            fg_color="#252529",
            button_color="#333333",
            button_hover_color="#444444",
            dropdown_fg_color="#1b1b1f",
            font=fuente_ui(size=11)
        )
        self.combo_kbps.set("128 kbps")
        self.combo_kbps.pack(side="right", padx=8, pady=6)

        # Selector de cantidad; las búsquedas normales piden al menos 10 temas.
        self.frame_cantidad = ctk.CTkFrame(self.frame_configs, fg_color="#1b1b1f", corner_radius=8)
        self.frame_cantidad.grid(row=0, column=1, sticky="ew", padx=(4, 0))

        self.label_cantidad_titulo = ctk.CTkLabel(
            self.frame_cantidad,
            text="# Máx. temas:",
            font=fuente_ui(size=11, weight="bold"),
            text_color="#f4f4f5"
        )
        self.label_cantidad_titulo.pack(side="left", padx=8, pady=6)

        self.combo_cantidad = ctk.CTkComboBox(
            self.frame_cantidad,
            values=["10 (Mínimo)", "25", "50", "100", "150", "200", "250 (Máximo)"],
            width=130,
            height=26,
            fg_color="#252529",
            button_color="#333333",
            button_hover_color="#444444",
            dropdown_fg_color="#1b1b1f",
            font=fuente_ui(size=11)
        )
        self.combo_cantidad.set("100")
        self.combo_cantidad.pack(side="right", padx=8, pady=6)

        self.frame_modo_busqueda = ctk.CTkFrame(self.frame_configs, fg_color="#1b1b1f", corner_radius=8)
        self.frame_modo_busqueda.grid(row=0, column=2, sticky="ew", padx=(4, 0))
        ctk.CTkLabel(
            self.frame_modo_busqueda, text="⌕ Búsqueda:",
            font=fuente_ui(size=11, weight="bold"), text_color="#f4f4f5"
        ).pack(side="left", padx=8, pady=6)
        self.combo_modo_busqueda = ctk.CTkComboBox(
            self.frame_modo_busqueda,
            values=["Amplia", "Normal"], width=130, height=26,
            fg_color="#252529", button_color="#333333",
            button_hover_color="#444444", dropdown_fg_color="#1b1b1f",
            font=fuente_ui(size=11)
        )
        self.combo_modo_busqueda.set("Amplia")
        self.combo_modo_busqueda.pack(side="right", padx=8, pady=6)

        # --- BARRA DE BÚSQUEDA ---
        self.frame_busqueda = ctk.CTkFrame(self.frame_main, fg_color="transparent")
        self.frame_busqueda.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        self.frame_busqueda.grid_columnconfigure(0, weight=1, uniform="busqueda")
        self.frame_busqueda.grid_columnconfigure(1, weight=1, uniform="busqueda")

        self.entry_query = ctk.CTkEntry(
            self.frame_busqueda, 
            placeholder_text="Escribe la canción, artista o pega un link...", 
            height=42, 
            fg_color="#1b1b1f",
            border_color="#2b2b2b",
            text_color="#ffffff",
            font=fuente_ui(size=14)
        )
        self.entry_query.grid(row=0, column=0, sticky="ew", columnspan=2, pady=(0, 8))
        self.entry_query.bind("<Return>", lambda event: self.buscar_youtube())

        self.btn_yt = ctk.CTkButton(
            self.frame_busqueda, 
            text="▶ Buscar en YouTube", 
            height=36, 
            fg_color="#303034",
            hover_color="#45454b",
            border_width=1,
            border_color="#55555c",
            font=fuente_ui(size=13, weight="bold"),
            command=self.buscar_youtube
        )
        self.btn_yt.grid(row=1, column=0, sticky="ew", padx=(0, 5))

        self.btn_sc = ctk.CTkButton(
            self.frame_busqueda, 
            text="☁ Buscar en SoundCloud", 
            height=36, 
            fg_color="#303034",
            hover_color="#45454b",
            border_width=1,
            border_color="#55555c",
            font=fuente_ui(size=13, weight="bold"),
            command=self.buscar_soundcloud
        )
        self.btn_sc.grid(row=1, column=1, sticky="ew", padx=(5, 0))

        self.btn_playlist = ctk.CTkButton(
            self.frame_busqueda,
            text="≡ Descargar playlist de YouTube como MP3",
            height=34,
            fg_color="#303034",
            hover_color="#494950",
            font=fuente_ui(size=12, weight="bold"),
            command=self.descargar_playlist_youtube
        )
        self.btn_playlist.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(7, 0))

        self.frame_resultados_tools = ctk.CTkFrame(self.frame_busqueda, fg_color="transparent")
        self.frame_resultados_tools.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(5, 0))
        self.frame_resultados_tools.grid_columnconfigure(0, weight=1)
        self.entry_filter_results = ctk.CTkEntry(
            self.frame_resultados_tools, height=30,
            placeholder_text="Filtrar por título o canal…", fg_color="#1b1b1f",
            border_color="#2b2b2b", font=fuente_ui(size=11)
        )
        self.entry_filter_results.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        self.entry_filter_results.bind("<KeyRelease>", self._programar_filtro_resultados)
        self.btn_paste_link = ctk.CTkButton(
            self.frame_resultados_tools, text="Pegar enlace", width=105, height=30,
            fg_color="#252529", hover_color="#333333", font=fuente_ui(size=11),
            command=self.pegar_enlace
        )
        self.btn_paste_link.grid(row=0, column=1, padx=3)
        self.btn_queue_results = ctk.CTkButton(
            self.frame_resultados_tools, text="⬇ Descargar resultados", width=155, height=30,
            fg_color="#252529", hover_color="#333333", state="disabled",
            font=fuente_ui(size=11, weight="bold"), command=self.iniciar_cola_resultados
        )
        self.btn_queue_results.grid(row=0, column=2, padx=3)
        self.btn_pause_queue = ctk.CTkButton(
            self.frame_resultados_tools, text="Pausar cola", width=92, height=30,
            fg_color="#303034", hover_color="#494950", state="disabled",
            font=fuente_ui(size=10), command=self.toggle_pausa_cola
        )
        self.btn_pause_queue.grid(row=0, column=4, padx=3)
        self.btn_cancel_queue = ctk.CTkButton(
            self.frame_resultados_tools, text="Cancelar cola", width=100, height=30,
            fg_color="#303034", hover_color="#494950", state="disabled",
            font=fuente_ui(size=11), command=self.cancelar_cola_resultados
        )
        self.btn_cancel_queue.grid(row=0, column=3, padx=(3, 0))

        self._button(
            self.frame_busqueda, "Descargar varios enlaces…", self.abrir_lote_enlaces,
            width=210, fg_color="#252529", hover_color="#333333"
        ).grid(row=4, column=0, columnspan=2, sticky="ew", pady=(5, 0))

        # Una sola barra pequeña para buscar dentro de la vista previa activa;
        # evita crear un slider y dos etiquetas por cada resultado.
        self.frame_preview_seek = ctk.CTkFrame(self.frame_main, fg_color="#18181b", height=28, corner_radius=8)
        self.frame_preview_seek.grid(row=4, column=0, sticky="ew", pady=(2, 0))
        self.label_seek_titulo = ctk.CTkLabel(
            self.frame_preview_seek, text="Posición:", width=58,
            font=fuente_ui(size=9), text_color="#f4f4f5"
        )
        self.label_seek_titulo.grid(row=0, column=0, padx=(8, 2), pady=3)
        self.label_preview_inicio = ctk.CTkLabel(
            self.frame_preview_seek, text="00:00.0", width=48, font=fuente_ui(size=9), text_color="#f4f4f5"
        )
        self.label_preview_inicio.grid(row=0, column=1, padx=(2, 2), pady=3)
        self.slider_preview = ctk.CTkSlider(
            self.frame_preview_seek, from_=0, to=1, number_of_steps=100,
            height=10, progress_color="#85858b", state="disabled",
            command=self._actualizar_etiqueta_seek
        )
        self.slider_preview.grid(row=0, column=2, sticky="ew", padx=4, pady=3)
        self.slider_preview.bind("<ButtonPress-1>", lambda _event: setattr(self, "_is_result_seeking", True))
        self.slider_preview.bind("<ButtonRelease-1>", self._soltar_slider_preview)
        self.label_preview_final = ctk.CTkLabel(
            self.frame_preview_seek, text="00:00.0", width=52, font=fuente_ui(size=9), text_color="#f4f4f5"
        )
        self.label_preview_final.grid(row=0, column=3, padx=(2, 8), pady=3)
        self.frame_preview_seek.grid_columnconfigure(2, weight=1)

        # --- ÁREA DE RESULTADOS ---
        self.scroll_resultados = ctk.CTkScrollableFrame(
            self.frame_main, 
            corner_radius=8,
            fg_color="#0a0a0a"
        )
        self.scroll_resultados.grid(row=5, column=0, sticky="nsew", pady=(5, 5))
        # Paso corto por rueda para que la lista no se dispare al desplazarse.
        try:
            self.scroll_resultados._parent_canvas.configure(yscrollincrement=6)
        except (AttributeError, tk.TclError):
            pass

        self.likes_seek = ctk.CTkFrame(self.tab_likes, fg_color="#18181b", height=28, corner_radius=8)
        self.likes_seek.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 2))
        ctk.CTkLabel(
            self.likes_seek, text="Posición:", width=58,
            font=fuente_ui(size=9), text_color="#f4f4f5"
        ).grid(row=0, column=0, padx=(8, 2), pady=3)
        self.likes_time_start = ctk.CTkLabel(
            self.likes_seek, text="00:00.0", width=48,
            font=fuente_ui(size=9), text_color="#f4f4f5"
        )
        self.likes_time_start.grid(row=0, column=1, padx=2, pady=3)
        self.likes_slider = ctk.CTkSlider(
            self.likes_seek, from_=0, to=1, number_of_steps=100,
            height=10, progress_color="#85858b", state="disabled",
            command=lambda value: self.likes_time_start.configure(
                text=self._format_result_time(value)
            )
        )
        self.likes_slider.grid(row=0, column=2, sticky="ew", padx=4, pady=3)
        self.likes_slider.bind("<ButtonPress-1>", lambda _event: setattr(self, "_is_result_seeking", True))
        self.likes_slider.bind("<ButtonRelease-1>", self._soltar_slider_likes)
        self.likes_time_end = ctk.CTkLabel(
            self.likes_seek, text="00:00.0", width=52,
            font=fuente_ui(size=9), text_color="#f4f4f5"
        )
        self.likes_time_end.grid(row=0, column=3, padx=(2, 8), pady=3)
        self.likes_seek.grid_columnconfigure(2, weight=1)

        self.likes_heading = ctk.CTkLabel(
            self.tab_likes, text="LIKES · TEMAS PARA DESCARGAR DESPUÉS",
            font=fuente_ui(size=15, weight="bold"), text_color="#f4f4f5"
        )
        self.likes_heading.grid(row=1, column=0, sticky="w", padx=12, pady=(8, 6))
        self.likes_scroll = ctk.CTkScrollableFrame(
            self.tab_likes, fg_color="#0a0a0a", corner_radius=8
        )
        self.likes_scroll.grid(row=2, column=0, sticky="nsew", padx=8, pady=(2, 8))
        try:
            self.likes_scroll._parent_canvas.configure(yscrollincrement=6)
        except (AttributeError, tk.TclError):
            pass

        # --- PANEL INFERIOR ---
        self.frame_bottom = ctk.CTkFrame(self, fg_color="transparent")
        self.frame_bottom.grid(row=2, column=0, sticky="ew", padx=25, pady=(0, 12))
        self.frame_bottom.grid_columnconfigure(0, weight=1)

        self.label_info = ctk.CTkLabel(
            self.frame_bottom, 
            text="Listo para descargar o editar audio.", 
            font=fuente_ui(size=12), 
            text_color="#f4f4f5",
            anchor="w"
        )
        self.label_info.grid(row=0, column=0, sticky="w", pady=(0, 5))

        self.frame_creditos_row = ctk.CTkFrame(self.frame_bottom, fg_color="transparent")
        self.frame_creditos_row.grid(row=1, column=0, sticky="ew")

        self.label_creditos = ctk.CTkLabel(
            self.frame_creditos_row, 
            text="Desarrollado por @EthanBeis", 
            font=fuente_ui(size=11, weight="bold"), 
            text_color="#f4f4f5"
        )
        self.label_creditos.pack(side="left")

        self.btn_fullscreen = ctk.CTkButton(
            self.frame_creditos_row,
            text="⛶ Pantalla completa",
            width=150,
            height=26,
            fg_color="#1a1a1a",
            hover_color="#333333",
            font=fuente_ui(size=11),
            command=self.toggle_fullscreen
        )
        self.btn_fullscreen.pack(side="right")

        self.bind("<F11>", lambda event: self.toggle_fullscreen())
        self.bind("<Escape>", lambda event: self.salir_fullscreen())
        self._dibujar_likes()
        self._offered_update_version = None
        if getattr(sys, "frozen", False) and os.name == "nt":
            self.after(2200, self._check_for_updates)

    def _update_manifest_url(self):
        """Lee el endpoint publicado por el desarrollador; vacío desactiva el chequeo."""
        try:
            with open(ruta_recurso(UPDATE_CONFIG_FILE), "r", encoding="utf-8") as config_file:
                config = json.load(config_file)
            url = str(config.get("manifest_url", "")).strip()
            if url.startswith("https://"):
                return url
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        return ""

    def _check_for_updates(self):
        manifest_url = self._update_manifest_url()
        if not manifest_url or self._closing:
            return

        def worker():
            try:
                request = urllib.request.Request(
                    manifest_url, headers={"User-Agent": "CorneterosStudio-Updater/1"}
                )
                with urllib.request.urlopen(request, timeout=8) as response:
                    manifest = json.loads(response.read(128 * 1024).decode("utf-8"))
                version = str(manifest.get("version", "")).strip()
                is_wine = _is_wine_runtime()
                if is_wine:
                    asset_url = str(manifest.get("winlator_url", "")).strip()
                    checksum = str(
                        manifest.get("winlator_sha256", manifest.get("sha256", ""))
                    ).strip().lower()
                    update_kind = "archive"
                else:
                    asset_url = str(manifest.get("installer_url", "")).strip()
                    checksum = str(
                        manifest.get("installer_sha256", manifest.get("sha256", ""))
                    ).strip().lower()
                    update_kind = "installer"
                if (_version_tuple(version) <= _version_tuple(APP_VERSION)
                        or not asset_url.startswith("https://")
                        or not re.fullmatch(r"[0-9a-f]{64}", checksum)):
                    manifest = None
                if manifest is not None:
                    manifest = dict(manifest)
                    manifest.update({
                        "_asset_url": asset_url,
                        "_asset_sha256": checksum,
                        "_update_kind": update_kind,
                    })
                    self.after(0, lambda data=manifest: self._offer_update(data))
            except Exception:
                # Un fallo de conexión nunca debe impedir que la app abra.
                pass
            finally:
                try:
                    self.after(10 * 60 * 1000, self._check_for_updates)
                except (RuntimeError, tk.TclError):
                    pass

        threading.Thread(target=worker, name="CorneterosUpdateCheck", daemon=True).start()

    def _offer_update(self, manifest):
        if self._closing or not self.winfo_exists():
            return
        version = str(manifest.get("version", "")).strip()
        if version == self._offered_update_version:
            return
        self._offered_update_version = version
        self._download_update(manifest)

    def _download_update(self, manifest):
        dialog = ctk.CTkToplevel(self)
        dialog.title("Actualizando Corneteros Studio")
        dialog.geometry("460x185")
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()
        try:
            aplicar_icono_ventana(dialog)
        except Exception:
            pass
        status = ctk.CTkLabel(
            dialog,
            text=f"Descargando la versión {manifest.get('version', '')}…",
            anchor="w",
            font=fuente_ui(size=13, weight="bold")
        )
        status.pack(fill="x", padx=20, pady=(22, 8))
        progress = ctk.CTkProgressBar(dialog, mode="determinate")
        progress.pack(fill="x", padx=20, pady=6)
        progress.set(0)
        detail = ctk.CTkLabel(dialog, text="Preparando descarga…", anchor="w", text_color="#bcbcc0")
        detail.pack(fill="x", padx=20, pady=(3, 15))
        cancel_event = threading.Event()
        cancel = self._button(dialog, "Cancelar", cancel_event.set, width=100)
        cancel.pack(anchor="e", padx=20, pady=(0, 14))

        def set_progress(received, total):
            def update_ui():
                if not dialog.winfo_exists():
                    return
                if total > 0:
                    progress.set(min(1.0, received / total))
                    detail.configure(text=f"{received / 1048576:.1f} de {total / 1048576:.1f} MB")
                else:
                    detail.configure(text=f"Descargados {received / 1048576:.1f} MB")
            try:
                self.after(0, update_ui)
            except (RuntimeError, tk.TclError):
                pass

        def worker():
            download_path = ""
            try:
                request = urllib.request.Request(
                    str(manifest["_asset_url"]),
                    headers={"User-Agent": "CorneterosStudio-Updater/1"}
                )
                with urllib.request.urlopen(request, timeout=20) as response:
                    total = int(response.headers.get("Content-Length", "0") or 0)
                    max_size = 700 * 1024 * 1024
                    if total > max_size:
                        raise ValueError("La actualización supera el tamaño máximo permitido.")
                    digest = hashlib.sha256()
                    received = 0
                    with tempfile.NamedTemporaryFile(
                        prefix="corneteros_update_",
                        suffix=".zip" if manifest.get("_update_kind") == "archive" else ".exe",
                        delete=False,
                    ) as output:
                        download_path = output.name
                        while True:
                            if cancel_event.is_set():
                                raise InterruptedError("Descarga cancelada.")
                            chunk = response.read(1024 * 1024)
                            if not chunk:
                                break
                            received += len(chunk)
                            if received > max_size:
                                raise ValueError("La actualización supera el tamaño máximo permitido.")
                            digest.update(chunk)
                            output.write(chunk)
                            set_progress(received, total)
                if digest.hexdigest() != str(manifest["_asset_sha256"]).strip().lower():
                    raise ValueError("La verificación de seguridad del instalador no coincide.")
                if cancel_event.is_set():
                    raise InterruptedError("Descarga cancelada.")
                self.after(0, lambda path=download_path, kind=manifest.get("_update_kind", "installer"):
                           self._start_update_helper(path, dialog, kind))
            except InterruptedError:
                if download_path:
                    try:
                        os.remove(download_path)
                    except OSError:
                        pass
                try:
                    self.after(0, dialog.destroy)
                except (RuntimeError, tk.TclError):
                    pass
            except Exception as exc:
                if download_path:
                    try:
                        os.remove(download_path)
                    except OSError:
                        pass
                error = str(exc)
                try:
                    self.after(0, lambda message=error: self._update_download_failed(dialog, message))
                except (RuntimeError, tk.TclError):
                    pass

        threading.Thread(target=worker, name="CorneterosUpdateDownload", daemon=True).start()

    def _update_download_failed(self, dialog, error):
        try:
            if dialog.winfo_exists():
                dialog.destroy()
            if not self._closing and self.winfo_exists():
                messagebox.showerror("No se pudo actualizar", error, parent=self)
        except tk.TclError:
            pass

    def _start_update_helper(self, installer_path, dialog, update_kind="installer"):
        if self._closing:
            try:
                os.remove(installer_path)
            except OSError:
                pass
            return
        try:
            app_path = os.path.abspath(sys.executable)
            app_dir = os.path.dirname(app_path)
            updater_path = os.path.join(app_dir, "CorneterosUpdater.exe")
            if os.path.isfile(updater_path):
                action = "--archive" if update_kind == "archive" else "--installer"
                arguments = [updater_path, action, installer_path, str(os.getpid()), app_path]
            elif update_kind != "archive":
                # Permite que la versión Windows anterior actualice la primera versión
                # que ya incluye el helper separado.
                arguments = [app_path, "--apply-update", installer_path, str(os.getpid()), app_path]
            else:
                raise FileNotFoundError(
                    "Falta CorneterosUpdater.exe. Instalá una vez el paquete Winlator más nuevo."
                )
            flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
            )
            subprocess.Popen(arguments, cwd=os.path.dirname(app_path), creationflags=flags, close_fds=True)
            try:
                dialog.destroy()
            except tk.TclError:
                pass
            self.al_cerrar()
        except OSError as exc:
            try:
                os.remove(installer_path)
            except OSError:
                pass
            self._update_download_failed(dialog, str(exc))

    def abrir_editor_audio(self):
        """Abre el editor desde el botón permanente de la ventana principal."""
        missing = []
        if np is None:
            missing.append("numpy")
        if sf is None:
            missing.append("soundfile")
        if signal is None:
            missing.append("scipy")

        if missing:
            messagebox.showerror(
                "Faltan dependencias",
                "Faltan estas dependencias del editor: " + ", ".join(missing)
                + "\\n\\nInstalalas con:\\npy -m pip install -U numpy soundfile scipy",
                parent=self
            )
            return

        ventana = getattr(self, "_ventana_editor_audio", None)
        try:
            if ventana is not None and ventana.winfo_exists():
                ventana.deiconify()
                ventana.lift()
                ventana.focus_force()
                return
        except Exception:
            pass

        self._ventana_editor_audio = EditorAudio(self)

    def toggle_fullscreen(self):
        self.pantalla_completa_estado = not self.pantalla_completa_estado
        self.attributes("-fullscreen", self.pantalla_completa_estado)

    def salir_fullscreen(self):
        if self.pantalla_completa_estado:
            self.pantalla_completa_estado = False
            self.attributes("-fullscreen", False)

    def asegurar_carpeta(self, ruta):
        if not os.path.exists(ruta):
            try:
                os.makedirs(ruta)
            except Exception:
                pass

    def seleccionar_carpeta(self):
        nueva_ruta = filedialog.askdirectory(initialdir=self.carpeta_destino, title="Selecciona la carpeta de destino")
        if nueva_ruta:
            self.carpeta_destino = nueva_ruta
            self.asegurar_carpeta(self.carpeta_destino)
            guardar_configuracion(self.carpeta_destino)
            self.label_ruta.configure(text=self.carpeta_destino)
            self.label_info.configure(text="Carpeta actualizada correctamente 📂", text_color="#f4f4f5")

    def abrir_carpeta_destino(self):
        """Abre la carpeta de destino con el explorador del sistema."""
        try:
            os.makedirs(self.carpeta_destino, exist_ok=True)
            if sys.platform == "win32":
                os.startfile(self.carpeta_destino)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", self.carpeta_destino])
            else:
                subprocess.Popen(["xdg-open", self.carpeta_destino])
        except (OSError, subprocess.SubprocessError) as exc:
            messagebox.showerror("No se pudo abrir la carpeta", str(exc), parent=self)

    def abrir_canal(self):
        webbrowser.open("https://www.youtube.com/@EthanBeis")

    def pegar_enlace(self):
        try:
            texto = self.clipboard_get().strip()
        except tk.TclError:
            texto = ""
        if not texto:
            self.label_info.configure(text="El portapapeles está vacío.", text_color="#f4f4f5")
            return
        self.entry_query.delete(0, "end")
        self.entry_query.insert(0, texto)
        self.label_info.configure(text="Enlace pegado. Elegí YouTube, SoundCloud o playlist.", text_color="#f4f4f5")

    def abrir_lote_enlaces(self):
        win = ctk.CTkToplevel(self)
        win.title("Descargar varios enlaces")
        win.geometry("620x490")
        win.minsize(500, 350)
        win.configure(fg_color="#18181b")
        win.transient(self)
        win.grab_set()
        ctk.CTkLabel(
            win, text="DESCARGA POR LOTES", font=fuente_ui(size=16, weight="bold"),
            text_color="#f4f4f5"
        ).pack(anchor="w", padx=16, pady=(15, 5))
        ctk.CTkLabel(
            win, text="Pegá un enlace por línea. La cola se puede ordenar antes de empezar.",
            text_color="#bcbcc0", anchor="w"
        ).pack(anchor="w", padx=16, pady=(0, 9))
        links = ctk.CTkTextbox(
            win, wrap="word", fg_color="#101012", text_color="#f4f4f5",
            border_width=1, border_color="#303034", font=fuente_ui(size=11)
        )
        links.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        try:
            clipboard = self.clipboard_get().strip()
            if clipboard.startswith(("http://", "https://")):
                links.insert("1.0", clipboard)
        except tk.TclError:
            pass
        footer = ctk.CTkFrame(win, fg_color="transparent")
        footer.pack(fill="x", padx=14, pady=(3, 13))

        def submit():
            raw = links.get("1.0", "end-1c")
            candidates = re.findall(r"https?://[^\s<>\"']+", raw, flags=re.IGNORECASE)
            unique = []
            seen = set()
            for candidate in candidates:
                url = candidate.rstrip(".,;)]}")
                if url and url.casefold() not in seen:
                    seen.add(url.casefold())
                    unique.append((url, urllib.parse.urlparse(url).netloc or url[:48]))
            if not unique:
                messagebox.showinfo(
                    "No encontré enlaces", "Pegá uno o más enlaces http/https, uno por línea.", parent=win
                )
                return
            win.destroy()
            self.iniciar_cola_resultados(unique)

        self._button(footer, "Cancelar", win.destroy, width=100).pack(side="right", padx=4)
        self._button(footer, "Preparar cola", submit, width=130).pack(side="right", padx=4)

    def _leer_likes(self):
        try:
            with open(LIKES_FILE, "r", encoding="utf-8") as archivo:
                datos = json.load(archivo)
            return [
                item for item in datos
                if isinstance(item, dict)
                and isinstance(item.get("url"), str)
                and item["url"].startswith(("http://", "https://"))
            ] if isinstance(datos, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def _guardar_likes(self):
        temporal = LIKES_FILE + ".tmp"
        try:
            with open(temporal, "w", encoding="utf-8") as archivo:
                json.dump(self.likes_data, archivo, ensure_ascii=False, indent=2)
            os.replace(temporal, LIKES_FILE)
            return True
        except OSError as exc:
            try:
                if os.path.exists(temporal):
                    os.remove(temporal)
            except OSError:
                pass
            self.label_info.configure(
                text=f"No se pudieron guardar los Likes: {exc}", text_color="#f4f4f5"
            )
            return False

    def toggle_like(self, index, button=None):
        if not 0 <= index < len(self.resultados_data):
            return
        item = self.resultados_data[index]
        try:
            url = self._url_resultado(item)
        except ValueError as exc:
            self.label_info.configure(text=str(exc), text_color="#f4f4f5")
            return

        existing = next((fav for fav in self.likes_data if fav.get("url") == url), None)
        if existing:
            self.likes_data = [fav for fav in self.likes_data if fav.get("url") != url]
            message = f"Like quitado: {item.get('title', 'Tema')}"
            liked = False
        else:
            self.likes_data.append({
                "title": str(item.get("title") or "Sin título"),
                "uploader": str(item.get("uploader") or item.get("channel") or "Desconocido"),
                "url": url,
                "duration": item.get("duration"),
            })
            message = f"Guardado en Likes para descargar después: {item.get('title', 'Tema')}"
            liked = True

        if self._guardar_likes():
            if button is not None:
                button.configure(
                    text="Dislike" if liked else "♡ Like",
                    fg_color="#3b3b40" if liked else "#262626",
                    hover_color="#55555c" if liked else "#3a3a3a"
                )
            self._dibujar_likes()
            self.label_info.configure(text=message, text_color="#f4f4f5")

    def _quitar_like(self, url):
        active_like = self._resultado_preview_item_override
        if active_like and active_like.get("url") == url:
            self.parar_reproduccion()
        antes = len(self.likes_data)
        self.likes_data = [item for item in self.likes_data if item.get("url") != url]
        if len(self.likes_data) != antes and self._guardar_likes():
            self._dibujar_likes()
            self.label_info.configure(text="Tema quitado de Likes.", text_color="#f4f4f5")

    def _dibujar_likes(self):
        if not hasattr(self, "likes_scroll"):
            return
        for child in self.likes_scroll.winfo_children():
            child.destroy()
        self.likes_heading.configure(
            text=f"LIKES · TEMAS PARA DESCARGAR DESPUÉS · {len(self.likes_data)} guardados"
        )
        if not self.likes_data:
            ctk.CTkLabel(
                self.likes_scroll,
                text="Todavía no guardaste temas. Tocá ♡ en un resultado para sumarlo acá.",
                text_color="#f4f4f5", font=fuente_ui(size=12)
            ).pack(anchor="w", padx=12, pady=14)
            return

        for item in self.likes_data:
            row = ctk.CTkFrame(self.likes_scroll, fg_color="#141414", corner_radius=8, height=44)
            row.pack(fill="x", pady=3, padx=3)
            row.pack_propagate(False)
            title = str(item.get("title") or "Sin título")
            uploader = str(item.get("uploader") or "Desconocido")
            ctk.CTkLabel(
                row, text=f"♡  {title}  ·  {uploader}", anchor="w",
                text_color="#f4f4f5", font=fuente_ui(size=11)
            ).pack(side="left", fill="x", expand=True, padx=10)
            ctk.CTkButton(
                row, text="Descargar", width=90, height=28,
                fg_color="#29292e", hover_color="#363636",
                command=lambda fav=item: self.descargar_like(fav)
            ).pack(side="right", padx=(3, 7), pady=5)
            listen = ctk.CTkButton(
                row, text="▶ Escuchar", width=100, height=28,
                fg_color="#303034", hover_color="#45454b",
                command=None
            )
            listen.configure(command=lambda fav=item, button=listen: self.escuchar_like(fav, button))
            listen.pack(side="right", padx=3, pady=5)
            ctk.CTkButton(
                row, text="Quitar", width=65, height=28,
                fg_color="#303034", hover_color="#494950",
                command=lambda url=item["url"]: self._quitar_like(url)
            ).pack(side="right", padx=3, pady=5)

    def descargar_like(self, favorite):
        url = favorite.get("url", "")
        title = favorite.get("title") or "Audio guardado"
        if not url.startswith(("http://", "https://")):
            self.label_info.configure(text="El enlace guardado no es válido.", text_color="#f4f4f5")
            return
        calidad = self.combo_kbps.get()
        kbps = "320" if "320" in calidad else "192" if "192" in calidad else "128"
        self.label_info.configure(text=f"Descargando desde Likes: {title[:55]}…", text_color="#f4f4f5")
        threading.Thread(
            target=self._ejecutar_descarga, args=(url, title, kbps), daemon=True
        ).start()

    def escuchar_like(self, favorite, button):
        """Reproduce un Like con el mismo preview de streaming de los resultados."""
        url = favorite.get("url", "")
        if not url.startswith(("http://", "https://")):
            self.label_info.configure(text="El enlace guardado no es válido.", text_color="#f4f4f5")
            return
        preview_id = -1 - self.likes_data.index(favorite)
        if self.proceso_audio and self._resultado_preview_index == preview_id:
            self.parar_reproduccion()
            self.label_info.configure(text="Vista previa detenida ⏹", text_color="#f4f4f5")
            return

        self.parar_reproduccion()
        item = dict(favorite)
        duration = float(item.get("duration") or 0)
        self.likes_slider.set(0)
        self.likes_time_start.configure(text="00:00.0")
        self.likes_slider.configure(
            to=max(duration, 0.001),
            number_of_steps=max(1, min(20000, int(max(duration, 1) * 10))),
            state="normal"
        )
        self.likes_time_end.configure(text=self._format_result_time(duration))
        button.configure(text="⏹ Detener", fg_color="#494950", hover_color="#5b5b62")
        self.boton_reproduciendo_actual = button
        self._resultado_preview_index = preview_id
        self._resultado_preview_item_override = item
        self._resultado_preview_start = 0.0
        self._resultado_preview_base_position = 0.0
        self._resultado_preview_started_at = None
        self._resultado_preview_duration = duration
        self._resultado_preview_slider = self.likes_slider
        self._resultado_preview_label = self.likes_time_start
        self._resultado_preview_final_label = self.likes_time_end
        self._resultado_preview_btn = button
        generation = self._resultado_preview_generation
        title = item.get("title", "Tema guardado")
        self.label_info.configure(text=f"Cargando vista previa: {title[:55]}…", text_color="#f4f4f5")
        threading.Thread(
            target=self._reproducir_stream,
            args=(preview_id, 0.0, generation, item), daemon=True
        ).start()

    def _leer_historial_descargas(self):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as archivo:
                data = json.load(archivo)
            return data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def _registrar_descarga(self, titulo, url, calidad, ruta):
        registro = {
            "titulo": str(titulo), "url": str(url), "calidad": str(calidad),
            "ruta": str(ruta), "fecha": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        with self._history_lock:
            historial = self._leer_historial_descargas()
            historial.append(registro)
            historial = historial[-2000:]
            temporal = HISTORY_FILE + ".tmp"
            with open(temporal, "w", encoding="utf-8") as archivo:
                json.dump(historial, archivo, ensure_ascii=False, indent=2)
            os.replace(temporal, HISTORY_FILE)

    def abrir_historial(self):
        historial = list(reversed(self._leer_historial_descargas()))
        visibles = []
        win = ctk.CTkToplevel(self)
        win.title("Historial de descargas")
        win.geometry("780x480")
        win.minsize(560, 320)
        win.configure(fg_color="#18181b")
        win.transient(self)
        titulo = ctk.CTkLabel(
            win, text=f"DESCARGAS RECIENTES · {len(historial)} registros",
            font=fuente_ui(size=15, weight="bold")
        )
        titulo.pack(anchor="w", padx=14, pady=(12, 8))
        filtro = ctk.CTkEntry(
            win, placeholder_text="Buscar por título, fecha o ubicación…",
            fg_color="#101012", border_color="#303034", font=fuente_ui(size=11)
        )
        filtro.pack(fill="x", padx=12, pady=(0, 8))
        body = ctk.CTkFrame(win, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        lista = tk.Listbox(
            body, bg="#101012", fg="#f4f4f5", selectbackground="#45454b",
            relief="flat", highlightthickness=0, font=("Segoe UI", 11), exportselection=False
        )
        lista.pack(fill="both", expand=True)
        def texto_historial(item):
            ruta = item.get("ruta", "")
            tamano = ""
            try:
                tamano = f" · {os.path.getsize(ruta) / (1024 * 1024):.1f} MB"
            except OSError:
                pass
            return f"{item.get('fecha', '')} · {item.get('titulo', 'Audio')} · {item.get('calidad', '')} kbps{tamano} · {ruta}"

        def refrescar_historial(_event=None):
            query = filtro.get().strip().casefold()
            visibles[:] = [
                item for item in historial
                if not query or query in texto_historial(item).casefold()
            ]
            lista.delete(0, "end")
            for item in visibles:
                lista.insert("end", texto_historial(item))
            titulo.configure(text=f"DESCARGAS RECIENTES · {len(visibles)} de {len(historial)} registros")

        filtro.bind("<KeyRelease>", refrescar_historial)
        refrescar_historial()

        def abrir_seleccion(_event=None):
            seleccion = lista.curselection()
            if not seleccion:
                return
            ruta = visibles[seleccion[0]].get("ruta", "")
            if not ruta or not os.path.isfile(ruta):
                messagebox.showinfo("Archivo no encontrado", "El archivo ya no está en esa ubicación.", parent=win)
                return
            try:
                if sys.platform == "win32":
                    os.startfile(ruta)
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", ruta])
                else:
                    subprocess.Popen(["xdg-open", ruta])
            except OSError as exc:
                messagebox.showerror("No se pudo abrir el archivo", str(exc), parent=win)

        lista.bind("<Double-Button-1>", abrir_seleccion)
        ctk.CTkButton(win, text="Abrir archivo seleccionado", command=abrir_seleccion).pack(
            anchor="e", padx=12, pady=(0, 12)
        )

    def _programar_filtro_resultados(self, _event=None):
        if self._filter_after is not None:
            try:
                self.after_cancel(self._filter_after)
            except tk.TclError:
                pass
        self._filter_after = self.after(120, self.filtrar_resultados)

    def filtrar_resultados(self):
        self._filter_after = None
        filtro = self.entry_filter_results.get().strip().casefold()
        for card, _texto in self._resultado_card_records:
            try:
                card.pack_forget()
            except tk.TclError:
                pass
        visibles = 0
        for card, texto in self._resultado_card_records:
            coincide = not filtro or filtro in texto
            try:
                if coincide:
                    card.pack(fill="x", pady=2, padx=3)
                    visibles += 1
                else:
                    card.pack_forget()
            except tk.TclError:
                continue
        if self.resultados_data:
            self.label_info.configure(
                text=f"Mostrando {visibles} de {len(self.resultados_data)} temas.",
                text_color="#f4f4f5"
            )

    def iniciar_cola_resultados(self, elementos=None):
        if self._queue_active or self._playlist_download_active:
            return
        if elementos is None:
            filtro = self.entry_filter_results.get().strip().casefold()
            elementos = []
            for index, item in enumerate(self.resultados_data):
                titulo = str(item.get("title") or "")
                canal = str(item.get("uploader") or item.get("channel") or "")
                if not filtro or filtro in f"{titulo} {canal}".casefold():
                    try:
                        elementos.append((self._url_resultado(item), titulo or f"Tema {index + 1}"))
                    except ValueError:
                        continue
        elementos = list(elementos or [])
        if not elementos:
            self.label_info.configure(text="No hay temas para agregar a la cola.", text_color="#f4f4f5")
            return
        calidad_txt = self.combo_kbps.get()
        calidad = "320" if "320" in calidad_txt else "192" if "192" in calidad_txt else "128"
        self._mostrar_gestor_cola(elementos, calidad)

    def _mostrar_gestor_cola(self, elementos, calidad):
        win = ctk.CTkToplevel(self)
        win.title("Ordenar descargas")
        win.geometry("650x490")
        win.minsize(520, 360)
        win.configure(fg_color="#18181b")
        win.transient(self)
        win.grab_set()
        ctk.CTkLabel(
            win, text=f"COLA DE DESCARGA · {len(elementos)} temas",
            font=fuente_ui(size=16, weight="bold"), text_color="#f4f4f5"
        ).pack(anchor="w", padx=16, pady=(15, 8))
        body = ctk.CTkFrame(win, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=14, pady=4)
        listing = tk.Listbox(
            body, bg="#101012", fg="#f4f4f5", selectbackground="#494950",
            relief="flat", highlightthickness=0, font=("Segoe UI", 10),
            exportselection=False
        )
        listing.pack(side="left", fill="both", expand=True)
        scrollbar = ctk.CTkScrollbar(body, command=listing.yview)
        scrollbar.pack(side="left", fill="y", padx=(4, 8))
        listing.configure(yscrollcommand=scrollbar.set)
        controls = ctk.CTkFrame(body, fg_color="transparent", width=125)
        controls.pack(side="right", fill="y")
        controls.pack_propagate(False)
        queue_items = list(elementos)

        def refresh_queue(select=None):
            listing.delete(0, "end")
            for index, (_url, title) in enumerate(queue_items, start=1):
                listing.insert("end", f"{index:02d}. {title}")
            if select is not None and queue_items:
                select = max(0, min(select, len(queue_items) - 1))
                listing.selection_set(select)
                listing.activate(select)
                listing.see(select)

        def move(delta):
            selected = listing.curselection()
            if not selected:
                return
            current = selected[0]
            target = current + delta
            if not 0 <= target < len(queue_items):
                return
            queue_items[current], queue_items[target] = queue_items[target], queue_items[current]
            refresh_queue(target)

        def remove_selected():
            selected = listing.curselection()
            if not selected:
                return
            current = selected[0]
            queue_items.pop(current)
            refresh_queue(min(current, len(queue_items) - 1) if queue_items else None)

        self._button(controls, "Subir ↑", lambda: move(-1), width=112).pack(pady=(4, 5))
        self._button(controls, "Bajar ↓", lambda: move(1), width=112).pack(pady=5)
        self._button(
            controls, "Quitar", remove_selected, width=112,
            fg_color="#303034", hover_color="#494950"
        ).pack(pady=5)
        refresh_queue()

        footer = ctk.CTkFrame(win, fg_color="transparent")
        footer.pack(fill="x", padx=14, pady=(7, 14))
        self._button(footer, "Cancelar", win.destroy, width=100).pack(side="right", padx=4)

        def start_queue():
            if not queue_items:
                messagebox.showinfo("Cola vacía", "Agregá al menos un tema.", parent=win)
                return
            ordered = list(queue_items)
            win.destroy()
            self._iniciar_cola_descarga(ordered, calidad)

        self._button(footer, "Iniciar cola", start_queue, width=130).pack(side="right", padx=4)

    def _iniciar_cola_descarga(self, elementos, calidad):
        self._queue_cancel_event.clear()
        self._queue_pause_event.set()
        self._queue_paused = False
        self._queue_active = True
        self.btn_queue_results.configure(state="disabled")
        self.btn_pause_queue.configure(state="normal", text="Pausar cola")
        self.btn_cancel_queue.configure(state="normal", text="Cancelar cola")
        threading.Thread(
            target=self._ejecutar_cola_resultados, args=(elementos, calidad), daemon=True
        ).start()

    def toggle_pausa_cola(self):
        if not self._queue_active:
            return
        self._queue_paused = not self._queue_paused
        if self._queue_paused:
            self._queue_pause_event.clear()
            self.btn_pause_queue.configure(text="Reanudar cola")
            self.label_info.configure(text="Cola pausada; el tema actual conserva su avance.", text_color="#f4f4f5")
        else:
            self._queue_pause_event.set()
            self.btn_pause_queue.configure(text="Pausar cola")
            self.label_info.configure(text="Cola reanudada.", text_color="#f4f4f5")

    def cancelar_cola_resultados(self):
        if self._queue_active:
            self._queue_cancel_event.set()
            self._queue_pause_event.set()
            self._queue_paused = False
            self.btn_cancel_queue.configure(state="disabled", text="Cancelando…")
            self.btn_pause_queue.configure(state="disabled", text="Pausar cola")
            self.label_info.configure(text="La cola se está cancelando; los fragmentos incompletos se pueden reanudar.", text_color="#f4f4f5")

    def _ejecutar_cola_resultados(self, elementos, calidad):
        total = len(elementos)
        exitos = 0
        existentes = 0
        fallidos = 0
        procesados = 0
        for numero, (url, titulo) in enumerate(elementos, start=1):
            self._queue_pause_event.wait()
            if self._queue_cancel_event.is_set():
                break
            try:
                self.after(0, lambda n=numero, t=total, name=titulo: self.label_info.configure(
                    text=f"Cola {n}/{t} · {name[:55]}", text_color="#f4f4f5"
                ))
            except (RuntimeError, tk.TclError):
                return
            result = self._ejecutar_descarga(
                url, titulo, calidad, pause_event=self._queue_pause_event,
                cancel_event=self._queue_cancel_event
            )
            if result == "exists":
                existentes += 1
            elif result:
                exitos += 1
            elif self._queue_cancel_event.is_set():
                break
            else:
                fallidos += 1
            procesados += 1
        cancelada = self._queue_cancel_event.is_set()
        texto = f"Cola finalizada: {exitos} listas · {existentes} ya existentes · {fallidos} con error"
        if cancelada:
            texto = f"Cola cancelada: {procesados}/{total} procesados · {exitos} listas · {existentes} duplicados · {fallidos} con error"
        self._queue_pause_event.set()
        try:
            self.after(0, lambda: self._terminar_cola_resultados(texto))
        except (RuntimeError, tk.TclError):
            pass

    def _terminar_cola_resultados(self, texto):
        self._queue_active = False
        self._queue_paused = False
        self.btn_queue_results.configure(state="normal")
        self.btn_pause_queue.configure(state="disabled", text="Pausar cola")
        self.btn_cancel_queue.configure(state="disabled", text="Cancelar cola")
        self.label_info.configure(text=texto, text_color="#f4f4f5")

    def _url_resultado(self, item):
        for clave in ("webpage_url", "original_url", "url"):
            valor = item.get(clave)
            if not isinstance(valor, str) or not valor.startswith(("http://", "https://")):
                continue
            partes = urllib.parse.urlparse(valor)
            host = (partes.hostname or "").lower()
            if host == "youtu.be":
                video_id = partes.path.strip("/").split("/")[0]
                if re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
                    return f"https://www.youtube.com/watch?v={video_id}"
                continue
            if host == "youtube.com" or host.endswith(".youtube.com"):
                query = urllib.parse.parse_qs(partes.query)
                video_id = (query.get("v") or [""])[0]
                if not video_id:
                    match = re.match(r"^/(?:shorts|embed|live)/([A-Za-z0-9_-]{11})(?:/|$)", partes.path)
                    video_id = match.group(1) if match else ""
                if re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
                    return f"https://www.youtube.com/watch?v={video_id}"
                # Algunos resultados incompletos devuelven la búsqueda dentro
                # del parámetro v; nunca intentar abrir eso como si fuera un video.
                continue
            if partes.scheme in ("http", "https") and host and not any(ch.isspace() for ch in valor):
                return valor
        video_id = item.get("id")
        if isinstance(video_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            return f"https://www.youtube.com/watch?v={video_id}"
        raise ValueError("El resultado no tiene un enlace descargable.")

    def parar_reproduccion(self):
        self._resultado_preview_generation += 1
        self._is_result_seeking = False
        self._resultado_preview_started_at = None
        was_like_preview = self._resultado_preview_item_override is not None
        if getattr(self, "_seek_after", None) is not None:
            try:
                self.after_cancel(self._seek_after)
            except Exception:
                pass
            self._seek_after = None
        if getattr(self, "_resultado_preview_after", None) is not None:
            try:
                self.after_cancel(self._resultado_preview_after)
            except Exception:
                pass
            self._resultado_preview_after = None
        if self.proceso_audio:
            process = self.proceso_audio
            self.proceso_audio = None
            try:
                process.terminate()
            except Exception:
                pass
            if isinstance(process, subprocess.Popen):
                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    try:
                        process.kill()
                        process.wait(timeout=1.0)
                    except Exception:
                        pass
                except Exception:
                    pass

        if self.boton_reproduciendo_actual and self.boton_reproduciendo_actual.winfo_exists():
            self.boton_reproduciendo_actual.configure(
                text="▶ Escuchar",
                fg_color="#303034",
                hover_color="#45454b"
            )
        self.boton_reproduciendo_actual = None
        self._resultado_preview_item_override = None
        if was_like_preview and hasattr(self, "likes_slider"):
            self.likes_slider.configure(state="disabled")

    def al_cerrar(self):
        if self._closing:
            return
        self._closing = True
        raiz = self.master
        try:
            self._queue_cancel_event.set()
            self._queue_pause_event.set()
            self._shutdown_event.set()
            try:
                self.parar_reproduccion()
            except Exception:
                pass

            # Cierra primero las ventanas secundarias (en especial el editor,
            # que puede tener su propio proceso de reproducción abierto).
            try:
                for child in tuple(self.winfo_children()):
                    if isinstance(child, tk.Toplevel):
                        try:
                            child.destroy()
                        except Exception:
                            pass
            except Exception:
                pass
        finally:
            try:
                self.destroy()
            except Exception:
                pass
            # La raíz queda oculta después del login. Aseguramos cerrar también
            # el mainloop para que no quede un proceso invisible en segundo plano.
            try:
                raiz.quit()
            except Exception:
                pass
            try:
                raiz.destroy()
            except Exception:
                pass

    def bloquear_botones(self, estado):
        s = "disabled" if estado else "normal"
        self.btn_yt.configure(state=s)
        self.btn_sc.configure(state=s)

    def obtener_limite_resultados(self):
        txt = self.combo_cantidad.get()
        numeros = "".join(char for char in txt if char.isdigit())
        try:
            return max(10, min(250, int(numeros)))
        except (TypeError, ValueError):
            return 100

    def buscar_youtube(self):
        query = self.entry_query.get().strip()
        if not query:
            return
        
        limite = self.obtener_limite_resultados()
        self._cancelar_render_resultados()
        self._resultado_card_records = []
        self.btn_queue_results.configure(state="disabled")
        self.entry_filter_results.delete(0, "end")
        self.parar_reproduccion()
        self._resultado_preview_index = None
        self.slider_preview.configure(state="disabled")
        self.slider_preview.set(0)
        self.label_preview_inicio.configure(text="00:00.0")
        self.label_preview_final.configure(text="00:00.0")
        self._resultado_preview_cache.clear()
        self.bloquear_botones(True)
        self.label_info.configure(text=f"Buscando {limite} resultados en YouTube...", text_color="#f4f4f5")
        
        for widget in self.scroll_resultados.winfo_children():
            widget.destroy()

        es_enlace = query.lower().startswith(("http://", "https://"))
        modo_amplio = self.combo_modo_busqueda.get() == "Amplia" and not es_enlace
        busqueda = query if es_enlace else f"ytsearch{limite}:{query}"
        threading.Thread(
            target=self._ejecutar_busqueda,
            args=(busqueda, query if modo_amplio else None, limite),
            daemon=True
        ).start()

    def buscar_soundcloud(self):
        query = self.entry_query.get().strip()
        if not query:
            return
        
        limite = self.obtener_limite_resultados()
        self._cancelar_render_resultados()
        self._resultado_card_records = []
        self.btn_queue_results.configure(state="disabled")
        self.entry_filter_results.delete(0, "end")
        self.parar_reproduccion()
        self._resultado_preview_index = None
        self.slider_preview.configure(state="disabled")
        self.slider_preview.set(0)
        self.label_preview_inicio.configure(text="00:00.0")
        self.label_preview_final.configure(text="00:00.0")
        self._resultado_preview_cache.clear()
        self.bloquear_botones(True)
        self.label_info.configure(text=f"Buscando {limite} resultados en SoundCloud...", text_color="#f4f4f5")
        
        for widget in self.scroll_resultados.winfo_children():
            widget.destroy()

        busqueda = query if query.lower().startswith(("http://", "https://")) else f"scsearch{limite}:{query}"
        threading.Thread(target=self._ejecutar_busqueda, args=(busqueda,), daemon=True).start()

    def _ejecutar_busqueda(self, search_query, consulta_amplia=None, limite=None):
        try:
            opts_busqueda = {
                'extract_flat': 'in_playlist',
                'skip_download': True,
                'quiet': True,
            }
            agregar_runtime_ytdlp(opts_busqueda)
            grupos_resultados = []
            consultas = [search_query]
            if consulta_amplia:
                max_por_consulta = max(10, min(100, (int(limite or 50) + 2) // 3))
                consultas = [
                    f"ytsearch{max_por_consulta}:{consulta_amplia}",
                    f"ytsearch{max_por_consulta}:{consulta_amplia} audio",
                    f"ytsearch{max_por_consulta}:{consulta_amplia} -mix -enganchado -dj",
                ]
            with yt_dlp.YoutubeDL(opts_busqueda) as ydl:
                for consulta in consultas:
                    info = ydl.extract_info(consulta, download=False)
                    entries = info.get("entries") if isinstance(info, dict) else None
                    grupo_valido = []
                    for item in (entries or ([info] if info else [])):
                        if not isinstance(item, dict):
                            continue
                        try:
                            self._url_resultado(item)
                        except (TypeError, ValueError):
                            continue
                        grupo_valido.append(item)
                    grupos_resultados.append(grupo_valido)

            resultados = []
            vistos = set()
            # Alterna los resultados de cada consulta para que el modo amplio
            # sí muestre descubrimientos además de los primeros puestos de YouTube.
            for indice in range(max((len(grupo) for grupo in grupos_resultados), default=0)):
                for grupo in grupos_resultados:
                    if indice >= len(grupo):
                        continue
                    item = grupo[indice]
                    item_id = item.get("id") or item.get("url")
                    if item_id and item_id not in vistos:
                        vistos.add(item_id)
                        resultados.append(item)
                        if limite and len(resultados) >= limite:
                            break
                if limite and len(resultados) >= limite:
                    break
            self.resultados_data = resultados[:limite] if limite else resultados

            self.after(0, self.mostrar_resultados)
        except Exception as e:
            error_msg = str(e)
            self.after(0, lambda msg=error_msg: self.error_busqueda(msg))

    def error_busqueda(self, err):
        self.bloquear_botones(False)
        self.label_info.configure(text=f"Error en la búsqueda: {err}", text_color="#f4f4f5")

    def _cancelar_render_resultados(self):
        self._resultados_render_generation += 1
        if self._resultados_render_after is not None:
            try:
                self.after_cancel(self._resultados_render_after)
            except (tk.TclError, RuntimeError):
                pass
            self._resultados_render_after = None
        self._resultados_render_index = 0

    def mostrar_resultados(self):
        self.bloquear_botones(False)

        for widget in self.scroll_resultados.winfo_children():
            widget.destroy()

        if not self.resultados_data:
            self._resultado_card_records = []
            self.btn_queue_results.configure(state="disabled")
            self.label_info.configure(text="No se encontraron resultados.", text_color="#f4f4f5")
            return

        self._resultado_card_records = []
        self.btn_queue_results.configure(state="normal")

        total = len(self.resultados_data)
        self.label_info.configure(
            text=f"Se encontraron {total} temas. Elegí uno para escucharlo o descargarlo.",
            text_color="#f4f4f5"
        )

        # Una lista continua muestra todos los resultados devueltos por el límite elegido.
        self._cancelar_render_resultados()
        self._dibujar_pagina_resultados(self._resultados_render_generation)

    def _dibujar_pagina_resultados(self, generation):
        if generation != self._resultados_render_generation:
            return
        inicio = self._resultados_render_index
        lote = self.resultados_data[inicio:inicio + 20]
        for idx, item in enumerate(lote, start=inicio + 1):
            titulo = item.get("title", "Sin título")
            uploader = item.get("uploader") or item.get("channel", "Desconocido")

            card = ctk.CTkFrame(self.scroll_resultados, fg_color="#141414", corner_radius=8, height=42)
            card.pack(fill="x", pady=2, padx=3)
            card.pack_propagate(False)
            self._resultado_card_records.append((card, f"{titulo} {uploader}".casefold()))
            lbl_texto = ctk.CTkLabel(
                card,
                text=f"{idx}. {titulo}  ·  {uploader}",
                font=fuente_ui(size=11),
                anchor="w",
                text_color="#f4f4f5"
            )
            lbl_texto.pack(side="left", fill="x", expand=True, padx=(10, 4))

            btn_desc = ctk.CTkButton(
                card,
                text="Descargar",
                width=90,
                height=28,
                fg_color="#252529",
                hover_color="#333333",
                font=fuente_ui(size=12, weight="bold"),
                command=lambda i=idx-1: self.descargar_tema(i)
            )
            btn_desc.pack(side="right", padx=(3, 7), pady=5)

            btn_effects = ctk.CTkButton(
                card, text="Hacer Cornetero Normal", width=155, height=28,
                fg_color="#303034", hover_color="#494950",
                font=fuente_ui(size=11, weight="bold"),
                command=lambda i=idx-1: self.descargar_tema_con_efectos(i, "normal")
            )
            btn_effects.pack(side="right", padx=(2, 3), pady=5)

            btn_effects_reverb = ctk.CTkButton(
                card, text="Hacer Cornetero Reverb", width=155, height=28,
                fg_color="#303034", hover_color="#494950",
                font=fuente_ui(size=11, weight="bold"),
                command=lambda i=idx-1: self.descargar_tema_con_efectos(i, "reverb")
            )
            btn_effects_reverb.pack(side="right", padx=(2, 3), pady=5)

            btn_play = ctk.CTkButton(
                card,
                text="▶ Escuchar",
                width=115,
                height=28,
                fg_color="#303034",
                hover_color="#45454b",
                font=fuente_ui(size=12)
            )
            btn_play.pack(side="right", padx=(0, 3), pady=5)

            btn_play.configure(
                command=lambda i=idx-1, b=btn_play:
                    self.toggle_reproducir(i, b)
            )

            try:
                result_url = self._url_resultado(item)
            except ValueError:
                result_url = ""
            is_liked = any(fav.get("url") == result_url for fav in self.likes_data)
            btn_like = ctk.CTkButton(
                card, text="Dislike" if is_liked else "♡ Like", width=82, height=28,
                fg_color="#3b3b40" if is_liked else "#262626",
                hover_color="#55555c" if is_liked else "#3a3a3a",
                font=fuente_ui(size=10, weight="bold"),
                command=None
            )
            btn_like.configure(command=lambda i=idx-1, b=btn_like: self.toggle_like(i, b))
            btn_like.pack(side="right", padx=(2, 4), pady=5)
        if self.entry_filter_results.get().strip():
            self.filtrar_resultados()
        self._resultados_render_index = inicio + len(lote)
        if self._resultados_render_index < len(self.resultados_data):
            self._resultados_render_after = self.after(
                1, lambda token=generation: self._dibujar_pagina_resultados(token)
            )
        else:
            self._resultados_render_after = None

    def _format_result_time(self, seconds):
        seconds = max(0.0, float(seconds))
        minutes = int(seconds // 60)
        secs = seconds - minutes * 60
        return f"{minutes:02d}:{secs:04.1f}"

    def _actualizar_etiqueta_seek(self, value):
        self.label_preview_inicio.configure(text=self._format_result_time(value))

    def _soltar_slider_preview(self, _event=None):
        index = self._resultado_preview_index
        if index is None:
            self._is_result_seeking = False
            return
        self.buscar_mientras_reproduce(
            index, self.slider_preview, self.boton_reproduciendo_actual,
            self.label_preview_inicio, self.label_preview_final
        )

    def _soltar_slider_likes(self, _event=None):
        index = self._resultado_preview_index
        if index is None or self._resultado_preview_item_override is None:
            self._is_result_seeking = False
            return
        self.buscar_mientras_reproduce(
            index, self.likes_slider, self.boton_reproduciendo_actual,
            self.likes_time_start, self.likes_time_end
        )

    def buscar_mientras_reproduce(self, index, slider, btn_clicado, lbl_inicio, lbl_final):
        """Permite mover la barra durante la reproducción y saltar al punto elegido."""
        self._is_result_seeking = False
        nuevo_punto = float(slider.get())
        lbl_inicio.configure(text=self._format_result_time(nuevo_punto))

        # Solo reinicia automáticamente si ESTE mismo resultado está sonando.
        if (
            self.proceso_audio
            and self.proceso_audio.poll() is None
            and self._resultado_preview_index == index
        ):
            self._is_result_seeking = True
            if self._seek_after is not None:
                try:
                    self.after_cancel(self._seek_after)
                except Exception:
                    pass

            # Pequeña pausa para evitar reinicios múltiples al soltar la barra.
            self._seek_after = self.after(
                80,
                lambda: self._reiniciar_preview_en_punto(
                    index, nuevo_punto, btn_clicado, slider, lbl_inicio, lbl_final
                )
            )

    def _reiniciar_preview_en_punto(self, index, punto, btn, slider, lbl_inicio, lbl_final):
        self._seek_after = None
        self._resultado_preview_generation += 1
        generation = self._resultado_preview_generation

        # Detiene únicamente el stream actual, conserva el estado visual del botón.
        if self._resultado_preview_after is not None:
            try:
                self.after_cancel(self._resultado_preview_after)
            except Exception:
                pass
            self._resultado_preview_after = None

        if self.proceso_audio and self.proceso_audio.poll() is None:
            try:
                self.proceso_audio.terminate()
            except Exception:
                pass
        self.proceso_audio = None

        self._resultado_preview_start = punto
        self._resultado_preview_base_position = punto
        self._resultado_preview_started_at = None
        self._resultado_preview_index = index
        self._resultado_preview_slider = slider
        self._resultado_preview_label = lbl_inicio
        self._resultado_preview_final_label = lbl_final
        self._resultado_preview_btn = btn
        preview_item = self._resultado_preview_item_override

        self.label_info.configure(
            text=f"Saltando a {self._format_result_time(punto)}...",
            text_color="#f4f4f5"
        )
        threading.Thread(
            target=self._reproducir_stream,
            args=(index, punto, generation, preview_item),
            daemon=True
        ).start()

    def toggle_reproducir(self, index, btn_clicado, slider=None, lbl_inicio=None, lbl_final=None):
        if self.proceso_audio and self._resultado_preview_index == index:
            self.parar_reproduccion()
            self.label_info.configure(text="Vista previa detenida ⏹", text_color="#f4f4f5")
            return

        previous_index = self._resultado_preview_index
        self.parar_reproduccion()

        item = self.resultados_data[index]
        duration = float(item.get("duration") or 0)
        slider = self.slider_preview
        lbl_inicio = self.label_preview_inicio
        lbl_final = self.label_preview_final
        if previous_index != index:
            slider.set(0)
            lbl_inicio.configure(text="00:00.0")
        slider.configure(
            to=max(duration, 0.001),
            number_of_steps=max(1, min(20000, int(max(duration, 1) * 10))),
            state="normal"
        )
        lbl_final.configure(text=self._format_result_time(duration))
        start_pos = float(slider.get())
        start_pos = max(0.0, min(start_pos, max(0.0, duration - 0.01)))

        self.boton_reproduciendo_actual = btn_clicado
        btn_clicado.configure(
                text="⏹ Detener",
            fg_color="#494950",
            hover_color="#5b5b62"
        )

        self._resultado_preview_start = start_pos
        self._resultado_preview_base_position = start_pos
        self._resultado_preview_started_at = None
        self._resultado_preview_duration = duration
        self._resultado_preview_slider = slider
        self._resultado_preview_label = lbl_inicio
        self._resultado_preview_final_label = lbl_final
        self._resultado_preview_index = index
        self._resultado_preview_item_override = None
        self._resultado_preview_btn = btn_clicado
        generation = self._resultado_preview_generation

        self.label_info.configure(
            text=f"Cargando vista previa desde {self._format_result_time(start_pos)}...",
            text_color="#f4f4f5"
        )
        threading.Thread(
            target=self._reproducir_stream,
            args=(index, start_pos, generation),
            daemon=True
        ).start()

    def _actualizar_tiempo_resultado(self):
        process = self.proceso_audio
        if process is None:
            self._resultado_preview_after = None
            return
        if process.poll() is not None:
            self._resultado_preview_after = None
            error_salida = ""
            try:
                _, stderr_data = process.communicate(timeout=0.1)
                if stderr_data:
                    error_salida = stderr_data.decode("utf-8", errors="replace").strip()
            except (OSError, subprocess.TimeoutExpired):
                pass
            self.parar_reproduccion()
            if process.returncode and error_salida:
                self.label_info.configure(
                    text=f"No se pudo reproducir: {error_salida[:180]}", text_color="#f4f4f5"
                )
            else:
                self.label_info.configure(text="Vista previa finalizada.", text_color="#f4f4f5")
            return

        if self._is_result_seeking:
            self._resultado_preview_after = self.after(100, self._actualizar_tiempo_resultado)
            return

        elapsed = time.monotonic() - (self._resultado_preview_started_at or time.monotonic())
        pos = self._resultado_preview_base_position + elapsed
        self._resultado_preview_start = pos
        duration = self._resultado_preview_duration

        if duration > 0:
            pos = min(pos, duration)

        if self._resultado_preview_slider is not None:
            try:
                self._resultado_preview_slider.set(pos)
            except Exception:
                pass

        if self._resultado_preview_label is not None:
            try:
                self._resultado_preview_label.configure(text=self._format_result_time(pos))
            except Exception:
                pass

        self._resultado_preview_after = self.after(100, self._actualizar_tiempo_resultado)

    def _reproducir_stream(self, index, start_pos=0.0, generation=None, item_override=None):
        if generation is None:
            generation = self._resultado_preview_generation
        try:
            item = item_override if item_override is not None else self.resultados_data[index]
            url = self._url_resultado(item)
            titulo = item.get("title", "Audio")

            cache_key = ("like", url) if item_override is not None else index
            cached = self._resultado_preview_cache.get(cache_key)
            if cached and cached["expires"] > time.time() + 20:
                stream_url = cached["url"]
                stream_headers = cached.get("headers") or {}
            else:
                opts = {
                    "format": "bestaudio/best",
                    "quiet": True,
                    "noplaylist": True,
                    "no_warnings": True,
                }
                agregar_runtime_ytdlp(opts)
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                if generation != self._resultado_preview_generation:
                    return
                stream_url = info.get("url") if isinstance(info, dict) else None
                stream_headers = info.get("http_headers") or {} if isinstance(info, dict) else {}
                if stream_url:
                    query = urllib.parse.parse_qs(urllib.parse.urlparse(stream_url).query)
                    try:
                        expires = float(query.get("expire", [0])[0]) or time.time() + 1800
                    except (TypeError, ValueError):
                        expires = time.time() + 1800
                    self._resultado_preview_cache[cache_key] = {
                        "url": stream_url, "expires": expires,
                        "headers": stream_headers,
                    }

            if not stream_url:
                raise RuntimeError("No se pudo obtener el stream de audio.")
            if generation != self._resultado_preview_generation:
                return

            ffplay = localizar_programa("ffplay")
            if not ffplay:
                raise RuntimeError(
                    "No se encontró FFplay. Reinstalá la app con FFmpeg incluido "
                    "o instalá FFmpeg y agregá su carpeta bin al PATH."
                )
            # Reutiliza la URL firmada cacheada: al buscar otro segundo no vuelve
            # a consultar la página de YouTube mediante yt-dlp.
            cmd = [
                ffplay, "-nodisp", "-autoexit", "-loglevel", "error",
                "-probesize", "65536", "-analyzeduration", "1000000",
                "-ss", f"{start_pos:.3f}",
            ]
            # YouTube y otras fuentes pueden exigir User-Agent/Referer del extractor.
            # Pasarlos a FFplay evita el 403 que cerraba el proceso al instante.
            if stream_headers:
                headers_text = "".join(f"{key}: {value}\r\n" for key, value in stream_headers.items())
                cmd.extend(["-headers", headers_text])
            cmd.append(stream_url)
            process = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                **opciones_subproceso_oculto()
            )
            try:
                self.after(0, lambda proc=process: self._adjuntar_preview(
                    proc, index, titulo, start_pos, generation
                ))
            except (RuntimeError, tk.TclError):
                process.terminate()
        except Exception as e:
            if generation != self._resultado_preview_generation:
                return
            error_msg = str(e)
            try:
                self.after(0, lambda msg=error_msg, token=generation: self._error_preview_stream(token, msg))
            except (RuntimeError, tk.TclError):
                pass

    def _adjuntar_preview(self, process, index, titulo, start_pos, generation):
        if generation != self._resultado_preview_generation:
            try:
                process.terminate()
            except OSError:
                pass
            return
        self.proceso_audio = process
        self._resultado_preview_base_position = start_pos
        self._resultado_preview_start = start_pos
        self._resultado_preview_started_at = time.monotonic()
        self._is_result_seeking = False
        self.label_info.configure(
            text=f"Vista previa: '{titulo}' desde {self._format_result_time(start_pos)}",
            text_color="#f4f4f5"
        )
        self._resultado_preview_after = self.after(80, self._actualizar_tiempo_resultado)

    def _error_preview_stream(self, generation, error):
        if generation != self._resultado_preview_generation:
            return
        self.parar_reproduccion()
        self.label_info.configure(text=f"Error al reproducir: {error[:220]}", text_color="#f4f4f5")

    def descargar_tema(self, index):
        item = self.resultados_data[index]
        titulo = item.get('title', 'Audio')
        try:
            url = self._url_resultado(item)
        except ValueError as exc:
            self.label_info.configure(text=str(exc), text_color="#f4f4f5")
            return

        seleccion_kbps = self.combo_kbps.get()
        if "320" in seleccion_kbps:
            kbps = "320"
        elif "192" in seleccion_kbps:
            kbps = "192"
        else:
            kbps = "128"

        self.label_info.configure(text=f"Descargando MP3 a {kbps} kbps: '{titulo}'...", text_color="#f4f4f5")
        threading.Thread(target=self._ejecutar_descarga, args=(url, titulo, kbps), daemon=True).start()

    def descargar_tema_con_efectos(self, index, reverb_variant="normal"):
        item = self.resultados_data[index]
        titulo = item.get("title", "Audio")
        try:
            url = self._url_resultado(item)
        except ValueError as exc:
            self.label_info.configure(text=str(exc), text_color="#f4f4f5")
            return
        calidad = self.combo_kbps.get()
        kbps = "320" if "320" in calidad else "192" if "192" in calidad else "128"
        variant_name = "Normal" if reverb_variant == "normal" else "Reverb"
        self.label_info.configure(
            text=f"Descargando y preparando Cornetero {variant_name}: {titulo[:55]}…",
            text_color="#f4f4f5"
        )
        threading.Thread(
            target=self._ejecutar_descarga,
            args=(url, titulo, kbps, True, reverb_variant), daemon=True
        ).start()

    def descargar_playlist_youtube(self):
        """Descarga como MP3 todos los elementos de una playlist de YouTube."""
        url = self.entry_query.get().strip()
        if not url.lower().startswith(("http://", "https://")):
            messagebox.showinfo(
                "Pegá el enlace de una playlist",
                "Ingresá un enlace de YouTube que contenga el parámetro list=.",
                parent=self
            )
            return

        try:
            parsed = urllib.parse.urlparse(url)
            host = (parsed.hostname or "").lower()
            params = urllib.parse.parse_qs(parsed.query)
            es_youtube = host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com")
            if not es_youtube or not params.get("list"):
                raise ValueError("El enlace debe ser una playlist de YouTube con un parámetro list=.")
        except ValueError as exc:
            messagebox.showerror("Enlace no válido", str(exc), parent=self)
            return

        if self._playlist_download_active:
            self.label_info.configure(
                text="Ya hay una playlist descargándose. Esperá a que termine.",
                text_color="#f4f4f5"
            )
            return

        if not localizar_programa("ffmpeg"):
            messagebox.showerror(
                "Falta FFmpeg",
                "La conversión de la playlist a MP3 requiere FFmpeg. Reinstalá la app "
                "con FFmpeg incluido o agregá su carpeta bin al PATH.",
                parent=self
            )
            return

        kbps_txt = self.combo_kbps.get()
        kbps = "320" if "320" in kbps_txt else "192" if "192" in kbps_txt else "128"
        self._playlist_download_active = True
        self.btn_playlist.configure(state="disabled", text="Descargando playlist…")
        self.label_info.configure(text="Preparando playlist de YouTube…", text_color="#f4f4f5")
        threading.Thread(
            target=self._ejecutar_descarga_playlist,
            args=(url, kbps),
            daemon=True
        ).start()

    def _ejecutar_descarga_playlist(self, url, kbps, entradas_preseleccionadas=None,
                                    titulo_lista=None):
        """Prepara la lista, permite elegir temas y luego descarga la selección."""
        selector_pendiente = False
        try:
            os.makedirs(self.carpeta_destino, exist_ok=True)
            if entradas_preseleccionadas is None:
                opciones_lista = {
                    "extract_flat": "in_playlist",
                    "skip_download": True,
                    "ignoreerrors": True,
                    "quiet": True,
                }
                agregar_runtime_ytdlp(opciones_lista)
                with yt_dlp.YoutubeDL(opciones_lista) as ydl:
                    playlist = ydl.extract_info(url, download=False)
                if not isinstance(playlist, dict):
                    raise RuntimeError("No se pudo leer la información de la playlist.")
                entradas = [item for item in (playlist.get("entries") or []) if isinstance(item, dict)]
                if not entradas:
                    raise RuntimeError("La playlist está vacía o no contiene temas accesibles.")
                titulo_lista = playlist.get("title") or "Playlist YouTube"
                self.after(0, lambda entries=list(entradas), name=titulo_lista, rate=kbps:
                           self._mostrar_selector_playlist(entries, name, rate))
                selector_pendiente = True
                return
            entradas = [item for item in entradas_preseleccionadas if isinstance(item, dict)]
            if not entradas:
                raise RuntimeError("Elegí al menos un tema de la playlist.")
            titulo_lista = titulo_lista or "Playlist YouTube"
            carpeta_lista = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", titulo_lista).strip(" .")
            carpeta_lista = carpeta_lista[:100] or "Playlist YouTube"
            if carpeta_lista.upper() in {"CON", "PRN", "AUX", "NUL"} or re.fullmatch(r"(?:COM|LPT)[1-9]", carpeta_lista.upper()):
                carpeta_lista = "_" + carpeta_lista
            destino_lista = os.path.join(self.carpeta_destino, carpeta_lista)
            os.makedirs(destino_lista, exist_ok=True)

            total = len(entradas)
            exitos = []
            errores = []
            finalizados = 0
            lock = threading.Lock()
            trabajadores, fragmentos = perfil_descarga_adaptativo()

            def descargar_uno(numero, entrada):
                titulo = entrada.get("title") or f"Tema {numero}"
                video_url = entrada.get("webpage_url") or entrada.get("url")
                ultimo_aviso_ui = [0.0]
                if not video_url or not str(video_url).startswith(("http://", "https://")):
                    video_id = entrada.get("id") or video_url
                    if not video_id:
                        raise RuntimeError("El tema no tiene un enlace válido.")
                    video_url = f"https://www.youtube.com/watch?v={video_id}"

                def progreso_tema(d):
                    if self._queue_cancel_event.is_set():
                        raise yt_dlp.utils.DownloadCancelled("Cola cancelada por el usuario.")
                    while not self._queue_pause_event.wait(0.2):
                        if self._queue_cancel_event.is_set():
                            raise yt_dlp.utils.DownloadCancelled("Cola cancelada por el usuario.")
                    if d.get("status") == "downloading":
                        ahora = time.monotonic()
                        if ahora - ultimo_aviso_ui[0] < 0.35:
                            return
                        ultimo_aviso_ui[0] = ahora
                        bytes_total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                        bytes_descargados = d.get("downloaded_bytes") or 0
                        avance = f"{min(100, bytes_descargados * 100 / bytes_total):.0f}%" if bytes_total else "descargando"
                        texto = f"Tema {numero}/{total} · {avance} · {titulo[:42]}"
                        try:
                            self.after(0, lambda msg=texto: self.label_info.configure(
                                text=msg, text_color="#f4f4f5"
                            ))
                        except (RuntimeError, tk.TclError):
                            pass
                    elif d.get("status") == "finished":
                        texto = f"Tema {numero}/{total} · convirtiendo a MP3 · {titulo[:42]}"
                        try:
                            self.after(0, lambda msg=texto: self.label_info.configure(
                                text=msg, text_color="#f4f4f5"
                            ))
                        except (RuntimeError, tk.TclError):
                            pass

                opciones_tema = {
                    "format": formato_audio_descarga(kbps),
                    "outtmpl": os.path.join(destino_lista, f"{numero:03d} - %(title)s.%(ext)s"),
                    "noplaylist": True,
                    "nooverwrites": True,
                    "ignoreerrors": False,
                    "quiet": True,
                    "no_warnings": False,
                    "retries": 5,
                    "fragment_retries": 5,
                    "socket_timeout": 30,
                    "concurrent_fragment_downloads": fragmentos,
                    "writethumbnail": False,
                    "progress_hooks": [progreso_tema],
                    "postprocessors": [
                        {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": str(kbps)},
                        {"key": "FFmpegMetadata"},
                    ],
                }
                agregar_runtime_ytdlp(opciones_tema)
                with yt_dlp.YoutubeDL(opciones_tema) as ydl:
                    info = ydl.extract_info(video_url, download=True)
                    if not isinstance(info, dict):
                        raise RuntimeError("yt-dlp no devolvió información del tema.")
                    base = ydl.prepare_filename(info)
                    candidatos = [info.get("filepath"), os.path.splitext(base)[0] + ".mp3"]
                    candidatos.extend(
                        item.get("filepath") for item in info.get("requested_downloads", [])
                        if isinstance(item, dict)
                    )
                    archivo = next(
                        (p for p in candidatos if p and os.path.isfile(p) and os.path.getsize(p) > 0),
                        None
                    )
                    if not archivo:
                        raise RuntimeError("La conversión no produjo un MP3.")
                    return archivo

            with concurrent.futures.ThreadPoolExecutor(
                max_workers=min(trabajadores, total), thread_name_prefix="playlist-mp3"
            ) as pool:
                tareas = {
                    pool.submit(descargar_uno, numero, entrada): numero
                    for numero, entrada in enumerate(entradas, start=1)
                }
                for tarea in concurrent.futures.as_completed(tareas):
                    numero = tareas[tarea]
                    try:
                        exitos.append(tarea.result())
                    except Exception as exc:
                        errores.append(f"Tema {numero}: {str(exc).strip() or repr(exc)}")
                    with lock:
                        finalizados += 1
                        procesados = finalizados
                    estado = f"Playlist: {procesados}/{total} procesados · {len(exitos)} MP3 listos · {len(errores)} omitidos"
                    try:
                        self.after(0, lambda msg=estado: self.label_info.configure(
                            text=msg, text_color="#f4f4f5"
                        ))
                    except (RuntimeError, tk.TclError):
                        pass

            if not exitos:
                detalle = errores[0] if errores else "No hubo temas descargables."
                raise RuntimeError(
                    f"No se generaron MP3. {detalle[:150]}"
                )
            mensaje = f"Playlist terminada: {len(exitos)}/{total} MP3 descargados en {destino_lista}"
            if errores:
                mensaje += f" · {len(errores)} tema(s) omitido(s)"
            self.after(0, lambda msg=mensaje: self.label_info.configure(
                text=msg, text_color="#f4f4f5"
            ))
        except Exception as exc:
            msg = str(exc).strip() or repr(exc)
            self.after(0, lambda error=msg: self.label_info.configure(
                text=f"Error al descargar la playlist: {error[:190]}", text_color="#f4f4f5"
            ))
        finally:
            if not selector_pendiente:
                self._playlist_download_active = False
                self._queue_active = False
                self._queue_paused = False
                try:
                    self.after(0, lambda: self.btn_playlist.configure(
                        state="normal", text="≡ Descargar playlist de YouTube como MP3"
                    ))
                    self.after(0, lambda: self.btn_pause_queue.configure(
                        state="disabled", text="Pausar cola"
                    ))
                    self.after(0, lambda: self.btn_cancel_queue.configure(
                        state="disabled", text="Cancelar cola"
                    ))
                except RuntimeError:
                    pass

    def _mostrar_selector_playlist(self, entradas, titulo_lista, kbps):
        win = ctk.CTkToplevel(self)
        win.title(f"Elegir temas · {titulo_lista}")
        win.geometry("720x560")
        win.minsize(540, 380)
        win.configure(fg_color="#18181b")
        win.transient(self)
        win.grab_set()
        ctk.CTkLabel(
            win, text=f"PLAYLIST · {len(entradas)} temas",
            font=fuente_ui(size=16, weight="bold"), text_color="#f4f4f5"
        ).pack(anchor="w", padx=16, pady=(14, 4))
        ctk.CTkLabel(
            win, text=titulo_lista, text_color="#bcbcc0", anchor="w"
        ).pack(anchor="w", padx=16, pady=(0, 8))
        body = ctk.CTkFrame(win, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=14, pady=4)
        listing = tk.Listbox(
            body, bg="#101012", fg="#f4f4f5", selectbackground="#494950",
            selectmode="extended", relief="flat", highlightthickness=0,
            font=("Segoe UI", 10), exportselection=False
        )
        listing.pack(side="left", fill="both", expand=True)
        scrollbar = ctk.CTkScrollbar(body, command=listing.yview)
        scrollbar.pack(side="left", fill="y", padx=(4, 0))
        listing.configure(yscrollcommand=scrollbar.set)
        for index, item in enumerate(entradas, start=1):
            listing.insert("end", f"{index:03d}. {item.get('title') or item.get('id') or 'Tema'}")
        if entradas:
            listing.selection_set(0, "end")
        controls = ctk.CTkFrame(win, fg_color="transparent")
        controls.pack(fill="x", padx=14, pady=(5, 13))

        def start_selected():
            indices = set(listing.curselection())
            selection = [item for index, item in enumerate(entradas) if index in indices]
            if not selection:
                messagebox.showinfo("Sin temas", "Seleccioná al menos un tema para descargar.", parent=win)
                return
            self._playlist_download_active = True
            self._queue_cancel_event.clear()
            self._queue_pause_event.set()
            self._queue_paused = False
            self._queue_active = True
            self.btn_playlist.configure(state="disabled", text="Descargando playlist…")
            self.btn_pause_queue.configure(state="normal", text="Pausar cola")
            self.btn_cancel_queue.configure(state="normal", text="Cancelar cola")
            self.label_info.configure(text=f"Descargando {len(selection)} temas de la playlist…", text_color="#f4f4f5")
            win.destroy()
            threading.Thread(
                target=self._ejecutar_descarga_playlist,
                args=(None, kbps, selection, titulo_lista), daemon=True
            ).start()

        def cancel_selection():
            win.destroy()
            self._playlist_download_active = False
            self.btn_playlist.configure(state="normal", text="≡ Descargar playlist de YouTube como MP3")
            self.label_info.configure(text="Selección de playlist cancelada.", text_color="#f4f4f5")

        self._button(controls, "Todas", lambda: listing.selection_set(0, "end"), width=90).pack(side="left", padx=3)
        self._button(controls, "Ninguna", lambda: listing.selection_clear(0, "end"), width=90).pack(side="left", padx=3)
        self._button(controls, "Cancelar", cancel_selection, width=100).pack(side="right", padx=3)
        self._button(controls, "Descargar selección", start_selected, width=160).pack(side="right", padx=3)

    def _ejecutar_descarga(self, url, titulo, kbps, con_efectos=False, reverb_variant="normal",
                           pause_event=None, cancel_event=None):
        """Descarga el audio en un hilo y actualiza la interfaz de forma segura."""
        if cancel_event is None:
            cancel_event = self._shutdown_event
        try:
            os.makedirs(self.carpeta_destino, exist_ok=True)
            ruta_salida = os.path.join(self.carpeta_destino, '%(title)s.%(ext)s')
            ultimo_aviso = [0.0]

            def progreso(d):
                if cancel_event is not None and cancel_event.is_set():
                    raise yt_dlp.utils.DownloadCancelled("Cola cancelada por el usuario.")
                if pause_event is not None:
                    while not pause_event.wait(0.2):
                        if cancel_event is not None and cancel_event.is_set():
                            raise yt_dlp.utils.DownloadCancelled("Cola cancelada por el usuario.")
                estado = d.get("status")
                if estado == "downloading":
                    ahora = time.monotonic()
                    if ahora - ultimo_aviso[0] < 0.25:
                        return
                    ultimo_aviso[0] = ahora
                    total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                    descargado = d.get("downloaded_bytes", 0)
                    if total:
                        porcentaje = max(0, min(100, descargado * 100 / total))
                        texto = f"Descargando: {porcentaje:.1f}% — {titulo[:55]}"
                    else:
                        mb = descargado / (1024 * 1024)
                        texto = f"Descargando: {mb:.1f} MB — {titulo[:55]}"
                    self.after(0, lambda msg=texto: self.label_info.configure(
                        text=msg, text_color="#f4f4f5"
                    ))
                elif estado == "finished":
                    self.after(0, lambda: self.label_info.configure(
                        text="Descarga recibida; convirtiendo a MP3…",
                        text_color="#f4f4f5"
                    ))

            opts_descarga = {
                "format": formato_audio_descarga(kbps),
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": str(kbps),
                    },
                    {"key": "FFmpegMetadata"},
                ],
                "outtmpl": ruta_salida,
                "noplaylist": True,
                "nooverwrites": True,
                "keepvideo": False,
                "prefer_ffmpeg": True,
                "concurrent_fragment_downloads": perfil_descarga_adaptativo()[1],
                "retries": 5,
                "fragment_retries": 5,
                "socket_timeout": 30,
                "ignoreerrors": False,
                "quiet": True,
                "no_warnings": False,
                "progress_hooks": [progreso],
            }

            # Thumbnail embedding is optional: don't let missing artwork prevent a download.
            opts_descarga["writethumbnail"] = True
            opts_descarga["postprocessors"].append({
                "key": "EmbedThumbnail",
                "already_have_thumbnail": False,
            })

            agregar_runtime_ytdlp(opts_descarga)

            with yt_dlp.YoutubeDL(opts_descarga) as ydl:
                info = ydl.extract_info(url, download=False)
                if not info:
                    raise RuntimeError(
                        "yt-dlp no devolvió información del audio. Puede que el enlace esté "
                        "privado, eliminado o restringido."
                    )
                metadata_title = str(info.get("title") or "").strip()
                if metadata_title:
                    titulo = metadata_title
                try:
                    expected_name = ydl.prepare_filename(info)
                    expected_mp3 = os.path.splitext(expected_name)[0] + ".mp3"
                    if os.path.isfile(expected_mp3) and os.path.getsize(expected_mp3) > 0:
                        try:
                            self.after(0, lambda path=expected_mp3: self.label_info.configure(
                                text=f"Ya estaba descargado; no se reemplazó: {path}",
                                text_color="#f4f4f5"
                            ))
                        except (RuntimeError, tk.TclError):
                            pass
                        return "exists"
                except (OSError, TypeError, ValueError):
                    pass
                info = ydl.process_ie_result(info, download=True)

            if not info:
                raise RuntimeError(
                    "yt-dlp no devolvió información del audio. Puede que el enlace esté "
                    "privado, eliminado o restringido."
                )

            # Verify a finished audio file exists; don't report success on ignored failures.
            base = ydl.prepare_filename(info)
            candidates = [
                info.get("filepath"),
                os.path.splitext(base)[0] + ".mp3",
                base,
            ]
            # Algunos extractores publican el nombre final sólo en requested_downloads.
            candidates.extend(
                entry.get("filepath")
                for entry in info.get("requested_downloads", [])
                if isinstance(entry, dict)
            )
            output_file = next(
                (p for p in candidates if p and os.path.isfile(p) and os.path.getsize(p) > 0),
                None
            )
            if not output_file:
                raise RuntimeError(
                    "La descarga no generó un MP3. Revisá que FFmpeg esté instalado y que "
                    "el audio permita la descarga."
                )

            if con_efectos:
                variant_name = "Normal" if reverb_variant == "normal" else "Reverb"
                self.after(0, lambda: self.label_info.configure(
                    text=f"Aplicando Cornetero {variant_name}: paso alto, curva y paso bajo · {titulo[:38]}…",
                    text_color="#f4f4f5"
                ))
                effect_tracks = self._crear_descarga_con_efectos(
                    output_file, titulo, kbps, reverb_variant
                )
                for effect_track in effect_tracks:
                    try:
                        self._registrar_descarga(
                            effect_track["name"], url, kbps, effect_track["path"]
                        )
                    except OSError as exc:
                        print(f"No se pudo guardar el historial de '{effect_track['name']}': {exc}")
                self.after(0, lambda tracks=effect_tracks, name=titulo, variant=reverb_variant:
                           self._abrir_editor_descarga_efectos(tracks, name, variant))

            try:
                self._registrar_descarga(titulo, url, kbps, output_file)
            except OSError as exc:
                print(f"No se pudo guardar el historial de '{titulo}': {exc}")

            self.after(0, lambda path=output_file: self.label_info.configure(
                text=f"¡Descarga completada! Guardado en: {path}",
                text_color="#f4f4f5"
            ))
            return True

        except Exception as e:
            if cancel_event is not None and cancel_event.is_set():
                return False
            error_msg = str(e).strip() or repr(e)
            # SoundCloud sometimes marks a track as available only to registered users.
            if "Original download format is only available for registered users" in error_msg:
                error_msg = (
                    "SoundCloud restringe la descarga original de este tema a usuarios registrados. "
                    "Probá otro resultado o un enlace que permita la descarga."
                )
            print(f"Error al descargar '{titulo}' ({type(e).__name__}): {error_msg}")
            self.after(0, lambda msg=error_msg: self.label_info.configure(
                text=f"Error al descargar: {msg[:220]}",
                text_color="#f4f4f5"
            ))
            return False

    def _crear_descarga_con_efectos(self, source_path, titulo, kbps, reverb_variant="normal"):
        """Crea las dos pistas Cornetero con la variante de reverb solicitada."""
        if np is None or sf is None or signal is None:
            raise RuntimeError("Para aplicar estos efectos instalá numpy, soundfile y scipy.")
        ffmpeg = localizar_programa("ffmpeg")
        if not ffmpeg:
            raise RuntimeError(
                "No se encontró FFmpeg junto a la app ni en el PATH de Windows. "
                "Reinstalá con FFmpeg incluido o agregá su carpeta bin al PATH."
            )
        fd, decoded_path = tempfile.mkstemp(prefix="descarga_efectos_", suffix=".wav")
        os.close(fd)
        try:
            subprocess.run(
                [ffmpeg, "-y", "-i", source_path, "-vn", "-ar", "44100",
                 "-acodec", "pcm_f32le", decoded_path],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                **opciones_subproceso_oculto()
            )
            original, sample_rate = sf.read(decoded_path, dtype="float32", always_2d=True)
        finally:
            if os.path.exists(decoded_path):
                os.remove(decoded_path)
        if not len(original):
            raise RuntimeError("El MP3 descargado no contiene audio para procesar.")

        def apply_filter(data, kind, cutoff):
            sos = signal.butter(4, cutoff, btype=kind, fs=sample_rate, output="sos")
            # Un pase de Butterworth de orden 4 corresponde a 24 dB/octava,
            # igual a la opción visible en los cuadros de filtro del editor.
            return signal.sosfilt(sos, data, axis=0)

        # Procesar en serie limita el pico de RAM en enganchados largos.
        # Duplicado de abajo: paso bajo de 48 Hz, cuarto orden.
        low_track = apply_filter(original, "lowpass", 48.0).astype(np.float32, copy=False)
        main_track = apply_filter(original, "highpass", 300.0).astype(np.float32, copy=False)
        del original
        # Original de arriba: paso alto de 300 Hz, curva adjunta y reverb sólo wet.
        main_track = EditorAudio._filter_curve_data(
            main_track, sample_rate, EditorAudio.DESCARGA_EQ_POINTS
        ).astype(np.float32)

        if reverb_variant == "normal":
            # Cornetero Normal conserva el reverb original (20% sala, 30% wet).
            room, amount = 0.20, 0.30
            base_delay = max(1, int(sample_rate * (12 + 45 * room) / 1000))
            delays = [base_delay, int(base_delay * 1.37), int(base_delay * 1.91), int(base_delay * 2.63)]
            decay = min(0.88, 0.25 + amount * 0.62 + room * 0.08)
            length, channels = main_track.shape
            dry = main_track.copy()
            for channel in range(channels):
                source = dry[:, channel]
                for repeat in range(1, 5):
                    for index, delay in enumerate(delays):
                        offset = delay * repeat
                        available = length - offset
                        if available > 0:
                            main_track[offset:, channel] += (
                                source[:available] * (decay ** repeat) * (0.7 ** index)
                            )
            del dry  # Cornetero Normal: Sólo Wet, 0 dB, sin damping, ancho 100%.
        # La variante Reverb usa los valores exactos de la captura:
        # sala 0%, retardo 0 ms, reverberación 0%, wet/dry 0 dB,
        # tonos 0/100%, ancho estéreo 100%, Sólo Wet desactivado.

        # Fade In dos veces en los primeros 5 segundos de la pista superior,
        # equivalente a aplicar dos veces el fade de 8 s sobre una selección de 5 s.
        fade_samples = min(len(main_track), int(round(5.0 * sample_rate)))
        if fade_samples > 1:
            fade_curve = np.linspace(0.0, 1.0, fade_samples, dtype=np.float32)[:, None]
            main_track[:fade_samples] *= fade_curve
            main_track[:fade_samples] *= fade_curve

        safe_stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", os.path.splitext(os.path.basename(source_path))[0])
        safe_stem = safe_stem.strip(" .")[:110] or "Audio"
        prefix = "Cornetero_Normal" if reverb_variant == "normal" else "Cornetero_Reverb"
        variants = [
            (f"{prefix}_01_Original_PasoAlto_Curva", main_track),
            (f"{prefix}_02_Duplicado_PasoBajo", low_track),
        ]
        def encode_variant(item):
            suffix, data = item
            base = os.path.join(self.carpeta_destino, f"{safe_stem}_{suffix}")
            path = base + ".mp3"
            number = 2
            while os.path.exists(path):
                path = f"{base}_{number}.mp3"
                number += 1
            fd, temp_wav = tempfile.mkstemp(prefix="efecto_mp3_", suffix=".wav")
            os.close(fd)
            try:
                # El reproductor recorta picos fuera del rango audible; exportamos igual.
                sf.write(temp_wav, np.clip(data, -1.0, 1.0), sample_rate, subtype="PCM_16")
                subprocess.run(
                    [ffmpeg, "-y", "-i", temp_wav, "-vn", "-codec:a", "libmp3lame",
                     "-b:a", f"{kbps}k", path],
                    check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                    **opciones_subproceso_oculto()
                )
            finally:
                if os.path.exists(temp_wav):
                    os.remove(temp_wav)
            return {
                "name": f"{titulo} · {suffix}", "path": path, "data": data,
                "sr": int(sample_rate), "undo": [], "redo": [],
                "muted": False, "solo": False, "gain_db": 1.0,
                "gain_min": 1.0, "gain_max": 36.0, "pan": 0.0,
            }

        with concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="mp3-efectos") as pool:
            tracks = list(pool.map(encode_variant, variants))

        intro_path = os.path.join(os.path.expanduser("~"), "Downloads", "EthanSinnotekopie.mp3")
        if os.path.isfile(intro_path):
            try:
                fd, intro_wav = tempfile.mkstemp(prefix="editor_intro_", suffix=".wav")
                os.close(fd)
                try:
                    subprocess.run(
                        [ffmpeg, "-y", "-i", intro_path, "-vn", "-ar", "44100",
                         "-acodec", "pcm_f32le", intro_wav],
                        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                        **opciones_subproceso_oculto()
                    )
                    intro_data, intro_sr = sf.read(intro_wav, dtype="float32", always_2d=True)
                finally:
                    if os.path.exists(intro_wav):
                        os.remove(intro_wav)
                if len(intro_data):
                    tracks.append({
                        "name": "EthanSinnotekopie.mp3", "path": intro_path,
                        "data": intro_data, "sr": int(intro_sr),
                        "undo": [], "redo": [], "muted": False, "solo": False,
                        "gain_db": 0.0, "gain_min": -36.0, "gain_max": 36.0, "pan": 0.0,
                    })
            except Exception as exc:
                # La intro es opcional: un archivo ausente o ilegible no debe
                # impedir abrir el proyecto con las dos pistas procesadas.
                print(f"Se omite la intro opcional '{intro_path}': {exc}")
        return tracks

    def _abrir_editor_descarga_efectos(self, tracks, titulo, reverb_variant="normal"):
        editor = EditorAudio(self)
        editor.tracks = tracks
        editor.selected_index = 0
        editor.refresh_tracks()
        intro_info = "; pista 3 EthanSinnotekopie.mp3" if len(tracks) > 2 else ""
        variant_name = "Cornetero Normal (reverb original)" if reverb_variant == "normal" else "Cornetero Reverb (ajustes de la captura)"
        editor.set_info(
            f"{titulo}: {variant_name}; pista 1 con paso alto, curva y doble Fade In de 5 s; "
            f"pista 2 con paso bajo{intro_info}. "
            "Tocá ▶ para preescuchar la mezcla.",
            "#f4f4f5"
        )
        editor.lift()
        self.label_info.configure(
            text=f"Descarga procesada y proyecto de {len(tracks)} pistas abierto desde {self.carpeta_destino}",
            text_color="#f4f4f5"
        )


class _AudioStreamHandle:
    """Adaptador mínimo para que el monitor de preview trate el stream como proceso."""

    def __init__(self, stream):
        self.stream = stream

    def poll(self):
        try:
            return None if self.stream.active else 0
        except Exception:
            return 0

    def terminate(self):
        try:
            self.stream.stop()
        except Exception:
            pass
        try:
            self.stream.close()
        except Exception:
            pass


class EditorAudio(ctk.CTkToplevel):
    """Editor multipista sencillo con filtros, curva EQ, reverberación y exportación."""

    PRESET_FILE = os.path.join(os.path.expanduser("~"), ".buscador_mp3_filter_curve.json")
    EFFECT_PRESETS_FILE = os.path.join(os.path.expanduser("~"), ".buscador_mp3_effect_presets.json")
    DESCARGA_EQ_POINTS = (
        (20.0, 6.0), (293.02522, 5.9312325), (388.18995, 6.7191982),
        (492.80786, 6.446991), (505.56989, 9.7134666), (599.52184, 15.214899),
        (692.98725, 18.137535), (999.71553, 18.137535), (1393.8719, 18.137535),
        (2324.3113, 15.04298), (4920.5789, 6.5968246), (19312.131, 7.1555519),
    )
    GRAPHIC_EQ_FREQUENCIES = (
        20, 25, 31.5, 40, 50, 63, 80, 100, 125, 160, 200, 250, 315, 400,
        500, 630, 800, 1000, 1250, 1600, 2000, 2500, 3150, 4000, 5000,
        6300, 8000, 10000, 12500, 16000, 20000,
    )

    def __init__(self, master):
        super().__init__(master)
        self.title("Corneteros Studio - Editor de audio")
        aplicar_icono_ventana(self)
        self.geometry("1120x760")
        self.minsize(900, 620)
        self.configure(fg_color="#080808")
        self.tracks = []
        self.selected_index = None
        self.selected_indices = set()
        self.current_project_path = None
        self._autosave_after = None
        self._autosave_file = PROJECT_AUTOSAVE_FILE[:-4] + f"_{os.getpid()}_{uuid.uuid4().hex[:8]}.csp"
        self._audio_output_device = None
        self._load_audio_output_settings()
        self._preview_process = None
        self._preview_temp = None
        self._preview_start = 0.0
        self._preview_position = 0.0
        self._preview_duration = 0.0
        self._preview_after = None
        self._preview_track_index = None
        self._preview_mix_path = None
        self._preview_live_tracks = []
        self._preview_started_at = None
        self._loop_restart_after = None
        self._preview_generation = 0
        self._preview_preparing = False
        self._preview_paused = False
        self._mix_active = False
        self._effect_preview_session = None
        self._is_seeking = False
        self._wave_view_center = 0.0
        self._selection_start = None
        self._selection_end = None
        self._audacity_temp_paths = []
        self.wave_zoom = tk.DoubleVar(value=1.0)
        self.preview_volume = tk.IntVar(value=85)
        self._preview_volume_live = 85
        self.preview_speed = tk.StringVar(value="1.0x")
        self.preview_loop = tk.BooleanVar(value=False)

        top = ctk.CTkFrame(self, fg_color="#111111", corner_radius=8)
        top.pack(fill="x", padx=12, pady=(12, 8))
        brand = ctk.CTkFrame(top, fg_color="transparent")
        brand.pack(side="left", fill="x", expand=True, padx=14, pady=8)
        ctk.CTkLabel(
            brand, text="EDITOR DE AUDIO", text_color="#ffffff",
            font=fuente_ui(size=18, weight="bold")
        ).pack(anchor="w")
        ctk.CTkLabel(
            brand, text="Multipista · selección múltiple · Ctrl+A pistas · Ctrl+D duplicar",
            text_color="#a1a1aa", font=fuente_ui(size=10), anchor="w"
        ).pack(anchor="w", pady=(1, 0))

        actions = ctk.CTkFrame(top, fg_color="transparent")
        actions.pack(side="right", padx=8, pady=7)
        self._button(actions, "＋ Importar audio", self.import_audio, width=135).pack(side="left", padx=3)
        self._button(actions, "Guardar proyecto", self.guardar_proyecto, width=145).pack(side="left", padx=3)

        transport = ctk.CTkFrame(self, fg_color="#18181b", corner_radius=8)
        transport.pack(fill="x", padx=12, pady=(0, 8))
        transport_buttons = ctk.CTkFrame(transport, fg_color="transparent")
        transport_buttons.pack(side="left", padx=8, pady=5)
        for icon, command, hint in (
            ("Ⅱ", self.toggle_pause_preview, "Pausar / continuar"),
            ("▶", self.play_preview, "Reproducir"),
            ("■", self.stop_preview, "Detener"),
            ("|◀", self.restart_preview, "Volver al inicio"),
            ("▶|", self.end_preview, "Ir al final"),
        ):
            button = self._button(transport_buttons, icon, command, width=48)
            button.configure(height=38, font=fuente_ui(size=17, weight="bold"))
            button.pack(side="left", padx=2)
        ctk.CTkLabel(
            transport, text="Transporte · reproduce la mezcla audible",
            text_color="#f4f4f5", font=fuente_ui(size=10)
        ).pack(side="left", padx=10)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(body, fg_color="#18181b", corner_radius=8)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left.grid_rowconfigure(0, weight=1)
        left.grid_columnconfigure(0, weight=1)

        # Vista multipista: cada pista muestra su propia forma de onda y su menú.
        self.tracks_view = ctk.CTkScrollableFrame(
            left, fg_color="#101012", corner_radius=8,
            scrollbar_button_color="#444444", scrollbar_button_hover_color="#f4f4f5"
        )
        self.tracks_view.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 4))
        self.track_wave_canvases = []
        self.track_select_buttons = []
        self.track_rows = []
        self.track_mute_buttons = []
        self.track_solo_buttons = []
        self.track_gain_buttons = []
        self.track_pan_buttons = []
        self.wave_canvas = None

        timeline = ctk.CTkFrame(left, fg_color="#0a0a0a", corner_radius=8)
        timeline.grid(row=1, column=0, sticky="ew", padx=8, pady=(2, 8))
        timebar = ctk.CTkFrame(timeline, fg_color="transparent")
        timebar.pack(fill="x", padx=8, pady=(5, 3))
        self.time_current = ctk.CTkLabel(timebar, text="00:00.0", width=70, anchor="w", text_color="#f4f4f5")
        self.time_current.pack(side="left")
        self.seek_slider = ctk.CTkSlider(
            timebar, from_=0, to=1, number_of_steps=1000,
            command=self.seek_from_slider, button_color="#ffffff",
            button_hover_color="#bcbcc0", progress_color="#85858b"
        )
        self.seek_slider.pack(side="left", fill="x", expand=True, padx=8)
        self.seek_slider.bind("<ButtonPress-1>", self._begin_seek)
        self.seek_slider.bind("<ButtonRelease-1>", self._end_seek)
        self.time_total = ctk.CTkLabel(timebar, text="00:00.0", width=70, anchor="e", text_color="#f4f4f5")
        self.time_total.pack(side="right")

        preview_controls = ctk.CTkFrame(timeline, fg_color="transparent")
        preview_controls.pack(fill="x", padx=10, pady=(0, 4))
        ctk.CTkLabel(preview_controls, text="Volumen", width=55, anchor="w").pack(side="left")
        self.preview_volume_slider = ctk.CTkSlider(
            preview_controls, from_=0, to=100, number_of_steps=100,
            variable=self.preview_volume, width=115,
            command=lambda _v: self._preview_setting_changed()
        )
        self.preview_volume_slider.pack(side="left", padx=(2, 8))
        self.preview_volume_slider.bind("<ButtonRelease-1>", lambda _event: self._restart_preview_if_active())
        self.preview_volume_label = ctk.CTkLabel(preview_controls, text="85%", width=36)
        self.preview_volume_label.pack(side="left")
        ctk.CTkLabel(preview_controls, text="Velocidad").pack(side="left", padx=(12, 4))
        self.preview_speed_combo = ctk.CTkComboBox(
            preview_controls, values=["0.75x", "1.0x", "1.25x", "1.5x", "2.0x"],
            variable=self.preview_speed, width=82, height=26,
            command=self._preview_speed_selected
        )
        self.preview_speed_combo.pack(side="left")
        ctk.CTkCheckBox(
            preview_controls, text="Repetir", variable=self.preview_loop,
            checkbox_width=18, checkbox_height=18
        ).pack(side="right")

        zoom_controls = ctk.CTkFrame(timeline, fg_color="transparent")
        zoom_controls.pack(fill="x", padx=10, pady=(0, 5))
        ctk.CTkLabel(zoom_controls, text="Zoom onda", width=68, anchor="w").pack(side="left")
        self.wave_zoom_slider = ctk.CTkSlider(
            zoom_controls, from_=1, to=8, number_of_steps=7,
            variable=self.wave_zoom, width=150, command=self._set_wave_zoom
        )
        self.wave_zoom_slider.pack(side="left", padx=(2, 5))
        self.wave_zoom_label = ctk.CTkLabel(zoom_controls, text="1×", width=36)
        self.wave_zoom_label.pack(side="left")
        self._button(zoom_controls, "Centrar playhead", self._center_wave_on_playhead, width=125).pack(side="right", padx=4)
        self._button(zoom_controls, "Limpiar selección", self.clear_selection, width=125).pack(side="right", padx=4)

        right = ctk.CTkFrame(body, fg_color="#18181b", corner_radius=8, width=292)
        right.grid(row=0, column=1, sticky="ns")
        right.grid_propagate(False)
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(0, weight=1)
        self.editor_tools = ctk.CTkTabview(
            right, width=278, fg_color="#18181b", corner_radius=8,
            segmented_button_fg_color="#101012",
            segmented_button_selected_color="#5a2529",
            segmented_button_selected_hover_color="#713036",
            segmented_button_unselected_color="#101012",
            segmented_button_unselected_hover_color="#29292e",
            text_color="#f4f4f5",
        )
        self.editor_tools.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        effects_tab = self.editor_tools.add("Efectos")
        project_tab = self.editor_tools.add("Proyecto")
        edit_tab = self.editor_tools.add("Edición")
        effects_content = ctk.CTkScrollableFrame(
            effects_tab, fg_color="transparent", corner_radius=0
        )
        effects_content.pack(fill="both", expand=True, padx=2, pady=2)
        ctk.CTkLabel(
            effects_content, text="PROCESAMIENTO", font=fuente_ui(size=12, weight="bold"),
            text_color="#ffffff", anchor="w"
        ).pack(fill="x", padx=8, pady=(8, 7))
        self._effect_categories = {
            "EQ y filtros": ["Graves y agudos", "Ecualizador gráfico", "Ecualizador de curva",
                "Filtro paso alto", "Filtro paso bajo"],
            "Dinámica": ["Compresor", "Limitador", "Normalizar", "Fade in / out"],
            "Espacial": ["Reverberación", "Ancho estéreo + mono graves",
                "Chorus / Flanger / Phaser", "Delay sincronizado", "Mezcla DJ"],
            "Creativos": ["Distorsión", "Retumbe de baúl", "Gritón · presencia vocal"],
            "Edición": ["Cortador", "Cambiar tono", "Cambiar tempo"],
        }
        ctk.CTkLabel(
            effects_content, text="Categoría", text_color="#d4d4d8", anchor="w"
        ).pack(fill="x", padx=8, pady=(0, 3))
        self.effect_category = ctk.CTkOptionMenu(
            effects_content, values=list(self._effect_categories), width=238, height=30,
            font=fuente_ui(size=10)
        )
        self.effect_category.set("EQ y filtros")
        self.effect_category.pack(fill="x", padx=8, pady=(0, 8))
        ctk.CTkLabel(
            effects_content, text="Efecto", text_color="#d4d4d8", anchor="w"
        ).pack(fill="x", padx=8, pady=(0, 3))
        self.effect_choice = ctk.CTkOptionMenu(
            effects_content,
            values=["Elegí un efecto…"] + self._effect_categories["EQ y filtros"],
            width=238, height=34, font=fuente_ui(size=10)
        )
        self.effect_choice.set("Elegí un efecto…")
        self.effect_choice.pack(fill="x", padx=8, pady=(0, 7))
        def update_effect_category(category):
            self.effect_choice.configure(
                values=["Elegí un efecto…"] + self._effect_categories.get(category, [])
            )
            self.effect_choice.set("Elegí un efecto…")
        self.effect_category.configure(command=update_effect_category)
        self._button(effects_content, "Abrir efecto", self.aplicar_efecto_elegido, width=238).pack(padx=8, pady=(0, 8))
        self._button(
            effects_content, "Presets · guardar / importar / exportar",
            self.abrir_gestor_presets, width=238
        ).pack(padx=8, pady=(0, 8))
        ctk.CTkLabel(
            effects_content, text="Vista previa antes de aplicar. Si no te gusta, detenela y seguí ajustando.",
            text_color="#a1a1aa", wraplength=230, justify="left", anchor="w"
        ).pack(fill="x", padx=9, pady=(4, 8))

        project_content = ctk.CTkScrollableFrame(
            project_tab, fg_color="transparent", corner_radius=0
        )
        project_content.pack(fill="both", expand=True, padx=2, pady=2)
        ctk.CTkLabel(
            project_content, text="PROYECTO Y SALIDA", font=fuente_ui(size=12, weight="bold"),
            text_color="#ffffff", anchor="w"
        ).pack(fill="x", padx=8, pady=(8, 7))
        self._button(project_content, "Guardar proyecto", self.guardar_proyecto, width=238).pack(padx=8, pady=4)
        self._button(project_content, "Guardar como…", lambda: self.guardar_proyecto(como_nuevo=True), width=238).pack(padx=8, pady=4)
        self._button(project_content, "Abrir proyecto…", self.abrir_proyecto, width=238).pack(padx=8, pady=4)
        self._button(project_content, "Dispositivo de audio…", self.open_audio_preferences, width=238).pack(padx=8, pady=4)
        ctk.CTkLabel(
            project_content, text="Los proyectos guardan pistas, posiciones, volumen, panorama y automatización.",
            text_color="#a1a1aa", wraplength=230, justify="left", anchor="w"
        ).pack(fill="x", padx=9, pady=(8, 6))

        edit_content = ctk.CTkScrollableFrame(
            edit_tab, fg_color="transparent", corner_radius=0
        )
        edit_content.pack(fill="both", expand=True, padx=2, pady=2)
        ctk.CTkLabel(
            edit_content, text="PISTAS Y EXPORTACIÓN", font=fuente_ui(size=12, weight="bold"),
            text_color="#ffffff", anchor="w"
        ).pack(fill="x", padx=8, pady=(8, 7))
        edit_row = ctk.CTkFrame(edit_content, fg_color="transparent")
        edit_row.pack(fill="x", padx=5, pady=2)
        self._button(edit_row, "Duplicar", self.duplicate_track, width=112).pack(side="left", padx=3)
        self._button(
            edit_row, "Eliminar", self.remove_selected_track, width=112,
            fg_color="#35353a", hover_color="#494950"
        ).pack(side="left", padx=3)
        self._button(edit_content, "Exportar pista seleccionada", self.export_track, width=238).pack(padx=8, pady=4)
        self._button(edit_content, "Exportar mezcla", self.export_mix, width=238).pack(padx=8, pady=4)
        self._button(edit_content, "Usar pista como intro", self.use_selected_as_intro, width=238).pack(padx=8, pady=4)
        self._button(edit_content, "Abrir pista en Audacity", self.open_in_audacity, width=238).pack(padx=8, pady=4)
        ctk.CTkLabel(
            edit_content, text="Deshacer / rehacer efectos", text_color="#d4d4d8", anchor="w"
        ).pack(fill="x", padx=9, pady=(13, 4))
        history_buttons = ctk.CTkFrame(edit_content, fg_color="transparent")
        history_buttons.pack(fill="x", padx=7, pady=2)
        self._button(history_buttons, "↶ Deshacer", self.undo_effect, width=112).pack(side="left", padx=3)
        self._button(history_buttons, "↷ Rehacer", self.redo_effect, width=112).pack(side="left", padx=3)
        ctk.CTkLabel(
            edit_content, text="Atajos: Ctrl+A seleccionar pistas · Ctrl+D duplicar · Ctrl+S guardar · Espacio reproducir",
            text_color="#a1a1aa", wraplength=230, justify="left", anchor="w"
        ).pack(fill="x", padx=9, pady=(10, 8))
        self.info = ctk.CTkLabel(
            self, text="Arrastrá sobre una onda para seleccionar un tramo; los efectos se aplican a esa selección.",
            text_color="#f4f4f5", anchor="w"
        )
        self.info.pack(fill="x", padx=16, pady=(0, 12))
        self.bind("<Control-a>", self._shortcut_select_all_tracks, add="+")
        self.bind("<Control-A>", self._shortcut_select_all_tracks, add="+")
        self.bind("<Control-d>", self._shortcut_duplicate_track, add="+")
        self.bind("<Control-D>", self._shortcut_duplicate_track, add="+")
        self.bind("<Control-s>", self._shortcut_save_project, add="+")
        self.bind("<Control-o>", self._shortcut_open_project, add="+")
        self.bind("<Control-z>", self._shortcut_undo, add="+")
        self.bind("<Control-y>", self._shortcut_redo, add="+")
        self.bind("<space>", self._shortcut_play_pause, add="+")
        self.bind("<Delete>", self._shortcut_delete_track, add="+")
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self._autosave_after = self.after(120000, self._autosave_tick)
        self.after(900, self._ofrecer_recuperacion_autosave)

    def _button(self, parent, text, command, width=130, **style):
        return ctk.CTkButton(
            parent, text=text, command=command, width=width, height=34,
            fg_color=style.pop("fg_color", "#29292e"),
            hover_color=style.pop("hover_color", "#363636"),
            text_color="#f4f4f5", font=fuente_ui(size=11, weight="bold"), **style
        )

    def _repetition_control(self, parent):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=(2, 5))
        ctk.CTkLabel(
            row, text="Repeticiones (1–100)", text_color="#f4f4f5", anchor="w"
        ).pack(side="left")
        choices = [str(number) for number in range(1, 101)]
        selector = ctk.CTkOptionMenu(
            row, values=choices, width=86, height=28,
            fg_color="#252529", button_color="#333333",
            button_hover_color="#444444", dropdown_fg_color="#1b1b1f",
            dropdown_hover_color="#333333", text_color="#ffffff"
        )
        selector.pack(side="right")
        selector.set("1")
        return selector

    def _apply_repeated(self, processor, label, repetitions):
        try:
            repetitions = int(str(repetitions).strip() or "1")
        except (TypeError, ValueError):
            messagebox.showerror(
                "Repeticiones no válidas", "Elegí un número entre 1 y 100.", parent=self
            )
            return
        if not 1 <= repetitions <= 100:
            messagebox.showerror(
                "Repeticiones no válidas", "Elegí un número entre 1 y 100.", parent=self
            )
            return

        def process(data, sample_rate):
            result = data
            for _ in range(repetitions):
                result = processor(result, sample_rate)
            return result

        suffix = f" × {repetitions}" if repetitions > 1 else ""
        self._apply(process, f"{label}{suffix}")

    @staticmethod
    def _read_repetitions(selector, dialog):
        try:
            value = int(str(selector.get()).strip() or "1")
            if 1 <= value <= 100:
                return value
        except (TypeError, ValueError):
            pass
        messagebox.showerror(
            "Repeticiones no válidas", "Elegí un número entre 1 y 100.", parent=dialog
        )
        return None

    @staticmethod
    def _set_repetition(selector, value):
        repetitions = int(value)
        if not 1 <= repetitions <= 100:
            raise ValueError("Las repeticiones deben estar entre 1 y 100.")
        selector.set(str(repetitions))

    def _shortcuts_should_yield(self, event):
        try:
            return event.widget.winfo_class() in {
                "Entry", "Text", "TEntry", "TCombobox", "Spinbox",
                "CTkEntry", "CTkTextbox", "CTkComboBox",
            }
        except (AttributeError, tk.TclError):
            return False

    def _shortcut_select_all_tracks(self, event=None):
        if event is not None and self._shortcuts_should_yield(event):
            return None
        if self.tracks:
            self.selected_indices = set(range(len(self.tracks)))
            if self.selected_index is None:
                self.selected_index = 0
            self.refresh_tracks()
            self.set_info(f"Seleccionadas las {len(self.tracks)} pistas. Ctrl+A en un campo de texto sigue seleccionando su texto.", "#f4f4f5")
        return "break"

    def _shortcut_duplicate_track(self, event=None):
        if event is not None and self._shortcuts_should_yield(event):
            return None
        self.duplicate_track()
        return "break"

    def _shortcut_save_project(self, event=None):
        if event is not None and self._shortcuts_should_yield(event):
            return None
        self.guardar_proyecto()
        return "break"

    def _shortcut_open_project(self, event=None):
        if event is not None and self._shortcuts_should_yield(event):
            return None
        self.abrir_proyecto()
        return "break"

    def _shortcut_undo(self, event=None):
        if event is not None and self._shortcuts_should_yield(event):
            return None
        self.undo_effect()
        return "break"

    def _shortcut_redo(self, event=None):
        if event is not None and self._shortcuts_should_yield(event):
            return None
        self.redo_effect()
        return "break"

    def _shortcut_play_pause(self, event=None):
        if event is not None and self._shortcuts_should_yield(event):
            return None
        if self._preview_process and self._preview_process.poll() is None:
            self.toggle_pause_preview()
        else:
            self.play_preview()
        return "break"

    def _shortcut_delete_track(self, event=None):
        if event is not None and self._shortcuts_should_yield(event):
            return None
        self.remove_selected_track()
        return "break"

    def abrir_menu_proyecto(self):
        win = ctk.CTkToplevel(self)
        win.title("Proyecto de audio")
        win.geometry("380x245")
        win.resizable(False, False)
        win.configure(fg_color="#18181b")
        win.transient(self)
        win.grab_set()
        ctk.CTkLabel(
            win, text="PROYECTO", font=fuente_ui(size=16, weight="bold")
        ).pack(anchor="w", padx=16, pady=(16, 5))
        ctk.CTkLabel(
            win, text="Guardá las pistas, sus posiciones y sus ajustes para continuar después.",
            text_color="#bcbcc0", wraplength=340, anchor="w", justify="left"
        ).pack(anchor="w", padx=16, pady=(0, 12))

        def do_save():
            win.destroy()
            self.guardar_proyecto()

        def do_save_as():
            win.destroy()
            self.guardar_proyecto(como_nuevo=True)

        def do_open():
            win.destroy()
            self.abrir_proyecto()

        self._button(win, "Guardar proyecto", do_save, width=335).pack(padx=18, pady=4)
        self._button(win, "Guardar como…", do_save_as, width=335).pack(padx=18, pady=4)
        self._button(win, "Abrir proyecto…", do_open, width=335).pack(padx=18, pady=4)

    def _project_track_metadata(self, track, audio_member):
        return {
            "audio": audio_member,
            "name": str(track.get("name") or "Pista"),
            "path": str(track.get("path") or ""),
            "sr": int(track.get("sr") or 0),
            "start_sec": float(track.get("start_sec", 0.0)),
            "gain_db": float(track.get("gain_db", 0.0)),
            "gain_min": float(track.get("gain_min", -36.0)),
            "gain_max": float(track.get("gain_max", 36.0)),
            "pan": float(track.get("pan", 0.0)),
            "muted": bool(track.get("muted", False)),
            "solo": bool(track.get("solo", False)),
            "gain_automation": track.get("gain_automation", []),
            "pan_automation": track.get("pan_automation", []),
            "group": str(track.get("group") or ""),
        }

    def _write_project_file(self, path):
        if not self.tracks:
            raise ValueError("Agregá al menos una pista antes de guardar el proyecto.")
        target_dir = os.path.dirname(os.path.abspath(path))
        os.makedirs(target_dir, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix="corneteros_project_", suffix=".tmp", dir=target_dir)
        os.close(fd)
        try:
            tracks_meta = []
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for index, track in enumerate(self.tracks):
                    member = f"audio/track_{index:04d}.npy"
                    buffer = io.BytesIO()
                    np.save(buffer, np.asarray(track["data"], dtype=np.float32), allow_pickle=False)
                    archive.writestr(member, buffer.getvalue())
                    tracks_meta.append(self._project_track_metadata(track, member))
                meta = {
                    "format": "corneteros_studio_project_v1",
                    "selected_index": self.selected_index or 0,
                    "tracks": tracks_meta,
                }
                archive.writestr("project.json", json.dumps(meta, ensure_ascii=False, indent=2))
            os.replace(temporary, path)
        except Exception:
            try:
                if os.path.exists(temporary):
                    os.remove(temporary)
            except OSError:
                pass
            raise

    def guardar_proyecto(self, como_nuevo=False):
        path = None if como_nuevo else self.current_project_path
        if not path:
            path = filedialog.asksaveasfilename(
                parent=self, title="Guardar proyecto de audio", defaultextension=".csp",
                initialfile="Proyecto Corneteros.csp",
                filetypes=[("Proyecto Corneteros Studio", "*.csp"), ("Todos los archivos", "*.*")]
            )
        if not path:
            return
        try:
            self._write_project_file(path)
            self.current_project_path = path
            self.set_info(f"Proyecto guardado: {path}", "#f4f4f5")
        except Exception as exc:
            messagebox.showerror("No se pudo guardar el proyecto", str(exc), parent=self)

    def _load_project_file(self, path):
        with zipfile.ZipFile(path, "r") as archive:
            metadata = json.loads(archive.read("project.json").decode("utf-8"))
            if metadata.get("format") != "corneteros_studio_project_v1":
                raise ValueError("El archivo no es un proyecto compatible de Corneteros Studio.")
            serialized_tracks = metadata.get("tracks")
            if not isinstance(serialized_tracks, list) or not serialized_tracks:
                raise ValueError("El proyecto no contiene pistas.")
            loaded = []
            for item in serialized_tracks:
                if not isinstance(item, dict) or not item.get("audio"):
                    raise ValueError("Hay una pista incompleta en el proyecto.")
                audio = np.load(io.BytesIO(archive.read(item["audio"])), allow_pickle=False)
                audio = np.asarray(audio, dtype=np.float32)
                if audio.ndim == 1:
                    audio = audio[:, None]
                sr = int(item.get("sr", 0))
                if audio.ndim != 2 or not len(audio) or sr <= 0 or not np.isfinite(audio).all():
                    raise ValueError("Una pista del proyecto tiene audio o frecuencia no válidos.")
                track = {
                    "name": str(item.get("name") or "Pista"),
                    "path": str(item.get("path") or ""),
                    "data": audio.copy(),
                    "sr": sr,
                    "start_sec": max(0.0, float(item.get("start_sec", 0.0))),
                    "gain_db": float(item.get("gain_db", 0.0)),
                    "gain_min": float(item.get("gain_min", -36.0)),
                    "gain_max": float(item.get("gain_max", 36.0)),
                    "pan": max(-100.0, min(100.0, float(item.get("pan", 0.0)))),
                    "muted": bool(item.get("muted", False)),
                    "solo": bool(item.get("solo", False)),
                    "gain_automation": item.get("gain_automation", []),
                    "pan_automation": item.get("pan_automation", []),
                    "group": str(item.get("group") or ""),
                    "undo": [], "redo": [],
                }
                loaded.append(track)
        return loaded, int(metadata.get("selected_index", 0))

    def _install_project(self, path, set_current_path=True):
        loaded, selected = self._load_project_file(path)
        self.stop_preview()
        for old_track in self.tracks:
            self._discard_track_preview_cache(old_track)
        self.tracks = loaded
        self.selected_index = max(0, min(selected, len(loaded) - 1))
        self.selected_indices = {self.selected_index}
        self._selection_start = None
        self._selection_end = None
        self._preview_position = 0.0
        self._wave_view_center = 0.0
        if set_current_path:
            self.current_project_path = path
        self.refresh_tracks()
        self.set_info(f"Proyecto abierto: {path}", "#f4f4f5")

    def abrir_proyecto(self):
        path = filedialog.askopenfilename(
            parent=self, title="Abrir proyecto de audio",
            filetypes=[("Proyecto Corneteros Studio", "*.csp"), ("Todos los archivos", "*.*")]
        )
        if not path:
            return
        try:
            if self.tracks and not messagebox.askyesno(
                "Reemplazar proyecto", "Abrir este archivo reemplaza las pistas actuales. ¿Continuar?", parent=self
            ):
                return
            self._install_project(path)
        except Exception as exc:
            messagebox.showerror("No se pudo abrir el proyecto", str(exc), parent=self)

    def _autosave_tick(self):
        self._autosave_after = None
        try:
            if self.tracks:
                self._write_project_file(self._autosave_file)
            elif os.path.isfile(self._autosave_file):
                os.remove(self._autosave_file)
        except Exception as exc:
            print(f"No se pudo guardar el autoguardado del proyecto: {exc}")
        try:
            if self.winfo_exists():
                self._autosave_after = self.after(120000, self._autosave_tick)
        except tk.TclError:
            pass

    def _ofrecer_recuperacion_autosave(self):
        directory = os.path.dirname(PROJECT_AUTOSAVE_FILE)
        prefix = os.path.basename(PROJECT_AUTOSAVE_FILE)[:-4] + "_"
        try:
            candidates = [
                os.path.join(directory, name) for name in os.listdir(directory)
                if name.startswith(prefix) and name.endswith(".csp")
            ]
        except OSError:
            candidates = []
        if os.path.isfile(PROJECT_AUTOSAVE_FILE):
            candidates.append(PROJECT_AUTOSAVE_FILE)
        if not candidates:
            return
        recovery_path = max(candidates, key=lambda path: os.path.getmtime(path))
        try:
            if not self.tracks and messagebox.askyesno(
                "Recuperar proyecto", "Encontré un autoguardado de una sesión anterior. ¿Querés recuperarlo?", parent=self
            ):
                self._install_project(recovery_path, set_current_path=False)
                self._write_project_file(self._autosave_file)
                self.set_info("Proyecto recuperado del autoguardado. Guardalo con Ctrl+S para crear un archivo normal.", "#f4f4f5")
        except Exception as exc:
            messagebox.showerror("No se pudo recuperar el proyecto", str(exc), parent=self)

    def _load_audio_output_settings(self):
        if sd is None:
            return
        try:
            with open(AUDIO_SETTINGS_FILE, "r", encoding="utf-8") as archivo:
                saved = json.load(archivo)
            wanted_name = saved.get("device_name")
            wanted_host = saved.get("host_name")
            hostapis = sd.query_hostapis()
            for index, device in enumerate(sd.query_devices()):
                host_index = int(device.get("hostapi", -1))
                host_name = hostapis[host_index].get("name", "") if 0 <= host_index < len(hostapis) else ""
                if int(device.get("max_output_channels", 0)) > 0 and device.get("name") == wanted_name and host_name == wanted_host:
                    self._audio_output_device = index
                    return
        except Exception:
            pass

    def open_audio_preferences(self):
        win = ctk.CTkToplevel(self)
        win.title("Dispositivos de audio")
        win.geometry("540x300")
        win.resizable(False, False)
        win.configure(fg_color="#18181b")
        win.transient(self)
        win.grab_set()
        ctk.CTkLabel(
            win, text="DISPOSITIVO DE REPRODUCCIÓN", font=fuente_ui(size=15, weight="bold")
        ).pack(anchor="w", padx=18, pady=(17, 8))
        if sd is None:
            ctk.CTkLabel(
                win, text="La selección de dispositivo necesita sounddevice/PortAudio.",
                text_color="#bcbcc0", wraplength=470, anchor="w"
            ).pack(anchor="w", padx=18, pady=15)
            self._button(win, "Cerrar", win.destroy, width=110).pack(anchor="e", padx=16, pady=12)
            return
        ctk.CTkLabel(win, text="Host", text_color="#f4f4f5").pack(anchor="w", padx=18, pady=(4, 2))
        host_combo = ctk.CTkComboBox(win, values=["Predeterminado"], width=490, state="readonly")
        host_combo.pack(padx=18, pady=(0, 8))
        ctk.CTkLabel(win, text="Salida", text_color="#f4f4f5").pack(anchor="w", padx=18, pady=(2, 2))
        output_combo = ctk.CTkComboBox(win, values=["Predeterminado"], width=490, state="readonly")
        output_combo.pack(padx=18, pady=(0, 8))
        try:
            hostapis = list(sd.query_hostapis())
        except Exception as exc:
            messagebox.showerror("No se pudieron leer los hosts de audio", str(exc), parent=win)
            self._button(win, "Cerrar", win.destroy, width=110).pack(anchor="e", padx=16, pady=12)
            return
        host_names = ["Predeterminado"] + [item.get("name", f"Host {i}") for i, item in enumerate(hostapis)]
        host_combo.configure(values=host_names)
        devices = []

        def refresh_devices(_value=None):
            devices.clear()
            selected_host = host_combo.get()
            try:
                available = list(sd.query_devices())
            except Exception as exc:
                messagebox.showerror("No se pudieron leer los dispositivos", str(exc), parent=win)
                return
            for index, device in enumerate(available):
                host_index = int(device.get("hostapi", -1))
                host_name = hostapis[host_index].get("name", "") if 0 <= host_index < len(hostapis) else ""
                if int(device.get("max_output_channels", 0)) < 1:
                    continue
                if selected_host != "Predeterminado" and host_name != selected_host:
                    continue
                label = f"{device.get('name', 'Salida')} · {host_name}"
                devices.append((index, device, host_name, label))
            choices = ["Predeterminado"] + [entry[3] for entry in devices]
            output_combo.configure(values=choices)
            current = next((entry for entry in devices if entry[0] == self._audio_output_device), None)
            output_combo.set(current[3] if current else "Predeterminado")

        host_combo.set("Predeterminado")
        host_combo.configure(command=refresh_devices)
        refresh_devices()

        def save_device():
            choice = output_combo.get()
            selected = next((entry for entry in devices if entry[3] == choice), None)
            if selected:
                chosen_index, chosen_device, chosen_host = selected[0], selected[1], selected[2]
            elif host_combo.get() != "Predeterminado":
                host_index = next((i for i, item in enumerate(hostapis)
                                   if item.get("name") == host_combo.get()), None)
                chosen_index = hostapis[host_index].get("default_output_device") if host_index is not None else None
                try:
                    chosen_index = int(chosen_index)
                except (TypeError, ValueError):
                    chosen_index = -1
                host_default = next((item for item in devices if item[0] == chosen_index), None)
                if host_default is None:
                    host_default = devices[0] if devices else None
                if host_default is not None:
                    chosen_index, chosen_device, chosen_host = host_default[0], host_default[1], host_default[2]
                else:
                    chosen_index, chosen_device, chosen_host = None, None, host_combo.get()
            else:
                chosen_index, chosen_device, chosen_host = None, None, "Predeterminado"
            self._audio_output_device = chosen_index
            if chosen_device is not None:
                settings = {"device_name": chosen_device.get("name"), "host_name": chosen_host}
            else:
                settings = {"device_name": None, "host_name": chosen_host}
            try:
                with open(AUDIO_SETTINGS_FILE + ".tmp", "w", encoding="utf-8") as archivo:
                    json.dump(settings, archivo, ensure_ascii=False, indent=2)
                os.replace(AUDIO_SETTINGS_FILE + ".tmp", AUDIO_SETTINGS_FILE)
                self._stop_effect_preview()
                self.stop_preview()
                self.set_info(
                    f"Salida de audio: {chosen_device.get('name') if chosen_device else 'Predeterminada del sistema'}",
                    "#f4f4f5"
                )
                win.destroy()
            except OSError as exc:
                messagebox.showerror("No se pudo guardar el dispositivo", str(exc), parent=win)

        footer = ctk.CTkFrame(win, fg_color="transparent")
        footer.pack(fill="x", padx=14, pady=(6, 14))
        self._button(footer, "Actualizar dispositivos", refresh_devices, width=170).pack(side="left", padx=4)
        self._button(footer, "Cancelar", win.destroy, width=100).pack(side="right", padx=4)
        self._button(footer, "Guardar", save_device, width=100).pack(side="right", padx=4)

    def _leer_presets_efectos(self):
        try:
            with open(self.EFFECT_PRESETS_FILE, "r", encoding="utf-8") as archivo:
                data = json.load(archivo)
            records = data.get("presets", []) if isinstance(data, dict) else []
            return [
                item for item in records
                if isinstance(item, dict) and item.get("effect")
                and item.get("name") and isinstance(item.get("values"), dict)
            ]
        except (OSError, json.JSONDecodeError):
            return []

    def _escribir_presets_efectos(self, records):
        temporal = self.EFFECT_PRESETS_FILE + ".tmp"
        with open(temporal, "w", encoding="utf-8") as archivo:
            json.dump({"format": "buscador_mp3_effect_presets_v1", "presets": records},
                      archivo, ensure_ascii=False, indent=2)
        os.replace(temporal, self.EFFECT_PRESETS_FILE)

    def _effect_preview_source(self, source_factory=None, seconds=8):
        if source_factory is not None:
            data, sample_rate = source_factory()
        else:
            track = self.get_track()
            if track is None:
                raise ValueError("Importá una pista antes de escuchar la vista previa.")
            original = track["data"]
            start = 0
            end = len(original)
            if self._selection_start is not None and self._selection_end is not None:
                first = int(round(min(self._selection_start, self._selection_end) * track["sr"]))
                last = int(round(max(self._selection_start, self._selection_end) * track["sr"]))
                first = max(0, min(first, len(original)))
                last = max(0, min(last, len(original)))
                if last - first >= 2:
                    start, end = first, last
            data = original[start:end]
            sample_rate = int(track["sr"])

        if np is None:
            raise RuntimeError("La vista previa requiere NumPy.")
        data = np.asarray(data, dtype=np.float32)
        if data.ndim == 1:
            data = data[:, None]
        if data.ndim != 2 or data.shape[0] < 2 or not np.isfinite(data).all():
            raise ValueError("No hay audio válido para escuchar la vista previa.")
        sample_rate = int(sample_rate)
        data = np.ascontiguousarray(data[:max(2, int(sample_rate * seconds))])
        return data, sample_rate

    def _stop_effect_preview(self, owner=None):
        session = self._effect_preview_session
        if session is None or (owner is not None and session["owner"] is not owner):
            return
        self._effect_preview_session = None
        session["cancelled"] = True
        if session.get("after") is not None:
            try:
                session["owner"].after_cancel(session["after"])
            except (RuntimeError, tk.TclError):
                pass
        process = session.get("process")
        if process is not None and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=1.0)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                except OSError:
                    pass
        stream = session.get("stream")
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass
        path = session.get("path")
        if path and os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                pass
        try:
            if session["button"].winfo_exists():
                session["button"].configure(text="Vista previa", state="normal")
        except (RuntimeError, tk.TclError):
            pass

    def _toggle_effect_preview(self, owner, button, processor_factory, source_factory=None):
        current = self._effect_preview_session
        if current is not None and current["owner"] is owner:
            self._stop_effect_preview(owner)
            self.set_info("Vista previa detenida.", "#f4f4f5")
            return
        self._stop_effect_preview()
        if sf is None or np is None:
            messagebox.showerror(
                "Vista previa no disponible",
                "Instalá las dependencias de audio para escuchar efectos antes de aplicarlos.",
                parent=owner,
            )
            return
        try:
            source, sample_rate = self._effect_preview_source(source_factory)
            processor = processor_factory()
            if not callable(processor):
                raise ValueError("No se pudo preparar este efecto para la vista previa.")
        except Exception as exc:
            messagebox.showerror("No se pudo preparar la vista previa", str(exc), parent=owner)
            return

        self.stop_preview()
        session = {
            "owner": owner, "button": button, "process": None, "path": None,
            "after": None, "cancelled": False, "stream": None,
        }
        self._effect_preview_session = session
        button.configure(text="Detener vista previa")
        self.set_info("Procesando unos segundos para la vista previa…", "#f4f4f5")

        def worker():
            path = None
            try:
                result = np.asarray(processor(source.copy(), sample_rate), dtype=np.float32)
                if result.ndim == 1:
                    result = result[:, None]
                if result.ndim != 2 or result.shape[0] < 2 or not np.isfinite(result).all():
                    raise ValueError("El efecto produjo un audio vacío o no válido.")
                fd, path = tempfile.mkstemp(prefix="corneteros_preview_", suffix=".wav")
                os.close(fd)
                sf.write(path, result, sample_rate, subtype="PCM_16")
                session["path"] = path
                if self._effect_preview_session is not session or session["cancelled"]:
                    try:
                        os.remove(path)
                    except OSError:
                        pass
                    return
                self.after(0, lambda: start_player(path))
            except Exception as exc:
                if path and os.path.isfile(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass
                if self._effect_preview_session is session and not session["cancelled"]:
                    message = str(exc)
                    try:
                        self.after(0, lambda error=message: fail_preview(error))
                    except (RuntimeError, tk.TclError):
                        pass

        def fail_preview(message):
            if self._effect_preview_session is not session:
                return
            self._stop_effect_preview(owner)
            messagebox.showerror("No se pudo escuchar el efecto", message, parent=owner)

        def start_player(path):
            if self._effect_preview_session is not session or session["cancelled"]:
                if os.path.isfile(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass
                return
            try:
                if sd is not None and sf is not None and self._audio_output_device is not None:
                    audio, rate = sf.read(path, dtype="float32", always_2d=True)
                    if audio.shape[1] == 1:
                        audio = np.repeat(audio, 2, axis=1)
                    elif audio.shape[1] > 2:
                        audio = audio[:, :2]
                    audio *= np.float32(max(0, min(100, int(self.preview_volume.get()))) / 100.0)
                    position = {"frame": 0}
                    def callback(outdata, frames, _time_info, _status):
                        first = position["frame"]
                        last = min(len(audio), first + frames)
                        outdata.fill(0)
                        if last > first:
                            outdata[:last - first] = audio[first:last]
                        position["frame"] = first + frames
                    try:
                        device_info = sd.query_devices(self._audio_output_device, "output")
                        channels = 1 if int(device_info.get("max_output_channels", 2)) < 2 else 2
                    except Exception:
                        channels = 2
                    if channels == 1:
                        audio = np.mean(audio, axis=1, keepdims=True)
                    stream = sd.OutputStream(
                        samplerate=int(rate), channels=channels, dtype="float32",
                        callback=callback, device=self._audio_output_device
                    )
                    stream.start()
                    session["stream"] = stream
                    session["position"] = position
                    session["length"] = len(audio)
                    self.set_info(
                        "Reproduciendo vista previa del efecto por el dispositivo elegido. Podés detenerla y seguir ajustando.",
                        "#f4f4f5"
                    )
                    poll_player()
                    return
                player = localizar_programa("ffplay")
                if not player:
                    raise RuntimeError("No se encontró FFplay junto a la aplicación.")
                volume = int(self.preview_volume.get()) if hasattr(self, "preview_volume") else 85
                session["process"] = subprocess.Popen(
                    [player, "-nodisp", "-autoexit", "-loglevel", "error",
                     "-volume", str(max(0, min(100, volume))), path],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, **opciones_subproceso_oculto()
                )
                self.set_info("Reproduciendo vista previa del efecto. Podés detenerla y seguir ajustando.", "#f4f4f5")
                poll_player()
            except Exception as exc:
                fail_preview(str(exc))

        def poll_player():
            if self._effect_preview_session is not session:
                return
            process = session.get("process")
            stream = session.get("stream")
            stream_running = stream is not None and session.get("position", {}).get("frame", 0) < session.get("length", 0)
            process_running = process is None or process.poll() is None
            if stream_running or (stream is None and process_running):
                try:
                    session["after"] = owner.after(120, poll_player)
                except (RuntimeError, tk.TclError):
                    self._stop_effect_preview(owner)
                return
            self._stop_effect_preview(owner)
            self.set_info("Terminó la vista previa. Podés cambiar los ajustes y escucharla otra vez.", "#f4f4f5")

        owner.bind(
            "<Destroy>",
            lambda event, target=owner: self._stop_effect_preview(target)
            if event.widget is target else None,
            add="+",
        )
        threading.Thread(target=worker, daemon=True).start()

    def _preset_controls(self, parent, effect, get_values, set_values,
                         preview_factory=None, preview_source_factory=None):
        row = ctk.CTkFrame(parent, fg_color="#151515", corner_radius=5)
        row.pack(fill="x", padx=12, pady=(4, 8))
        select_row = ctk.CTkFrame(row, fg_color="transparent")
        select_row.pack(fill="x", padx=5, pady=(5, 1))
        ctk.CTkLabel(select_row, text="Preset", width=44, text_color="#f4f4f5").pack(side="left", padx=(2, 2))
        combo = ctk.CTkComboBox(select_row, values=[], width=118, height=27, state="readonly")
        combo.pack(side="left", padx=3)
        action_row = ctk.CTkFrame(row, fg_color="transparent")
        action_row.pack(fill="x", padx=5, pady=(1, 5))

        def refresh(selected=None):
            names = [p["name"] for p in self._leer_presets_efectos() if p["effect"] == effect]
            combo.configure(values=names or ["(sin presets)"])
            if selected in names:
                combo.set(selected)
            elif names:
                combo.set(names[0])
            else:
                combo.set("(sin presets)")

        def load():
            name = combo.get()
            record = next((p for p in self._leer_presets_efectos()
                           if p["effect"] == effect and p["name"] == name), None)
            if record:
                try:
                    set_values(record["values"])
                except Exception as exc:
                    messagebox.showerror("Preset incompatible", str(exc), parent=parent.winfo_toplevel())

        def save():
            dialog = ctk.CTkToplevel(parent.winfo_toplevel())
            dialog.title("Guardar preset")
            dialog.geometry("350x150")
            dialog.resizable(False, False)
            dialog.transient(parent.winfo_toplevel())
            dialog.grab_set()
            ctk.CTkLabel(dialog, text=f"Guardar configuración · {effect}").pack(pady=(15, 7))
            name_entry = ctk.CTkEntry(dialog, placeholder_text="Nombre del preset", width=280)
            name_entry.pack(pady=4)

            def confirm():
                name = name_entry.get().strip()
                if not name:
                    messagebox.showinfo("Falta el nombre", "Escribí un nombre para el preset.", parent=dialog)
                    return
                try:
                    values = get_values()
                    records = self._leer_presets_efectos()
                    records = [p for p in records if not (
                        p["effect"] == effect and p["name"].casefold() == name.casefold()
                    )]
                    records.append({"id": str(uuid.uuid4()), "effect": effect,
                                    "name": name, "values": values})
                    self._escribir_presets_efectos(records)
                    refresh(name)
                    dialog.destroy()
                    self.set_info(f"Preset guardado: {name}", "#f4f4f5")
                except Exception as exc:
                    messagebox.showerror("No se pudo guardar", str(exc), parent=dialog)

            self._button(dialog, "Guardar", confirm, width=110).pack(side="right", padx=20, pady=12)
            self._button(dialog, "Cancelar", dialog.destroy, width=100).pack(side="right", pady=12)
            name_entry.focus_set()
            name_entry.bind("<Return>", lambda _event: confirm())

        def delete():
            name = combo.get()
            records = self._leer_presets_efectos()
            remaining = [p for p in records if not (p["effect"] == effect and p["name"] == name)]
            if len(remaining) == len(records):
                return
            if not messagebox.askyesno("Borrar preset", f"¿Borrar '{name}'?", parent=parent.winfo_toplevel()):
                return
            try:
                self._escribir_presets_efectos(remaining)
                refresh()
                self.set_info(f"Preset eliminado: {name}", "#f4f4f5")
            except OSError as exc:
                messagebox.showerror("No se pudo borrar", str(exc), parent=parent.winfo_toplevel())

        self._button(select_row, "Cargar", load, width=62).pack(side="left", padx=3)

        def open_library():
            owner = parent.winfo_toplevel()
            try:
                owner.grab_release()
            except (RuntimeError, tk.TclError):
                pass
            self.abrir_gestor_presets(owner)

        self._button(select_row, "Importar/exportar", open_library, width=118).pack(side="left", padx=3)
        self._button(action_row, "Guardar valores", save, width=128).pack(side="left", padx=3)
        self._button(action_row, "Borrar preset", delete, width=115,
                     fg_color="#303034", hover_color="#494950").pack(side="left", padx=3)
        if preview_factory is not None:
            preview_button = self._button(action_row, "Vista previa", lambda: self._toggle_effect_preview(
                parent.winfo_toplevel(), preview_button, preview_factory, preview_source_factory
            ), width=112)
            preview_button.pack(side="right", padx=3)
        refresh()
        return row

    def abrir_gestor_presets(self, parent=None):
        owner = parent or self
        win = ctk.CTkToplevel(owner)
        win.title("Biblioteca de presets de efectos")
        win.geometry("660x450")
        win.minsize(520, 320)
        win.configure(fg_color="#18181b")
        win.transient(owner)
        if parent is not None:
            win.grab_set()

            def restore_owner_grab(event=None):
                if event is not None and event.widget is not win:
                    return
                try:
                    if owner.winfo_exists():
                        owner.grab_set()
                except (RuntimeError, tk.TclError):
                    pass

            win.bind("<Destroy>", restore_owner_grab, add="+")
        ctk.CTkLabel(
            win, text="PRESETS DE EFECTOS", font=fuente_ui(size=16, weight="bold")
        ).pack(anchor="w", padx=14, pady=(14, 8))
        listing = tk.Listbox(
            win, bg="#101012", fg="#f4f4f5", selectbackground="#45454b",
            relief="flat", highlightthickness=0, font=("Segoe UI", 11), exportselection=False
        )
        listing.pack(fill="both", expand=True, padx=12, pady=6)
        current = []

        def refresh():
            current[:] = self._leer_presets_efectos()
            listing.delete(0, "end")
            for record in current:
                listing.insert("end", f"{record['effect']}  ·  {record['name']}")
            if not current:
                listing.insert("end", "No hay presets guardados todavía.")

        def import_file():
            path = filedialog.askopenfilename(
                parent=win, title="Importar biblioteca de presets",
                filetypes=[("Preset JSON", "*.json"), ("Todos los archivos", "*.*")]
            )
            if not path:
                return
            try:
                with open(path, "r", encoding="utf-8-sig") as archivo:
                    data = json.load(archivo)
                incoming = data.get("presets") if isinstance(data, dict) else None
                if not isinstance(incoming, list):
                    raise ValueError("El JSON no contiene una lista 'presets'.")
                merged = self._leer_presets_efectos()
                for record in incoming:
                    if not (isinstance(record, dict) and record.get("effect") and record.get("name")
                            and isinstance(record.get("values"), dict)):
                        continue
                    merged = [p for p in merged if not (
                        p["effect"] == record["effect"]
                        and p["name"].casefold() == str(record["name"]).casefold()
                    )]
                    merged.append({"id": str(uuid.uuid4()), "effect": str(record["effect"]),
                                   "name": str(record["name"]), "values": record["values"]})
                self._escribir_presets_efectos(merged)
                refresh()
                messagebox.showinfo("Importación", "Biblioteca de presets importada.", parent=win)
            except Exception as exc:
                messagebox.showerror("No se pudo importar", str(exc), parent=win)

        def export_file():
            path = filedialog.asksaveasfilename(
                parent=win, title="Exportar biblioteca de presets", defaultextension=".json",
                filetypes=[("Preset JSON", "*.json")], initialfile="presets_efectos.json"
            )
            if not path:
                return
            try:
                with open(path, "w", encoding="utf-8") as archivo:
                    json.dump({"format": "buscador_mp3_effect_presets_v1", "presets": current},
                              archivo, ensure_ascii=False, indent=2)
                messagebox.showinfo("Exportación", "Biblioteca de presets exportada.", parent=win)
            except OSError as exc:
                messagebox.showerror("No se pudo exportar", str(exc), parent=win)

        def delete_selected():
            selected = listing.curselection()
            if not selected or selected[0] >= len(current):
                return
            record = current[selected[0]]
            records = [p for p in self._leer_presets_efectos() if p.get("id") != record.get("id")]
            try:
                self._escribir_presets_efectos(records)
                refresh()
            except OSError as exc:
                messagebox.showerror("No se pudo borrar", str(exc), parent=win)

        actions = ctk.CTkFrame(win, fg_color="transparent")
        actions.pack(fill="x", padx=12, pady=(4, 12))
        self._button(actions, "Importar…", import_file, width=100).pack(side="left", padx=3)
        self._button(actions, "Exportar…", export_file, width=100).pack(side="left", padx=3)
        self._button(actions, "Borrar seleccionado", delete_selected, width=140,
                     fg_color="#303034", hover_color="#494950").pack(side="left", padx=3)
        self._button(actions, "Cerrar", win.destroy, width=90).pack(side="right", padx=3)
        refresh()
        return win

    def aplicar_efecto_elegido(self):
        opciones = {
            "Graves y agudos": self.open_bass_treble,
            "Ecualizador gráfico": self.open_graphic_eq,
            "Ecualizador de curva": self.open_filter_curve,
            "Filtro paso alto": self.open_highpass,
            "Filtro paso bajo": self.open_lowpass,
            "Reverberación": self.open_reverb,
            "Cortador": self.open_cutter_dialog,
            "Normalizar": self.normalize_track,
            "Fade in / out": self.open_fade_dialog,
            "Mezcla DJ": self.open_dj_mix_dialog,
            "Distorsión": lambda: self.open_audio_effect_dialog("distortion"),
            "Compresor": lambda: self.open_audio_effect_dialog("compressor"),
            "Limitador": lambda: self.open_audio_effect_dialog("limiter"),
            "Ancho estéreo + mono graves": lambda: self.open_audio_effect_dialog("stereo"),
            "Chorus / Flanger / Phaser": lambda: self.open_audio_effect_dialog("modulation"),
            "Delay sincronizado": lambda: self.open_audio_effect_dialog("delay"),
            "Cambiar tono": lambda: self.open_audio_effect_dialog("pitch"),
            "Cambiar tempo": lambda: self.open_audio_effect_dialog("tempo"),
            "Retumbe de baúl": lambda: self.open_audio_effect_dialog("trunk"),
            "Gritón · presencia vocal": lambda: self.open_audio_effect_dialog("shout"),
        }
        accion = opciones.get(self.effect_choice.get())
        if accion:
            accion()
        else:
            self.set_info("Elegí un efecto en el menú para abrir sus controles.", "#f4f4f5")

    def set_info(self, text, color="#f4f4f5"):
        self.info.configure(text=text, text_color=color)

    def clear_selection(self):
        self._selection_start = None
        self._selection_end = None
        self.draw_waveform()
        self.set_info("Selección de tiempo limpiada.", "#f4f4f5")

    def import_audio(self):
        paths = filedialog.askopenfilenames(
            parent=self,
            title="Importar audio",
            filetypes=[
                ("Audio", "*.wav *.flac *.aiff *.aif *.ogg *.mp3 *.m4a *.aac *.wma"),
                ("Todos los archivos", "*.*")
            ]
        )
        if not paths:
            return
        errors = []
        for path in paths:
            temp_decode = None
            try:
                source_path = path
                ext = os.path.splitext(path)[1].lower()
                if ext in {".mp3", ".m4a", ".aac", ".wma"}:
                    ffmpeg = localizar_programa("ffmpeg")
                    if not ffmpeg:
                        raise RuntimeError(
                            "Este formato necesita FFmpeg. Reinstalá la app con FFmpeg incluido "
                            "o agregá su carpeta bin al PATH."
                        )
                    fd, temp_decode = tempfile.mkstemp(prefix="editor_import_", suffix=".wav")
                    os.close(fd)
                    subprocess.run(
                        [ffmpeg, "-y", "-i", path, "-vn", "-acodec", "pcm_s16le", temp_decode],
                        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                        **opciones_subproceso_oculto()
                    )
                    source_path = temp_decode
                data, sr = sf.read(source_path, dtype="float32", always_2d=True)
                if data.size == 0:
                    raise ValueError("el archivo no contiene audio")
                self.tracks.append({
                    "name": os.path.basename(path),
                    "path": path,
                    "data": data,
                    "sr": int(sr),
                    "undo": [], "muted": False, "solo": False
                })
            except Exception as exc:
                errors.append(f"{os.path.basename(path)}: {exc}")
            finally:
                if temp_decode and os.path.exists(temp_decode):
                    try:
                        os.remove(temp_decode)
                    except OSError:
                        pass
        self.refresh_tracks()
        if errors:
            messagebox.showwarning("Algunos archivos no se pudieron importar", "\n".join(errors), parent=self)
        elif paths:
            self.set_info(f"Importación completada: {len(paths)} archivo(s).", "#f4f4f5")

    def refresh_tracks(self):
        for child in self.tracks_view.winfo_children():
            child.destroy()
        self.track_wave_canvases = []
        self.track_select_buttons = []
        self.track_rows = []
        self.track_mute_buttons = []
        self.track_solo_buttons = []
        self.track_gain_buttons = []
        self.track_pan_buttons = []
        if not self.tracks:
            self.selected_index = None
            self.selected_indices = set()
            self.wave_canvas = None
            ctk.CTkLabel(
                self.tracks_view, text="Importá un audio para crear tu primera pista.",
                text_color="#f4f4f5", font=fuente_ui(size=13)
            ).pack(fill="x", padx=18, pady=28)
            self.update_timeline_labels()
            return
        if self.selected_index is None or not 0 <= self.selected_index < len(self.tracks):
            self.selected_index = 0
        self.selected_indices = {
            index for index in self.selected_indices if 0 <= index < len(self.tracks)
        }
        if not self.selected_indices:
            self.selected_indices = {self.selected_index}

        for index, track in enumerate(self.tracks):
            selected = index in self.selected_indices
            row = ctk.CTkFrame(
                self.tracks_view, fg_color="#1b1b1f" if selected else "#111111",
                corner_radius=8, border_width=1,
                border_color="#45454b" if selected else "#292929"
            )
            row.pack(fill="x", padx=4, pady=4)
            self.track_rows.append(row)
            header = ctk.CTkFrame(row, fg_color="transparent")
            header.pack(fill="x", padx=5, pady=(4, 2))
            duration = len(track["data"]) / max(1, track["sr"])
            title = ctk.CTkButton(
                header, text=(f"{index + 1}. {track['name']}  ·  {self._format_time(duration)}"
                              f"  ·  Inicio {self._format_time(track.get('start_sec', 0.0))}"),
                command=lambda i=index: self.on_track_select(i), anchor="w", height=26,
                fg_color="#4a4a50" if selected else "#29292e",
                hover_color="#5b5b62", font=fuente_ui(size=10, weight="bold")
            )
            title.pack(side="left", fill="x", expand=True, padx=(0, 5))
            self.track_select_buttons.append(title)
            title.bind(
                "<Control-Button-1>",
                lambda event, i=index: self.on_track_select(i, additive=True) or "break",
                add="+",
            )
            track.setdefault("muted", False)
            track.setdefault("solo", False)
            track.setdefault("gain_db", 0.0)
            track.setdefault("pan", 0.0)
            track.setdefault("start_sec", 0.0)
            track.setdefault("gain_automation", [])
            track.setdefault("pan_automation", [])
            gain_min = float(track.get("gain_min", -36.0))
            gain_max = float(track.get("gain_max", 36.0))
            track["gain_db"] = max(gain_min, min(gain_max, float(track["gain_db"])))
            gain_control = ctk.CTkFrame(header, fg_color="transparent", width=145, height=28)
            gain_control.pack(side="right", padx=2)
            gain_control.pack_propagate(False)
            gain_slider = ctk.CTkSlider(
                gain_control, from_=gain_min, to=gain_max,
                number_of_steps=max(1, int((gain_max - gain_min) * 20)),
                width=94, height=18,
                command=lambda value, i=index: self.set_track_gain(i, value)
            )
            gain_slider.set(track["gain_db"])
            gain_slider.pack(side="left", padx=(1, 3), pady=4)
            gain_label = ctk.CTkLabel(
                gain_control, text=f"{track['gain_db']:+.1f} dB", width=45,
                font=fuente_ui(size=9), text_color="#f4f4f5"
            )
            gain_label.pack(side="right")
            self.track_gain_buttons.append(gain_label)
            pan_text = "C" if abs(track["pan"]) < 0.5 else "I" if track["pan"] < 0 else "D"
            pan_button = self._button(
                header, f"Pan {pan_text} {abs(track['pan']):.0f}%",
                lambda i=index: self.open_track_level_dialog(i, "pan"), width=82
            )
            pan_button.configure(height=26, font=fuente_ui(size=9))
            pan_button.pack(side="right", padx=2)
            self.track_pan_buttons.append(pan_button)
            mute = self._button(
                header, "M", lambda i=index: self.toggle_track_mute(i), width=32
            )
            mute.configure(
                height=26, fg_color="#35353a" if track["muted"] else "#252525",
                hover_color="#414147"
            )
            mute.pack(side="right", padx=2)
            self.track_mute_buttons.append(mute)
            solo = self._button(
                header, "S", lambda i=index: self.toggle_track_solo(i), width=32
            )
            solo.configure(
                height=26, fg_color="#45454b" if track["solo"] else "#252525",
                hover_color="#55555c", text_color="#ffffff"
            )
            solo.pack(side="right", padx=2)
            self.track_solo_buttons.append(solo)
            menu = ctk.CTkOptionMenu(
                header, values=["Pista ▾", "Duplicar", "Eliminar", "Automatizar"], width=110, height=26,
                font=fuente_ui(size=10),
                command=lambda choice, i=index: self._track_menu_action(choice, i)
            )
            menu.set("Pista ▾")
            menu.pack(side="right")
            canvas = tk.Canvas(
                row, height=100, bg="#101012", highlightthickness=1,
                highlightbackground="#45454b" if selected else "#303030", cursor="crosshair"
            )
            canvas.pack(fill="x", padx=6, pady=(1, 6))
            canvas.bind("<ButtonPress-1>", lambda event, i=index: self._track_canvas_press(event, i))
            canvas.bind(
                "<Control-Button-1>",
                lambda _event, i=index: (self.on_track_select(i, additive=True), "break")[1],
                add="+",
            )
            canvas.bind("<B1-Motion>", lambda event, i=index: self._track_canvas_seek(event, i))
            canvas.bind("<ButtonRelease-1>", self._end_seek)
            canvas.bind("<Configure>", lambda _event, i=index: self.draw_waveform(i))
            self.track_wave_canvases.append(canvas)

        self.wave_canvas = self.track_wave_canvases[self.selected_index]
        self.update_timeline_labels()
        self.draw_waveform()

    def _track_menu_action(self, choice, index):
        if choice == "Pista ▾":
            return
        self.on_track_select(index)
        if choice == "Duplicar":
            self.duplicate_track()
        elif choice == "Eliminar":
            self.remove_track(index)
        elif choice == "Automatizar":
            self.open_track_automation_dialog(index)

    def open_track_automation_dialog(self, index):
        if not 0 <= index < len(self.tracks):
            return
        track = self.tracks[index]
        duration = len(track["data"]) / max(1, track["sr"])
        win = ctk.CTkToplevel(self)
        win.title(f"Automatización · {track.get('name', 'Pista')}")
        win.geometry("570x475")
        win.minsize(480, 390)
        win.configure(fg_color="#18181b")
        win.transient(self)
        win.grab_set()
        ctk.CTkLabel(
            win, text="AUTOMATIZACIÓN DE PISTA", font=fuente_ui(size=16, weight="bold")
        ).pack(anchor="w", padx=16, pady=(15, 5))
        ctk.CTkLabel(
            win,
            text=f"Definí puntos como segundo; valor. El valor entre puntos se interpola linealmente. Duración: {self._format_time(duration)}.",
            text_color="#bcbcc0", wraplength=525, anchor="w"
        ).pack(anchor="w", padx=16, pady=(0, 9))
        mode = ctk.CTkComboBox(win, values=["Volumen (dB)", "Panorama (%)"], width=210, state="readonly")
        mode.set("Volumen (dB)")
        mode.pack(anchor="w", padx=16, pady=(0, 7))
        editor = ctk.CTkTextbox(
            win, wrap="none", fg_color="#101012", text_color="#f4f4f5",
            border_width=1, border_color="#303034", font=("Consolas", 11)
        )
        editor.pack(fill="both", expand=True, padx=16, pady=(0, 8))

        def show_points(_value=None):
            key = "gain_automation" if mode.get().startswith("Volumen") else "pan_automation"
            points = track.get(key, [])
            editor.delete("1.0", "end")
            editor.insert("1.0", "\n".join(f"{float(t):.3f}; {float(v):.2f}" for t, v in points))

        mode.configure(command=show_points)
        show_points()
        ctk.CTkLabel(
            win, text="Volumen: −36 a +36 dB · Panorama: −100 (izq.) a +100 (der.).",
            text_color="#8f8f96", anchor="w"
        ).pack(anchor="w", padx=16, pady=(0, 5))
        footer = ctk.CTkFrame(win, fg_color="transparent")
        footer.pack(fill="x", padx=13, pady=(4, 13))

        def save_points():
            key = "gain_automation" if mode.get().startswith("Volumen") else "pan_automation"
            lower, upper = (-36.0, 36.0) if key == "gain_automation" else (-100.0, 100.0)
            raw_lines = editor.get("1.0", "end-1c").splitlines()
            parsed = []
            for line_number, line in enumerate(raw_lines, start=1):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = [part.strip().replace(",", ".") for part in line.split(";")]
                if len(parts) != 2:
                    raise ValueError(f"La línea {line_number} debe tener el formato segundo; valor.")
                moment, value = map(float, parts)
                if not math.isfinite(moment) or not math.isfinite(value):
                    raise ValueError(f"La línea {line_number} contiene un número no válido.")
                if not 0 <= moment <= duration or not lower <= value <= upper:
                    raise ValueError(f"La línea {line_number} está fuera del rango permitido.")
                parsed.append([moment, value])
            parsed.sort(key=lambda point: point[0])
            if parsed and (len(parsed) < 2 or any(a[0] >= b[0] for a, b in zip(parsed, parsed[1:]))):
                raise ValueError("Usá al menos dos tiempos distintos y sin repetir.")
            self._stop_effect_preview()
            self.stop_preview()
            track[key] = parsed
            win.destroy()
            self.refresh_tracks()
            kind = "volumen" if key == "gain_automation" else "panorama"
            status = "borrada" if not parsed else f"actualizada con {len(parsed)} puntos"
            self.set_info(f"Automatización de {kind} {status}: {track.get('name', 'Pista')}", "#f4f4f5")

        def save_safe():
            try:
                save_points()
            except (TypeError, ValueError) as exc:
                messagebox.showerror("Puntos no válidos", str(exc), parent=win)

        self._button(footer, "Cancelar", win.destroy, width=100).pack(side="right", padx=4)
        self._button(footer, "Guardar puntos", save_safe, width=140).pack(side="right", padx=4)

    def toggle_track_mute(self, index):
        if not 0 <= index < len(self.tracks):
            return
        track = self.tracks[index]
        track["muted"] = not track.get("muted", False)
        if self._preview_preparing or (self._preview_process and self._preview_process.poll() is None):
            self.stop_preview()
        if index < len(self.track_mute_buttons):
            self.track_mute_buttons[index].configure(
                fg_color="#35353a" if track["muted"] else "#252525"
            )
        self.set_info(f"Pista {'silenciada' if track['muted'] else 'activada'}: {track['name']}", "#f4f4f5")

    def set_track_gain(self, index, value):
        if not 0 <= index < len(self.tracks):
            return
        low = float(self.tracks[index].get("gain_min", -36.0))
        high = float(self.tracks[index].get("gain_max", 36.0))
        value = max(low, min(high, float(value)))
        self.tracks[index]["gain_db"] = value
        if index < len(self.track_gain_buttons):
            self.track_gain_buttons[index].configure(text=f"{value:+.1f} dB")
        if sd is None and self._preview_process and self._preview_process.poll() is None:
            self.set_info("Ganancia lista; instalá sounddevice para oír el ajuste en vivo mientras reproduce.", "#f4f4f5")

    def open_track_level_dialog(self, index, control):
        if not 0 <= index < len(self.tracks):
            return
        track = self.tracks[index]
        is_gain = control == "gain_db"
        title = "Ganancia" if is_gain else "Panorama" if control == "pan" else "Control de pista"
        low, high = (
            (float(track.get("gain_min", -36.0)), float(track.get("gain_max", 36.0)))
            if is_gain else (-100.0, 100.0)
        )
        current = float(track.get(control, 0.0))
        win = ctk.CTkToplevel(self)
        win.title(title)
        win.geometry("390x205")
        win.resizable(False, False)
        win.configure(fg_color="#18181b")
        win.transient(self)
        win.grab_set()
        ctk.CTkLabel(
            win, text=f"{title} · {track['name']}", font=fuente_ui(size=14, weight="bold")
        ).pack(anchor="w", padx=18, pady=(16, 8))
        entry = ctk.CTkEntry(win, width=90)
        entry.insert(0, f"{current:.1f}")
        entry.pack(anchor="w", padx=18, pady=(0, 6))
        slider = ctk.CTkSlider(win, from_=low, to=high, number_of_steps=720 if is_gain else 200, width=345)
        slider.set(current)
        slider.pack(fill="x", padx=18, pady=6)
        unit = "dB" if is_gain else "%  (−100 izquierda · 0 centro · +100 derecha)"

        def sync_entry(value):
            entry.delete(0, "end")
            entry.insert(0, f"{float(value):.1f}")

        slider.configure(command=sync_entry)

        def sync_slider(_event=None):
            try:
                value = float(entry.get().replace(",", "."))
                slider.set(max(low, min(high, value)))
                sync_entry(slider.get())
            except ValueError:
                pass

        entry.bind("<Return>", sync_slider)
        ctk.CTkLabel(win, text=unit, text_color="#f4f4f5", font=fuente_ui(size=10)).pack(anchor="w", padx=18)

        def apply():
            try:
                value = float(entry.get().replace(",", "."))
                if not low <= value <= high:
                    raise ValueError(f"El valor debe estar entre {low:g} y {high:g}.")
            except Exception as exc:
                messagebox.showerror("Valor no válido", str(exc), parent=win)
                return
            self.stop_preview()
            track[control] = value
            if is_gain:
                self.track_gain_buttons[index].configure(text=f"Ganancia {value:+.1f} dB")
                self.set_info(f"Ganancia de '{track['name']}': {value:+.1f} dB", "#f4f4f5")
            else:
                pan_label = "Centro" if abs(value) < 0.5 else f"{'Izquierda' if value < 0 else 'Derecha'} {abs(value):.0f}%"
                self.track_pan_buttons[index].configure(text=f"Pan {('C' if abs(value) < 0.5 else 'I' if value < 0 else 'D')} {abs(value):.0f}%")
                self.set_info(f"Panorama de '{track['name']}': {pan_label}", "#f4f4f5")
            win.destroy()

        footer = ctk.CTkFrame(win, fg_color="transparent")
        footer.pack(side="bottom", fill="x", padx=14, pady=12)
        self._button(footer, "Cancelar", win.destroy, width=95).pack(side="right", padx=4)
        self._button(footer, "Aceptar", apply, width=95).pack(side="right", padx=4)

    def toggle_track_solo(self, index):
        if not 0 <= index < len(self.tracks):
            return
        track = self.tracks[index]
        track["solo"] = not track.get("solo", False)
        if self._preview_preparing or (self._preview_process and self._preview_process.poll() is None):
            self.stop_preview()
        for i, button in enumerate(self.track_solo_buttons):
            button.configure(
                fg_color="#45454b" if self.tracks[i].get("solo", False) else "#252525",
                hover_color="#55555c", text_color="#ffffff"
            )
        self.set_info(f"Solo {'activado' if track['solo'] else 'desactivado'}: {track['name']}", "#f4f4f5")

    def remove_track(self, index):
        if not 0 <= index < len(self.tracks):
            return
        if self._preview_preparing or (self._preview_process and self._preview_process.poll() is None):
            self.stop_preview()
        removed = self.tracks.pop(index)
        self._discard_track_preview_cache(removed)
        self.selected_indices = {
            selected if selected < index else selected - 1
            for selected in self.selected_indices if selected != index
        }
        if not self.tracks:
            self.selected_index = None
            self.selected_indices.clear()
        elif self.selected_index >= len(self.tracks):
            self.selected_index = len(self.tracks) - 1
        elif index < self.selected_index:
            self.selected_index -= 1
        if self.tracks and not self.selected_indices:
            self.selected_indices = {self.selected_index}
        self._preview_position = 0.0
        self._wave_view_center = 0.0
        self._selection_start = None
        self._selection_end = None
        self.refresh_tracks()
        self.set_info("Pista eliminada del proyecto.", "#f4f4f5")

    def remove_selected_track(self):
        """Elimina desde el botón superior la pista actualmente seleccionada."""
        indices = self._selected_track_indices()
        if not indices:
            messagebox.showinfo(
                "Borrar pista", "Seleccioná una pista para borrarla.", parent=self
            )
            return
        names = [self.tracks[index].get("name", "Pista") for index in indices]
        description = names[0] if len(names) == 1 else f"{len(names)} pistas seleccionadas"
        if not messagebox.askyesno(
            "Borrar pista", f"¿Querés borrar '{description}' del proyecto?", parent=self
        ):
            return
        for index in reversed(indices):
            self.remove_track(index)

    def _track_canvas_press(self, event, index):
        self.on_track_select(index)
        self._begin_seek(event)
        position = self._track_time_from_x(event.x, index)
        self._selection_start = position
        self._selection_end = position
        self._track_canvas_seek(event, index)

    def _track_canvas_seek(self, event, index):
        if self.selected_index != index:
            self.on_track_select(index)
        self.wave_canvas = self.track_wave_canvases[index]
        position = self._track_time_from_x(event.x, index)
        if self._selection_start is None:
            self._selection_start = position
        self._selection_end = position
        self._preview_position = float(self.tracks[index].get("start_sec", 0.0)) + position
        self.update_timeline_labels()
        self.draw_waveform(index)

    def _track_time_from_x(self, x, index):
        track = self.tracks[index]
        duration = len(track["data"]) / max(1, track["sr"])
        canvas = self.track_wave_canvases[index]
        width = max(canvas.winfo_width(), 1)
        zoom = max(1.0, float(self.wave_zoom.get()))
        project_duration = max(self.get_project_duration(), 0.001)
        visible_duration = project_duration / zoom
        visible_start = max(0.0, min(project_duration - visible_duration,
                                     self._wave_view_center - visible_duration / 2))
        absolute_time = visible_start + max(0.0, min(width, x)) / width * visible_duration
        local_time = absolute_time - float(track.get("start_sec", 0.0))
        return max(0.0, min(duration, local_time))

    def _selected_track_indices(self):
        indices = sorted(i for i in self.selected_indices if 0 <= i < len(self.tracks))
        if indices:
            return indices
        return [self.selected_index] if self.selected_index is not None and 0 <= self.selected_index < len(self.tracks) else []

    def on_track_select(self, index=None, additive=False):
        if index is None or not (0 <= index < len(self.tracks)):
            return
        if additive:
            self.stop_preview()
            if index in self.selected_indices and len(self.selected_indices) > 1:
                self.selected_indices.discard(index)
                if self.selected_index == index:
                    self.selected_index = min(self.selected_indices)
            else:
                self.selected_indices.add(index)
                self.selected_index = index
        else:
            changed = self.selected_index != index or self.selected_indices != {index}
            self.selected_indices = {index}
            if changed:
                self.stop_preview()
                self._preview_position = 0.0
                self._wave_view_center = 0.0
                self._selection_start = None
                self._selection_end = None
                self._is_seeking = False
            self.selected_index = index
        if not self.selected_indices:
            self.selected_indices = {index}
            self.selected_index = index
        if not additive and self.selected_index == index:
            pass
        elif additive:
            self._selection_start = None
            self._selection_end = None
            if 0 <= self.selected_index < len(self.track_wave_canvases):
                self.wave_canvas = self.track_wave_canvases[self.selected_index]
        for i, button in enumerate(self.track_select_buttons):
            selected = i in self.selected_indices
            button.configure(fg_color="#4a4a50" if selected else "#29292e")
        for i, row in enumerate(self.track_rows):
            if i < len(self.tracks):
                selected = i in self.selected_indices
                row.configure(
                    fg_color="#1b1b1f" if selected else "#111111",
                    border_color="#45454b" if selected else "#292929",
                )
        self.update_timeline_labels()
        self.draw_waveform()

    def _format_time(self, seconds):
        seconds = max(0.0, float(seconds))
        minutes = int(seconds // 60)
        secs = seconds - minutes * 60
        return f"{minutes:02d}:{secs:04.1f}"

    def get_duration(self):
        if self.selected_index is None or not (0 <= self.selected_index < len(self.tracks)):
            return 0.0
        track = self.tracks[self.selected_index]
        return len(track["data"]) / max(1, track["sr"])

    def get_project_duration(self):
        return max(
            float(track.get("start_sec", 0.0)) + len(track["data"]) / max(1, track["sr"])
            for track in self.tracks
        ) if self.tracks else 0.0

    def use_selected_as_intro(self):
        track = self.get_track()
        if track is None:
            return
        intro_duration = len(track["data"]) / max(1, track["sr"])
        name = track["name"]
        self.stop_preview()
        for index, other in enumerate(self.tracks):
            other["start_sec"] = 0.0 if index == self.selected_index else intro_duration
        self._preview_position = 0.0
        self._wave_view_center = 0.0
        self.refresh_tracks()
        self.set_info(
            f"'{name}' empieza el proyecto; las otras pistas entran en "
            f"{self._format_time(intro_duration)}. Usá Exportar mezcla para incluirlas.",
            "#f4f4f5"
        )

    def update_timeline_labels(self):
        if self._preview_preparing or (self._preview_process and self._preview_process.poll() is None):
            duration = self._preview_duration or self.get_duration()
        else:
            duration = self.get_project_duration()
        pos = min(max(0.0, self._preview_position), duration)
        self.time_current.configure(text=self._format_time(pos))
        self.time_total.configure(text=self._format_time(duration))
        self.seek_slider.configure(to=max(duration, 0.001))
        try:
            self.seek_slider.set(pos)
        except Exception:
            pass

    def draw_waveform(self, only_index=None):
        """Dibuja todas las pistas apiladas como un proyecto multipista."""
        if not self.tracks or not getattr(self, "track_wave_canvases", None):
            return
        indices = [only_index] if only_index is not None else range(len(self.track_wave_canvases))
        for index in indices:
            if index >= len(self.tracks) or index >= len(self.track_wave_canvases):
                continue
            canvas = self.track_wave_canvases[index]
            track = self.tracks[index]
            canvas.delete("all")
            width = max(canvas.winfo_width(), 10)
            height = max(canvas.winfo_height(), 10)
            duration = len(track["data"]) / max(1, track["sr"])
            if duration <= 0:
                continue
            track_start = float(track.get("start_sec", 0.0))
            project_duration = max(self.get_project_duration(), 0.001)
            envelope, bin_samples = self._get_waveform_channel_envelope(track)
            zoom = max(1.0, float(self.wave_zoom.get()))
            visible_duration = project_duration / zoom
            visible_start = max(0.0, min(project_duration - visible_duration,
                                         self._wave_view_center - visible_duration / 2))
            visible_end = visible_start + visible_duration
            local_start = max(0.0, visible_start - track_start)
            local_end = min(duration, visible_end - track_start)
            if local_end <= local_start:
                peaks = np.zeros((0, envelope.shape[1]), dtype=np.float32)
                first_bin = 0
            else:
                first_bin = max(0, int(local_start * track["sr"] / bin_samples))
                last_bin = min(len(envelope), max(first_bin + 1,
                                                  int(math.ceil(local_end * track["sr"] / bin_samples))))
                peaks = envelope[first_bin:last_bin]
            pixel_step = max(1, int(math.ceil(len(peaks) / width)))
            full = (len(peaks) // pixel_step) * pixel_step
            if full:
                peak = np.max(peaks[:full].reshape(-1, pixel_step, peaks.shape[1]), axis=1)
                if full < len(peaks):
                    peak = np.vstack((peak, np.max(peaks[full:], axis=0)))
            else:
                peak = peaks
            peak = peak[:width]
            channels = max(1, int(peak.shape[1]))
            lane_height = height / channels
            color = "#f4f4f5" if index == self.selected_index else "#f4f4f5"
            for channel in range(channels):
                mid = lane_height * channel + lane_height / 2
                scale = max(1.0, lane_height / 2 - 7)
                upper = []
                lower = []
                x_start = ((track_start + first_bin * bin_samples / track["sr"] - visible_start)
                           / max(visible_duration, 0.001) * width)
                x_step = pixel_step * bin_samples / track["sr"] / max(visible_duration, 0.001) * width
                for bin_index, value in enumerate(peak[:, channel]):
                    x = x_start + bin_index * x_step
                    amp = min(1.0, float(value)) * scale
                    upper.extend((x, mid - amp))
                    lower.append((x, mid + amp))
                if upper:
                    lower_coords = [coordinate for point in reversed(lower) for coordinate in point]
                    canvas.create_polygon(*(upper + lower_coords), fill=color, outline=color)
                canvas.create_line(0, mid, width, mid, fill="#333333")
            if index == self.selected_index and self._selection_start is not None and self._selection_end is not None:
                selection_left, selection_right = sorted((self._selection_start, self._selection_end))
                x1 = (track_start + selection_left - visible_start) / max(visible_duration, 0.001) * width
                x2 = (track_start + selection_right - visible_start) / max(visible_duration, 0.001) * width
                if x2 - x1 > 1:
                    canvas.create_rectangle(
                        max(0, x1), 1, min(width, x2), height - 1,
                        fill="#45454b", stipple="gray50", outline="#45454b", width=1
                    )
            ratio = min(1.0, max(0.0, (self._preview_position - visible_start) / max(visible_duration, 0.001)))
            px = ratio * width
            canvas.create_line(px, 0, px, height, fill="#bcbcc0" if index == self.selected_index else "#bcbcc0", width=2)
            for tick in range(0, 6):
                tick_x = width * tick / 5
                tick_time = visible_start + visible_duration * tick / 5
                canvas.create_line(tick_x, height - 11, tick_x, height, fill="#f4f4f5")
                canvas.create_text(tick_x + 2, height - 2, text=self._format_time(tick_time),
                                   fill="#f4f4f5", anchor="sw", font=("Segoe UI", 9))
            canvas.create_text(
                8, 7, anchor="nw",
                text=f"{self._format_time(visible_start)} — {self._format_time(visible_end)}",
                fill="#f4f4f5" if index == self.selected_index else "#bcbcc0"
            )

    def _get_waveform_channel_envelope(self, track, target_bins=12000):
        cached = track.get("wave_channel_envelope")
        if cached is not None:
            return cached, track["wave_bin_samples"]
        data = track["data"]
        channels = data.shape[1] if data.ndim > 1 else 1
        frame_count = len(data)
        bin_samples = max(256, int(math.ceil(frame_count / target_bins)))
        batch_frames = max(bin_samples, (1_000_000 // bin_samples) * bin_samples)
        batches = []
        for start in range(0, frame_count, batch_frames):
            chunk = data[start:min(frame_count, start + batch_frames)]
            if chunk.ndim == 1:
                chunk = chunk[:, None]
            complete = (len(chunk) // bin_samples) * bin_samples
            if complete:
                grouped = np.abs(chunk[:complete]).reshape(-1, bin_samples, channels)
                batches.append(np.max(grouped, axis=1))
            if complete < len(chunk):
                batches.append(np.max(np.abs(chunk[complete:]), axis=0, keepdims=True))
        envelope = np.concatenate(batches, axis=0).astype(np.float32, copy=False) if batches else np.zeros((1, channels), dtype=np.float32)
        track["wave_channel_envelope"] = envelope
        track["wave_bin_samples"] = bin_samples
        return envelope, bin_samples

    def _get_waveform_envelope(self, track, target_bins=12000):
        """Calcula una envolvente liviana una sola vez y la reutiliza al dibujar/arrastrar."""
        cached = track.get("wave_envelope")
        if cached is not None:
            return cached, track["wave_bin_samples"]

        data = track["data"]
        frame_count = len(data)
        bin_samples = max(256, int(math.ceil(frame_count / target_bins)))
        batch_frames = max(bin_samples, (1_000_000 // bin_samples) * bin_samples)
        peaks = []
        for start in range(0, frame_count, batch_frames):
            chunk = data[start:min(frame_count, start + batch_frames)]
            mono = chunk.mean(axis=1) if getattr(chunk, "ndim", 1) > 1 else chunk
            complete = (len(mono) // bin_samples) * bin_samples
            if complete:
                grouped = np.abs(mono[:complete]).reshape(-1, bin_samples)
                peaks.append(np.max(grouped, axis=1))
            if complete < len(mono):
                peaks.append(np.array([np.max(np.abs(mono[complete:]))], dtype=np.float32))
        envelope = np.concatenate(peaks).astype(np.float32, copy=False) if peaks else np.zeros(1, dtype=np.float32)
        track["wave_envelope"] = envelope
        track["wave_bin_samples"] = bin_samples
        return envelope, bin_samples

    def draw_wave_overview(self):
        if not hasattr(self, "overview_canvas"):
            return
        canvas = self.overview_canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 10)
        height = max(canvas.winfo_height(), 10)
        duration = self.get_duration()
        if duration <= 0:
            return
        track = self.tracks[self.selected_index]
        envelope, _bin_samples = self._get_waveform_envelope(track)
        step = max(1, int(math.ceil(len(envelope) / width)))
        full = (len(envelope) // step) * step
        if full:
            peaks = np.max(envelope[:full].reshape(-1, step), axis=1)
            if full < len(envelope):
                peaks = np.append(peaks, np.max(envelope[full:]))
        else:
            peaks = envelope
        peaks = peaks[:width]
        mid = height / 2
        scale = max(1.0, mid - 4)
        top = []
        bottom = []
        for x, value in enumerate(peaks):
            amp = min(1.0, float(value)) * scale
            top.extend((x, mid - amp))
            bottom.append((x, mid + amp))
        if top:
            bottom_coords = [coordinate for point in reversed(bottom) for coordinate in point]
            canvas.create_polygon(*(top + bottom_coords), fill="#f4f4f5", outline="#f4f4f5")

        zoom = max(1.0, float(self.wave_zoom.get()))
        visible_duration = duration / zoom
        visible_start = max(0.0, min(duration - visible_duration,
                                     self._wave_view_center - visible_duration / 2))
        x1 = visible_start / duration * width
        x2 = (visible_start + visible_duration) / duration * width
        canvas.create_rectangle(x1, 1, max(x1 + 2, x2), height - 1,
                                outline="#45454b", fill="#45454b", stipple="gray50")
        play_x = min(width, max(0.0, self._preview_position / duration * width))
        canvas.create_line(play_x, 0, play_x, height, fill="#bcbcc0", width=2)

    def seek_from_overview(self, event):
        duration = self.get_duration()
        if duration <= 0:
            return
        width = max(self.overview_canvas.winfo_width(), 1)
        self._preview_position = min(duration, max(0.0, event.x / width * duration))
        self._wave_view_center = self._preview_position
        self.update_timeline_labels()
        self.draw_waveform()

    def _begin_seek(self, _event=None):
        self._is_seeking = True

    def _end_seek(self, _event=None):
        self._is_seeking = False
        self._wave_view_center = self._preview_position
        self.draw_waveform()
        self._restart_preview_if_active()

    def _set_wave_zoom(self, value):
        self._wave_view_center = self._preview_position
        self.wave_zoom_label.configure(text=f"{float(value):.0f}×")
        self.draw_waveform()

    def _center_wave_on_playhead(self):
        self._wave_view_center = self._preview_position
        self.draw_waveform()

    def seek_from_canvas(self, event):
        duration = self._preview_duration if self._preview_process or self._preview_preparing else self.get_project_duration()
        if duration <= 0:
            return
        width = max(self.wave_canvas.winfo_width(), 1)
        zoom = max(1.0, float(self.wave_zoom.get()))
        visible_duration = duration / zoom
        visible_start = max(0.0, min(duration - visible_duration,
                                     self._wave_view_center - visible_duration / 2))
        self._preview_position = max(
            visible_start,
            min(visible_start + visible_duration, visible_start + event.x / width * visible_duration)
        )
        self.update_timeline_labels()
        self.draw_waveform()

    def seek_from_slider(self, value):
        if self._preview_process and self._preview_process.poll() is None:
            # El deslizador se usa como marcador mientras suena; el usuario puede volver a iniciar desde ahí.
            pass
        self._preview_position = float(value)
        self.update_timeline_labels()
        self.draw_waveform()

    def _preview_setting_changed(self):
        self._preview_volume_live = int(self.preview_volume.get())
        if hasattr(self, "preview_volume_label"):
            self.preview_volume_label.configure(text=f"{int(self.preview_volume.get())}%")
        if self._preview_process and self._preview_process.poll() is None:
            self.set_info("Volumen y velocidad nuevos se aplican al reiniciar la vista previa.", "#f4f4f5")

    def _preview_speed_selected(self, _value=None):
        self._preview_setting_changed()
        self._restart_preview_if_active()

    def _restart_preview_if_active(self):
        if self._preview_process and self._preview_process.poll() is None:
            self.preview_track()
        elif self._preview_preparing:
            # El render sigue; al terminar toma la posición que el usuario eligió.
            return

    def get_track(self):
        if self.selected_index is None or not (0 <= self.selected_index < len(self.tracks)):
            messagebox.showinfo("Seleccioná una pista", "Importá una pista y tocá su encabezado para seleccionarla.", parent=self)
            return None
        return self.tracks[self.selected_index]

    def open_in_audacity(self):
        """Abre una copia WAV de la pista editada en la instalación real de Audacity."""
        track = self.get_track()
        if track is None:
            return
        candidates = [
            shutil.which("audacity"),
            r"C:\Program Files\Audacity\audacity.exe",
            r"C:\Program Files (x86)\Audacity\audacity.exe",
            os.path.join(os.path.expanduser("~"), "AppData", "Local", "Programs", "Audacity", "audacity.exe"),
        ]
        executable = next((p for p in candidates if p and os.path.isfile(p)), None)
        if not executable:
            messagebox.showerror("Audacity no encontrado", "No se encontró audacity.exe en las ubicaciones habituales.", parent=self)
            return
        path = None
        try:
            rendered = self._render_track_gain_pan(
                track["data"], track.get("gain_db", 0.0), track.get("pan", 0.0), track=track
            )
            path = self._temp_wav(rendered, track["sr"])
            subprocess.Popen([executable, path], close_fds=True)
            self._audacity_temp_paths.append(path)
            self.set_info("Pista abierta en Audacity (copia WAV de los cambios actuales).", "#f4f4f5")
        except Exception as exc:
            if path and os.path.isfile(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
            messagebox.showerror("No se pudo abrir Audacity", str(exc), parent=self)

    def duplicate_track(self):
        indices = self._selected_track_indices()
        if not indices:
            self.get_track()
            return
        self.stop_preview()
        new_indices = []
        for prior_count, original_index in enumerate(indices):
            source_index = original_index + prior_count
            source = self.tracks[source_index]
            duplicate = dict(source)
            duplicate["name"] = str(source.get("name", "Pista")) + " (copia)"
            duplicate["data"] = source["data"].copy()
            duplicate["undo"] = []
            duplicate["redo"] = []
            duplicate["muted"] = False
            duplicate["solo"] = False
            for cache_key in ("_preview_temp", "wave_envelope", "wave_channel_envelope", "wave_bin_samples"):
                duplicate.pop(cache_key, None)
            insert_at = source_index + 1
            self.tracks.insert(insert_at, duplicate)
            new_indices.append(insert_at)
        self.selected_indices = set(new_indices)
        self.selected_index = new_indices[-1]
        self._selection_start = None
        self._selection_end = None
        self.refresh_tracks()
        self.set_info(
            f"{len(new_indices)} pista(s) duplicada(s). Las copias se pueden editar de forma independiente.",
            "#f4f4f5"
        )

    def push_undo(self, track):
        track.setdefault("undo", [])
        track.setdefault("redo", [])
        track["undo"].append(track["data"].copy())
        if len(track["undo"]) > 10:
            track["undo"].pop(0)
        track["redo"].clear()

    def undo_effect(self):
        indices = self._selected_track_indices()
        if not indices:
            self.get_track()
            return
        candidates = [self.tracks[i] for i in indices if self.tracks[i].get("undo")]
        if not candidates:
            self.set_info("No hay cambios para deshacer en las pistas seleccionadas.", "#f4f4f5")
            return
        self.stop_preview()
        for track in candidates:
            track.setdefault("redo", []).append(track["data"].copy())
            self._discard_track_preview_cache(track)
            track["data"] = track["undo"].pop()
            for key in ("wave_envelope", "wave_channel_envelope", "wave_bin_samples"):
                track.pop(key, None)
        self.refresh_tracks()
        self.set_info(f"Se deshicieron cambios en {len(candidates)} pista(s). Podés rehacer con Ctrl+Y.", "#f4f4f5")

    def redo_effect(self):
        indices = self._selected_track_indices()
        if not indices:
            self.get_track()
            return
        candidates = [self.tracks[i] for i in indices if self.tracks[i].get("redo")]
        if not candidates:
            self.set_info("No hay cambios para rehacer en las pistas seleccionadas.", "#f4f4f5")
            return
        self.stop_preview()
        for track in candidates:
            self._discard_track_preview_cache(track)
            track.setdefault("undo", []).append(track["data"].copy())
            if len(track["undo"]) > 10:
                track["undo"].pop(0)
            track["data"] = track["redo"].pop()
            for key in ("wave_envelope", "wave_channel_envelope", "wave_bin_samples"):
                track.pop(key, None)
        self.refresh_tracks()
        self.set_info(f"Se rehízo el cambio en {len(candidates)} pista(s).", "#f4f4f5")

    def _parse_timecode(self, value):
        value = value.strip().replace(",", ".")
        parts = value.split(":")
        try:
            if len(parts) == 1:
                seconds = float(parts[0])
            elif len(parts) == 2:
                seconds = int(parts[0]) * 60 + float(parts[1])
            elif len(parts) == 3:
                seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
            else:
                raise ValueError
        except ValueError:
            raise ValueError("Usá segundos, MM:SS o HH:MM:SS.")
        if not np.isfinite(seconds) or seconds < 0:
            raise ValueError("El tiempo debe ser un número positivo.")
        return seconds

    def open_cutter_dialog(self):
        track = self.get_track()
        if track is None:
            return
        duration = len(track["data"]) / max(1, track["sr"])
        win = ctk.CTkToplevel(self)
        win.title("Cortador de audio")
        win.geometry("470x365")
        win.resizable(False, False)
        win.configure(fg_color="#18181b")
        win.transient(self)
        win.grab_set()
        ctk.CTkLabel(win, text="CORTAR SECCIÓN", font=fuente_ui(size=17, weight="bold")).pack(pady=(18, 5))
        ctk.CTkLabel(
            win, text=f"Pista: {track['name']} · duración {self._format_time(duration)}",
            text_color="#f4f4f5", wraplength=420
        ).pack(padx=16, pady=(0, 12))

        fields = ctk.CTkFrame(win, fg_color="transparent")
        fields.pack(fill="x", padx=22)
        entries = {}
        for label, key, default in (
            ("Desde", "start", self._format_time(0)),
            ("Hasta", "end", self._format_time(duration)),
        ):
            row = ctk.CTkFrame(fields, fg_color="transparent")
            row.pack(fill="x", pady=5)
            ctk.CTkLabel(row, text=label, width=70, anchor="w").pack(side="left")
            entry = ctk.CTkEntry(row, width=140)
            entry.insert(0, default)
            entry.pack(side="left", padx=4)
            entries[key] = entry

        def get_cutter_preset():
            return {key: entry.get().strip() for key, entry in entries.items()}

        def set_cutter_preset(values):
            start = self._parse_timecode(str(values["start"]))
            end = self._parse_timecode(str(values["end"]))
            if start >= end or end > duration:
                raise ValueError("El rango guardado no entra en la duración de esta pista.")
            for key, entry in entries.items():
                entry.delete(0, "end")
                entry.insert(0, str(values[key]))

        def cutter_preview_source():
            start = self._parse_timecode(entries["start"].get())
            end = self._parse_timecode(entries["end"].get())
            if start >= end or end > duration:
                raise ValueError("El rango debe cumplir 0 ≤ inicio < fin ≤ la duración de la pista.")
            first = max(0, int(round(start * track["sr"])))
            last = min(len(track["data"]), int(round(end * track["sr"])))
            return track["data"][first:last].copy(), track["sr"]

        self._preset_controls(
            win, "Cortador", get_cutter_preset, set_cutter_preset,
            lambda: (lambda data, _sr: data), cutter_preview_source,
        )

        marks = ctk.CTkFrame(win, fg_color="transparent")
        marks.pack(fill="x", padx=18, pady=(10, 4))

        def mark(which):
            entry = entries[which]
            entry.delete(0, "end")
            entry.insert(0, self._format_time(self._preview_position))

        self._button(marks, "Marcar inicio en playhead", lambda: mark("start"), width=190).pack(side="left", padx=4)
        self._button(marks, "Marcar final en playhead", lambda: mark("end"), width=190).pack(side="left", padx=4)

        def make_clip():
            try:
                start = self._parse_timecode(entries["start"].get())
                end = self._parse_timecode(entries["end"].get())
                if start >= end or end > duration:
                    raise ValueError(f"El rango debe cumplir 0 ≤ inicio < fin ≤ {duration:.2f} segundos.")
                first = max(0, int(round(start * track["sr"])))
                last = min(len(track["data"]), int(round(end * track["sr"])))
                clip = track["data"][first:last].copy()
                if len(clip) < 2:
                    raise ValueError("El corte es demasiado corto.")
            except Exception as exc:
                messagebox.showerror("Rango no válido", str(exc), parent=win)
                return
            self.stop_preview()
            base = os.path.splitext(track["name"])[0]
            new_track = {
                "name": f"{base}_corte_{int(start)}s-{int(end)}s",
                "path": track.get("path", ""),
                "data": clip,
                "sr": track["sr"],
                "undo": [],
                "muted": False,
                "solo": False,
            }
            self.tracks.insert(self.selected_index + 1, new_track)
            self.selected_index += 1
            self._selection_start = None
            self._selection_end = None
            self.refresh_tracks()
            self.set_info(
                f"Corte creado ({self._format_time(end - start)}). Seleccionalo y exportalo como MP3 desde ‘Exportar pista’.",
                "#f4f4f5"
            )
            win.destroy()

        bottom = ctk.CTkFrame(win, fg_color="transparent")
        bottom.pack(fill="x", padx=18, pady=14)
        self._button(bottom, "Cancelar", win.destroy).pack(side="right", padx=4)
        self._button(bottom, "Crear corte", make_clip).pack(side="right", padx=4)

    def normalize_track(self):
        if self.get_track() is None:
            return
        win = ctk.CTkToplevel(self)
        win.title("Normalizar audio")
        win.geometry("480x350")
        win.resizable(False, False)
        win.configure(fg_color="#18181b")
        win.transient(self)
        win.grab_set()
        ctk.CTkLabel(win, text="NORMALIZACIÓN DE PICO", font=fuente_ui(size=17, weight="bold")).pack(pady=(18, 12))
        values = {}
        for label, key, default in (("Pico objetivo (dBFS)", "target_db", "-1"),
                                    ("Ganancia máxima (dB)", "max_gain_db", "24")):
            row = ctk.CTkFrame(win, fg_color="transparent")
            row.pack(fill="x", padx=24, pady=6)
            ctk.CTkLabel(row, text=label, anchor="w").pack(side="left")
            entry = ctk.CTkEntry(row, width=90, justify="center")
            entry.insert(0, default)
            entry.pack(side="right")
            values[key] = entry

        def get_normalize_preset():
            return {key: float(entry.get().replace(",", ".")) for key, entry in values.items()}

        def set_normalize_preset(preset):
            target = float(preset["target_db"])
            max_gain = float(preset["max_gain_db"])
            if not math.isfinite(target) or not -24 <= target <= 0:
                raise ValueError("El pico objetivo debe estar entre -24 y 0 dBFS.")
            if not math.isfinite(max_gain) or not 0 <= max_gain <= 36:
                raise ValueError("La ganancia máxima debe estar entre 0 y 36 dB.")
            values["target_db"].delete(0, "end")
            values["target_db"].insert(0, f"{target:g}")
            values["max_gain_db"].delete(0, "end")
            values["max_gain_db"].insert(0, f"{max_gain:g}")

        def normalize_processor_factory():
            target_db = float(values["target_db"].get().replace(",", "."))
            max_gain_db = float(values["max_gain_db"].get().replace(",", "."))
            if not math.isfinite(target_db) or not -24 <= target_db <= 0:
                raise ValueError("El pico objetivo debe estar entre -24 y 0 dBFS.")
            if not math.isfinite(max_gain_db) or not 0 <= max_gain_db <= 36:
                raise ValueError("La ganancia máxima debe estar entre 0 y 36 dB.")

            def normalize(data, _sr):
                peak = float(np.max(np.abs(data))) if data.size else 0.0
                if peak < 1e-8:
                    return data
                target_peak = 10 ** (target_db / 20.0)
                gain = min(target_peak / peak, 10 ** (max_gain_db / 20.0))
                return data * gain
            return normalize

        self._preset_controls(
            win, "Normalización", get_normalize_preset, set_normalize_preset,
            normalize_processor_factory,
        )
        ctk.CTkLabel(
            win, text="Escuchá la muestra antes de aplicar el ajuste a la pista o selección.",
            text_color="#bcbcc0", wraplength=430
        ).pack(padx=24, pady=8)

        def apply_normalize():
            try:
                processor = normalize_processor_factory()
                preset = get_normalize_preset()
            except Exception as exc:
                messagebox.showerror("Valores no válidos", str(exc), parent=win)
                return
            win.destroy()
            self._apply(
                processor,
                f"Normalización a {preset['target_db']:g} dBFS (máx. +{preset['max_gain_db']:g} dB)",
            )

        bottom = ctk.CTkFrame(win, fg_color="transparent")
        bottom.pack(fill="x", padx=18, pady=14)
        self._button(bottom, "Cancelar", win.destroy).pack(side="right", padx=4)
        self._button(bottom, "Aceptar", apply_normalize).pack(side="right", padx=4)

    def open_fade_dialog(self):
        track = self.get_track()
        if track is None:
            return
        duration = len(track["data"]) / max(1, track["sr"])
        win = ctk.CTkToplevel(self)
        win.title("Fade in / out")
        win.geometry("430x425")
        win.resizable(False, False)
        win.configure(fg_color="#18181b")
        win.transient(self)
        win.grab_set()
        ctk.CTkLabel(win, text="FADE IN / OUT", font=fuente_ui(size=17, weight="bold")).pack(pady=(18, 12))
        entries = {}
        for label, key, default in (("Entrada (segundos)", "in", "2.0"), ("Salida (segundos)", "out", "3.0")):
            row = ctk.CTkFrame(win, fg_color="transparent")
            row.pack(fill="x", padx=24, pady=6)
            ctk.CTkLabel(row, text=label, anchor="w").pack(side="left")
            entry = ctk.CTkEntry(row, width=100)
            entry.insert(0, default)
            entry.pack(side="right")
            entries[key] = entry

        curve = ctk.CTkComboBox(
            win, values=["Lineal", "Suave (S)", "Exponencial", "Logarítmica"],
            state="readonly", width=190
        )
        curve.set("Lineal")
        curve.pack(anchor="w", padx=24, pady=(7, 3))
        ctk.CTkLabel(
            win, text="Curva de entrada y salida", text_color="#bcbcc0"
        ).pack(anchor="w", padx=24, pady=(0, 5))

        fade_repetitions = self._repetition_control(win)

        def get_fade_preset():
            values = {key: float(entry.get().replace(",", ".")) for key, entry in entries.items()}
            values["repetitions"] = int(fade_repetitions.get())
            values["curve"] = curve.get()
            return values

        def set_fade_preset(values):
            for key, entry in entries.items():
                value = float(values[key])
                if not np.isfinite(value) or value < 0:
                    raise ValueError("Los tiempos del fade deben ser positivos.")
                entry.delete(0, "end")
                entry.insert(0, f"{value:g}")
            self._set_repetition(fade_repetitions, values.get("repetitions", 1))
            curve.set(values.get("curve", "Lineal"))

        def fade_processor_factory():
            fade_in = float(entries["in"].get().replace(",", "."))
            fade_out = float(entries["out"].get().replace(",", "."))
            repetitions = int(fade_repetitions.get())
            curve_name = curve.get()
            if not np.isfinite(fade_in) or not np.isfinite(fade_out) or min(fade_in, fade_out) < 0:
                raise ValueError("Los tiempos deben ser positivos.")
            if fade_in + fade_out > duration * 2:
                raise ValueError("La duración del fade es demasiado larga para esta pista.")
            if not 1 <= repetitions <= 100:
                raise ValueError("Elegí entre 1 y 100 repeticiones.")
            if curve_name not in {"Lineal", "Suave (S)", "Exponencial", "Logarítmica"}:
                raise ValueError("Elegí una curva de fade válida.")

            def shape(progress):
                progress = np.clip(progress, 0.0, 1.0)
                if curve_name == "Suave (S)":
                    return progress * progress * (3.0 - 2.0 * progress)
                if curve_name == "Exponencial":
                    return progress * progress
                if curve_name == "Logarítmica":
                    return np.sqrt(progress)
                return progress

            def process(data, sr):
                result = data.copy()
                for _ in range(repetitions):
                    fade_in_samples = min(len(result), int(fade_in * sr))
                    fade_out_samples = min(len(result), int(fade_out * sr))
                    if fade_in_samples > 1:
                        ramp = shape(np.linspace(0, 1, fade_in_samples, dtype=np.float32))
                        result[:fade_in_samples] *= ramp.astype(np.float32, copy=False)[:, None]
                    if fade_out_samples > 1:
                        ramp = shape(np.linspace(1, 0, fade_out_samples, dtype=np.float32))
                        result[-fade_out_samples:] *= ramp.astype(np.float32, copy=False)[:, None]
                return result
            return process

        self._preset_controls(
            win, "Fade in / out", get_fade_preset, set_fade_preset,
            fade_processor_factory,
        )

        def apply_fade():
            try:
                process = fade_processor_factory()
            except Exception as exc:
                messagebox.showerror("Valores no válidos", str(exc), parent=win)
                return
            fade_in = float(entries["in"].get().replace(",", "."))
            fade_out = float(entries["out"].get().replace(",", "."))
            win.destroy()
            self._apply(process, f"Fade {fade_in:g}s / {fade_out:g}s · {curve.get()} × {int(fade_repetitions.get())}")

        bottom = ctk.CTkFrame(win, fg_color="transparent")
        bottom.pack(fill="x", padx=18, pady=16)
        self._button(bottom, "Cancelar", win.destroy).pack(side="right", padx=4)
        self._button(bottom, "Aplicar", apply_fade).pack(side="right", padx=4)

    def open_dj_mix_dialog(self):
        hay_solo = any(track.get("solo", False) for track in self.tracks)
        pistas_mezcla = [
            track for track in self.tracks
            if not track.get("muted", False) and (not hay_solo or track.get("solo", False))
        ]
        if len(pistas_mezcla) < 2:
            messagebox.showinfo(
                "Mezcla DJ", "Necesitás al menos dos pistas activas. Revisá los botones M (mute) y S (solo).",
                parent=self
            )
            return
        if self._mix_active:
            self.set_info("Ya se está procesando una mezcla.", "#f4f4f5")
            return
        win = ctk.CTkToplevel(self)
        win.title("Mezcla DJ")
        win.geometry("470x380")
        win.resizable(False, False)
        win.configure(fg_color="#18181b")
        win.transient(self)
        win.grab_set()
        ctk.CTkLabel(win, text="CREAR MEZCLA DJ", font=fuente_ui(size=17, weight="bold")).pack(pady=(18, 8))
        ctk.CTkLabel(
            win, text=f"Se unirán {len(pistas_mezcla)} pistas activas en el orden del proyecto.",
            text_color="#f4f4f5"
        ).pack(pady=(0, 12))
        row = ctk.CTkFrame(win, fg_color="transparent")
        row.pack(fill="x", padx=22, pady=8)
        ctk.CTkLabel(row, text="Crossfade entre pistas (segundos)").pack(side="left")
        crossfade_entry = ctk.CTkEntry(row, width=80)
        crossfade_entry.insert(0, "3")
        crossfade_entry.pack(side="right")
        normalize_mix = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            win, text="Nivelar volumen de cada pista y evitar clipping",
            variable=normalize_mix
        ).pack(anchor="w", padx=24, pady=10)

        def get_mix_preset():
            return {
                "crossfade_seconds": float(crossfade_entry.get().replace(",", ".")),
                "normalize": bool(normalize_mix.get()),
            }

        def set_mix_preset(values):
            crossfade = float(values["crossfade_seconds"])
            if not np.isfinite(crossfade) or not 0 <= crossfade <= 20:
                raise ValueError("El crossfade guardado debe estar entre 0 y 20 segundos.")
            crossfade_entry.delete(0, "end")
            crossfade_entry.insert(0, f"{crossfade:g}")
            normalize_mix.set(bool(values.get("normalize", True)))

        def mix_preview_source():
            crossfade = float(crossfade_entry.get().replace(",", "."))
            if not np.isfinite(crossfade) or not 0 <= crossfade <= 20:
                raise ValueError("El crossfade debe estar entre 0 y 20 segundos.")
            snapshots = [
                (track["data"][:max(2, int(track["sr"] * 12))].copy(), int(track["sr"]),
                 float(track.get("gain_db", 0.0)), float(track.get("pan", 0.0)))
                for track in pistas_mezcla
            ]
            return self._compose_dj_mix_preview(
                snapshots, crossfade, bool(normalize_mix.get())
            )

        self._preset_controls(
            win, "Mezcla DJ", get_mix_preset, set_mix_preset,
            lambda: (lambda data, _sr: data), mix_preview_source,
        )
        ctk.CTkLabel(
            win, text="La mezcla se agregará como una pista nueva; después podés previsualizarla y exportarla.",
            text_color="#f4f4f5", wraplength=410
        ).pack(padx=20, pady=8)

        def start_mix():
            try:
                crossfade = float(crossfade_entry.get().replace(",", "."))
                if not np.isfinite(crossfade) or not 0 <= crossfade <= 20:
                    raise ValueError("El crossfade debe estar entre 0 y 20 segundos.")
            except Exception as exc:
                messagebox.showerror("Valor no válido", str(exc), parent=win)
                return
            win.destroy()
            self.stop_preview()
            self._mix_active = True
            self.set_info("Preparando mezcla DJ y crossfades…", "#f4f4f5")
            tracks = [
                (
                    track["name"],
                    self._render_track_gain_pan(
                        track["data"], track.get("gain_db", 0.0), track.get("pan", 0.0), track=track
                    ),
                    track["sr"],
                )
                for track in pistas_mezcla
            ]
            threading.Thread(
                target=self._build_dj_mix,
                args=(tracks, crossfade, bool(normalize_mix.get())),
                daemon=True
            ).start()

        bottom = ctk.CTkFrame(win, fg_color="transparent")
        bottom.pack(fill="x", padx=18, pady=14)
        self._button(bottom, "Cancelar", win.destroy).pack(side="right", padx=4)
        self._button(bottom, "Crear mezcla", start_mix).pack(side="right", padx=4)

    def _compose_dj_mix_preview(self, tracks, crossfade_seconds, normalize_tracks):
        target_sr = 44100
        max_samples = int(target_sr * 12)
        target_channels = 2 if any(data.ndim > 1 and data.shape[1] > 1 for data, *_ in tracks) else 1
        converted = []
        for data, source_sr, gain_db, pan in tracks:
            data = np.asarray(data, dtype=np.float32)
            if data.ndim == 1:
                data = data[:, None]
            data = self._render_track_gain_pan(data, gain_db, pan)
            if target_channels == 2:
                if data.shape[1] == 1:
                    data = np.repeat(data, 2, axis=1)
                elif data.shape[1] > 2:
                    data = data[:, :2]
            elif data.shape[1] > 1:
                data = data.mean(axis=1, keepdims=True)
            if int(source_sr) != target_sr:
                divisor = math.gcd(int(source_sr), target_sr)
                data = signal.resample_poly(
                    data, target_sr // divisor, int(source_sr) // divisor, axis=0
                ).astype(np.float32, copy=False)
            if normalize_tracks:
                peak = float(np.max(np.abs(data))) if data.size else 0.0
                if peak > 1e-8:
                    data *= min((10 ** (-3.0 / 20.0)) / peak, 10 ** (12.0 / 20.0))
            converted.append(data[:max_samples])

        mix = converted[0].copy()
        requested_overlap = int(crossfade_seconds * target_sr)
        for segment in converted[1:]:
            overlap = min(requested_overlap, len(mix) // 2, len(segment) // 2)
            if overlap > 0:
                theta = np.linspace(0, np.pi / 2, overlap, dtype=np.float32)[:, None]
                blended = mix[-overlap:] * np.cos(theta) + segment[:overlap] * np.sin(theta)
                mix = np.concatenate((mix[:-overlap], blended, segment[overlap:]), axis=0)
            else:
                mix = np.concatenate((mix, segment), axis=0)
            if len(mix) >= max_samples:
                mix = mix[:max_samples]
                break
        peak = float(np.max(np.abs(mix))) if mix.size else 0.0
        if peak > 10 ** (-1.0 / 20.0):
            mix *= (10 ** (-1.0 / 20.0)) / peak
        return np.asarray(mix[:max_samples], dtype=np.float32), target_sr

    def _build_dj_mix(self, tracks, crossfade_seconds, normalize_tracks):
        try:
            target_sr = 44100
            target_channels = 2 if any(data.ndim > 1 and data.shape[1] > 1 for _, data, _ in tracks) else 1
            converted = []
            for _name, source, source_sr in tracks:
                data = np.asarray(source, dtype=np.float32)
                if data.ndim == 1:
                    data = data[:, None]
                if target_channels == 2:
                    if data.shape[1] == 1:
                        data = np.repeat(data, 2, axis=1)
                    elif data.shape[1] > 2:
                        data = data[:, :2]
                elif data.shape[1] > 1:
                    data = data.mean(axis=1, keepdims=True)
                if int(source_sr) != target_sr:
                    divisor = math.gcd(int(source_sr), target_sr)
                    data = signal.resample_poly(
                        data, target_sr // divisor, int(source_sr) // divisor, axis=0
                    ).astype(np.float32, copy=False)
                if normalize_tracks:
                    peak = float(np.max(np.abs(data))) if data.size else 0.0
                    if peak > 1e-8:
                        gain = min((10 ** (-3.0 / 20.0)) / peak, 10 ** (12.0 / 20.0))
                        data = data * gain
                converted.append(data)

            mix = converted[0].copy()
            requested_overlap = int(crossfade_seconds * target_sr)
            for index, segment in enumerate(converted[1:], start=2):
                overlap = min(requested_overlap, len(mix) // 2, len(segment) // 2)
                self.after(0, lambda i=index: self.set_info(
                    f"Mezclando pista {i}/{len(converted)}…", "#f4f4f5"
                ))
                if overlap > 0:
                    theta = np.linspace(0, np.pi / 2, overlap, dtype=np.float32)[:, None]
                    blended = mix[-overlap:] * np.cos(theta) + segment[:overlap] * np.sin(theta)
                    mix = np.concatenate((mix[:-overlap], blended, segment[overlap:]), axis=0)
                else:
                    mix = np.concatenate((mix, segment), axis=0)
            peak = float(np.max(np.abs(mix))) if mix.size else 0.0
            if peak > 10 ** (-1.0 / 20.0):
                mix *= (10 ** (-1.0 / 20.0)) / peak
            mix = np.asarray(mix, dtype=np.float32)
            name = f"Mezcla DJ ({len(tracks)} pistas)"
            self.after(0, lambda result=mix, title=name: self._finish_dj_mix(result, title))
        except Exception as exc:
            message = str(exc)
            try:
                self.after(0, lambda error=message: self._finish_dj_mix(None, "", error))
            except (RuntimeError, tk.TclError):
                pass

    def _finish_dj_mix(self, data, name, error=None):
        self._mix_active = False
        if error:
            messagebox.showerror("No se pudo crear la mezcla", error, parent=self)
            self.set_info("Falló la creación de la mezcla.", "#f4f4f5")
            return
        self.tracks.append({
            "name": name, "path": "", "data": data, "sr": 44100,
            "undo": [], "muted": False, "solo": False
        })
        self.selected_index = len(self.tracks) - 1
        self._selection_start = None
        self._selection_end = None
        self.refresh_tracks()
        self.set_info("Mezcla DJ lista. Previsualizala y exportala cuando quieras.", "#f4f4f5")

    def _apply(self, processor, label):
        indices = self._selected_track_indices()
        if not indices:
            self.get_track()
            return
        self._stop_effect_preview()
        self.stop_preview()
        try:
            had_selection = self._selection_start is not None and self._selection_end is not None
            multiple = len(indices) > 1
            to_commit = []
            for index in indices:
                track = self.tracks[index]
                original = track["data"]
                selection = None
                if self._selection_start is not None and self._selection_end is not None:
                    first = int(round(min(self._selection_start, self._selection_end) * track["sr"]))
                    last = int(round(max(self._selection_start, self._selection_end) * track["sr"]))
                    first = max(0, min(first, len(original)))
                    last = max(0, min(last, len(original)))
                    if last - first >= 2:
                        selection = (first, last)
                    elif multiple:
                        continue
                source = original[selection[0]:selection[1]].copy() if selection else original.copy()
                processed = processor(source, track["sr"])
                processed = np.asarray(processed, dtype=np.float32)
                if processed.ndim == 1:
                    processed = processed[:, None]
                if processed.ndim != 2 or processed.shape[0] == 0 or not np.isfinite(processed).all():
                    raise ValueError(f"el resultado de '{track.get('name', 'Pista')}' contiene datos no válidos")
                to_commit.append((track, original, selection, processed))
            if not to_commit:
                raise ValueError("La selección no coincide con audio de las pistas marcadas.")

            # Primero procesamos todas las pistas; si falla una, ninguna queda a medio aplicar.
            for track, original, selection, processed in to_commit:
                self.push_undo(track)
                self._discard_track_preview_cache(track)
                if selection:
                    first, last = selection
                    track["data"] = np.concatenate((original[:first], processed, original[last:]), axis=0)
                else:
                    track["data"] = processed
                for key in ("wave_envelope", "wave_channel_envelope", "wave_bin_samples"):
                    track.pop(key, None)
            self._selection_start = None
            self._selection_end = None
            self.refresh_tracks()
            scope = "a la selección" if had_selection else "a toda la pista"
            self.set_info(
                f"Efecto aplicado en {len(to_commit)} pista(s) · {scope}: {label}", "#f4f4f5"
            )
        except Exception as exc:
            messagebox.showerror("No se pudo aplicar el efecto", str(exc), parent=self)

    def _filter(self, data, sr, kind, cutoff, order):
        cutoff = float(cutoff)
        nyquist = sr / 2.0
        if not 1 < cutoff < nyquist:
            raise ValueError(f"La frecuencia debe estar entre 1 y {nyquist - 1:.0f} Hz.")
        sos = signal.butter(int(order), cutoff, btype=kind, fs=sr, output="sos")
        padlen = min(data.shape[0] - 1, 3 * (2 * len(sos) + 1))
        if padlen < 1:
            return signal.sosfilt(sos, data, axis=0)
        return signal.sosfiltfilt(sos, data, axis=0, padlen=padlen)

    @staticmethod
    def _shelf_coefficients(gain_db, cutoff, sample_rate, shelf):
        """Coeficientes de shelving RBJ; half-gain a 250 Hz y 4 kHz."""
        cutoff = min(float(cutoff), sample_rate * 0.45)
        if not 1.0 < cutoff < sample_rate / 2:
            raise ValueError("La frecuencia de muestreo es demasiado baja para este efecto.")
        amplitude = 10.0 ** (float(gain_db) / 40.0)
        omega = 2.0 * math.pi * cutoff / sample_rate
        cosine = math.cos(omega)
        sine = math.sin(omega)
        root_amplitude = math.sqrt(amplitude)
        alpha = sine / 2.0 * math.sqrt(2.0)

        if shelf == "low":
            b0 = amplitude * ((amplitude + 1) - (amplitude - 1) * cosine + 2 * root_amplitude * alpha)
            b1 = 2 * amplitude * ((amplitude - 1) - (amplitude + 1) * cosine)
            b2 = amplitude * ((amplitude + 1) - (amplitude - 1) * cosine - 2 * root_amplitude * alpha)
            a0 = (amplitude + 1) + (amplitude - 1) * cosine + 2 * root_amplitude * alpha
            a1 = -2 * ((amplitude - 1) + (amplitude + 1) * cosine)
            a2 = (amplitude + 1) + (amplitude - 1) * cosine - 2 * root_amplitude * alpha
        else:
            b0 = amplitude * ((amplitude + 1) + (amplitude - 1) * cosine + 2 * root_amplitude * alpha)
            b1 = -2 * amplitude * ((amplitude - 1) + (amplitude + 1) * cosine)
            b2 = amplitude * ((amplitude + 1) + (amplitude - 1) * cosine - 2 * root_amplitude * alpha)
            a0 = (amplitude + 1) - (amplitude - 1) * cosine + 2 * root_amplitude * alpha
            a1 = 2 * ((amplitude - 1) - (amplitude + 1) * cosine)
            a2 = (amplitude + 1) - (amplitude - 1) * cosine - 2 * root_amplitude * alpha

        b = np.asarray([b0, b1, b2], dtype=np.float64) / a0
        a = np.asarray([1.0, a1 / a0, a2 / a0], dtype=np.float64)
        return b, a

    @classmethod
    def _bass_treble_process(cls, data, sample_rate, bass_db, treble_db, volume_db):
        if signal is None or np is None:
            raise RuntimeError("Graves y agudos requiere scipy y numpy.")
        output = np.asarray(data, dtype=np.float32).copy()
        for gain, cutoff, shelf in (
            (bass_db, 250.0, "low"),
            (treble_db, 4000.0, "high"),
        ):
            if abs(float(gain)) < 1e-6:
                continue
            b, a = cls._shelf_coefficients(gain, cutoff, sample_rate, shelf)
            output = signal.lfilter(b, a, output, axis=0).astype(np.float32, copy=False)
        output *= np.float32(10.0 ** (float(volume_db) / 20.0))
        return output

    @classmethod
    def _graphic_eq_process(cls, data, sample_rate, gains_db):
        """Aplica 31 filtros peaking con centros a un tercio de octava."""
        if signal is None or np is None:
            raise RuntimeError("El ecualizador gráfico requiere scipy y numpy.")
        if len(gains_db) != len(cls.GRAPHIC_EQ_FREQUENCIES):
            raise ValueError("El ecualizador gráfico necesita exactamente 31 bandas.")
        output = np.asarray(data, dtype=np.float32).copy()
        rate = float(sample_rate)
        if not math.isfinite(rate) or rate <= 0:
            raise ValueError("La frecuencia de muestreo no es válida.")
        q_factor = 4.318
        for frequency, gain_db in zip(cls.GRAPHIC_EQ_FREQUENCIES, gains_db):
            gain_db = float(gain_db)
            if not math.isfinite(gain_db) or not -20 <= gain_db <= 20:
                raise ValueError("Cada banda debe estar entre -20 y +20 dB.")
            frequency = float(frequency)
            if abs(gain_db) < 0.01 or frequency <= 0 or frequency >= rate * 0.49:
                continue
            amplitude = 10.0 ** (gain_db / 40.0)
            omega = 2.0 * np.pi * frequency / rate
            cosine = np.cos(omega)
            alpha = np.sin(omega) / (2.0 * q_factor)
            b = np.asarray([
                1.0 + alpha * amplitude,
                -2.0 * cosine,
                1.0 - alpha * amplitude,
            ], dtype=np.float64)
            a = np.asarray([
                1.0 + alpha / amplitude,
                -2.0 * cosine,
                1.0 - alpha / amplitude,
            ], dtype=np.float64)
            output = signal.lfilter(b / a[0], a / a[0], output, axis=0).astype(
                np.float32, copy=False
            )
        return output

    def open_graphic_eq(self):
        if self.get_track() is None:
            return
        if signal is None or np is None:
            messagebox.showerror(
                "Falta una dependencia", "El ecualizador gráfico necesita numpy y scipy.", parent=self
            )
            return

        win = ctk.CTkToplevel(self)
        win.title("Ecualizador gráfico · Corneteros Studio")
        win.geometry("1160x690")
        win.minsize(920, 610)
        win.configure(fg_color="#101012")
        win.transient(self)
        win.grab_set()

        ctk.CTkLabel(
            win, text="Ecualizador gráfico", font=fuente_ui(size=20, weight="bold"),
            text_color="#f4f4f5"
        ).pack(anchor="w", padx=20, pady=(16, 3))
        ctk.CTkLabel(
            win,
            text="31 bandas de 20 Hz a 20 kHz · mové los controles para moldear el tono de la pista o selección.",
            text_color="#bcbcc0", anchor="w"
        ).pack(anchor="w", padx=20, pady=(0, 10))

        graph = tk.Canvas(
            win, height=150, bg="#101012", highlightthickness=1,
            highlightbackground="#303034"
        )
        graph.pack(fill="x", padx=20, pady=(0, 8))

        levels = [tk.DoubleVar(value=0.0) for _ in self.GRAPHIC_EQ_FREQUENCIES]
        value_labels = []
        sliders = []

        def db_to_y(db, top, bottom):
            return top + (20.0 - max(-20.0, min(20.0, db))) / 40.0 * (bottom - top)

        def draw_graph(_event=None):
            graph.delete("all")
            width = max(600, graph.winfo_width())
            height = max(120, graph.winfo_height())
            left, right, top, bottom = 44, width - 16, 12, height - 30
            for db in (-20, -12, -6, 0, 6, 12, 20):
                y = db_to_y(db, top, bottom)
                color = "#55555c" if db == 0 else "#29292e"
                graph.create_line(left, y, right, y, fill=color)
                graph.create_text(left - 7, y, text=f"{db:+d}", anchor="e", fill="#bcbcc0", font=("Segoe UI", 8))
            for hz in (20, 100, 1000, 10000, 20000):
                x = left + (math.log10(hz) - math.log10(20)) / (math.log10(20000) - math.log10(20)) * (right - left)
                graph.create_line(x, top, x, bottom, fill="#252529")
                label = f"{hz / 1000:g}k" if hz >= 1000 else str(hz)
                graph.create_text(x, height - 12, text=label, fill="#bcbcc0", font=("Segoe UI", 8))
            coordinates = []
            for index, (hz, variable) in enumerate(zip(self.GRAPHIC_EQ_FREQUENCIES, levels)):
                x = left + (math.log10(hz) - math.log10(20)) / (math.log10(20000) - math.log10(20)) * (right - left)
                coordinates.extend((x, db_to_y(float(variable.get()), top, bottom)))
            if len(coordinates) >= 4:
                graph.create_line(*coordinates, fill="#d0d0d3", width=2, smooth=True, splinesteps=8)
            for index in range(0, len(coordinates), 2):
                x, y = coordinates[index], coordinates[index + 1]
                graph.create_oval(x - 2.5, y - 2.5, x + 2.5, y + 2.5, fill="#f4f4f5", outline="#101012")

        slider_frame = ctk.CTkScrollableFrame(
            win, orientation="horizontal", height=245,
            fg_color="#18181b", corner_radius=9,
        )
        slider_frame.pack(fill="x", padx=20, pady=(0, 8))
        for index, frequency in enumerate(self.GRAPHIC_EQ_FREQUENCIES):
            band = ctk.CTkFrame(slider_frame, width=34, fg_color="transparent")
            band.grid(row=0, column=index, padx=1, sticky="ns")
            band.grid_propagate(False)
            readout = ctk.CTkLabel(
                band, text="0 dB", width=34, font=fuente_ui(size=8), text_color="#c9c9cd"
            )
            readout.pack(pady=(5, 0))
            value_labels.append(readout)

            def changed(raw_value, band_index=index):
                value = max(-20.0, min(20.0, round(float(raw_value) * 2.0) / 2.0))
                levels[band_index].set(value)
                value_labels[band_index].configure(text=f"{value:+g} dB")
                draw_graph()

            slider = ctk.CTkSlider(
                band, from_=-20, to=20, number_of_steps=80,
                orientation="vertical", width=18, height=166,
                variable=levels[index], progress_color="#68686f",
                button_color="#eeeeef", button_hover_color="#bcbcc0",
                command=changed,
            )
            slider.pack(pady=(3, 2))
            sliders.append(slider)
            label = f"{frequency / 1000:g}k" if frequency >= 1000 else f"{frequency:g}"
            ctk.CTkLabel(
                band, text=label, width=34, font=fuente_ui(size=8), text_color="#c9c9cd"
            ).pack(pady=(1, 5))

        def get_eq_preset():
            return {"gains_db": [float(variable.get()) for variable in levels]}

        def set_eq_preset(preset):
            gains = preset.get("gains_db")
            if not isinstance(gains, list) or len(gains) != len(levels):
                raise ValueError("Este preset no tiene las 31 bandas del ecualizador gráfico.")
            for index, gain in enumerate(gains):
                value = float(gain)
                if not math.isfinite(value) or not -20 <= value <= 20:
                    raise ValueError("Cada banda debe estar entre -20 y +20 dB.")
                value = round(value * 2.0) / 2.0
                levels[index].set(value)
                sliders[index].set(value)
                value_labels[index].configure(text=f"{value:+g} dB")
            draw_graph()

        def preset_profile(anchors):
            positions = np.log10(np.asarray([pair[0] for pair in anchors], dtype=float))
            values_db = np.asarray([pair[1] for pair in anchors], dtype=float)
            return [
                round(float(np.interp(math.log10(hz), positions, values_db)) * 2.0) / 2.0
                for hz in self.GRAPHIC_EQ_FREQUENCIES
            ]

        factory_presets = {
            "Plano": [0.0] * len(levels),
            "Realce de graves": preset_profile(
                [(20, 7), (80, 7), (160, 5), (315, 2), (630, 0), (20000, 0)]
            ),
            "Teléfono": preset_profile(
                [(20, -20), (250, -16), (315, -8), (500, 0), (1000, 4),
                 (2500, 4), (3150, 0), (4000, -8), (20000, -20)]
            ),
            "Walkie-talkie": preset_profile(
                [(20, -20), (250, -18), (400, -4), (630, 2), (1000, 5),
                 (2000, 5), (3150, 0), (4000, -8), (20000, -20)]
            ),
            "Voz clara": preset_profile(
                [(20, -2), (125, -1), (250, -2), (500, -1), (1000, 0),
                 (2000, 2), (4000, 4), (8000, 2), (12500, 1), (20000, 0)]
            ),
        }
        factory_row = ctk.CTkFrame(win, fg_color="transparent")
        factory_row.pack(fill="x", padx=20, pady=(0, 5))
        ctk.CTkLabel(factory_row, text="Preajuste de fábrica", text_color="#f4f4f5").pack(side="left", padx=(0, 8))
        factory_combo = ctk.CTkComboBox(
            factory_row, values=list(factory_presets), width=190, height=30, state="readonly"
        )
        factory_combo.set("Plano")
        factory_combo.pack(side="left", padx=3)
        self._button(
            factory_row, "Cargar", lambda: set_eq_preset({"gains_db": factory_presets[factory_combo.get()]}),
            width=82,
        ).pack(side="left", padx=4)
        self._preset_controls(
            win, "Ecualizador gráfico", get_eq_preset, set_eq_preset,
            lambda: (lambda data, sr: self._graphic_eq_process(
                data, sr, [float(variable.get()) for variable in levels]
            )),
        )

        actions = ctk.CTkFrame(win, fg_color="transparent")
        actions.pack(fill="x", padx=20, pady=(3, 14))

        def flatten():
            set_eq_preset({"gains_db": [0.0] * len(levels)})

        def invert():
            set_eq_preset({"gains_db": [-float(variable.get()) for variable in levels]})

        self._button(actions, "Aplanar", flatten, width=92).pack(side="left", padx=3)
        self._button(actions, "Invertir", invert, width=92).pack(side="left", padx=3)
        self._button(actions, "Cancelar", win.destroy, width=105).pack(side="right", padx=3)

        def apply_eq():
            gains = [float(variable.get()) for variable in levels]
            win.destroy()
            self._apply(
                lambda data, sr: self._graphic_eq_process(data, sr, gains),
                "Ecualizador gráfico de 31 bandas",
            )

        self._button(actions, "Aceptar y aplicar", apply_eq, width=150).pack(side="right", padx=3)
        graph.bind("<Configure>", draw_graph, add="+")
        win.after(100, draw_graph)

    def open_bass_treble(self):
        if self.get_track() is None:
            return
        if signal is None or np is None:
            messagebox.showerror(
                "Falta una dependencia",
                "Graves y agudos necesita numpy y scipy para procesar el audio.",
                parent=self,
            )
            return

        win = ctk.CTkToplevel(self)
        win.title("Graves y agudos - Corneteros Studio")
        win.geometry("690x560")
        win.minsize(640, 440)
        win.configure(fg_color="#101012")
        win.transient(self)
        win.grab_set()

        ctk.CTkLabel(
            win, text="Graves y agudos", font=fuente_ui(size=20, weight="bold"),
            text_color="#f4f4f5"
        ).pack(anchor="w", padx=24, pady=(22, 4))
        ctk.CTkLabel(
            win,
            text="Ajusta el cuerpo y el brillo de la pista seleccionada o del tramo marcado.",
            text_color="#c9c9cd", anchor="w", wraplength=630,
        ).pack(anchor="w", padx=24, pady=(0, 16))

        controls = ctk.CTkFrame(win, fg_color="#18181b", corner_radius=10)
        controls.pack(fill="x", padx=20, pady=(0, 12))
        controls.grid_columnconfigure(1, weight=1)
        values = {"bass": tk.DoubleVar(value=0.0), "treble": tk.DoubleVar(value=0.0), "volume": tk.DoubleVar(value=0.0)}
        entries = {}
        sliders = {}
        labels = {"bass": "Graves (dB)", "treble": "Agudos (dB)", "volume": "Volumen (dB)"}

        def display_value(key):
            value = float(values[key].get())
            entries[key].delete(0, "end")
            entries[key].insert(0, f"{value:+.1f}")

        def set_linked_volume():
            if not link_var.get():
                return
            compensation = -(
                float(values["bass"].get()) + float(values["treble"].get())
            ) / 2.0
            compensation = max(-30.0, min(30.0, compensation))
            values["volume"].set(compensation)
            sliders["volume"].set(compensation)
            display_value("volume")

        def slider_changed(key, raw_value):
            value = max(-30.0, min(30.0, round(float(raw_value) * 2.0) / 2.0))
            values[key].set(value)
            sliders[key].set(value)
            display_value(key)
            if key in ("bass", "treble"):
                set_linked_volume()

        def commit_entry(key, _event=None):
            try:
                value = float(entries[key].get().strip().replace(",", "."))
                if not math.isfinite(value):
                    raise ValueError
                value = max(-30.0, min(30.0, round(value * 2.0) / 2.0))
            except (TypeError, ValueError):
                display_value(key)
                return "break"
            values[key].set(value)
            sliders[key].set(value)
            display_value(key)
            if key in ("bass", "treble"):
                set_linked_volume()
            return "break"

        for row_index, key in enumerate(("bass", "treble", "volume")):
            ctk.CTkLabel(
                controls, text=labels[key], width=120, anchor="w",
                font=fuente_ui(size=12, weight="bold"), text_color="#f4f4f5"
            ).grid(row=row_index, column=0, sticky="w", padx=(16, 8), pady=12)
            slider = ctk.CTkSlider(
                controls, from_=-30, to=30, number_of_steps=120,
                variable=values[key], progress_color="#85858b",
                button_color="#f4f4f5", button_hover_color="#bcbcc0",
                command=lambda value, k=key: slider_changed(k, value),
            )
            slider.grid(row=row_index, column=1, sticky="ew", padx=10, pady=12)
            sliders[key] = slider
            entry = ctk.CTkEntry(controls, width=78, height=32, justify="center")
            entry.grid(row=row_index, column=2, padx=(4, 16), pady=12)
            entries[key] = entry
            display_value(key)
            entry.bind("<Return>", lambda event, k=key: commit_entry(k, event))
            entry.bind("<FocusOut>", lambda event, k=key: commit_entry(k, event))

        link_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            win, text="Vincular volumen con los controles de tono",
            variable=link_var, command=set_linked_volume,
            font=fuente_ui(size=12, weight="bold"), checkbox_width=20, checkbox_height=20,
        ).pack(anchor="w", padx=26, pady=(2, 4))

        def get_bass_treble_preset():
            return {
                "bass_db": float(values["bass"].get()),
                "treble_db": float(values["treble"].get()),
                "volume_db": float(values["volume"].get()),
                "link_volume": bool(link_var.get()),
            }

        def set_bass_treble_preset(preset):
            for key, preset_key in (("bass", "bass_db"), ("treble", "treble_db"), ("volume", "volume_db")):
                value = float(preset[preset_key])
                if not math.isfinite(value) or not -30 <= value <= 30:
                    raise ValueError("Cada valor debe estar entre -30 y +30 dB.")
                values[key].set(value)
                sliders[key].set(value)
                display_value(key)
            link_var.set(bool(preset.get("link_volume", False)))

        def bass_treble_preview_factory():
            bass = float(values["bass"].get())
            treble = float(values["treble"].get())
            volume = float(values["volume"].get())
            return lambda data, sr: self._bass_treble_process(data, sr, bass, treble, volume)

        self._preset_controls(
            win, "Graves y agudos", get_bass_treble_preset,
            set_bass_treble_preset, bass_treble_preview_factory
        )
        ctk.CTkLabel(
            win,
            text="El vínculo compensa automáticamente los cambios de tono. Si lo dejas apagado, revisa el volumen para evitar saturación.",
            text_color="#bcbcc0", anchor="w", wraplength=630,
        ).pack(anchor="w", padx=26, pady=(0, 12))

        buttons = ctk.CTkFrame(win, fg_color="transparent")
        buttons.pack(fill="x", padx=20, pady=(0, 18))

        def apply():
            bass = float(values["bass"].get())
            treble = float(values["treble"].get())
            volume = float(values["volume"].get())
            win.destroy()
            self._apply(
                lambda data, sr: self._bass_treble_process(data, sr, bass, treble, volume),
                f"Graves y agudos · graves {bass:+.1f} dB · agudos {treble:+.1f} dB · volumen {volume:+.1f} dB",
            )

        self._button(buttons, "Cancelar", win.destroy, width=112).pack(side="right", padx=5)
        self._button(
            buttons, "Aplicar efecto", apply, width=142,
            fg_color="#303034", hover_color="#45454b", border_color="#55555c", border_width=1,
        ).pack(side="right", padx=5)

    def _filter_dialog(self, title, kind, default_freq):
        win = ctk.CTkToplevel(self)
        win.title(title)
        win.geometry("470x410")
        win.resizable(False, False)
        win.configure(fg_color="#18181b")
        win.transient(self)
        win.grab_set()

        ctk.CTkLabel(win, text=title, font=fuente_ui(size=16, weight="bold")).pack(pady=(18, 12))
        row = ctk.CTkFrame(win, fg_color="transparent")
        row.pack(fill="x", padx=24, pady=5)
        ctk.CTkLabel(row, text="Frecuencia (Hz):").pack(side="left")
        freq = ctk.CTkEntry(row, width=120)
        freq.insert(0, str(default_freq))
        freq.pack(side="right")
        row2 = ctk.CTkFrame(win, fg_color="transparent")
        row2.pack(fill="x", padx=24, pady=5)
        ctk.CTkLabel(row2, text="Pendiente / orden:").pack(side="left")
        order = ctk.CTkComboBox(row2, values=["1 (suave)", "2", "3", "4 (24 dB/oct)"], width=150)
        order.set("4 (24 dB/oct)")
        order.pack(side="right")

        def get_filter_preset():
            return {
                "frequency_hz": float(freq.get().replace(",", ".")),
                "order": order.get()
            }

        def set_filter_preset(values):
            hz = float(values["frequency_hz"])
            chosen_order = str(values["order"])
            allowed = ["1 (suave)", "2", "3", "4 (24 dB/oct)"]
            if not 1 < hz < 22000 or chosen_order not in allowed:
                raise ValueError("Los valores del preset de filtro no son válidos.")
            freq.delete(0, "end")
            freq.insert(0, f"{hz:g}")
            order.set(chosen_order)

        def filter_preview_factory():
            hz = float(freq.get().replace(",", "."))
            n = int(order.get().split()[0])
            if not 1 < hz < 22000 or n not in (1, 2, 3, 4):
                raise ValueError("Ingresá una frecuencia y un orden válidos.")
            return lambda data, sr: self._filter(data, sr, kind, hz, n)

        self._preset_controls(
            win, f"Filtro {kind}", get_filter_preset, set_filter_preset,
            filter_preview_factory,
        )

        def apply():
            try:
                hz = float(freq.get().replace(",", "."))
                n = int(order.get().split()[0])
            except Exception:
                messagebox.showerror("Valor incorrecto", "Ingresá una frecuencia y un orden válidos.", parent=win)
                return
            win.destroy()
            self._apply(
                lambda data, sr: self._filter(data, sr, kind, hz, n),
                f"{title} ({hz:g} Hz, orden {n})"
            )

        bottom = ctk.CTkFrame(win, fg_color="transparent")
        bottom.pack(side="bottom", fill="x", padx=18, pady=16)
        self._button(bottom, "Cancelar", win.destroy).pack(side="right", padx=4)
        self._button(bottom, "Aceptar", apply).pack(side="right", padx=4)

    def open_highpass(self):
        self._filter_dialog("Filtro de paso alto", "highpass", 300)

    def open_lowpass(self):
        self._filter_dialog("Filtro de paso bajo", "lowpass", 6000)

    def open_filter_curve(self):
        track = self.get_track()
        if track is None:
            return
        win = ctk.CTkToplevel(self)
        win.title("Ecualizador de curva de filtro")
        win.geometry("900x570")
        win.minsize(760, 500)
        win.configure(fg_color="#18181b")
        win.transient(self)
        win.grab_set()

        # Curva en escala logarítmica: puntos expresados en Hz y dB.
        default_points = [(20, 0), (200, 0), (1000, 0), (5000, 0), (20000, 0)]
        try:
            with open(self.PRESET_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict) and isinstance(loaded.get("points"), list):
                    default_points = [(float(p[0]), float(p[1])) for p in loaded["points"]]
        except Exception:
            pass
        points = sorted(default_points)

        header = ctk.CTkFrame(win, fg_color="transparent")
        header.pack(fill="x", padx=14, pady=(12, 0))
        ctk.CTkLabel(
            header, text="Curva de filtro", font=fuente_ui(size=16, weight="bold"),
            text_color="#ffffff"
        ).pack(side="left")
        ctk.CTkLabel(
            header, text="Frecuencia abajo · realce/atenuación en dB · clic agrega punto",
            text_color="#f4f4f5"
        ).pack(side="right")

        graph = tk.Canvas(
            win, bg="#101012", highlightthickness=1,
            highlightbackground="#303034", height=340
        )
        graph.pack(fill="both", expand=True, padx=14, pady=12)

        controls = ctk.CTkFrame(win, fg_color="transparent")
        controls.pack(fill="x", padx=14, pady=(0, 12))
        freq_var = tk.StringVar(value="—")
        gain_var = tk.StringVar(value="—")
        ctk.CTkLabel(controls, text="Frecuencia (Hz):").pack(side="left")
        ctk.CTkLabel(controls, textvariable=freq_var, text_color="#f4f4f5").pack(side="left", padx=(3, 14))
        ctk.CTkLabel(controls, text="Ajuste (dB):").pack(side="left")
        ctk.CTkLabel(controls, textvariable=gain_var, text_color="#f4f4f5").pack(side="left", padx=3)

        def get_curve_preset():
            return {"points": [[float(f), float(db)] for f, db in sorted(points)]}

        def set_curve_preset(values):
            candidate = values.get("points")
            if not isinstance(candidate, list) or len(candidate) < 2:
                raise ValueError("El preset necesita al menos dos puntos de curva.")
            parsed = sorted((float(point[0]), float(point[1])) for point in candidate)
            if any(not (20 <= f <= 20000 and -30 <= db <= 30) for f, db in parsed):
                raise ValueError("Los puntos deben estar entre 20–20000 Hz y -30–30 dB.")
            points[:] = parsed
            draw()

        self._preset_controls(
            win, "Curva de filtro", get_curve_preset, set_curve_preset,
            lambda: (lambda data, sr: self._filter_curve_data(data, sr, sorted(points))),
        )

        bottom = ctk.CTkFrame(win, fg_color="transparent")
        bottom.pack(fill="x", padx=14, pady=(0, 14))

        state = {"drag": None, "selected": None}

        def xy(point):
            w = max(graph.winfo_width(), 500)
            h = max(graph.winfo_height(), 250)
            f, db = point
            x = 48 + (math.log10(max(20, min(20000, f))) - math.log10(20)) / (math.log10(20000) - math.log10(20)) * (w - 68)
            y = 12 + (30 - max(-30, min(30, db))) / 60 * (h - 38)
            return x, y

        def from_xy(x, y):
            w = max(graph.winfo_width(), 500)
            h = max(graph.winfo_height(), 250)
            x = max(48, min(w - 20, x))
            y = max(12, min(h - 26, y))
            f = 10 ** (math.log10(20) + ((x - 48) / (w - 68)) * (math.log10(20000) - math.log10(20)))
            db = 30 - ((y - 12) / (h - 38)) * 60
            return (float(f), float(db))

        def draw(_event=None):
            graph.delete("all")
            w = max(graph.winfo_width(), 500)
            h = max(graph.winfo_height(), 250)
            left, right, top, bottom_y = 48, w - 20, 12, h - 26
            for db in range(-30, 31, 6):
                y = top + (30 - db) / 60 * (bottom_y - top)
                graph.create_line(left, y, right, y, fill="#303034")
                graph.create_text(left - 7, y, text=str(db), anchor="e", fill="#bcbcc0", font=("Segoe UI", 9))
            for f in [20, 40, 60, 100, 200, 300, 500, 1000, 2000, 4000, 6100, 10000, 20000]:
                x, _ = xy((f, 0))
                graph.create_line(x, top, x, bottom_y, fill="#252529")
                graph.create_text(x, bottom_y + 12, text=str(f), fill="#bcbcc0", font=("Segoe UI", 9))
            zero_y = xy((20, 0))[1]
            graph.create_line(left, zero_y, right, zero_y, fill="#45454b", width=1)
            coords = [xy(p) for p in sorted(points)]
            for a, b in zip(coords, coords[1:]):
                graph.create_line(a[0], a[1], b[0], b[1], fill="#bcbcc0", width=2)
            for i, (x, y) in enumerate(coords):
                color = "#55555c" if state["selected"] == i else "#f4f4f5"
                graph.create_oval(x-4, y-4, x+4, y+4, fill=color, outline="#f4f4f5", width=2)
            graph.create_text(w-25, 10, text="+dB", anchor="e", fill="#bcbcc0", font=("Segoe UI", 9))

        def nearest(x, y):
            coords = [xy(p) for p in points]
            if not coords:
                return None
            i = min(range(len(coords)), key=lambda j: (coords[j][0]-x)**2 + (coords[j][1]-y)**2)
            return i if (coords[i][0]-x)**2 + (coords[i][1]-y)**2 <= 14**2 else None

        def down(event):
            idx = nearest(event.x, event.y)
            if idx is None:
                p = from_xy(event.x, event.y)
                points.append(p)
                points.sort(key=lambda p: p[0])
                idx = min(range(len(points)), key=lambda i: abs(points[i][0]-p[0]))
            state["drag"] = idx
            state["selected"] = idx
            freq_var.set(f"{points[idx][0]:.1f}")
            gain_var.set(f"{points[idx][1]:.1f}")
            draw()

        def motion(event):
            idx = state["drag"]
            if idx is None or idx >= len(points):
                return
            f, db = from_xy(event.x, event.y)
            # Evita que los puntos se crucen entre sí.
            low = points[idx-1][0] + 1 if idx > 0 else 20
            high = points[idx+1][0] - 1 if idx < len(points)-1 else 20000
            points[idx] = (max(low, min(high, f)), db)
            freq_var.set(f"{points[idx][0]:.1f}")
            gain_var.set(f"{points[idx][1]:.1f}")
            draw()

        def up(_event):
            state["drag"] = None

        def remove_point(_event):
            idx = nearest(_event.x, _event.y)
            if idx is not None and len(points) > 2:
                points.pop(idx)
                state["selected"] = None
                draw()

        graph.bind("<Configure>", draw)
        graph.bind("<Button-1>", down)
        graph.bind("<B1-Motion>", motion)
        graph.bind("<ButtonRelease-1>", up)
        graph.bind("<Button-3>", remove_point)

        def flatten():
            for i, (f, _db) in enumerate(points):
                points[i] = (f, 0.0)
            draw()

        def invert_curve():
            for i, (f, db) in enumerate(points):
                points[i] = (f, -db)
            draw()

        def save_preset():
            try:
                with open(self.PRESET_FILE, "w", encoding="utf-8") as f:
                    json.dump({"format": "buscador_mp3_filter_curve_v1", "points": points},
                              f, indent=2)
                messagebox.showinfo("Preset guardado", "Se guardó el preset de curva de esta aplicación.", parent=win)
            except Exception as exc:
                messagebox.showerror("Error", str(exc), parent=win)

        def export_preset():
            path = filedialog.asksaveasfilename(
                parent=win, title="Exportar curva", defaultextension=".json",
                filetypes=[("Preset de curva JSON", "*.json")]
            )
            if not path:
                return
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump({"format": "buscador_mp3_filter_curve_v1", "points": points},
                              f, indent=2)
                messagebox.showinfo("Exportación", "Preset exportado.", parent=win)
            except Exception as exc:
                messagebox.showerror("Error", str(exc), parent=win)

        def import_preset():
            path = filedialog.askopenfilename(
                parent=win, title="Importar curva",
                filetypes=[("Curva de Audacity o preset JSON", "*.txt *.json"),
                           ("Texto de curva de Audacity", "*.txt"),
                           ("Preset JSON", "*.json"), ("Todos los archivos", "*.*")]
            )
            if not path:
                return
            try:
                with open(path, "r", encoding="utf-8-sig") as f:
                    contents = f.read().strip()
                if contents.startswith("FilterCurve:"):
                    # Formato nativo de Audacity: fN = frecuencia, vN = ganancia.
                    # Se admite coma decimal para los archivos guardados con esa región.
                    attributes = dict(re.findall(r'([fv]\d+)="([^"]+)"', contents))
                    parsed = []
                    indices = sorted(int(key[1:]) for key in attributes if key.startswith("f"))
                    for idx in indices:
                        if f"v{idx}" not in attributes:
                            continue
                        hz = float(attributes[f"f{idx}"].replace(",", "."))
                        db = float(attributes[f"v{idx}"].replace(",", "."))
                        parsed.append((hz, db))
                else:
                    raw = json.loads(contents)
                    candidate = raw.get("points") if isinstance(raw, dict) else raw
                    if not isinstance(candidate, list) or len(candidate) < 2:
                        raise ValueError("El preset debe contener al menos dos puntos.")
                    parsed = []
                    for item in candidate:
                        if not isinstance(item, (list, tuple)) or len(item) != 2:
                            raise ValueError("Cada punto debe tener frecuencia y ajuste.")
                        parsed.append((float(item[0]), float(item[1])))
                if len(parsed) < 2:
                    raise ValueError("El archivo no contiene al menos dos puntos de curva válidos.")
                if any(not (20 <= hz <= 20000 and -30 <= db <= 30) for hz, db in parsed):
                    raise ValueError("Los puntos deben estar entre 20–20000 Hz y -30–30 dB.")
                points[:] = sorted(parsed)
                draw()
                messagebox.showinfo(
                    "Curva importada",
                    f"Se cargaron {len(parsed)} puntos. Revisá la curva y tocá Aceptar para aplicarla al audio.",
                    parent=win
                )
            except Exception as exc:
                messagebox.showerror("Preset inválido", str(exc), parent=win)

        def apply_curve():
            curve_points = sorted((float(f), float(db)) for f, db in points)
            win.destroy()

            def process(data, sr):
                return self._filter_curve_data(data, sr, curve_points)
            self._apply(process, "Curva de filtro")

        self._button(bottom, "Aplanar", flatten, width=100).pack(side="left", padx=3)
        self._button(bottom, "Invertir", invert_curve, width=100).pack(side="left", padx=3)
        self._button(bottom, "Importar…", import_preset, width=100).pack(side="left", padx=3)
        self._button(bottom, "Exportar…", export_preset, width=100).pack(side="left", padx=3)
        self._button(bottom, "Guardar predeterminado", save_preset, width=155).pack(side="left", padx=3)
        self._button(bottom, "Cancelar", win.destroy, width=100).pack(side="right", padx=3)
        self._button(bottom, "Aceptar", apply_curve, width=100).pack(side="right", padx=3)
        win.after(100, draw)

    @staticmethod
    def _filter_curve_data(data, sr, curve_points):
        """Aplica EQ FIR por bloques para mantener acotado el uso de memoria."""
        if len(data) < 2:
            return data
        points = tuple((float(freq), float(db)) for freq, db in curve_points)
        taps = EditorAudio._filter_curve_taps(int(sr), points)
        channels = data[:, None] if data.ndim == 1 else data
        frame_count, channel_count = channels.shape
        filtered = np.empty((frame_count, channel_count), dtype=np.float32)
        half = (len(taps) - 1) // 2
        block_size = 524288
        for channel in range(channel_count):
            source = channels[:, channel]
            target = filtered[:, channel]
            for start in range(0, frame_count, block_size):
                end = min(frame_count, start + block_size)
                # Contexto a ambos lados del bloque conserva exactamente el
                # alineamiento del modo "same", sin convolucionar todo el tema.
                source_start = max(0, start + half - (len(taps) - 1))
                source_end = min(frame_count, end + half)
                segment = source[source_start:source_end]
                convolved = signal.oaconvolve(segment, taps, mode="full")
                result_start = start + half - source_start
                result_end = end + half - source_start
                target[start:end] = convolved[result_start:result_end]
        return filtered[:, 0] if data.ndim == 1 else filtered

    @staticmethod
    @lru_cache(maxsize=8)
    def _filter_curve_taps(sr, points):
        """Cachea el diseño FIR para reutilizar la misma curva entre descargas."""
        pf = np.array([p[0] for p in points], dtype=float)
        pg = np.array([p[1] for p in points], dtype=float)
        nyquist = sr / 2.0
        pf = np.minimum(pf, nyquist * 0.999)
        from scipy.interpolate import make_interp_spline
        spline = make_interp_spline(np.log10(pf), pg, k=min(3, len(pf) - 1))
        n_response = 16385
        frequencies = np.linspace(0.0, nyquist, n_response)
        query = np.log10(np.maximum(frequencies, pf[0]))
        response_db = np.asarray(spline(query), dtype=float)
        response_db[frequencies < pf[0]] = pg[0]
        response_db[frequencies > pf[-1]] = pg[-1]
        response = 10.0 ** (response_db / 20.0)
        taps = signal.firwin2(
            8191, frequencies, response, fs=sr, window="hann", nfreqs=n_response
        ).astype(np.float32, copy=False)
        return taps

    @staticmethod
    def _reverb_process(data, sr, values, wet_only_enabled):
        room = values["Tamaño de sala (%)"] / 100
        delay_ms = values["Tiempo de retardo (ms)"]
        amount = values["Reverberación (%)"] / 100
        damping = values["Amortiguación (%)"] / 100
        wet_gain = 10 ** (values["Ganancia Wet (dB)"] / 20)
        dry_gain = 10 ** (values["Ganancia Dry (dB)"] / 20)
        stereo_width = values["Ancho estéreo (%)"] / 100
        data = np.asarray(data, dtype=np.float32)
        if data.ndim == 1:
            data = data[:, None]
        n, channels = data.shape
        wet = np.zeros_like(data)
        base_delay = max(1, int(sr * max(delay_ms, 12 + 45 * room) / 1000))
        delay_samples = [base_delay, max(1, int(base_delay * 1.37)),
                         max(1, int(base_delay * 1.91)), max(1, int(base_delay * 2.63))]
        decay = min(0.88, 0.25 + amount * 0.62 + room * 0.08)
        for channel_index in range(channels):
            channel = data[:, channel_index].astype(np.float64)
            acc = np.zeros(n + max(delay_samples) * 4, dtype=np.float64)
            acc[:n] += channel
            for repeat in range(1, 5):
                for tap_index, delay in enumerate(delay_samples):
                    offset = delay * repeat
                    if offset < len(acc):
                        end = min(n, len(acc) - offset)
                        if end > 0:
                            attenuation = (decay ** repeat) * (0.7 ** tap_index)
                            acc[offset:offset + end] += channel[:end] * attenuation
            wet[:, channel_index] = acc[:n]

        if damping > 0:
            cutoff = max(1000, min(sr * 0.45, 18000 - damping * 12000))
            try:
                sos = signal.butter(2, cutoff, btype="lowpass", fs=sr, output="sos")
                wet = signal.sosfilt(sos, wet, axis=0)
            except Exception:
                pass

        low_tone = values["Tono bajo (%)"] / 100
        high_tone = values["Tono alto (%)"] / 100
        try:
            if low_tone > 0:
                low_cut = max(20.0, min(sr * 0.45, 250.0 + 900.0 * low_tone))
                sos = signal.butter(1, low_cut, btype="lowpass", fs=sr, output="sos")
                wet += signal.sosfilt(sos, wet, axis=0) * low_tone * 0.35
            if high_tone < 1:
                high_cut = max(500.0, min(sr * 0.45, 2000.0 + 16000.0 * high_tone))
                sos = signal.butter(2, high_cut, btype="lowpass", fs=sr, output="sos")
                wet = signal.sosfilt(sos, wet, axis=0)
        except Exception:
            pass

        if channels >= 2 and stereo_width < 1:
            mid = (wet[:, 0] + wet[:, 1]) / 2
            side = (wet[:, 0] - wet[:, 1]) / 2 * stereo_width
            wet[:, 0], wet[:, 1] = mid + side, mid - side
        if wet_only_enabled:
            return wet * wet_gain
        return data * dry_gain + wet * (amount * wet_gain)

    def open_reverb(self):
        track = self.get_track()
        if track is None:
            return
        win = ctk.CTkToplevel(self)
        win.title("Reverberación")
        win.geometry("490x660")
        win.resizable(False, False)
        win.configure(fg_color="#18181b")
        win.transient(self)
        win.grab_set()

        ctk.CTkLabel(win, text="Reverberación", font=fuente_ui(size=17, weight="bold")).pack(pady=(14, 8))
        specs = [
            ("Tamaño de sala (%)", 20, 0, 100),
            ("Tiempo de retardo (ms)", 0, 0, 500),
            ("Reverberación (%)", 30, 0, 100),
            ("Amortiguación (%)", 0, 0, 100),
            ("Tono bajo (%)", 0, 0, 100),
            ("Tono alto (%)", 100, 0, 100),
            ("Ganancia Wet (dB)", 0, -24, 12),
            ("Ganancia Dry (dB)", 0, -24, 12),
            ("Ancho estéreo (%)", 100, 0, 100),
        ]
        vars_ = {}
        for label, default, low, high in specs:
            row = ctk.CTkFrame(win, fg_color="transparent")
            row.pack(fill="x", padx=18, pady=3)
            ctk.CTkLabel(row, text=label, width=190, anchor="w").pack(side="left")
            var = tk.DoubleVar(value=default)
            slider = ctk.CTkSlider(row, from_=low, to=high, variable=var, width=175)
            slider.pack(side="left", padx=6)
            entry = ctk.CTkEntry(row, width=60)
            entry.insert(0, str(default))
            entry.pack(side="right")
            def sync_entry(v=var, e=entry):
                e.delete(0, "end")
                e.insert(0, f"{v.get():.1f}")
            slider.configure(command=lambda _v, fn=sync_entry: fn())
            def sync_slider(_event=None, v=var, e=entry, lo=low, hi=high):
                try:
                    v.set(max(lo, min(hi, float(e.get().replace(",", ".")))))
                except ValueError:
                    pass
            entry.bind("<Return>", sync_slider)
            vars_[label] = (var, entry, low, high)

        wet_only = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(win, text="Sólo Wet", variable=wet_only).pack(anchor="w", padx=22, pady=8)

        def get_reverb_preset():
            return {
                "values": {label: float(entry.get().replace(",", "."))
                           for label, (_var, entry, _low, _high) in vars_.items()},
                "wet_only": bool(wet_only.get()),
            }

        def set_reverb_preset(preset):
            values = preset.get("values", {})
            for label, (var, entry, low, high) in vars_.items():
                if label not in values:
                    continue
                value = float(values[label])
                if not low <= value <= high:
                    raise ValueError(f"{label}: valor fuera del rango permitido.")
                var.set(value)
                entry.delete(0, "end")
                entry.insert(0, f"{value:g}")
            wet_only.set(bool(preset.get("wet_only", False)))

        def read_reverb_values():
            values = {}
            for label, (_var, entry, low, high) in vars_.items():
                value = float(entry.get().replace(",", "."))
                if not math.isfinite(value) or not low <= value <= high:
                    raise ValueError(f"{label}: el valor debe estar entre {low} y {high}.")
                values[label] = value
            return values, bool(wet_only.get())

        def reverb_preview_factory():
            values, wet_enabled = read_reverb_values()
            return lambda data, sr: self._reverb_process(data, sr, values, wet_enabled)

        self._preset_controls(
            win, "Reverberación", get_reverb_preset, set_reverb_preset,
            reverb_preview_factory,
        )

        def apply():
            try:
                values, wet_only_enabled = read_reverb_values()
            except Exception as exc:
                messagebox.showerror("Valor incorrecto", str(exc), parent=win)
                return
            win.destroy()

            process = lambda data, sr: self._reverb_process(data, sr, values, wet_only_enabled)
            self._apply(process, "Reverberación")

        bottom = ctk.CTkFrame(win, fg_color="transparent")
        bottom.pack(fill="x", padx=16, pady=12)
        self._button(bottom, "Cancelar", win.destroy).pack(side="right", padx=4)
        self._button(bottom, "Aceptar", apply).pack(side="right", padx=4)

    def open_audio_effect_dialog(self, effect):
        track = self.get_track()
        if track is None:
            return
        configs = {
            "distortion": {
                "title": "Distorsión", "size": (600, 690),
                "choices": {"type": ["Recorte fuerte", "Recorte suave", "Distorsión suave",
                    "Distorsión media", "Distorsión fuerte", "Curva cúbica (armónicos impares)",
                    "Armónicos pares", "Expandir y comprimir", "Nivelador",
                    "Distorsión rectificada", "Limitador fuerte 1413"]},
                "defaults": {"type": "Distorsión fuerte"},
                "numbers": [("upper", "Umbral superior (dBFS)", -6, -48, 0),
                    ("noise_limit", "Límite inferior de ruido (dBFS)", -45, -80, -3),
                    ("amount", "Cantidad de distorsión (%)", 35, 0, 100),
                    ("output", "Nivel de salida (%)", 85, 0, 100),
                    ("repeats", "Repeticiones", 1, 1, 5)],
                "checks": [("dc", "Filtro de bloqueo DC", False)],
            },
            "compressor": {
                "title": "Compresor", "size": (540, 650), "choices": {},
                "numbers": [("threshold", "Umbral (dBFS)", -18, -48, 0),
                    ("ratio", "Relación", 3, 1, 20), ("attack", "Ataque (ms)", 10, 1, 100),
                    ("release", "Liberación (ms)", 120, 10, 1000),
                    ("makeup", "Ganancia de salida (dB)", 0, -12, 24)], "checks": [],
            },
            "limiter": {
                "title": "Limitador", "size": (520, 580), "choices": {},
                "numbers": [("ceiling", "Techo (dBFS)", -1, -12, 0),
                    ("release", "Liberación (ms)", 80, 10, 1000),
                    ("lookahead", "Anticipación (ms)", 5, 0, 20)], "checks": [],
            },
            "stereo": {
                "title": "Ancho estéreo y graves centrados", "size": (520, 560), "choices": {},
                "numbers": [("width", "Ancho estéreo (%)", 120, 0, 200),
                    ("crossover", "Crossover de graves (Hz)", 120, 40, 400)],
                "checks": [("mono_bass", "Centrar graves en mono", True)],
            },
            "modulation": {
                "title": "Chorus / Flanger / Phaser", "size": (540, 620),
                "choices": {"mode": ["Chorus", "Flanger", "Phaser"]},
                "numbers": [("rate", "Velocidad (Hz)", 0.35, 0.05, 5),
                    ("depth", "Profundidad (%)", 45, 0, 100),
                    ("wet", "Efecto (Wet %)", 35, 0, 100),
                    ("feedback", "Realimentación (%)", 10, -80, 80)], "checks": [],
            },
            "delay": {
                "title": "Delay sincronizado al tempo", "size": (550, 650),
                "choices": {"division": ["1/2", "1/4", "1/8", "1/8 con puntillo", "1/16"]},
                "numbers": [("bpm", "Tempo (BPM)", 120, 40, 240),
                    ("feedback", "Repeticiones (Feedback %)", 35, 0, 85),
                    ("wet", "Efecto (Wet %)", 30, 0, 100)],
                "checks": [("pingpong", "Rebote estéreo (Ping-Pong)", True)],
            },
            "pitch": {
                "title": "Cambiar tono · duración conservada", "size": (520, 510), "choices": {},
                "numbers": [("semitones", "Cambio de tono (semitonos)", 0, -12, 12)], "checks": [],
            },
            "tempo": {
                "title": "Cambiar tempo · tono conservado", "size": (520, 510), "choices": {},
                "numbers": [("percent", "Cambio de tempo (%)", 0, -50, 100)], "checks": [],
            },
            "trunk": {
                "title": "Retumbe de baúl · chapa vibrando", "size": (560, 690), "choices": {},
                "description": "Los subgraves excitan varios modos metálicos; la chapa vibra, traquetea y deja una cola corta.",
                "numbers": [("intensity", "Intensidad del baúl (%)", 70, 0, 100),
                    ("threshold", "Umbral de subgraves (%)", 10, 0, 90),
                    ("frequency", "Resonancia principal (Hz)", 165, 45, 600),
                    ("decay", "Cola de chapa (ms)", 320, 20, 800),
                    ("looseness", "Flojedad del panel (%)", 80, 0, 100),
                    ("clatter", "Traqueteo de lata (%)", 85, 0, 100),
                    ("cutoff", "Detección de subgraves (Hz)", 115, 35, 250)], "checks": [],
            },
            "shout": {
                "title": "Gritón · presencia vocal", "size": (540, 610), "choices": {},
                "numbers": [("presence", "Realce de presencia (%)", 45, 0, 100),
                    ("drive", "Saturación cálida (%)", 18, 0, 100),
                    ("mix", "Mezcla del efecto (%)", 60, 0, 100)], "checks": [],
            },
        }
        config = configs.get(effect)
        if config is None:
            return
        win = ctk.CTkToplevel(self)
        win.title(config["title"])
        width, height = config["size"]
        win.geometry(f"{width}x{height}")
        win.minsize(width - 40, height - 90)
        win.configure(fg_color="#18181b")
        win.transient(self)
        win.grab_set()
        ctk.CTkLabel(
            win, text=config["title"], font=fuente_ui(size=17, weight="bold")
        ).pack(anchor="w", padx=18, pady=(14, 7))
        ctk.CTkLabel(
            win, text=config.get(
                "description", "Ajustá, escuchá la vista previa y aplicá cuando te convenza."
            ), text_color="#bcbcc0", anchor="w", justify="left", wraplength=width - 50
        ).pack(anchor="w", padx=18, pady=(0, 8))
        panel = ctk.CTkScrollableFrame(win, fg_color="transparent", corner_radius=0)
        panel.pack(fill="both", expand=True, padx=8, pady=2)
        variables = {}
        for key, choices in config["choices"].items():
            row = ctk.CTkFrame(panel, fg_color="transparent")
            row.pack(fill="x", padx=8, pady=5)
            ctk.CTkLabel(row, text="Tipo" if key == "type" else "División", width=190, anchor="w").pack(side="left")
            widget = ctk.CTkComboBox(row, values=choices, state="readonly", width=260)
            widget.set(config.get("defaults", {}).get(key, choices[0] if key == "type" else "1/4"))
            widget.pack(side="right", fill="x", expand=True)
            variables[key] = ("choice", widget, choices)
        for key, label, default, low, high in config["numbers"]:
            row = ctk.CTkFrame(panel, fg_color="transparent")
            row.pack(fill="x", padx=8, pady=4)
            ctk.CTkLabel(row, text=label, width=190, anchor="w").pack(side="left")
            variable = tk.DoubleVar(value=default)
            slider = ctk.CTkSlider(row, from_=low, to=high, variable=variable, width=175)
            slider.pack(side="left", padx=6)
            entry = ctk.CTkEntry(row, width=72, justify="center")
            entry.insert(0, f"{default:g}")
            entry.pack(side="right")
            slider.configure(command=lambda _value, v=variable, e=entry:
                             (e.delete(0, "end"), e.insert(0, f"{v.get():.2f}".rstrip("0").rstrip("."))))
            def sync_slider(_event=None, v=variable, e=entry, lo=low, hi=high):
                try:
                    number = float(e.get().replace(",", "."))
                    if math.isfinite(number):
                        number = max(lo, min(hi, number))
                        v.set(number)
                        e.delete(0, "end")
                        e.insert(0, f"{number:g}")
                except ValueError:
                    pass
            entry.bind("<Return>", sync_slider)
            entry.bind("<FocusOut>", sync_slider)
            variables[key] = ("number", variable, entry, low, high, slider)
        for key, label, default in config["checks"]:
            variable = tk.BooleanVar(value=default)
            ctk.CTkCheckBox(panel, text=label, variable=variable).pack(anchor="w", padx=18, pady=7)
            variables[key] = ("check", variable)

        def get_values():
            result = {}
            for key, item in variables.items():
                if item[0] == "choice":
                    result[key] = item[1].get()
                elif item[0] == "number":
                    number = float(item[2].get().replace(",", "."))
                    if not math.isfinite(number) or not item[3] <= number <= item[4]:
                        raise ValueError(f"{key} debe estar entre {item[3]:g} y {item[4]:g}.")
                    result[key] = number
                else:
                    result[key] = bool(item[1].get())
            return result

        def set_values(values):
            for key, value in values.items():
                item = variables.get(key)
                if not item:
                    continue
                if item[0] == "choice":
                    if value not in item[2]:
                        raise ValueError(f"La opción guardada de {key} ya no está disponible.")
                    item[1].set(value)
                elif item[0] == "number":
                    number = float(value)
                    if not math.isfinite(number) or not item[3] <= number <= item[4]:
                        raise ValueError(f"El valor guardado de {key} está fuera de rango.")
                    item[1].set(number)
                    item[2].delete(0, "end")
                    item[2].insert(0, f"{number:g}")
                else:
                    item[1].set(bool(value))
            if effect == "distortion":
                update_distortion_controls()

        if effect == "distortion":
            type_widget = variables["type"][1]
            def update_distortion_controls(_value=None):
                mode = type_widget.get()
                active_upper = mode in {"Recorte fuerte", "Recorte suave", "Distorsión suave", "Distorsión media"}
                active_noise = mode in {"Expandir y comprimir", "Nivelador"}
                for key, enabled in (("upper", active_upper), ("noise_limit", active_noise),
                                     ("repeats", mode != "Distorsión fuerte")):
                    item = variables[key]
                    state = "normal" if enabled else "disabled"
                    item[2].configure(state=state)
                    item[5].configure(state=state)
            type_widget.configure(command=update_distortion_controls)
            update_distortion_controls()

        if effect == "trunk":
            def load_trunk_preset_1():
                set_values({
                    "intensity": 70, "threshold": 10, "frequency": 145,
                    "decay": 260, "looseness": 76, "clatter": 78, "cutoff": 105,
                })

            def load_trunk_preset_2():
                set_values({
                    "intensity": 84, "threshold": 8, "frequency": 165,
                    "decay": 390, "looseness": 92, "clatter": 100, "cutoff": 125,
                })
            preset_row = ctk.CTkFrame(panel, fg_color="transparent")
            preset_row.pack(fill="x", padx=8, pady=(5, 8))
            self._button(
                preset_row, "Preset 1", load_trunk_preset_1, width=140
            ).pack(side="left", expand=True, padx=(0, 5))
            self._button(
                preset_row, "Preset 2", load_trunk_preset_2, width=140
            ).pack(side="left", expand=True, padx=(5, 0))

        preset_name = config["title"]
        def processor_factory():
            values = get_values()
            return lambda data, sr: self._audio_effect_process(data, sr, effect, values)

        self._preset_controls(win, preset_name, get_values, set_values, processor_factory)
        footer = ctk.CTkFrame(win, fg_color="transparent")
        footer.pack(fill="x", padx=16, pady=(3, 12))

        def apply_effect():
            try:
                values = get_values()
                processor = lambda data, sr: self._audio_effect_process(data, sr, effect, values)
                processor(np.zeros((max(2, int(track["sr"] * 0.05)), 1), dtype=np.float32), track["sr"])
            except Exception as exc:
                messagebox.showerror("Ajustes no válidos", str(exc), parent=win)
                return
            win.destroy()
            self._apply(processor, config["title"])

        self._button(footer, "Cancelar", win.destroy, width=100).pack(side="right", padx=4)
        self._button(footer, "Aceptar", apply_effect, width=110).pack(side="right", padx=4)

    @staticmethod
    def _phase_vocoder(data, sample_rate, rate):
        if signal is None or np is None:
            raise RuntimeError("El cambio de tono y tempo requiere NumPy y SciPy.")
        if not math.isfinite(rate) or rate <= 0:
            raise ValueError("La velocidad debe ser mayor que cero.")
        audio = np.asarray(data, dtype=np.float32)
        if audio.ndim == 1:
            audio = audio[:, None]
        if len(audio) < 32 or abs(rate - 1.0) < 1e-6:
            return audio.copy()
        n_fft = min(2048, max(32, 2 ** int(math.floor(math.log2(len(audio))))))
        hop = max(8, n_fft // 4)
        result = []
        for channel in range(audio.shape[1]):
            _, _, spectrum = signal.stft(
                audio[:, channel], fs=sample_rate, nperseg=n_fft,
                noverlap=n_fft - hop, boundary="zeros", padded=True
            )
            steps = np.arange(0, max(1, spectrum.shape[1] - 1), rate)
            bins = spectrum.shape[0]
            phase_advance = 2.0 * np.pi * hop * np.arange(bins) / n_fft
            phase = np.angle(spectrum[:, 0])
            stretched = np.zeros((bins, len(steps)), dtype=np.complex128)
            for out_index, step in enumerate(steps):
                left = int(step)
                fraction = step - left
                right = min(left + 1, spectrum.shape[1] - 1)
                magnitude = (1.0 - fraction) * np.abs(spectrum[:, left]) + fraction * np.abs(spectrum[:, right])
                delta = np.angle(spectrum[:, right]) - np.angle(spectrum[:, left]) - phase_advance
                delta -= 2.0 * np.pi * np.round(delta / (2.0 * np.pi))
                phase += phase_advance + delta
                stretched[:, out_index] = magnitude * np.exp(1j * phase)
            _, restored = signal.istft(
                stretched, fs=sample_rate, nperseg=n_fft, noverlap=n_fft - hop,
                input_onesided=True, boundary=True
            )
            result.append(restored.astype(np.float32, copy=False))
        length = max(1, int(round(len(audio) / rate)))
        output = np.zeros((length, audio.shape[1]), dtype=np.float32)
        for channel, restored in enumerate(result):
            copied = min(length, len(restored))
            output[:copied, channel] = restored[:copied]
        return output

    @classmethod
    def _audio_effect_process(cls, data, sr, effect, values):
        if np is None:
            raise RuntimeError("Este efecto requiere NumPy.")
        audio = np.asarray(data, dtype=np.float32)
        if audio.ndim == 1:
            audio = audio[:, None]
        if len(audio) < 2 or not np.isfinite(audio).all():
            raise ValueError("No hay audio válido para procesar.")
        output = audio.copy()
        if effect == "distortion":
            amount = float(values["amount"]) / 100.0
            mode = values["type"]
            repeats = 1 if mode == "Distorsión fuerte" else int(values["repeats"])
            output_gain = float(values["output"]) / 100.0
            if amount <= 1e-6:
                neutral = audio.copy()
                if values.get("dc") and signal is not None and sr > 30:
                    sos = signal.butter(1, 12, btype="highpass", fs=sr, output="sos")
                    neutral = signal.sosfilt(sos, neutral, axis=0).astype(np.float32, copy=False)
                return np.clip(neutral * np.float32(output_gain), -1.0, 1.0)
            drive = 1.0 + amount * 24.0
            for _ in range(repeats):
                x = output * drive
                if mode in {"Recorte fuerte", "Limitador fuerte 1413"}:
                    threshold = 1.0 - amount * 0.92
                    if mode == "Recorte fuerte":
                        threshold = min(threshold, 10.0 ** (float(values["upper"]) / 20.0))
                    shaped = np.clip(x, -threshold, threshold) / max(threshold, 1e-6)
                elif mode == "Recorte suave":
                    threshold = 10.0 ** (float(values["upper"]) / 20.0)
                    shaped = np.tanh(x / max(threshold, 1e-6))
                elif mode == "Curva cúbica (armónicos impares)":
                    shaped = np.clip(x - 0.22 * x ** 3, -1.0, 1.0)
                elif mode == "Armónicos pares":
                    shaped = np.clip(x + 0.35 * np.abs(x) * np.sign(x), -1.0, 1.0)
                elif mode == "Expandir y comprimir":
                    shaped = np.sign(x) * np.power(np.abs(x) + 1e-8, 1.0 + amount * 1.4)
                    floor = 10.0 ** (float(values["noise_limit"]) / 20.0)
                    shaped[np.abs(output) < floor] = 0.0
                elif mode == "Nivelador":
                    shaped = np.tanh(x * (0.7 + amount * 5.0))
                elif mode == "Distorsión rectificada":
                    shaped = np.abs(x) * np.sign(x + 1e-8)
                else:
                    softness = {"Distorsión suave": 1.6, "Distorsión media": 3.2,
                                "Distorsión fuerte": 6.0}.get(mode, 3.2)
                    shaped = np.tanh(x * softness) / np.tanh(drive * softness)
                output = output * np.float32(1.0 - amount) + np.asarray(shaped, dtype=np.float32) * np.float32(amount)
                output = np.asarray(output, dtype=np.float32)
            if values.get("dc") and signal is not None and sr > 30:
                sos = signal.butter(1, 12, btype="highpass", fs=sr, output="sos")
                output = signal.sosfilt(sos, output, axis=0).astype(np.float32, copy=False)
            output *= np.float32(output_gain)
            return np.clip(output, -1.0, 1.0)
        if effect in {"compressor", "limiter"}:
            threshold_db = float(values["threshold"]) if effect == "compressor" else float(values["ceiling"])
            ratio = float(values["ratio"]) if effect == "compressor" else 30.0
            attack_ms = float(values["attack"]) if effect == "compressor" else 1.0
            release_ms = float(values["release"])
            block = max(16, int(sr * 0.003))
            lookahead_samples = int(sr * float(values["lookahead"]) / 1000.0) if effect == "limiter" else 0
            reduced = np.empty_like(output)
            envelope = 0.0
            attack_coeff = math.exp(-block / max(1.0, sr * attack_ms / 1000.0))
            release_coeff = math.exp(-block / max(1.0, sr * release_ms / 1000.0))
            makeup = 10.0 ** (float(values.get("makeup", 0.0)) / 20.0)
            for first in range(0, len(output), block):
                last = min(len(output), first + block)
                detector_end = min(len(output), last + lookahead_samples)
                peak = float(np.max(np.abs(output[first:detector_end])))
                coeff = attack_coeff if peak > envelope else release_coeff
                envelope = coeff * envelope + (1.0 - coeff) * peak
                level_db = 20.0 * math.log10(max(envelope, 1e-9))
                if level_db > threshold_db:
                    compressed_db = threshold_db + (level_db - threshold_db) / ratio
                    gain = 10.0 ** ((compressed_db - level_db) / 20.0)
                else:
                    gain = 1.0
                reduced[first:last] = output[first:last] * np.float32(gain * makeup)
            if effect == "limiter":
                ceiling = 10.0 ** (threshold_db / 20.0)
                reduced = np.clip(reduced, -ceiling, ceiling)
            return reduced
        if effect == "stereo":
            if signal is None:
                raise RuntimeError("El control de graves estéreo requiere SciPy.")
            if output.shape[1] == 1:
                output = np.repeat(output, 2, axis=1)
            elif output.shape[1] > 2:
                output = output[:, :2]
            mid = (output[:, 0] + output[:, 1]) * 0.5
            side = (output[:, 0] - output[:, 1]) * 0.5 * float(values["width"]) / 100.0
            left, right = mid + side, mid - side
            if values.get("mono_bass"):
                cutoff = min(float(values["crossover"]), sr * 0.45)
                if cutoff <= 1:
                    raise ValueError("La frecuencia de crossover es demasiado baja.")
                sos = signal.butter(2, cutoff, btype="lowpass", fs=sr, output="sos")
                low = signal.sosfilt(sos, output, axis=0)
                low_mono = np.mean(low, axis=1)
                left += low_mono - low[:, 0]
                right += low_mono - low[:, 1]
            return np.column_stack((left, right)).astype(np.float32, copy=False)
        if effect == "modulation":
            mode = values["mode"]
            rate = float(values["rate"])
            depth = float(values["depth"]) / 100.0
            wet = float(values["wet"]) / 100.0
            feedback = float(values["feedback"]) / 100.0
            if mode == "Phaser":
                if signal is None:
                    raise RuntimeError("El phaser requiere SciPy.")
                result = np.empty_like(output)
                block = 1024
                states = [np.zeros((4, 2), dtype=np.float64) for _ in range(output.shape[1])]
                for first in range(0, len(output), block):
                    last = min(len(output), first + block)
                    center = 350.0 + (1200.0 + 4800.0 * depth) * (
                        0.5 + 0.5 * math.sin(2.0 * math.pi * rate * (first + last) / (2.0 * sr))
                    )
                    sections = []
                    for multiplier in (0.5, 1.0, 2.0, 4.0):
                        frequency = min(sr * 0.44, max(20.0, center * multiplier))
                        tangent = math.tan(math.pi * frequency / sr)
                        coefficient = (tangent - 1.0) / (tangent + 1.0)
                        sections.append([coefficient, 1.0, 0.0, 1.0, coefficient, 0.0])
                    sos = np.asarray(sections, dtype=np.float64)
                    for channel in range(output.shape[1]):
                        filtered, states[channel] = signal.sosfilt(
                            sos, output[first:last, channel], zi=states[channel]
                        )
                        result[first:last, channel] = (
                            output[first:last, channel] * (1.0 - wet)
                            + filtered.astype(np.float32, copy=False) * wet
                        )
                return result.astype(np.float32, copy=False)
            delay_ms = {"Chorus": 18.0, "Flanger": 2.0, "Phaser": 5.0}[mode]
            positions = np.arange(len(output), dtype=np.float64)
            lfo = 0.5 + 0.5 * np.sin(2.0 * np.pi * rate * positions / sr)
            delay = np.maximum(1.0, sr * delay_ms / 1000.0 * (0.4 + depth * 1.6 * lfo))
            result = output.copy()
            for channel in range(output.shape[1]):
                source = output[:, channel]
                read = positions - delay
                delayed = np.interp(read, positions, source, left=0.0, right=0.0)
                if channel % 2:
                    delayed = np.roll(delayed, max(0, int(sr / max(0.1, rate) / 4)))
                result[:, channel] = source * (1.0 - wet) + (source * 0.5 + delayed * 0.5) * wet
                if abs(feedback) > 1e-5:
                    result[1:, channel] += delayed[:-1] * feedback * 0.25
            return result.astype(np.float32, copy=False)
        if effect == "delay":
            if output.shape[1] == 1:
                output = np.repeat(output, 2, axis=1)
            bpm = float(values["bpm"])
            beat = 60.0 / bpm
            multiplier = {"1/2": 2.0, "1/4": 1.0, "1/8": 0.5,
                          "1/8 con puntillo": 0.75, "1/16": 0.25}[values["division"]]
            delay = max(1, int(sr * beat * multiplier))
            feedback, wet = float(values["feedback"]) / 100.0, float(values["wet"]) / 100.0
            repeats = min(16, max(1, int(math.ceil(math.log(0.015) / math.log(max(0.015, feedback))))) if feedback > 0 else 1)
            tail = delay * repeats
            result = np.zeros((len(output) + tail, 2), dtype=np.float32)
            result[:len(output)] += output
            delayed = output.copy()
            for repeat in range(1, repeats + 1):
                if values.get("pingpong") and repeat % 2:
                    echo = delayed[:, ::-1]
                else:
                    echo = delayed
                offset = delay * repeat
                result[offset:offset + len(output)] += echo * np.float32(wet * feedback ** (repeat - 1))
            return result
        if effect == "pitch":
            semitones = float(values["semitones"])
            factor = 2.0 ** (semitones / 12.0)
            if abs(semitones) < 1e-6:
                return output
            divisor = max(1, int(round(1000 * factor)))
            numerator = 1000
            shifted = signal.resample_poly(output, numerator, divisor, axis=0) if signal is not None else output
            return cls._phase_vocoder(shifted, sr, 1.0 / factor)
        if effect == "tempo":
            ratio = 1.0 + float(values["percent"]) / 100.0
            if ratio <= 0:
                raise ValueError("El cambio de tempo no puede llevar la velocidad a cero.")
            return cls._phase_vocoder(output, sr, ratio)
        if effect == "trunk":
            if signal is None:
                raise RuntimeError("Retumbe de baúl requiere SciPy.")
            mono = np.mean(output, axis=1).astype(np.float32, copy=False)
            cutoff = min(float(values["cutoff"]), sr * 0.42)
            low_sos = signal.butter(2, cutoff, btype="lowpass", fs=sr, output="sos")
            bass = signal.sosfilt(low_sos, mono).astype(np.float32, copy=False)

            # Envelope follower gates the loose body panel from the sub content.
            rectified = np.abs(bass)
            envelope_sos = signal.butter(1, min(18.0, sr * 0.12), btype="lowpass", fs=sr, output="sos")
            envelope = signal.sosfilt(envelope_sos, rectified)
            peak = max(1e-7, float(np.percentile(envelope, 98)))
            if peak <= 1e-6:
                return output
            normalized = np.clip(envelope / peak, 0.0, 1.5)
            threshold = float(values["threshold"]) / 100.0
            gate = np.clip((normalized - threshold) / max(0.05, 1.0 - threshold), 0.0, 1.0)
            gate = np.power(gate, 1.15)

            # Sudden sub hits kick the panel; sustained bass keeps it chattering.
            fast_sos = signal.butter(1, min(42.0, sr * 0.2), btype="lowpass", fs=sr, output="sos")
            slow_sos = signal.butter(1, min(7.0, sr * 0.08), btype="lowpass", fs=sr, output="sos")
            fast_env = signal.sosfilt(fast_sos, rectified)
            slow_env = signal.sosfilt(slow_sos, rectified)
            impact = np.clip((fast_env - slow_env) / max(peak * 0.18, 1e-7), 0.0, 1.0)

            looseness = float(values["looseness"]) / 100.0
            clatter = float(values["clatter"]) / 100.0
            decay_s = float(values["decay"]) / 1000.0
            count = len(output)
            time_axis = np.arange(count, dtype=np.float64) / sr
            flutter_rate = 28.0 + 42.0 * looseness
            flutter = 0.35 + 0.65 * np.maximum(
                0.0, np.sin(2.0 * np.pi * flutter_rate * time_axis
                            + 0.22 * np.sin(2.0 * np.pi * 3.7 * time_axis))
            )
            rattle_gate = np.clip(gate * flutter + impact * (0.25 + 0.45 * looseness), 0.0, 1.0)

            rng = np.random.default_rng(147212)
            noise = rng.standard_normal(count).astype(np.float32)
            normalized_bass = np.clip(bass / peak, -1.5, 1.5)
            excitation = (
                noise * rattle_gate * (0.28 + 0.72 * clatter)
                + normalized_bass * impact * 0.7
            ).astype(np.float32, copy=False)

            # A loose sheet is a small bank of uneven resonances, each with its
            # own decay. White-noise bursts supply the metallic scrape/rattle.
            base_frequency = float(values["frequency"])
            mode_ratios = (1.0, 1.49, 2.17, 3.08, 4.27)
            mode_gains = (1.0, 0.62, 0.39, 0.24, 0.14)
            looseness_scale = 0.72 + 1.55 * looseness
            tail = max(1, int(sr * max(0.12, decay_s * 1.5)))
            padded_excitation = np.pad(excitation, (0, tail))
            metal = np.zeros(len(padded_excitation), dtype=np.float32)
            for mode_index, (ratio, gain) in enumerate(zip(mode_ratios, mode_gains)):
                frequency = min(sr * 0.43, base_frequency * ratio)
                if frequency < 20.0:
                    continue
                q_value = np.clip(
                    (math.pi * frequency * decay_s / math.log(1000.0)) * looseness_scale,
                    3.0, 32.0
                )
                b, a = signal.iirpeak(frequency, float(q_value), fs=sr)
                mode = signal.lfilter(b, a, padded_excitation).astype(np.float32, copy=False)
                metal += mode * np.float32(gain * (1.0 + 0.035 * mode_index))

            # A little gated high-frequency sheet buzz adds the papery can-like edge.
            high_cut = min(950.0, sr * 0.4)
            if high_cut < sr * 0.48:
                high_sos = signal.butter(2, high_cut, btype="highpass", fs=sr, output="sos")
                bright_noise = signal.sosfilt(high_sos, noise)
            else:
                bright_noise = noise
            bright_layer = np.pad(
                bright_noise * rattle_gate * (0.12 + 0.26 * clatter), (0, tail)
            )
            input_scale = min(1.0, peak * 5.0)
            intensity = float(values["intensity"]) / 100.0
            metal_layer = (metal * 0.34 + bright_layer).astype(np.float32, copy=False)
            metal_layer *= np.float32(intensity * input_scale)
            dry = np.pad(output, ((0, tail), (0, 0)))
            return dry + metal_layer[:, None]
        if effect == "shout":
            if signal is None:
                raise RuntimeError("El efecto de presencia vocal requiere SciPy.")
            low, high = max(300.0, min(1400.0, sr * 0.12)), min(4500.0, sr * 0.45)
            if high <= low:
                raise ValueError("La frecuencia de muestreo no permite realzar presencia vocal.")
            sos = signal.butter(2, [low, high], btype="bandpass", fs=sr, output="sos")
            presence = signal.sosfilt(sos, output, axis=0)
            presence *= np.float32(1.0 + float(values["presence"]) / 25.0)
            drive = 1.0 + float(values["drive"]) / 14.0
            saturated = np.tanh(output * drive) / max(1e-6, np.tanh(drive))
            wet = float(values["mix"]) / 100.0
            return output * (1.0 - wet) + (saturated + presence * 0.35) * wet
        raise ValueError("Este efecto todavía no tiene un procesador asignado.")

    def _temp_wav(self, data, sr):
        fd, path = tempfile.mkstemp(prefix="editor_audio_", suffix=".wav")
        os.close(fd)
        sf.write(path, data, sr, subtype="PCM_16")
        return path

    def _discard_track_preview_cache(self, track):
        cached = track.pop("_preview_temp", None)
        if cached and os.path.isfile(cached):
            try:
                os.remove(cached)
            except OSError:
                pass

    def preview_track(self):
        if not self.tracks:
            self.set_info("Importá una pista para escuchar el proyecto.", "#f4f4f5")
            return
        self.stop_preview()
        has_solo = any(item.get("solo", False) for item in self.tracks)
        active_tracks = [
            item for item in self.tracks
            if not item.get("muted", False) and (not has_solo or item.get("solo", False))
        ]
        if not active_tracks:
            self.set_info("No hay pistas audibles: desactivá Mute o revisá Solo.", "#f4f4f5")
            return
        duration = max(
            float(item.get("start_sec", 0.0)) + len(item["data"]) / max(1, item["sr"])
            for item in active_tracks
        )
        start_pos = min(max(0.0, self._preview_position), max(0.0, duration - 0.001))
        token = self._preview_generation
        snapshots = [
            (item["data"], int(item["sr"]), float(item.get("gain_db", 0.0)),
             float(item.get("pan", 0.0)), item)
            for item in active_tracks
        ]
        use_live_stream = sd is not None and float(self.preview_speed.get().lower().replace("x", "")) == 1.0
        self._preview_preparing = True
        self._preview_duration = duration
        self.update_timeline_labels()
        self.set_info(f"Mezclando {len(active_tracks)} pistas audibles para la vista previa…", "#f4f4f5")
        threading.Thread(
            target=self._prepare_preview_mix,
            args=(snapshots, start_pos, token, use_live_stream),
            daemon=True
        ).start()

    def play_preview(self):
        if self._preview_paused:
            self.toggle_pause_preview()
        else:
            self.preview_track()

    def toggle_pause_preview(self):
        if not self._preview_process or self._preview_process.poll() is not None:
            return
        self._preview_paused = not self._preview_paused
        if self._preview_paused:
            process = self._preview_process
            if isinstance(process, subprocess.Popen) and process.stdin:
                try:
                    process.stdin.write(b"p")
                    process.stdin.flush()
                except (OSError, ValueError):
                    pass
            self.set_info("Vista previa pausada.", "#f4f4f5")
        else:
            process = self._preview_process
            if isinstance(process, subprocess.Popen) and process.stdin:
                try:
                    process.stdin.write(b"p")
                    process.stdin.flush()
                except (OSError, ValueError):
                    pass
            elapsed = max(0.0, self._preview_position - self._preview_start)
            self._preview_started_at = time.monotonic() - elapsed / max(0.01, self._preview_rate)
            self.set_info("Vista previa en reproducción.", "#f4f4f5")

    def restart_preview(self):
        was_playing = self._preview_process is not None or self._preview_preparing
        self._preview_position = 0.0
        self._wave_view_center = 0.0
        self.update_timeline_labels()
        if was_playing:
            self.preview_track()

    def end_preview(self):
        duration = self._preview_duration if self._preview_duration > 0 else self.get_project_duration()
        was_playing = self._preview_process is not None or self._preview_preparing
        if was_playing:
            self.stop_preview()
        self._preview_position = duration
        self.update_timeline_labels()
        self.draw_waveform()

    @staticmethod
    def _automation_samples(points, frame_count, sample_rate):
        if not isinstance(points, (list, tuple)) or not points or frame_count <= 0:
            return None
        cleaned = []
        for point in points:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                continue
            moment, value = float(point[0]), float(point[1])
            if math.isfinite(moment) and math.isfinite(value):
                cleaned.append((max(0.0, moment), value))
        cleaned.sort(key=lambda pair: pair[0])
        if len(cleaned) < 2:
            return None
        times = np.arange(frame_count, dtype=np.float64) / max(1, int(sample_rate))
        knots = np.asarray([pair[0] for pair in cleaned], dtype=np.float64)
        values = np.asarray([pair[1] for pair in cleaned], dtype=np.float64)
        return np.interp(times, knots, values, left=values[0], right=values[-1]).astype(np.float32)

    @staticmethod
    def _render_track_gain_pan(data, gain_db, pan, limit_peak=False, track=None,
                               include_static=True):
        audio = np.asarray(data, dtype=np.float32)
        if audio.ndim == 1:
            audio = audio[:, None]
        if audio.shape[1] == 1:
            audio = np.repeat(audio, 2, axis=1)
        elif audio.shape[1] > 2:
            audio = audio[:, :2].copy()
        else:
            audio = audio.copy()
        base_gain = float(gain_db) if include_static else 0.0
        audio *= np.float32(10.0 ** (base_gain / 20.0))
        sample_rate = int(track.get("sr", 44100)) if track else 44100
        gain_curve = EditorAudio._automation_samples(
            track.get("gain_automation", []) if track else [], len(audio), sample_rate
        )
        if gain_curve is not None:
            audio *= np.power(10.0, gain_curve / 20.0).astype(np.float32, copy=False)[:, None]
        pan_curve = EditorAudio._automation_samples(
            track.get("pan_automation", []) if track else [], len(audio), sample_rate
        )
        if pan_curve is None:
            pan_curve = np.full(
                len(audio), max(-100.0, min(100.0, float(pan))) if include_static else 0.0,
                dtype=np.float32
            )
        else:
            pan_curve = np.clip(pan_curve, -100.0, 100.0)
        pan_values = pan_curve / 100.0
        audio[:, 0] *= (1.0 - np.maximum(0.0, pan_values)).astype(np.float32)
        audio[:, 1] *= (1.0 + np.minimum(0.0, pan_values)).astype(np.float32)
        if limit_peak and audio.size:
            peak = float(np.max(np.abs(audio)))
            if peak > 0.98:
                audio *= np.float32(0.98 / peak)
        return audio

    def _prepare_preview_mix(self, snapshots, start_pos, token, use_live_stream):
        path = None
        try:
            target_sr = 44100
            live_tracks = []
            if use_live_stream:
                for source, source_sr, _gain_db, _pan, track in snapshots:
                    audio = self._render_track_gain_pan(
                        source, 0.0, 0.0, track=track, include_static=False
                    )
                    if source_sr != target_sr:
                        divisor = math.gcd(source_sr, target_sr)
                        audio = signal.resample_poly(
                            audio, target_sr // divisor, source_sr // divisor, axis=0
                        ).astype(np.float32, copy=False)
                    offset_frames = int(round(float(track.get("start_sec", 0.0)) * target_sr))
                    live_tracks.append((audio, track, offset_frames))
                self.after(0, lambda tracks=live_tracks, t=token, pos=start_pos:
                           self._finish_live_preview(tracks, target_sr, pos, t))
                return
            mix = None
            for source, source_sr, gain_db, pan, track in snapshots:
                audio = self._render_track_gain_pan(source, gain_db, pan, track=track)
                if source_sr != target_sr:
                    divisor = math.gcd(source_sr, target_sr)
                    audio = signal.resample_poly(
                        audio, target_sr // divisor, source_sr // divisor, axis=0
                    ).astype(np.float32, copy=False)
                offset_frames = int(round(float(track.get("start_sec", 0.0)) * target_sr))
                required = offset_frames + len(audio)
                if mix is None or required > len(mix):
                    expanded = np.zeros((required, 2), dtype=np.float32)
                    if mix is not None:
                        expanded[:len(mix)] = mix
                    mix = expanded
                mix[offset_frames:required] += audio
            if mix is None or not len(mix):
                raise ValueError("No quedaron pistas de audio para reproducir.")
            peak = float(np.max(np.abs(mix)))
            if peak > 0.98:
                mix *= np.float32(0.98 / peak)
            path = self._temp_wav(mix, target_sr)
            self.after(0, lambda p=path, d=mix, t=token, pos=start_pos:
                       self._finish_preview_mix(p, d, target_sr, pos, t))
        except Exception as exc:
            if path and os.path.isfile(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
            message = str(exc)
            try:
                self.after(0, lambda msg=message, t=token: self._preview_prepare_error(t, msg))
            except (RuntimeError, tk.TclError):
                pass

    def _finish_live_preview(self, live_tracks, sample_rate, start_pos, token):
        self._preview_preparing = False
        if token != self._preview_generation or not self.winfo_exists():
            return
        self._preview_live_tracks = live_tracks
        duration = max((offset + len(audio)) / sample_rate for audio, _track, offset in live_tracks)
        self._preview_duration = duration
        self._preview_position = start_pos
        self._preview_start = start_pos
        self._preview_rate = 1.0
        self._preview_started_at = time.monotonic()
        frame_state = {"position": int(start_pos * sample_rate)}

        def callback(outdata, frames, _time_info, _status):
            if self._preview_paused:
                outdata.fill(0)
                return
            mixed = np.zeros((frames, 2), dtype=np.float32)
            first = frame_state["position"]
            last = first + frames
            has_solo = any(track.get("solo", False) for _audio, track, _offset in live_tracks)
            for audio, track, track_offset in live_tracks:
                if track.get("muted", False) or (has_solo and not track.get("solo", False)):
                    continue
                chunk = np.zeros((frames, 2), dtype=np.float32)
                source_first = max(0, first - track_offset)
                source_last = min(len(audio), last - track_offset)
                if source_last > source_first:
                    dest_first = track_offset + source_first - first
                    chunk[dest_first:dest_first + source_last - source_first] = audio[source_first:source_last]
                chunk *= np.float32(10.0 ** (float(track.get("gain_db", 0.0)) / 20.0))
                pan_value = (
                    0.0 if track.get("pan_automation") else
                    max(-100.0, min(100.0, float(track.get("pan", 0.0)))) / 100.0
                )
                chunk[:, 0] *= np.float32(1.0 - max(0.0, pan_value))
                chunk[:, 1] *= np.float32(1.0 + min(0.0, pan_value))
                mixed += chunk
            mixed *= np.float32(max(0, min(100, self._preview_volume_live)) / 100.0)
            outdata[:] = np.clip(mixed, -1.0, 1.0)
            frame_state["position"] = last

        try:
            stream = sd.OutputStream(
                samplerate=sample_rate, channels=2, dtype="float32",
                blocksize=512, callback=callback, device=self._audio_output_device
            )
            stream.start()
            self._preview_process = _AudioStreamHandle(stream)
            self.set_info(
                f"Vista previa en vivo · {len(live_tracks)} pistas · ganancia ajustable al instante",
                "#f4f4f5"
            )
            self._schedule_preview_update()
        except Exception as exc:
            self._preview_live_tracks = []
            self._preview_prepare_error(token, f"No se pudo iniciar el audio en vivo: {exc}")

    def _finish_preview_mix(self, path, data, sample_rate, start_pos, token):
        self._preview_preparing = False
        if token != self._preview_generation or not self.winfo_exists():
            try:
                os.remove(path)
            except OSError:
                pass
            return
        mixed_track = {"name": "Mezcla del proyecto", "data": data, "sr": sample_rate}
        self._preview_mix_path = path
        start_pos = min(max(0.0, start_pos), max(0.0, len(data) / sample_rate - 0.001))
        self._start_preview_process(mixed_track, path, start_pos, token)

    def _prepare_preview_file(self, track, data, sample_rate, start_pos, token):
        path = None
        try:
            path = self._temp_wav(data, sample_rate)
            self.after(0, lambda: self._finish_preview_prepare(track, path, start_pos, token))
        except Exception as exc:
            if path and os.path.isfile(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
            message = str(exc)
            try:
                self.after(0, lambda: self._preview_prepare_error(token, message))
            except (RuntimeError, tk.TclError):
                pass

    def _finish_preview_prepare(self, track, path, start_pos, token):
        self._preview_preparing = False
        if token != self._preview_generation or not self.winfo_exists():
            try:
                os.remove(path)
            except OSError:
                pass
            return
        track["_preview_temp"] = path
        duration = len(track["data"]) / max(1, track["sr"])
        start_pos = min(max(0.0, self._preview_position), max(0.0, duration - 0.001))
        self._start_preview_process(track, path, start_pos, token)

    def _preview_prepare_error(self, token, message):
        if token != self._preview_generation:
            return
        self._preview_preparing = False
        messagebox.showerror("Error de vista previa", message, parent=self)

    def _start_preview_process(self, track, path, start_pos, token):
        if token != self._preview_generation:
            return
        try:
            player = localizar_programa("ffplay")
            if not player:
                raise RuntimeError(
                    "No se encontró FFplay. Reinstalá la app con FFmpeg incluido "
                    "o instalá FFmpeg y agregá su carpeta bin al PATH."
                )
            duration = len(track["data"]) / max(1, track["sr"])
            speed = float(self.preview_speed.get().lower().replace("x", ""))
            self._is_seeking = False
            self._preview_temp = path
            self._preview_start = start_pos
            self._preview_position = start_pos
            self._preview_duration = duration
            self._preview_started_at = time.monotonic()
            self._preview_rate = speed
            self._preview_track_index = self.selected_index
            self._preview_process = subprocess.Popen(
                [player, "-nodisp", "-autoexit", "-loglevel", "quiet",
                 "-volume", str(int(self.preview_volume.get())),
                 "-af", f"atempo={speed}", "-ss", f"{start_pos:.3f}", path],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                **opciones_subproceso_oculto()
            )
            self._preview_paused = False
            self.set_info(
                f"Vista previa {speed:g}x · volumen {int(self.preview_volume.get())}% · desde {self._format_time(start_pos)}",
                "#f4f4f5"
            )
            self._schedule_preview_update()
        except Exception as exc:
            self._preview_process = None
            messagebox.showerror("Error de vista previa", str(exc), parent=self)

    def _schedule_preview_update(self):
        if self._preview_process is None:
            return
        if self._preview_process.poll() is not None:
            if self.preview_loop.get() and self._preview_duration > 0:
                self._restart_preview_loop()
                return
            self._finish_preview()
            return
        if self._preview_paused:
            self._preview_after = self.after(100, self._schedule_preview_update)
            return
        # Reloj monotónico para evitar deriva cuando la interfaz se retrasa.
        if not self._is_seeking:
            elapsed = time.monotonic() - (self._preview_started_at or time.monotonic())
            self._preview_position = self._preview_start + elapsed * getattr(self, "_preview_rate", 1.0)
            if self._preview_duration > 0 and float(self.wave_zoom.get()) > 1:
                visible = self._preview_duration / float(self.wave_zoom.get())
                visible_start = max(0.0, min(
                    self._preview_duration - visible,
                    self._wave_view_center - visible / 2
                ))
                if not visible_start <= self._preview_position <= visible_start + visible:
                    self._wave_view_center = self._preview_position
        if self._preview_position >= self._preview_duration:
            if self.preview_loop.get():
                self._restart_preview_loop()
            else:
                self._finish_preview()
            return
        self.update_timeline_labels()
        self.draw_waveform()
        self._preview_after = self.after(100, self._schedule_preview_update)

    def _restart_preview_loop(self):
        self._finish_preview()
        self._preview_position = 0.0
        self._wave_view_center = 0.0
        self.update_timeline_labels()
        self._loop_restart_after = self.after(80, self.preview_track)

    def _finish_preview(self):
        if self._preview_after is not None:
            try:
                self.after_cancel(self._preview_after)
            except Exception:
                pass
            self._preview_after = None
        self._preview_temp = None
        self._stop_preview_process()
        self._preview_started_at = None
        if not self.preview_loop.get():
            self._discard_preview_mix_file()
        if self._preview_position >= self._preview_duration:
            self._preview_position = self._preview_duration
        self.update_timeline_labels()
        self.draw_waveform()

    def stop_preview(self):
        self._is_seeking = False
        self._preview_paused = False
        self._preview_generation += 1
        self._preview_preparing = False
        if self._loop_restart_after is not None:
            try:
                self.after_cancel(self._loop_restart_after)
            except Exception:
                pass
            self._loop_restart_after = None
        if self._preview_after is not None:
            try:
                self.after_cancel(self._preview_after)
            except Exception:
                pass
            self._preview_after = None
        self._stop_preview_process()
        self._preview_temp = None
        self._preview_started_at = None
        self._preview_duration = 0.0
        self._preview_live_tracks = []
        self._discard_preview_mix_file()
        self.update_timeline_labels() if hasattr(self, "time_current") else None
        self.draw_waveform() if hasattr(self, "wave_canvas") else None

    def _discard_preview_mix_file(self):
        path = self._preview_mix_path
        self._preview_mix_path = None
        if path and os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                pass

    def _stop_preview_process(self):
        process = self._preview_process
        self._preview_process = None
        if process is None or process.poll() is not None:
            return
        if isinstance(process, _AudioStreamHandle):
            process.terminate()
            return
        try:
            process.terminate()
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
                process.wait(timeout=1.0)
            except Exception:
                pass
        except Exception:
            pass

    def export_track(self):
        track = self.get_track()
        if track is None:
            return
        if len(self.tracks) > 1:
            continuar = messagebox.askyesno(
                "Exportar solo esta pista",
                "Esta opción guarda únicamente la pista seleccionada.\n\n"
                "Para exportar la intro junto con el resto del proyecto, elegí Exportar mezcla.\n\n"
                "¿Querés continuar con esta pista solamente?",
                parent=self, default="no"
            )
            if not continuar:
                return
        path = filedialog.asksaveasfilename(
            parent=self, title="Exportar audio", defaultextension=".wav",
            initialfile=os.path.splitext(track["name"])[0] + "_editado.wav",
            filetypes=[
                ("WAV", "*.wav"), ("FLAC", "*.flac"), ("AIFF", "*.aiff"),
                ("OGG", "*.ogg"), ("MP3 (requiere FFmpeg)", "*.mp3")
            ]
        )
        if not path:
            return
        ext = os.path.splitext(path)[1].lower()
        try:
            rendered = self._render_track_gain_pan(
                track["data"], track.get("gain_db", 0.0), track.get("pan", 0.0), track=track
            )
            rendered = np.clip(rendered, -1.0, 1.0)
            self._save_audio_export(path, rendered, track["sr"])
            self.set_info(f"Audio exportado: {path}", "#f4f4f5")
            messagebox.showinfo("Exportación completada", f"Se exportó el audio en:\n{path}", parent=self)
        except Exception as exc:
            messagebox.showerror("No se pudo exportar", str(exc), parent=self)

    def export_mix(self):
        if not self.tracks:
            messagebox.showinfo("Exportar mezcla", "Importá al menos una pista.", parent=self)
            return
        has_solo = any(track.get("solo", False) for track in self.tracks)
        active = [
            track for track in self.tracks
            if not track.get("muted", False) and (not has_solo or track.get("solo", False))
        ]
        if not active:
            messagebox.showinfo("Exportar mezcla", "No hay pistas audibles. Revisá Mute y Solo.", parent=self)
            return
        path = filedialog.asksaveasfilename(
            parent=self, title="Exportar mezcla audible", defaultextension=".wav",
            initialfile="mezcla_editada.wav",
            filetypes=[("WAV", "*.wav"), ("FLAC", "*.flac"), ("AIFF", "*.aiff"),
                       ("OGG", "*.ogg"), ("MP3 (requiere FFmpeg)", "*.mp3")]
        )
        if not path:
            return
        try:
            target_sr = 44100
            rendered_tracks = []
            for track in active:
                audio = self._render_track_gain_pan(
                    track["data"], track.get("gain_db", 0.0), track.get("pan", 0.0), track=track
                )
                source_sr = int(track["sr"])
                if source_sr != target_sr:
                    divisor = math.gcd(source_sr, target_sr)
                    audio = signal.resample_poly(
                        audio, target_sr // divisor, source_sr // divisor, axis=0
                    ).astype(np.float32, copy=False)
                offset = int(round(float(track.get("start_sec", 0.0)) * target_sr))
                rendered_tracks.append((audio, offset))
            length = max(offset + len(audio) for audio, offset in rendered_tracks)
            mix = np.zeros((length, 2), dtype=np.float32)
            for audio, offset in rendered_tracks:
                mix[offset:offset + len(audio)] += audio
            # El preview también recorta la mezcla a rango audible en cada bloque.
            mix = np.clip(mix, -1.0, 1.0)
            self._save_audio_export(path, mix, target_sr)
            self.set_info(f"Mezcla exportada con {len(active)} pistas audibles: {path}", "#f4f4f5")
            messagebox.showinfo(
                "Exportación completada",
                f"Se exportó la mezcla de {len(active)} pistas con sus efectos, ganancia y paneo:\n{path}",
                parent=self
            )
        except Exception as exc:
            messagebox.showerror("No se pudo exportar la mezcla", str(exc), parent=self)

    def _save_audio_export(self, path, data, sample_rate):
        ext = os.path.splitext(path)[1].lower()
        if ext == ".mp3":
            ffmpeg = localizar_programa("ffmpeg")
            if not ffmpeg:
                raise RuntimeError(
                    "Para exportar MP3 se necesita FFmpeg. Reinstalá la app con FFmpeg incluido "
                    "o agregá su carpeta bin al PATH."
                )
            temp = self._temp_wav(data, sample_rate)
            try:
                subprocess.run(
                    [ffmpeg, "-y", "-i", temp, "-codec:a", "libmp3lame", "-q:a", "2", path],
                    check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                    **opciones_subproceso_oculto()
                )
            finally:
                if os.path.exists(temp):
                    os.remove(temp)
        else:
            sf.write(path, data, sample_rate)

    def destroy(self):
        if self._autosave_after is not None:
            try:
                self.after_cancel(self._autosave_after)
            except Exception:
                pass
            self._autosave_after = None
        try:
            self._stop_effect_preview()
        except Exception:
            pass
        try:
            self.stop_preview()
        except Exception:
            pass
        for track in self.tracks:
            self._discard_track_preview_cache(track)
        try:
            if os.path.isfile(self._autosave_file):
                os.remove(self._autosave_file)
        except OSError:
            pass
        super().destroy()


if __name__ == "__main__":
    if len(sys.argv) >= 5 and sys.argv[1] == "--apply-update":
        try:
            result = _finish_update_helper(sys.argv[2], int(sys.argv[3]), sys.argv[4])
        except Exception:
            result = 1
        raise SystemExit(result)
    login = VentanaLogin()
    login.mainloop()
