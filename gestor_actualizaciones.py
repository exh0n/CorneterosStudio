"""Panel local para compilar, subir el código inicial y publicar versiones."""

import base64
import hashlib
import http.client
import json
import os
import queue
import re
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import customtkinter as ctk
from tkinter import messagebox


ROOT = Path(__file__).resolve().parent
APP_SOURCE = ROOT / "descargador2.py"
ISS_FILE = ROOT / "Descargador.iss"
REPOSITORY = "exh0n/CorneterosStudio"
API_ROOT = f"https://api.github.com/repos/{REPOSITORY}"
USER_AGENT = "CorneterosStudio-ReleaseManager/1.0"
WINDOWS_PATTERN = "Instalador_Corneteros_Studio_{version}.exe"
WINLATOR_NAME = "CorneterosStudio_Winlator_x64.zip"

SOURCE_FILES = [
    ".gitignore", "README.md", "descargador2.py", "actualizador.py",
    "gestor_actualizaciones.py", "Descargador.iss", "Descargador2.spec",
    "compilar.bat", "preparar_ffmpeg.bat", "preparar_logos.py",
    "preparar_tcl_runtime.py", "preparar_winlator_zip.py", "generar_icono.py",
    "generar_manifest_release.py",
    "requirements.txt", "update_config.json", "latest.json", "LEEME_Winlator.txt",
    "EMPAQUETADO.md", "app.ico", "app_icon.png", "corneteros_studio_icon.svg",
    "logo_corneteros_fondo_negro.png", "logo_corneteros_transparente.png",
    "logo_cs_fondo_negro.png", "logo_cs_transparente.png",
    "assets/corneteros_cs_source.png", "installer/wizard.bmp",
    "installer/wizard_small.bmp",
]

BLACK = "#000000"
PANEL = "#111114"
WHITE = "#f4f4f5"
MUTED = "#a1a1aa"
BLUE = "#1687ff"


def current_version():
    source = APP_SOURCE.read_text(encoding="utf-8")
    match = re.search(r'^APP_VERSION\s*=\s*"([0-9]+(?:\.[0-9]+){1,3})"', source, re.M)
    if not match:
        raise RuntimeError("No se pudo leer APP_VERSION de descargador2.py.")
    return match.group(1)


def validate_version(value):
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value.strip()):
        raise ValueError("Usá una versión con formato X.Y.Z, por ejemplo 2.2.0.")
    return value.strip()


