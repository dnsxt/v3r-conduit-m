#!/usr/bin/env python3
"""
V3R Agent — reset automation browser fingerprint state and install what Grok automation needs.

Run this after a Cloudflare (or similar) block, or before first stealth run:
  python v3r_prepare_browser.py

What it does:
  - Stops stray chromedriver.exe processes (best-effort).
  - Deletes the dedicated Chrome profile under %TEMP%\\chrome_debug (cookies, CF bot score, etc.).
  - Clears Selenium Manager caches so drivers are re-resolved cleanly.
  - pip-installs undetected-chromedriver + requirements.txt (optional skips).
  - Optionally installs Google Chrome via winget if missing.

Use --kill-chrome only if no important Chrome windows are open; it force-closes ALL Chrome.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROFILE = Path(os.environ.get("TEMP", os.environ.get("TMP", "C:\\Temp"))) / "chrome_debug"
CACHE_SELENIUM = Path(os.environ.get("USERPROFILE", "")) / ".cache" / "selenium"
WDM_LEGACY = Path(os.environ.get("USERPROFILE", "")) / ".wdm"


def _rmtree(path: Path, label: str) -> None:
    if not path.exists():
        print(f"  (skip) {label}: not found — {path}")
        return
    try:
        shutil.rmtree(path, ignore_errors=False)
        print(f"  (ok)   Removed {label}: {path}")
    except PermissionError:
        print(f"  (!)    Could not remove {label} (files in use): {path}")
        print("         Close every Chrome window that used V3R, or run with --kill-chrome, then retry.")
        sys.exit(1)
    except OSError as e:
        print(f"  (!)    {label}: {e}")


def _chrome_installed() -> bool | Path:
    for base in (
        os.environ.get("PROGRAMFILES", r"C:\Program Files"),
        os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
    ):
        exe = Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"
        if exe.is_file():
            return exe
    return False


def _winget_install_chrome() -> None:
    try:
        r = subprocess.run(
            ["winget", "install", "-e", "--id", "Google.Chrome", "--accept-package-agreements"],
            capture_output=True,
            text=True,
            timeout=600,
        )
        print(r.stdout or "")
        if r.returncode != 0:
            print(r.stderr or "", file=sys.stderr)
            print("winget returned non-zero; install Chrome manually from https://www.google.com/chrome/")
    except FileNotFoundError:
        print("winget not found. Install Google Chrome manually from https://www.google.com/chrome/")
    except subprocess.TimeoutExpired:
        print("winget install timed out.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset V3R browser state and install dependencies.")
    parser.add_argument(
        "--kill-chrome",
        action="store_true",
        help="taskkill /F all chrome.exe (closes EVERY Chrome window — use only if you accept that).",
    )
    parser.add_argument("--skip-pip", action="store_true", help="Do not run pip install.")
    parser.add_argument("--skip-winget", action="store_true", help="Do not try winget to install Chrome.")
    args = parser.parse_args()

    print("V3R prepare browser — clearing automation footprint\n")

    if sys.platform == "win32":
        print("Stopping chromedriver.exe (if any) …")
        subprocess.run(
            ["taskkill", "/F", "/IM", "chromedriver.exe"],
            capture_output=True,
        )
        if args.kill_chrome:
            print("Stopping ALL chrome.exe (--kill-chrome) …")
            subprocess.run(
                ["taskkill", "/F", "/IM", "chrome.exe", "/T"],
                capture_output=True,
            )

    print("\nRemoving local state:")
    _rmtree(PROFILE, "Automation Chrome profile (TEMP\\chrome_debug)")
    _rmtree(CACHE_SELENIUM, "Selenium Manager cache")
    _rmtree(WDM_LEGACY, "Legacy webdriver-manager cache (.wdm)")

    if not args.skip_pip:
        print("\nInstalling / upgrading Python packages …")
        req = HERE / "requirements.txt"
        cmd = [sys.executable, "-m", "pip", "install", "-U"]
        if req.is_file():
            cmd.extend(["-r", str(req)])
        else:
            cmd.extend(
                [
                    "undetected-chromedriver>=3.5.0",
                    "selenium>=4.18.0",
                    "pillow>=10.0.0",
                    "numpy>=1.24.0",
                    "pystray>=0.19.0",
                    "keyboard>=0.13.5",
                ]
            )
        r = subprocess.run(cmd)
        if r.returncode != 0:
            print("pip failed; fix the error above and re-run.", file=sys.stderr)
            sys.exit(r.returncode)

    print("\nChecking Google Chrome …")
    c = _chrome_installed()
    if c:
        print(f"  Found: {c}")
    else:
        print("  Chrome not found under Program Files.")
        if not args.skip_winget:
            print("  Trying winget install Google.Chrome …")
            _winget_install_chrome()
        else:
            print("  Install Chrome from https://www.google.com/chrome/")

    print(
        "\nDone. Start the agent with: python v3r_agent.py\n"
        "Tip: keep use_undetected_chrome true in config.json (default). "
        "If a site still blocks, try a residential IP / less aggressive polling — "
        "no browser is 100%% invisible to Cloudflare."
    )


if __name__ == "__main__":
    main()
