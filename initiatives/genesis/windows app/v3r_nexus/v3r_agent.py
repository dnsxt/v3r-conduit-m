#!/usr/bin/env python3
"""
V3R Autonomous Agent — Grok tray monitor: detect instruction PNG, decode, execute, report.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import ctypes
import winreg
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import numpy as np
import pystray
from PIL import Image, ImageDraw
from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, WebDriverException
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
# Optional: global hotkey
try:
    import keyboard
except ImportError:
    keyboard = None

# -----------------------------------------------------------------------------
# Paths & globals
# -----------------------------------------------------------------------------

BASE_DIR = Path(os.environ.get("USERPROFILE", "C:\\Users\\Public")) / "Desktop" / "v3r_nexus"
CONFIG_PATH = BASE_DIR / "config.json"
STATE_PATH = BASE_DIR / "state.json"
LOGS_DIR = BASE_DIR / "logs"
TEMP_DIR = BASE_DIR / "temp"

config: dict = {}
state: dict = {}
driver: webdriver.Chrome | None = None
log = logging.getLogger("v3r_agent")

stop_monitoring = threading.Event()
monitor_thread: threading.Thread | None = None
tray_icon: pystray.Icon | None = None
_headers_cache: dict[str, str] | None = None
_headers_mtime: float | None = None

CHROME_DEBUG_PORT = 9222
GROK_IMG_SELECTOR = (
    "img.object-cover.relative.z-\\[200\\].w-full.m-0.text-transparent.sm\\:max-h-\\[500px\\]"
)
# Logged-in chrome: profile avatar in header (preferred over chat placeholder alone).
GROK_PFP_SELECTOR = "img.aspect-square.h-full.w-full[alt='pfp']"
GROK_SIGNIN_LOCATOR = (By.CSS_SELECTOR, 'a[href="/sign-in"]')
GROK_SIGNIN_URL = "https://grok.com/sign-in"
RATE_LIMIT_SNIPPETS = ("rate limit", "try again later", "too many requests")

# Heuristics for Cloudflare / bot interstitial (avoid bare "cloudflare" — appears on normal pages).
CF_WALL_HINTS = (
    "checking your browser before accessing",
    "just a moment...",
    "cf-browser-verification",
    "challenges.cloudflare.com",
    "cdn-cgi/challenge",
    "cf-chl-bun",
    "sorry, you have been blocked",
    "why have i been blocked",
    "enable javascript and cookies to continue",
    "error 1020",
    "attention required",
)

ERROR_PATTERNS = {
    "ModuleNotFoundError": {
        "action": "pip_inst",
        "extract": lambda e: re.search(r"No module named ['\"]([^'\"]+)['\"]", e),
    },
    "': not recognized'": {
        "action": "admin_cmd",
        "extract": lambda e: None,
    },
    "Access Denied": {
        "action": "retry_admin",
        "extract": lambda e: None,
    },
    "is not recognized as an internal or external command": {
        "action": "admin_cmd",
        "extract": lambda e: None,
    },
}

REGISTRY_RUN_KEY = (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run")
REGISTRY_VALUE_NAME = "V3RAgent"


# -----------------------------------------------------------------------------
# Directories & config
# -----------------------------------------------------------------------------


def ensure_directories() -> None:
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)


def default_config() -> dict:
    dl = str(Path(os.environ.get("USERPROFILE", "")) / "Downloads")
    return {
        "grok_accounts": [],
        "poll_interval_seconds": 5,
        "download_folder": dl,
        "temp_folder": str(TEMP_DIR),
        "state_file": str(STATE_PATH),
        "headers_file": str(BASE_DIR / "command_headers.txt"),
        "logs_folder": str(LOGS_DIR),
        "chrome_driver_path": None,
        # undetected-chromedriver: patched driver + flags (much harder for Cloudflare to flag than raw Selenium).
        "use_undetected_chrome": True,
        "chrome_binary_path": None,
        "chrome_version_main": None,
        "messages_per_account_limit": 5,
        "debug": False,
        # Extra seconds after Grok navigation before hunting for Sign in / Google (SPA hydration).
        "grok_page_settle_seconds": 4,
        # When a CF-style wall is detected: quit browser, delete %TEMP%\\chrome_debug, restart driver.
        "auto_recover_cloudflare_block": True,
        "max_cf_auto_recoveries": 3,
    }


def save_config(cfg: dict) -> None:
    cfg = {**cfg}
    cfg["temp_folder"] = str(TEMP_DIR)
    cfg["state_file"] = str(STATE_PATH)
    cfg["logs_folder"] = str(LOGS_DIR)
    if "headers_file" not in cfg or not cfg["headers_file"]:
        cfg["headers_file"] = str(BASE_DIR / "command_headers.txt")
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    global config
    config = cfg


def load_config() -> dict:
    global config
    if CONFIG_PATH.is_file():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = {**default_config(), **json.load(f)}
    else:
        config = default_config()
    config["temp_folder"] = str(TEMP_DIR)
    config["state_file"] = str(STATE_PATH)
    config["logs_folder"] = str(LOGS_DIR)
    if "headers_file" not in config or not config["headers_file"]:
        config["headers_file"] = str(BASE_DIR / "command_headers.txt")
    return config


# -----------------------------------------------------------------------------
# Startup registry
# -----------------------------------------------------------------------------


def get_startup_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    py = str(Path(sys.executable).resolve())
    if py.lower().endswith("python.exe"):
        pw = py[:-10] + "pythonw.exe"
        py = pw if Path(pw).is_file() else py
    script = str(Path(__file__).resolve())
    return f'"{py}" "{script}"'


def register_startup_registry() -> None:
    try:
        key = winreg.OpenKey(REGISTRY_RUN_KEY[0], REGISTRY_RUN_KEY[1], 0, winreg.KEY_SET_VALUE)
    except OSError:
        return
    try:
        winreg.SetValueEx(key, REGISTRY_VALUE_NAME, 0, winreg.REG_SZ, get_startup_command())
        log.info("Registered V3RAgent in HKCU Run.")
    finally:
        winreg.CloseKey(key)


# -----------------------------------------------------------------------------
# First-run credentials (Tkinter)
# -----------------------------------------------------------------------------


def run_credential_setup() -> bool:
    import tkinter as tk
    from tkinter import messagebox

    ensure_directories()
    rows: list[dict] = [{"email": "", "password": "", "nickname": ""}]

    root = tk.Tk()
    root.title("V3R Agent - Account Setup")
    root.geometry("520x400")
    root.resizable(True, True)

    frame = tk.Frame(root, padx=10, pady=10)
    frame.pack(fill=tk.BOTH, expand=True)

    tk.Label(frame, text="Add up to 5 Grok accounts (email, password, optional nickname).").pack(
        anchor="w"
    )

    accounts_container = tk.Frame(frame)
    accounts_container.pack(fill=tk.BOTH, expand=True, pady=8)

    entry_widgets: list[tuple[tk.Entry, tk.Entry, tk.Entry]] = []

    def refresh_rows() -> None:
        for w in accounts_container.winfo_children():
            w.destroy()
        entry_widgets.clear()
        for i, row in enumerate(rows):
            lf = tk.LabelFrame(accounts_container, text=f"Account {i + 1}")
            lf.pack(fill=tk.X, pady=4)
            e_email = tk.Entry(lf, width=50)
            e_email.insert(0, row.get("email", ""))
            e_email.pack(fill=tk.X, padx=6, pady=2)
            e_pass = tk.Entry(lf, width=50, show="*")
            e_pass.insert(0, row.get("password", ""))
            e_pass.pack(fill=tk.X, padx=6, pady=2)
            e_nick = tk.Entry(lf, width=50)
            e_nick.insert(0, row.get("nickname", ""))
            e_nick.pack(fill=tk.X, padx=6, pady=2)
            entry_widgets.append((e_email, e_pass, e_nick))

    def add_account() -> None:
        if len(rows) >= 5:
            messagebox.showinfo("Limit", "Maximum 5 accounts.")
            return
        rows.append({"email": "", "password": "", "nickname": ""})
        refresh_rows()

    def save_accounts() -> None:
        accs = []
        for e_email, e_pass, e_nick in entry_widgets:
            email = e_email.get().strip()
            password = e_pass.get()
            nickname = e_nick.get().strip() or None
            if email or password:
                if not email or not password:
                    messagebox.showerror("Validation", "Each account needs both email and password.")
                    return
                accs.append({"email": email, "password": password, "nickname": nickname or ""})
        if not accs:
            messagebox.showerror("Validation", "Enter at least one complete account.")
            return
        cfg = default_config()
        cfg["grok_accounts"] = accs
        save_config(cfg)
        register_startup_registry()
        names = ", ".join(
            a.get("nickname") or a["email"] for a in accs
        )
        messagebox.showinfo("Saved", f"Saved {len(accs)} account(s): {names}")
        root.destroy()

    btn_row = tk.Frame(frame)
    btn_row.pack(fill=tk.X, pady=6)
    tk.Button(btn_row, text="Add account", command=add_account).pack(side=tk.LEFT, padx=4)
    tk.Button(btn_row, text="Save", command=save_accounts).pack(side=tk.LEFT, padx=4)
    tk.Button(btn_row, text="Quit", command=root.destroy).pack(side=tk.RIGHT, padx=4)

    refresh_rows()
    root.mainloop()
    return CONFIG_PATH.is_file() and bool(load_config().get("grok_accounts"))


# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------


def setup_logging() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOGS_DIR / "agent.log"
    level = logging.DEBUG if config.get("debug") else logging.INFO
    logging.basicConfig(
        level=level,
        format="[%(asctime)s] %(levelname)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
        force=True,
    )


def log_error(msg: str) -> None:
    log.error(msg)


# -----------------------------------------------------------------------------
# State
# -----------------------------------------------------------------------------


def default_state() -> dict:
    return {
        "status": "idle",
        "current_image_id": None,
        "current_account_index": 0,
        "current_account_usage": 0,
        "last_processed_image_src": None,
        "pending_instruction": None,
        "pending_result": None,
        "consecutive_errors": 0,
        "completed_cycles": 0,
        "paused": False,
        "last_activity": datetime.now().isoformat(),
        "cooldown_until": None,
        "cf_auto_recovery_count": 0,
    }


def save_state() -> None:
    path = Path(config.get("state_file", STATE_PATH))
    path.parent.mkdir(parents=True, exist_ok=True)
    state["last_activity"] = datetime.now().isoformat()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def load_state() -> None:
    global state
    path = Path(config.get("state_file", STATE_PATH))
    if path.is_file():
        with open(path, "r", encoding="utf-8") as f:
            state = {**default_state(), **json.load(f)}
    else:
        state = default_state()
        save_state()


# -----------------------------------------------------------------------------
# Command headers
# -----------------------------------------------------------------------------


def load_header_library() -> dict[str, str]:
    global _headers_cache, _headers_mtime
    path = Path(config.get("headers_file", BASE_DIR / "command_headers.txt"))
    if not path.is_file():
        _headers_cache = {}
        return {}
    mtime = path.stat().st_mtime
    if _headers_cache is not None and _headers_mtime == mtime:
        return _headers_cache
    headers: dict[str, str] = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("=") or s.startswith("#"):
                continue
            parts = line.split("|", 1)
            if len(parts) < 2:
                continue
            header_name = parts[0].strip().strip("\\/").strip()
            command_template = parts[1].strip()
            if header_name:
                headers[header_name] = command_template
    _headers_cache = headers
    _headers_mtime = mtime
    return headers


# -----------------------------------------------------------------------------
# Image decode
# -----------------------------------------------------------------------------


def decode_bitonal_image(image_path: str | Path) -> str:
    """512×512 bitonal PNG: black=0, white=1, row-major MSB-first, UTF-8."""
    im = Image.open(image_path).convert("1")
    pixels = np.array(im)
    flat = pixels.flatten()
    if flat.size != 512 * 512:
        log.warning("Expected 512×512 bitonal image, got %s", flat.shape)
    if flat.max() <= 1:
        bits = flat.astype(np.uint8)
    else:
        bits = (flat > 0).astype(np.uint8)
    bit_string = "".join(str(int(b)) for b in bits)
    if len(bit_string) % 8 != 0:
        pad = 8 - (len(bit_string) % 8)
        bit_string += "0" * pad
    n_bytes = len(bit_string) // 8
    byte_data = int(bit_string, 2).to_bytes(n_bytes, "big")
    return byte_data.decode("utf-8", errors="replace")


# -----------------------------------------------------------------------------
# Download helpers
# -----------------------------------------------------------------------------


def wait_for_download(download_folder: str, timeout: float = 30.0) -> str:
    start = time.time()
    dl = Path(download_folder)
    if not dl.is_dir():
        dl.mkdir(parents=True, exist_ok=True)
    initial = set(os.listdir(download_folder))
    stable: dict[str, tuple[float, int]] = {}

    while time.time() - start < timeout:
        current = set(os.listdir(download_folder))
        new_files = current - initial

        for name in new_files:
            if not name.endswith(".png") or name.endswith(".crdownload"):
                continue
            p = dl / name
            if not p.is_file():
                continue
            if any(n == name + ".crdownload" or n.endswith(".crdownload") for n in current):
                continue
            try:
                sz = p.stat().st_size
            except OSError:
                continue
            if sz == 0:
                continue
            t = time.time()
            if name in stable:
                t0, z0 = stable[name]
                if sz == z0 and (t - t0) >= 0.4:
                    return str(p.resolve())
            else:
                stable[name] = (t, sz)
        time.sleep(0.5)

    raise TimeoutError("Download did not complete within timeout")


# -----------------------------------------------------------------------------
# Shell execution
# -----------------------------------------------------------------------------


def get_shell_type(cmd_name: str) -> str:
    if cmd_name.startswith("admin_"):
        return "admin"
    ps_names = {
        "ps",
        "s2_ps",
        "2nd_ps",
        "ps_profile",
        "ps_exec_policy",
        "ps_get_history",
        "ps_clear_history",
        "ps_alias",
        "ps_measure",
        "ps_export_csv",
        "ps_out_file",
        "ps_invoke_web",
        "ps_invoke_rest",
    }
    if cmd_name in ps_names:
        return "powershell"
    if cmd_name in ("cmd", "s2_cmd", "2nd_cmd"):
        return "cmd"
    if cmd_name in ("wsl", "s2_wsl", "2nd_wsl"):
        return "wsl"
    return "powershell"


def execute_powershell(command: str) -> dict:
    proc = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        capture_output=True,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    ok = proc.returncode == 0
    err = proc.stderr or ""
    out = proc.stdout or ""
    return {
        "success": ok,
        "stdout": out,
        "stderr": err,
        "error": err if not ok else "",
    }


def execute_cmd(command: str) -> dict:
    proc = subprocess.run(
        ["cmd.exe", "/d", "/s", "/c", command],
        capture_output=True,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    ok = proc.returncode == 0
    err = proc.stderr or ""
    out = proc.stdout or ""
    return {
        "success": ok,
        "stdout": out,
        "stderr": err,
        "error": err if not ok else "",
    }


def execute_wsl(command: str) -> dict:
    proc = subprocess.run(
        ["wsl.exe", "-e", "bash", "-lc", command],
        capture_output=True,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    ok = proc.returncode == 0
    err = proc.stderr or ""
    out = proc.stdout or ""
    return {
        "success": ok,
        "stdout": out,
        "stderr": err,
        "error": err if not ok else "",
    }


def execute_admin(command: str) -> dict:
    ps_script = (
        f'Start-Process powershell.exe -ArgumentList '
        f'"-NoProfile","-ExecutionPolicy","Bypass","-Command","{command.replace(chr(34), chr(39))}" '
        f"-Verb RunAs -Wait"
    )
    return execute_powershell(ps_script)


def run_in_shell(command: str, shell_type: str) -> dict:
    if shell_type == "powershell":
        return execute_powershell(command)
    if shell_type == "cmd":
        return execute_cmd(command)
    if shell_type == "wsl":
        return execute_wsl(command)
    if shell_type == "admin":
        return execute_admin(command)
    return execute_powershell(command)


def execute_instruction(instruction_json: dict, headers: dict) -> dict:
    cmd_name = instruction_json["instruction"]
    data = instruction_json.get("data", "")
    template = headers.get(cmd_name)
    if not template:
        return {"success": False, "error": f"Unknown command: {cmd_name}", "stdout": "", "stderr": ""}
    command = template.replace("{data}", data)
    shell_type = get_shell_type(cmd_name)
    return run_in_shell(command, shell_type)


def handle_error(
    error_output: str,
    failed_command: str | None,
    instruction_json: dict,
    headers: dict,
) -> dict:
    for pattern, recovery in ERROR_PATTERNS.items():
        if pattern not in error_output:
            continue
        action = recovery["action"]
        if action == "pip_inst":
            m = recovery["extract"](error_output)
            mod = m.group(1) if m else "unknown"
            install_json = {"instruction": "pip_inst", "data": mod}
            ir = execute_instruction(install_json, headers)
            if ir.get("success"):
                return execute_instruction(instruction_json, headers)
            return ir
        if action == "admin_cmd":
            admin_name = f"admin_{instruction_json['instruction']}"
            admin_json = {
                "instruction": admin_name,
                "data": instruction_json.get("data", ""),
            }
            return execute_instruction(admin_json, headers)
        if action == "retry_admin":
            admin_json = {
                "instruction": f"admin_{instruction_json['instruction']}",
                "data": instruction_json.get("data", ""),
            }
            return execute_instruction(admin_json, headers)
    return {
        "success": False,
        "error": error_output,
        "stdout": "",
        "stderr": error_output,
    }


# -----------------------------------------------------------------------------
# Chrome / Selenium
# -----------------------------------------------------------------------------


def _chrome_debug_listening(timeout: float = 1.0) -> bool:
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{CHROME_DEBUG_PORT}/json/version",
            headers={"Connection": "close"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def _wait_debug_port_ready(total_s: float = 25.0) -> bool:
    """Poll until Chrome answers on the debug port (bounded wait, fast poll)."""
    deadline = time.time() + total_s
    while time.time() < deadline:
        if _chrome_debug_listening():
            return True
        time.sleep(0.25)
    return False


def _ensure_chrome_debug_port(reason: str = "") -> None:
    """Chrome must be listening before chromedriver attaches; otherwise each attach can hang ~60s."""
    if _chrome_debug_listening():
        return
    if reason:
        log.info("Debug port 9222 closed (%s); starting Chrome.", reason)
    _launch_chrome_debug()


def _automation_chrome_profile() -> Path:
    """Dedicated profile for Grok automation (wipe with v3r_prepare_browser.py if Cloudflare blocks)."""
    return Path(os.environ.get("TEMP", "C:\\Temp")) / "chrome_debug"


def _find_chrome_exe() -> str:
    for candidate in (
        os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(
            os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
            "Google",
            "Chrome",
            "Application",
            "chrome.exe",
        ),
    ):
        if os.path.isfile(candidate):
            return candidate
    return "chrome.exe"


def _launch_chrome_debug() -> None:
    """Start one Chrome with remote debugging. Does not open grok.com here — doing so on every
    re-launch stacks extra Grok tabs when the browser is already running. Navigation is via Selenium."""
    if _chrome_debug_listening():
        log.info(
            "Chrome debug port %s already active; skipping new chrome.exe spawn.",
            CHROME_DEBUG_PORT,
        )
        return
    user_data = _automation_chrome_profile()
    user_data.mkdir(parents=True, exist_ok=True)
    exe = _find_chrome_exe()
    subprocess.Popen(
        [
            exe,
            f"--remote-debugging-port={CHROME_DEBUG_PORT}",
            f"--user-data-dir={user_data}",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    if not _wait_debug_port_ready():
        log.warning(
            "Chrome debug port did not become ready within timeout; Selenium attach may fail."
        )


def _init_driver_legacy_remote_attach() -> webdriver.Chrome:
    """Original flow: plain Chrome + debugger port + stock Selenium (easier for bot checks to spot)."""
    global driver
    _ensure_chrome_debug_port("before Selenium attach")

    options = webdriver.ChromeOptions()
    options.add_experimental_option("debuggerAddress", f"127.0.0.1:{CHROME_DEBUG_PORT}")

    custom = (config.get("chrome_driver_path") or "").strip()
    service = Service(custom) if custom else Service()

    last_err: Exception | None = None
    for attempt in range(1, 4):
        try:
            d = webdriver.Chrome(service=service, options=options)
            d.implicitly_wait(2)
            _ = d.current_url
            driver = d
            log.info("Attached Selenium to Chrome on 127.0.0.1:%s.", CHROME_DEBUG_PORT)
            return d
        except WebDriverException as e:
            last_err = e
            log.warning("Chrome attach attempt %s/3 failed: %s", attempt, e)
            msg = str(e).lower()
            if "only supports chrome version" in msg or (
                "chromedriver" in msg and "version" in msg
            ):
                log.warning(
                    "ChromeDriver does not match Chrome. Run v3r_prepare_browser.py or set chrome_driver_path."
                )
            _ensure_chrome_debug_port("after attach failure")
            time.sleep(1.0 + attempt * 0.4)

    raise RuntimeError(
        "Could not attach Selenium to Chrome on 127.0.0.1:%s." % CHROME_DEBUG_PORT
    ) from last_err


def _init_driver_undetected() -> webdriver.Chrome:
    """Launch Chrome through undetected-chromedriver (not remote-attach); reduces automation signals."""
    global driver
    import undetected_chromedriver as uc

    profile = _automation_chrome_profile()
    profile.mkdir(parents=True, exist_ok=True)
    opts = uc.ChromeOptions()

    custom_driver = (config.get("chrome_driver_path") or "").strip()
    custom_browser = (config.get("chrome_binary_path") or "").strip()

    kw: dict = {
        "options": opts,
        "user_data_dir": str(profile),
        "use_subprocess": True,
    }
    if custom_driver:
        kw["driver_executable_path"] = custom_driver
    if custom_browser:
        kw["browser_executable_path"] = custom_browser

    vm = config.get("chrome_version_main")
    if vm is not None:
        try:
            kw["version_main"] = int(vm)
        except (TypeError, ValueError):
            log.warning("chrome_version_main ignored (not an int): %s", vm)

    try:
        d = uc.Chrome(**kw)
    except Exception as e:
        log.error("undetected-chromedriver failed to start Chrome: %s", e)
        raise RuntimeError(
            "Stealth browser failed to start. Install Google Chrome, run python v3r_prepare_browser.py, "
            "and ensure undetected-chromedriver is up to date."
        ) from e

    d.implicitly_wait(2)
    driver = d
    log.info(
        "Chrome running via undetected-chromedriver (profile %s). "
        "If Cloudflare blocks, run v3r_prepare_browser.py to wipe this profile.",
        profile,
    )
    return d


def init_driver() -> webdriver.Chrome:
    global driver
    use_uc = bool(config.get("use_undetected_chrome", True))
    if use_uc:
        try:
            return _init_driver_undetected()
        except ImportError:
            log.warning(
                "undetected-chromedriver is missing. Install: pip install undetected-chromedriver "
                "or run v3r_prepare_browser.py"
            )
        except RuntimeError:
            raise
        except Exception as e:
            log.warning("undetected-chrome failed (%s); falling back to legacy attach.", e)

    return _init_driver_legacy_remote_attach()


def is_driver_alive(d: webdriver.Chrome | None) -> bool:
    if d is None:
        return False
    try:
        _ = d.current_url
        return True
    except Exception:
        return False


def chat_input_present(d: webdriver.Chrome, timeout: float = 15.0) -> bool:
    try:
        WebDriverWait(d, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "p[data-placeholder='Ask anything']"))
        )
        return True
    except Exception:
        return False


def grok_profile_present(d: webdriver.Chrome, timeout: float = 5.0) -> bool:
    """Header profile image indicates an authenticated Grok session."""
    try:
        WebDriverWait(d, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, GROK_PFP_SELECTOR))
        )
        return True
    except Exception:
        return False


def grok_session_ready(d: webdriver.Chrome, timeout: float = 5.0) -> bool:
    """True if logged in (pfp) or main chat is already usable."""
    if grok_profile_present(d, timeout=min(timeout, 2.0)):
        return True
    return chat_input_present(d, timeout=timeout)


def _switch_to_newest_window(d: webdriver.Chrome, previous_handles: set[str]) -> None:
    time.sleep(0.3)
    for h in reversed(d.window_handles):
        if h not in previous_handles:
            d.switch_to.window(h)
            return
    if d.window_handles:
        d.switch_to.window(d.window_handles[-1])


def _wait_grok_page_ready(drv: webdriver.Chrome, settle_s: float) -> None:
    try:
        WebDriverWait(drv, 45).until(
            lambda x: x.execute_script("return document.readyState") == "complete"
        )
    except Exception:
        pass
    time.sleep(max(0.0, settle_s))


def login_to_grok(account: dict, d: webdriver.Chrome) -> None:
    """
    Grok uses Sign in -> Login with Google -> Google account picker (data-email).
    Stored password is not used for this OAuth path; account email selects the session.
    """
    email = (account.get("email") or "").strip()
    if not email:
        raise ValueError("Account email is required for Google login.")

    settle = float(config.get("grok_page_settle_seconds", 4))
    d.get("https://grok.com")
    _wait_grok_page_ready(d, settle)

    if grok_session_ready(d, timeout=8):
        log.info("Grok session already active (pfp or chat).")
        return

    # 1) Sign-in screen: navigate directly. Clicking the <a href="/sign-in"> can use
    # target="_blank" and spawns extra Grok tabs; same-tab navigation avoids that.
    prev_handles = set(d.window_handles)
    try:
        d.get(GROK_SIGNIN_URL)
        _wait_grok_page_ready(d, settle)
    except Exception as e:
        log.warning("Direct /sign-in navigation failed: %s; trying Sign in control.", e)
        try:
            sign_in = WebDriverWait(d, 20).until(EC.element_to_be_clickable(GROK_SIGNIN_LOCATOR))
        except Exception:
            sign_in = WebDriverWait(d, 10).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//a[contains(@href,'sign-in')][contains(.,'Sign in')]")
                )
            )
        d.execute_script("arguments[0].scrollIntoView({block: 'center'});", sign_in)
        time.sleep(0.5)
        sign_in.click()
        time.sleep(2)
        _switch_to_newest_window(d, prev_handles)

    prev_handles = set(d.window_handles)

    # 2) Login with Google
    try:
        google_btn = WebDriverWait(d, 25).until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    "//button[.//span[contains(normalize-space(), 'Login with Google')]]",
                )
            )
        )
        d.execute_script("arguments[0].scrollIntoView({block: 'center'});", google_btn)
        time.sleep(0.2)
        google_btn.click()
    except Exception as e:
        log.warning("Login with Google not found: %s", e)
        if grok_session_ready(d, timeout=8):
            return
        raise RuntimeError("Could not click Login with Google.") from e

    time.sleep(2)
    _switch_to_newest_window(d, prev_handles)

    # 3) Google account list — div[data-email="…"]
    acc_sel = f'div[data-email="{email}"]'
    try:
        account_tile = WebDriverWait(d, 35).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, acc_sel))
        )
        d.execute_script("arguments[0].scrollIntoView({block: 'center'});", account_tile)
        time.sleep(0.2)
        try:
            account_tile.click()
        except Exception:
            d.execute_script("arguments[0].click();", account_tile)
    except Exception as e:
        log.warning("Account tile for %s not clickable: %s", email, e)
        raise RuntimeError(
            f"Could not select Google account {email!r}. "
            "Ensure this account appears on the chooser for this Chrome profile."
        ) from e

    # Return focus to Grok tab if OAuth opened a new window
    time.sleep(2)
    for h in d.window_handles:
        d.switch_to.window(h)
        if "grok.com" in (d.current_url or ""):
            break
    else:
        d.switch_to.window(d.window_handles[0])

    # Success: profile avatar on Grok (retry load if still on redirect)
    deadline = time.time() + 90
    while time.time() < deadline:
        if grok_profile_present(d, timeout=3):
            log.info("Grok login OK (profile image visible).")
            break
        if chat_input_present(d, timeout=2):
            log.info("Grok login OK (chat input visible).")
            break
        time.sleep(1)
    else:
        raise RuntimeError("Login did not show Grok profile (pfp) or chat in time.")

    # Land on main app so chat automation is reliable
    if not chat_input_present(d, timeout=8):
        d.get("https://grok.com")
        time.sleep(2)
    if not chat_input_present(d, timeout=25) and not grok_profile_present(d, timeout=3):
        raise RuntimeError("After login, chat input did not become available.")


def ensure_logged_in(d: webdriver.Chrome, account: dict) -> None:
    if grok_session_ready(d, timeout=4):
        return
    login_to_grok(account, d)


def page_has_rate_limit(d: webdriver.Chrome) -> bool:
    try:
        body = (d.page_source or "").lower()
    except Exception:
        return False
    return any(s in body for s in RATE_LIMIT_SNIPPETS)


def page_has_cloudflare_or_bot_wall(d: webdriver.Chrome) -> bool:
    try:
        src = (d.page_source or "").lower()
        url = (d.current_url or "").lower()
        title = (d.title or "").lower()
    except Exception:
        return False
    blob = f"{src}\n{url}\n{title}"
    return any(h in blob for h in CF_WALL_HINTS)


def wipe_automation_chrome_profile() -> None:
    """Delete dedicated automation profile (same as v3r_prepare_browser.py core step)."""
    profile = _automation_chrome_profile()
    if not profile.exists():
        return
    try:
        shutil.rmtree(profile)
        log.info("Removed automation Chrome profile: %s", profile)
    except OSError as e:
        log.error("Could not remove profile %s (close Chrome using it): %s", profile, e)


def recover_browser_after_cf_wall() -> None:
    """Quit Selenium, wipe profile so the next init_driver() gets a clean stealth session."""
    global driver
    try:
        if driver:
            driver.quit()
    except Exception:
        pass
    driver = None
    wipe_automation_chrome_profile()
    time.sleep(1.5)


# -----------------------------------------------------------------------------
# Grok DOM: image detect / download / report
# -----------------------------------------------------------------------------


def check_for_new_image(d: webdriver.Chrome, last_seen_src: str | None):
    try:
        images = d.find_elements(By.CSS_SELECTOR, GROK_IMG_SELECTOR)
    except Exception:
        return None, last_seen_src
    for img in images:
        src = img.get_attribute("src")
        if src and src != last_seen_src:
            return img, src
    return None, last_seen_src


def download_image(img_element, d: webdriver.Chrome, temp_folder: str) -> str:
    d.execute_script("arguments[0].scrollIntoView({block: 'center'});", img_element)
    time.sleep(0.5)
    img_element.click()
    time.sleep(2)
    download_btn = WebDriverWait(d, 15).until(
        EC.element_to_be_clickable(
            (
                By.CSS_SELECTOR,
                "button.inline-flex.items-center.justify-center.gap-2.whitespace-nowrap.font-medium.cursor-pointer",
            )
        )
    )
    download_btn.click()
    time.sleep(1)
    try:
        close_btn = d.find_element(By.CSS_SELECTOR, "svg.stroke-\\[2\\].size-5")
        close_btn.click()
    except NoSuchElementException:
        d.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
    time.sleep(1)
    downloaded_file = wait_for_download(config["download_folder"])
    dest_path = os.path.join(temp_folder, "latest_image.png")
    shutil.move(downloaded_file, dest_path)
    return dest_path


def report_to_grok(success: bool, result: dict, d: webdriver.Chrome) -> None:
    if success:
        full_message = "Completed. What is the single next concrete step?"
    else:
        error_msg = result.get("error") or result.get("stderr") or "Unknown error"
        if len(error_msg) > 2000:
            error_msg = error_msg[:2000] + "..."
        full_message = f"Error: {error_msg}"
    input_area = WebDriverWait(d, 15).until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, "p[data-placeholder='Ask anything']"))
    )
    input_area.click()
    time.sleep(0.3)
    try:
        input_area.clear()
    except Exception:
        pass
    for char in full_message:
        input_area.send_keys(char)
        time.sleep(0.01)
    send_btn = WebDriverWait(d, 10).until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, "svg.stroke-\\[2\\].relative"))
    )
    send_btn.click()
    state["current_account_usage"] = state.get("current_account_usage", 0) + 1
    state["last_activity"] = datetime.now().isoformat()
    save_state()
    time.sleep(5)


# -----------------------------------------------------------------------------
# Main monitoring loop
# -----------------------------------------------------------------------------


def rotate_account(reason: str = "") -> None:
    accounts = config.get("grok_accounts") or []
    if len(accounts) <= 1:
        return
    global driver
    idx = (state.get("current_account_index", 0) + 1) % len(accounts)
    state["current_account_index"] = idx
    state["current_account_usage"] = 0
    save_state()
    log.warning("Rotating Grok account (%s) -> index %s", reason, idx)
    try:
        if driver:
            driver.quit()
    except Exception:
        pass
    driver = None


def all_accounts_exhausted_wait() -> None:
    """After cycling all accounts, wait 1 hour before continuing."""
    until = datetime.now().timestamp() + 3600
    state["cooldown_until"] = datetime.fromtimestamp(until).isoformat()
    save_state()
    log.info("All accounts at limit; cooling down 1 hour until %s", state["cooldown_until"])
    while datetime.now().timestamp() < until:
        if stop_monitoring.is_set():
            return
        time.sleep(5)
    state["cooldown_until"] = None
    state["current_account_index"] = 0
    state["current_account_usage"] = 0
    save_state()


def main_loop_inner() -> None:
    global driver
    load_state()
    accounts = config.get("grok_accounts") or []
    if not accounts:
        log.error("No Grok accounts in config.")
        return

    poll = float(config.get("poll_interval_seconds", 5))
    limit = int(config.get("messages_per_account_limit", 5))

    while not stop_monitoring.is_set():
        if state.get("paused"):
            time.sleep(1)
            continue
        cu = state.get("cooldown_until")
        if cu:
            try:
                if datetime.now() < datetime.fromisoformat(cu):
                    time.sleep(5)
                    continue
            except ValueError:
                state["cooldown_until"] = None
                save_state()

        try:
            if not is_driver_alive(driver):
                driver = init_driver()

            acc = accounts[state["current_account_index"] % len(accounts)]
            ensure_logged_in(driver, acc)

            if grok_session_ready(driver, timeout=3):
                if state.get("cf_auto_recovery_count"):
                    state["cf_auto_recovery_count"] = 0
                    save_state()

            if (
                config.get("auto_recover_cloudflare_block", True)
                and page_has_cloudflare_or_bot_wall(driver)
            ):
                cap = max(1, int(config.get("max_cf_auto_recoveries", 3)))
                n = state.get("cf_auto_recovery_count", 0)
                if n >= cap:
                    log_error(
                        f"Anti-bot wall persists after {cap} automatic profile resets. Paused. "
                        "Run: python v3r_prepare_browser.py — pip, WAN IP, or site policy may still block."
                    )
                    state["paused"] = True
                    save_state()
                    try:
                        ctypes.windll.user32.MessageBoxW(
                            0,
                            "V3R paused: anti-bot page persists. Run v3r_prepare_browser.py and check your network.",
                            "V3R Agent",
                            0x40,
                        )
                    except Exception:
                        pass
                    continue
                state["cf_auto_recovery_count"] = n + 1
                save_state()
                log.warning(
                    "Anti-bot / Cloudflare-style page detected; wiping profile and restarting browser (%s/%s).",
                    n + 1,
                    cap,
                )
                recover_browser_after_cf_wall()
                driver = init_driver()
                continue

            if page_has_rate_limit(driver):
                if len(accounts) <= 1:
                    log.warning("Rate limit detected with a single account; cooling down 1h.")
                    try:
                        if driver:
                            driver.quit()
                    except Exception:
                        pass
                    driver = None
                    all_accounts_exhausted_wait()
                else:
                    rotate_account("rate limit UI")
                continue

            last_src = state.get("last_processed_image_src")
            img_el, img_src = check_for_new_image(driver, last_src)

            if not img_el:
                time.sleep(poll)
                continue

            state["status"] = "processing"
            state["last_processed_image_src"] = img_src
            try:
                state["current_image_id"] = (img_src or "").split("/")[-1].replace(".png", "")
            except Exception:
                state["current_image_id"] = None
            save_state()

            headers = load_header_library()

            state["status"] = "downloading"
            save_state()
            image_path = download_image(img_el, driver, config["temp_folder"])

            state["status"] = "decoding"
            save_state()
            decoded_text = decode_bitonal_image(image_path)
            instruction = json.loads(decoded_text)
            state["pending_instruction"] = instruction
            save_state()

            state["status"] = "executing"
            save_state()
            result = execute_instruction(instruction, headers)
            state["pending_result"] = result
            save_state()

            if not result.get("success"):
                err_text = result.get("stderr", "") + result.get("error", "") + result.get("stdout", "")
                result = handle_error(err_text, None, instruction, headers)

            state["status"] = "reporting"
            save_state()
            report_to_grok(bool(result.get("success")), result, driver)

            if result.get("success"):
                state["consecutive_errors"] = 0
                state["completed_cycles"] = state.get("completed_cycles", 0) + 1
            else:
                state["consecutive_errors"] = state.get("consecutive_errors", 0) + 1
                if state["consecutive_errors"] >= 3:
                    log_error("Too many consecutive errors. Pausing.")
                    state["paused"] = True
                    try:
                        ctypes.windll.user32.MessageBoxW(
                            0,
                            "Too many consecutive errors. Monitoring is paused.",
                            "V3R Agent",
                            0x40,
                        )
                    except Exception:
                        pass

            state["status"] = "idle"
            save_state()

            if state.get("current_account_usage", 0) >= limit:
                n = len(accounts)
                old_idx = state["current_account_index"] % n
                new_idx = (old_idx + 1) % n
                state["current_account_index"] = new_idx
                state["current_account_usage"] = 0
                save_state()
                full_cycle = n > 0 and old_idx == n - 1 and new_idx == 0
                log.info("Message limit reached; rotating account (%s -> %s).", old_idx, new_idx)
                try:
                    if driver:
                        driver.quit()
                except Exception:
                    pass
                driver = None
                if full_cycle:
                    all_accounts_exhausted_wait()
        except Exception as e:
            log_error(f"Main loop error: {e}")
            exc_type, _exc, _tb = sys.exc_info()
            if exc_type is not None:
                log.debug("Exception detail", exc_info=True)
            state["consecutive_errors"] = state.get("consecutive_errors", 0) + 1
            state["status"] = "error"
            save_state()
            time.sleep(10)


def monitoring_thread_target() -> None:
    stop_monitoring.clear()
    try:
        main_loop_inner()
    finally:
        log.info("Monitoring thread stopped.")


def start_monitoring() -> None:
    global monitor_thread
    if monitor_thread and monitor_thread.is_alive():
        log.info("Monitoring already running.")
        return
    monitor_thread = threading.Thread(target=monitoring_thread_target, daemon=True)
    monitor_thread.start()
    log.info("Monitoring started.")


def stop_monitoring_loop() -> None:
    stop_monitoring.set()
    global driver
    try:
        if driver:
            driver.quit()
    except Exception:
        pass
    driver = None
    log.info("Stop requested; driver closed.")


def toggle_pause_tray(_=None) -> None:
    load_state()
    state["paused"] = not state.get("paused", False)
    save_state()
    log.info("Paused=%s", state["paused"])


def resume_monitoring(_=None) -> None:
    load_state()
    state["paused"] = False
    save_state()
    log.info("Resumed monitoring.")


def cycle_account_manual(_=None) -> None:
    load_state()
    rotate_account("manual")
    log.info("Manual account cycle.")


def open_log(_=None) -> None:
    log_path = LOGS_DIR / "agent.log"
    if log_path.is_file():
        os.startfile(str(log_path))  # type: ignore[attr-defined]
    else:
        log.warning("No log file yet.")


def create_tray_icon() -> pystray.Icon:
    size = 64
    img = Image.new("RGBA", (size, size), (30, 144, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.ellipse((8, 8, size - 8, size - 8), fill=(255, 255, 255, 255))
    menu = pystray.Menu(
        pystray.MenuItem("Start Monitoring", lambda: start_monitoring(), default=True),
        pystray.MenuItem("Stop Monitoring", lambda: stop_monitoring_loop()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Pause", lambda: toggle_pause_tray()),
        pystray.MenuItem("Resume", resume_monitoring),
        pystray.MenuItem("Cycle Account", cycle_account_manual),
        pystray.MenuItem("View Log", open_log),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Register startup (registry)", lambda: register_startup_registry()),
        pystray.MenuItem("Exit", lambda: shutdown_app()),
    )
    return pystray.Icon("v3r_agent", img, "V3R Agent", menu)


def shutdown_app() -> None:
    stop_monitoring_loop()
    if tray_icon:
        tray_icon.stop()


def hotkey_insert() -> None:
    toggle_pause_tray()


def setup_global_hotkeys() -> None:
    if not keyboard:
        log.warning("keyboard module not available; Insert hotkey disabled.")
        return
    try:
        keyboard.add_hotkey("insert", hotkey_insert, suppress=False)
        keyboard.add_hotkey("ctrl+shift+s", lambda: start_monitoring(), suppress=False)
        keyboard.add_hotkey("ctrl+shift+x", lambda: stop_monitoring_loop(), suppress=False)
        keyboard.add_hotkey("ctrl+shift+p", lambda: toggle_pause_tray(), suppress=False)
        log.info("Global hotkeys registered (Insert, Ctrl+Shift+S/X/P).")
    except Exception as e:
        log.warning("Could not register global hotkeys: %s", e)


def main() -> None:
    ensure_directories()
    if not CONFIG_PATH.is_file():
        if not run_credential_setup():
            sys.exit(1)
    load_config()
    _hf = Path(config.get("headers_file", BASE_DIR / "command_headers.txt"))
    if not _hf.is_file():
        _tmpl = Path(__file__).resolve().parent / "command_headers_template.txt"
        if _tmpl.is_file():
            _hf.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(_tmpl, _hf)
    setup_logging()
    log.info("V3R Agent starting; base=%s", BASE_DIR)

    global tray_icon
    tray_icon = create_tray_icon()
    register_startup_registry()

    setup_global_hotkeys()

    start_monitoring()

    tray_icon.run()


if __name__ == "__main__":
    main()
