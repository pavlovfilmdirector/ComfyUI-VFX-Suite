"""
Keshiki VFX Suite - Native Splash Launcher
==========================================
Displays a sleek borderless splash window while ComfyUI initializes,
polls for the local server on 127.0.0.1:8188, and launches the Studio
in dedicated Chrome App mode as soon as it is ready.
"""

import os
import sys
import time
import threading
import subprocess
import urllib.request
import tkinter as tk
from PIL import Image, ImageTk

import json

VFX_SUITE_DIR = os.path.dirname(os.path.abspath(__file__))
_LOCAL_CFG = os.path.join(VFX_SUITE_DIR, "keshiki_local.json")  # written by install.py, not in git


def _local_cfg():
    try:
        with open(_LOCAL_CFG, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _find_comfy_dir():
    cand = [os.environ.get("KESHIKI_COMFY_DIR", ""), _local_cfg().get("comfy_dir", "")]
    parent = os.path.dirname(VFX_SUITE_DIR)
    if os.path.basename(parent).lower() == "custom_nodes":
        cand.append(os.path.dirname(parent))
    for c in cand:
        if c and os.path.isfile(os.path.join(c, "main.py")):
            return c
    return ""


COMFY_DIR = _find_comfy_dir()
PORT = int(os.environ.get("KESHIKI_PORT") or _local_cfg().get("port") or 8188)
STUDIO_URL = f"http://127.0.0.1:{PORT}/vfx/studio/"
LOGO_PATH = os.path.join(VFX_SUITE_DIR, "web", "standalone", "keshiki_mark.png")


def ensure_ollama_running():
    try:
        req = urllib.request.Request("http://127.0.0.1:11434/api/tags", headers={"User-Agent": "KeshikiLauncher/1.0"})
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            if resp.status == 200:
                return True
    except Exception:
        pass
    try:
        env = os.environ.copy()
        if _local_cfg().get("ollama_models"):
            env["OLLAMA_MODELS"] = _local_cfg()["ollama_models"]
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        subprocess.Popen(["ollama", "serve"], env=env, creationflags=flags)
        return True
    except Exception as e:
        print(f"[Launcher] Note: Could not auto-start Ollama: {e}")
        return False


def get_browser_app():
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return None


def is_server_ready():
    try:
        req = urllib.request.Request(STUDIO_URL, headers={"User-Agent": "KeshikiLauncher/1.0"})
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            return resp.status == 200
    except Exception:
        return False


PROFILE_DIR = os.path.join(VFX_SUITE_DIR, "output_cache", "app_profile")
os.makedirs(PROFILE_DIR, exist_ok=True)


def launch_chrome_app():
    browser = get_browser_app()
    if browser:
        flags = [
            browser,
            f"--app={STUDIO_URL}",
            f"--user-data-dir={PROFILE_DIR}",
            "--disable-extensions",
            "--enable-gpu-rasterization",
            "--enable-zero-copy",
            "--ignore-gpu-blocklist",
            "--use-angle=d3d11",
            "--start-maximized"
        ]
        subprocess.Popen(flags)
    else:
        import webbrowser
        webbrowser.open(STUDIO_URL)


class SplashWindow:
    def __init__(self, root):
        self.root = root
        self.root.title("Keshiki VFX Studio")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)

        width = 460
        height = 290
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = (screen_w - width) // 2
        y = (screen_h - height) // 2
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.configure(bg="#0c0c0f")

        # Outer border frame
        border = tk.Frame(self.root, bg="#23232a", bd=1)
        border.pack(fill=tk.BOTH, expand=True)

        inner = tk.Frame(border, bg="#0e0e12")
        inner.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        # Allow dragging window
        inner.bind("<ButtonPress-1>", self.start_move)
        inner.bind("<B1-Motion>", self.do_move)

        # Close button (small top-right)
        close_btn = tk.Label(inner, text="✕", font=("Segoe UI", 10), bg="#0e0e12", fg="#52525b", cursor="hand2")
        close_btn.place(relx=1.0, y=8, anchor="ne", x=-10)
        close_btn.bind("<Button-1>", lambda e: sys.exit(0))
        close_btn.bind("<Enter>", lambda e: close_btn.config(fg="#ef4444"))
        close_btn.bind("<Leave>", lambda e: close_btn.config(fg="#52525b"))

        # Logo
        if os.path.isfile(LOGO_PATH):
            try:
                pil_img = Image.open(LOGO_PATH)
                aspect = pil_img.width / max(1, pil_img.height)
                target_h = 56
                target_w = int(target_h * aspect)
                pil_img = pil_img.resize((target_w, target_h), Image.Resampling.LANCZOS)
                self.logo_photo = ImageTk.PhotoImage(pil_img)
                logo_lbl = tk.Label(inner, image=self.logo_photo, bg="#0e0e12")
                logo_lbl.pack(pady=(28, 12))
            except Exception:
                pass

        # Title & Subtitle
        title_lbl = tk.Label(inner, text="KESHIKI VFX SUITE", font=("Segoe UI", 15, "bold"), fg="#ffffff", bg="#0e0e12")
        title_lbl.pack()

        sub_lbl = tk.Label(inner, text="AI CINEMATIC DIRECTOR & 3D STUDIO", font=("Segoe UI", 8), fg="#71717a", bg="#0e0e12")
        sub_lbl.pack(pady=(2, 22))

        # Progress bar canvas
        self.canvas_w = 360
        self.canvas_h = 6
        self.progress_canvas = tk.Canvas(inner, width=self.canvas_w, height=self.canvas_h, bg="#1e1e24", highlightthickness=0)
        self.progress_canvas.pack(pady=(0, 14))

        self.progress_rect = self.progress_canvas.create_rectangle(0, 0, 20, self.canvas_h, fill="#38bdf8", outline="")

        # Status text
        self.status_var = tk.StringVar(value="Запуск локального сервера ComfyUI...")
        self.status_lbl = tk.Label(inner, textvariable=self.status_var, font=("Segoe UI", 9), fg="#a1a1aa", bg="#0e0e12")
        self.status_lbl.pack()

        # Step animation state
        self.step = 0
        self.server_ready = False
        self.progress_val = 0.08

        self.root.after(100, self.update_animation)
        self.root.after(500, self.check_loop)

    def start_move(self, event):
        self.drag_x = event.x
        self.drag_y = event.y

    def do_move(self, event):
        deltax = event.x - self.drag_x
        deltay = event.y - self.drag_y
        x = self.root.winfo_x() + deltax
        y = self.root.winfo_y() + deltay
        self.root.geometry(f"+{x}+{y}")

    def update_animation(self):
        if not self.server_ready:
            # Gradually advance progress bar toward 85%
            if self.progress_val < 0.85:
                self.progress_val += 0.015
            current_w = max(10, int(self.canvas_w * self.progress_val))
            self.progress_canvas.coords(self.progress_rect, 0, 0, current_w, self.canvas_h)
            self.root.after(300, self.update_animation)

    def check_loop(self):
        if is_server_ready():
            self.server_ready = True
            self.status_var.set("Сервер готов! Открытие Keshiki Studio...")
            self.progress_canvas.coords(self.progress_rect, 0, 0, self.canvas_w, self.canvas_h)
            self.root.update()
            time.sleep(0.4)
            self.launch_app_and_exit()
        else:
            self.step += 1
            if self.step == 6:
                self.status_var.set("Инициализация PyTorch 2.6 и CUDA 12.4...")
            elif self.step == 14:
                self.status_var.set("Загрузка кастомных нод и пайплайнов...")
            elif self.step == 26:
                self.status_var.set(f"Подключение к локальному порту :{PORT}...")
            self.root.after(800, self.check_loop)

    def launch_app_and_exit(self):
        launch_chrome_app()
        self.root.destroy()
        sys.exit(0)


def main():
    # Ensure local Ollama engine is running for AI Director
    ensure_ollama_running()

    # If server is already running, open app immediately and exit
    if is_server_ready():
        print(f"ComfyUI server is already running on :{PORT} - opening the Studio window.")
        print("To apply code changes, use the restart button in the Studio header.")
        launch_chrome_app()
        return

    if not COMFY_DIR:
        print("[Launcher] ComfyUI folder not found. Put this repo into ComfyUI/custom_nodes, "
              "run install.bat, or set KESHIKI_COMFY_DIR.")
        return

    # Start ComfyUI process if not running
    python_exe = _local_cfg().get("python") or sys.executable
    cmd = [
        python_exe,
        os.path.join(COMFY_DIR, "main.py"),
        "--listen", "127.0.0.1",
        "--port", str(PORT),
        "--disable-auto-launch"
    ]

    # Spawn ComfyUI server
    subprocess.Popen(cmd, cwd=COMFY_DIR)

    # Launch GUI Splash
    root = tk.Tk()
    app = SplashWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