def hash_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def request_json(token, method, path, payload=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.github.com{path}", data=data, method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2026-03-10",
            "User-Agent": USER_AGENT,
            **({"Content-Type": "application/json"} if data is not None else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            raw = response.read()
            return json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as exc:
        details = exc.read(8192).decode("utf-8", "replace")
        try:
            details = json.loads(details).get("message", details)
        except (ValueError, AttributeError):
            pass
        raise RuntimeError(f"GitHub respondió {exc.code}: {details}") from exc


def upload_asset(token, upload_url, path, progress):
    parsed = urllib.parse.urlsplit(upload_url.split("{", 1)[0])
    query = urllib.parse.urlencode({"name": path.name})
    request_path = f"{parsed.path}?{query}"
    size = path.stat().st_size
    connection = http.client.HTTPSConnection(parsed.hostname, timeout=180)
    try:
        connection.putrequest("POST", request_path)
        connection.putheader("Accept", "application/vnd.github+json")
        connection.putheader("Authorization", f"Bearer {token}")
        connection.putheader("X-GitHub-Api-Version", "2026-03-10")
        connection.putheader("User-Agent", USER_AGENT)
        connection.putheader("Content-Type", "application/octet-stream")
        connection.putheader("Content-Length", str(size))
        connection.endheaders()
        sent = 0
        with path.open("rb") as file:
            while True:
                chunk = file.read(4 * 1024 * 1024)
                if not chunk:
                    break
                connection.send(chunk)
                sent += len(chunk)
                progress(sent, size)
        response = connection.getresponse()
        raw = response.read(16 * 1024)
        if response.status not in (200, 201):
            try:
                reason = json.loads(raw.decode("utf-8", "replace")).get("message", raw.decode("utf-8", "replace"))
            except (ValueError, AttributeError):
                reason = raw.decode("utf-8", "replace")
            raise RuntimeError(f"No se pudo subir {path.name} ({response.status}): {reason}")
        return json.loads(raw.decode("utf-8"))
    finally:
        connection.close()


def build_manifest(version, notes, installer, winlator):
    release_base = f"https://github.com/{REPOSITORY}/releases/download/v{version}"
    manifest = {
        "version": version,
        "installer_url": f"{release_base}/{WINDOWS_PATTERN.format(version=version)}",
        "installer_sha256": hash_file(installer),
        "sha256": hash_file(installer),
        "winlator_url": f"{release_base}/{WINLATOR_NAME}",
        "winlator_sha256": hash_file(winlator),
        "notes": notes.strip()[:4000] or "Actualización de Corneteros Studio.",
    }
    path = ROOT / "latest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def public_source_paths():
    return [ROOT / relative for relative in SOURCE_FILES if (ROOT / relative).is_file()]


class ReleaseManager(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        self.title("Corneteros Studio · Gestor de versiones")
        self.geometry("790x810")
        self.minsize(720, 700)
        self.configure(fg_color=BLACK)
        self.events = queue.Queue()
        self.busy = False

        header = ctk.CTkFrame(self, fg_color=BLACK)
        header.pack(fill="x", padx=26, pady=(24, 12))
        try:
            from PIL import Image
            logo_file = ROOT / "logo_cs_fondo_negro.png"
            if logo_file.is_file():
                self.logo = ctk.CTkImage(
                    Image.open(logo_file), size=(66, 66)
                )
                ctk.CTkLabel(header, text="", image=self.logo).pack(side="left", padx=(0, 14))
        except Exception:
            self.logo = None
        title_frame = ctk.CTkFrame(header, fg_color=BLACK)
        title_frame.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(
            title_frame, text="GESTOR DE VERSIONES", text_color=WHITE,
            font=("Segoe UI", 20, "bold"), anchor="w"
        ).pack(anchor="w")
        ctk.CTkLabel(
            title_frame, text="Compilá Windows y Winlator, y prepará la actualización automática.",
            text_color=MUTED, font=("Segoe UI", 11), anchor="w"
        ).pack(anchor="w", pady=(4, 0))

        card = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=12)
        card.pack(fill="x", padx=26, pady=8)
        ctk.CTkLabel(card, text="VERSIÓN Y NOVEDADES", text_color=WHITE,
                     font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=18, pady=(16, 6))
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=16)
        self.version = ctk.CTkEntry(
            row, height=38, width=150, fg_color="#080808", border_color="#38383d",
            text_color=WHITE, placeholder_text="2.2.0"
        )
        self.version.insert(0, current_version())
        self.version.pack(side="left", padx=(0, 8))
        self._button(row, "Cambiar V", self.save_version, width=112).pack(side="left", padx=4)
        self._button(row, "Subir patch", self.increment_patch, width=112).pack(side="left", padx=4)
        ctk.CTkLabel(
            row, text=f"Versión del cliente: {current_version()}", text_color=MUTED,
            font=("Segoe UI", 10)
        ).pack(side="left", padx=12)
        self.notes = ctk.CTkTextbox(
            card, height=82, fg_color="#080808", border_color="#38383d",
            border_width=1, text_color=WHITE, font=("Segoe UI", 11), wrap="word"
        )
        self.notes.pack(fill="x", padx=16, pady=(10, 16))
        self.notes.insert("1.0", "Mejoras de descarga y vista previa. Actualizador automático para Windows y Winlator.")

        actions = ctk.CTkFrame(self, fg_color=BLACK)
        actions.pack(fill="x", padx=26, pady=(10, 6))
        self.build_button = self._button(
            actions, "Compilar Windows + Winlator", self.compile_release,
            width=235, height=42, primary=True
        )
        self.build_button.pack(side="left", padx=(0, 9))
        self.source_button = self._button(
            actions, "Subir código a GitHub", self.upload_source,
            width=180, height=42
        )
        self.source_button.pack(side="left", padx=4)
        self.publish_button = self._button(
            actions, "Actualizar a última V", self.publish_release,
            width=190, height=42, primary=True
        )
        self.publish_button.pack(side="right", padx=(9, 0))

        token_card = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=12)
        token_card.pack(fill="x", padx=26, pady=(8, 7))
        ctk.CTkLabel(
            token_card, text="TOKEN DE GITHUB · SOLO SE USA EN ESTA SESIÓN",
            text_color=WHITE, font=("Segoe UI", 10, "bold")
        ).pack(anchor="w", padx=16, pady=(12, 5))
        self.token = ctk.CTkEntry(
            token_card, height=36, show="●", fg_color="#080808",
            border_color="#38383d", text_color=WHITE,
            placeholder_text="Pegá un Fine-grained token con permiso Contents: Read and write"
        )
        self.token.pack(fill="x", padx=16, pady=(0, 12))

        self.status = ctk.CTkLabel(
            self, text="1) Compilá.  2) Subí el código inicial una vez.  3) Publicá la versión.",
            text_color=MUTED, anchor="w", font=("Segoe UI", 10)
        )
        self.status.pack(fill="x", padx=28, pady=(6, 4))
        self.log = ctk.CTkTextbox(
            self, fg_color="#070707", border_color="#29292e", border_width=1,
            text_color="#d4d4d8", font=("Consolas", 10), wrap="word"
        )
        self.log.pack(fill="both", expand=True, padx=26, pady=(4, 20))
        self._write_log(f"Repositorio configurado: https://github.com/{REPOSITORY}\n")
        self.after(100, self._drain_events)

    def _button(self, parent, text, command, width=130, height=36, primary=False):
        return ctk.CTkButton(
            parent, text=text, command=command, width=width, height=height,
            fg_color=BLUE if primary else "#242428",
            hover_color="#327df0" if primary else "#34343a",
            text_color="#ffffff", font=("Segoe UI", 11, "bold"), corner_radius=8
        )

    def _write_log(self, text):
        self.log.insert("end", text.rstrip() + "\n")
        self.log.see("end")

    def _event(self, kind, value=None):
        self.events.put((kind, value))

    def _drain_events(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "log":
                    self._write_log(value)
                elif kind == "status":
                    self.status.configure(text=value, text_color=WHITE)
                elif kind == "done":
                    self.busy = False
                    for button in (self.build_button, self.source_button, self.publish_button):
                        button.configure(state="normal")
                    self._write_log(value)
                    self.status.configure(text="Listo.", text_color="#a7f3d0")
                elif kind == "failed":
                    self.busy = False
                    for button in (self.build_button, self.source_button, self.publish_button):
                        button.configure(state="normal")
                    self._write_log("ERROR: " + str(value))
                    self.status.configure(text="La acción terminó con un error. Revisá el registro.", text_color="#ff8585")
        except queue.Empty:
            pass
        self.after(100, self._drain_events)

    def _start(self, label, worker):
        if self.busy:
            return
        self.busy = True
        self.status.configure(text=label, text_color=WHITE)
        for button in (self.build_button, self.source_button, self.publish_button):
            button.configure(state="disabled")
        threading.Thread(target=worker, name="release-manager", daemon=True).start()

    def _version_and_notes(self):
        return validate_version(self.version.get()), self.notes.get("1.0", "end").strip()

    def save_version(self):
        try:
            version, _ = self._version_and_notes()
            old_source = APP_SOURCE.read_text(encoding="utf-8")
            old_iss = ISS_FILE.read_text(encoding="utf-8")
            new_source, count_source = re.subn(
                r'^APP_VERSION\s*=\s*"[^"]+"',
                f'APP_VERSION = "{version}"', old_source, count=1, flags=re.M
            )
            new_iss, count_iss = re.subn(
                r'(?m)^#define AppVersion "[^"]+"$',
                f'#define AppVersion "{version}"', old_iss, count=1
            )
            if count_source != 1 or count_iss != 1:
                raise RuntimeError("No se pudo sincronizar la versión en Python e Inno Setup.")
            APP_SOURCE.write_text(new_source, encoding="utf-8")
            ISS_FILE.write_text(new_iss, encoding="utf-8")
            self._write_log(f"Versión sincronizada en la app y el instalador: {version}")
            messagebox.showinfo("Versión guardada", f"La versión {version} quedó aplicada al proyecto.", parent=self)
        except Exception as exc:
            messagebox.showerror("No se pudo cambiar la versión", str(exc), parent=self)

    def increment_patch(self):
        try:
            version = validate_version(self.version.get())
            major, minor, patch = (int(part) for part in version.split("."))
            self.version.delete(0, "end")
            self.version.insert(0, f"{major}.{minor}.{patch + 1}")
        except Exception as exc:
            messagebox.showerror("Versión inválida", str(exc), parent=self)

    def compile_release(self):
        try:
            version, notes = self._version_and_notes()
        except Exception as exc:
            messagebox.showerror("Revisá la versión", str(exc), parent=self)
            return
        if not messagebox.askyesno(
            "Compilar versión",
            f"Se compilarán el instalador negro de Windows y el paquete Winlator para {version}.",
            parent=self,
        ):
            return
        self._start("Compilando el cliente Windows y Winlator…", lambda: self._compile_worker(version, notes))

    def _compile_worker(self, version, notes):
        try:
            old_source = APP_SOURCE.read_text(encoding="utf-8")
            old_iss = ISS_FILE.read_text(encoding="utf-8")
            new_source = re.sub(
                r'^APP_VERSION\s*=\s*"[^"]+"',
                f'APP_VERSION = "{version}"', old_source, count=1, flags=re.M
            )
            new_iss = re.sub(
                r'(?m)^#define AppVersion "[^"]+"$',
                f'#define AppVersion "{version}"', old_iss, count=1
            )
            APP_SOURCE.write_text(new_source, encoding="utf-8")
            ISS_FILE.write_text(new_iss, encoding="utf-8")
            self._event("log", f"Versión fijada en {version}. Iniciando compilar.bat…")
            environment = os.environ.copy()
            environment.update({"CI": "1", "RELEASE_NOTES": notes, "SKIP_PIP_INSTALL": "1"})
            process = subprocess.Popen(
                ["cmd.exe", "/d", "/c", "compilar.bat"], cwd=ROOT,
                env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace", bufsize=1
            )
            for line in process.stdout:
                self._event("log", line.rstrip())
            code = process.wait()
            if code:
                raise RuntimeError(f"compilar.bat terminó con código {code}.")
            installer = ROOT / "installer" / WINDOWS_PATTERN.format(version=version)
            winlator = ROOT / WINLATOR_NAME
            if not installer.is_file() or not winlator.is_file():
                raise RuntimeError("La compilación terminó, pero falta el instalador o el ZIP Winlator.")
            self._event("done", "Compilación terminada. Ya están el instalador, el ZIP y latest.json.")
        except Exception as exc:
            self._event("failed", exc)

    def upload_source(self):
        token = self.token.get().strip()
        files = public_source_paths()
        missing = [name for name in SOURCE_FILES if (ROOT / name).exists() and (ROOT / name) not in files]
        summary = "\n".join(f"• {path.relative_to(ROOT).as_posix()}" for path in files)
        confirm = (
            f"Se sincronizarán {len(files)} archivos fuente y de marca con el repositorio público "
            f"{REPOSITORY}. No se subirán dist/, build/, "
            f"instaladores ni el ZIP grande.\n\n{summary}\n\n¿Subimos estos archivos?"
        )
        if not token:
            messagebox.showerror("Falta el token", "Pegá un token de GitHub antes de subir.", parent=self)
            return
        if not messagebox.askyesno("Subir código fuente público", confirm, parent=self):
            return
        self._start("Preparando el commit inicial en GitHub…", lambda: self._source_worker(token, files))

    def _source_worker(self, token, files):
        try:
            changed, branch = self._sync_source(token, files)
            message = (
                f"Código fuente sincronizado en {REPOSITORY} ({branch}): {changed} archivo(s) actualizado(s)."
                if changed else f"El código de {REPOSITORY} ya está al día ({branch})."
            )
            self._event("done", message)
        except Exception as exc:
            self._event("failed", exc)

    def _sync_source(self, token, files):
        repo = request_json(token, "GET", f"/repos/{REPOSITORY}")
        branch = repo.get("default_branch") or "main"
        parent_sha = None
        remote_blobs = {}
        try:
            ref = request_json(token, "GET", f"/repos/{REPOSITORY}/git/ref/heads/{branch}")
            parent_sha = ref["object"]["sha"]
            commit_data = request_json(token, "GET", f"/repos/{REPOSITORY}/git/commits/{parent_sha}")
            tree_sha = commit_data["tree"]["sha"]
            remote_tree = request_json(
                token, "GET", f"/repos/{REPOSITORY}/git/trees/{tree_sha}?recursive=1"
            )
            remote_blobs = {
                item["path"]: item["sha"] for item in remote_tree.get("tree", [])
                if item.get("type") == "blob"
            }
            if remote_tree.get("truncated"):
                raise RuntimeError("GitHub truncó el árbol remoto; no se modificó el proyecto.")
        except RuntimeError as exc:
            if "404" not in str(exc) and "409" not in str(exc) and "empty" not in str(exc).lower():
                raise

        entries = []
        total = len(files)
        for index, path in enumerate(files, 1):
            relative = path.relative_to(ROOT).as_posix()
            content = path.read_bytes()
            git_object = b"blob " + str(len(content)).encode("ascii") + b"\0" + content
            local_sha = hashlib.sha1(git_object).hexdigest()
            if remote_blobs.get(relative) == local_sha:
                continue
            self._event("status", f"Sincronizando {index}/{total}: {relative}")
            blob = request_json(token, "POST", f"/repos/{REPOSITORY}/git/blobs", {
                "content": base64.b64encode(content).decode("ascii"), "encoding": "base64",
            })
            entries.append({"path": relative, "mode": "100644", "type": "blob", "sha": blob["sha"]})

        if not entries:
            return 0, branch
        tree_payload = {"tree": entries}
        if parent_sha:
            tree_payload["base_tree"] = tree_sha
        tree_result = request_json(token, "POST", f"/repos/{REPOSITORY}/git/trees", tree_payload)
        commit_payload = {
            "message": "Update Corneteros Studio source and release tooling",
            "tree": tree_result["sha"], "parents": [parent_sha] if parent_sha else [],
        }
        commit = request_json(token, "POST", f"/repos/{REPOSITORY}/git/commits", commit_payload)
        if parent_sha:
            request_json(token, "PATCH", f"/repos/{REPOSITORY}/git/refs/heads/{branch}", {
                "sha": commit["sha"], "force": False,
            })
        else:
            request_json(token, "POST", f"/repos/{REPOSITORY}/git/refs", {
                "ref": f"refs/heads/{branch}", "sha": commit["sha"],
            })
        return len(entries), branch

    def publish_release(self):
        token = self.token.get().strip()
        if not token:
            messagebox.showerror("Falta el token", "Pegá un token de GitHub antes de publicar.", parent=self)
            return
        try:
            version, notes = self._version_and_notes()
        except Exception as exc:
            messagebox.showerror("Revisá la versión", str(exc), parent=self)
            return
        installer = ROOT / "installer" / WINDOWS_PATTERN.format(version=version)
        winlator = ROOT / WINLATOR_NAME
        if not installer.is_file() or not winlator.is_file():
            messagebox.showerror(
                "Faltan compilaciones",
                "Primero compilá Windows y Winlator para esta misma versión.", parent=self
            )
            return
        confirm = (
            f"Se sincronizará el código fuente y se publicará Corneteros Studio v{version} "
            f"en el repositorio público {REPOSITORY}.\n\n"
            f"• {installer.name}\n• {winlator.name}\n• latest.json\n\n"
            "Los clientes compatibles descargarán esta versión al abrirse. ¿Publicar ahora?"
        )
        if not messagebox.askyesno("Confirmar publicación pública", confirm, parent=self):
            return
        self._start("Publicando un borrador y subiendo los paquetes…", lambda: self._release_worker(token, version, notes, installer, winlator))

    def _release_worker(self, token, version, notes, installer, winlator):
        release = None
        try:
            self._event("log", "Verificando token y rama de código…")
            repo = request_json(token, "GET", f"/repos/{REPOSITORY}")
            branch = repo.get("default_branch") or "main"
            changed, branch = self._sync_source(token, public_source_paths())
            self._event("log", f"Código fuente listo en {branch}; archivos modificados: {changed}.")
            manifest = build_manifest(version, notes, installer, winlator)

            self._event("log", "Creando release en borrador para evitar que los clientes vean archivos incompletos…")
            release = request_json(token, "POST", f"/repos/{REPOSITORY}/releases", {
                "tag_name": f"v{version}", "target_commitish": branch,
                "name": f"Corneteros Studio v{version}", "body": notes,
                "draft": True, "prerelease": False,
            })
            upload_url = release["upload_url"]
            assets = [installer, winlator, manifest]
            for path in assets:
                self._event("status", f"Subiendo {path.name}…")
                last_bucket = [-1]

                def upload_progress(sent, size, name=path.name, bucket=last_bucket):
                    current_bucket = int(sent * 10 / max(size, 1))
                    if current_bucket != bucket[0]:
                        bucket[0] = current_bucket
                        self._event(
                            "status", f"Subiendo {name}: {min(100, current_bucket * 10)}%"
                        )

                upload_asset(token, upload_url, path, upload_progress)
                self._event("log", f"Subido: {path.name} ({path.stat().st_size / 1048576:.1f} MB)")
            self._event("log", "Publicando el release completo…")
            request_json(token, "PATCH", f"/repos/{REPOSITORY}/releases/{release['id']}", {"draft": False})
            self._event("done", f"Versión v{version} publicada. El actualizador ya la puede detectar.")
        except Exception as exc:
            if release and release.get("id"):
                self._event("log", "El borrador puede quedar guardado en GitHub; revisá Releases antes de reintentar.")
            self._event("failed", exc)


if __name__ == "__main__":
    app = ReleaseManager()
    app.mainloop()
