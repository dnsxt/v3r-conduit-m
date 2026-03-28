# V3R Autonomous Agent

Windows system tray agent that watches the Grok web UI for a 512×512 bitonal PNG “instruction” image, downloads it, decodes embedded JSON, runs the mapped command (PowerShell / CMD / WSL / elevated), and posts the result back into the Grok chat.

## Requirements

- Windows 10 or later  
- Python **3.10+** (if running from source)  
- **Google Chrome** (standard install — used by [undetected-chromedriver](https://github.com/ultrafunkamsterdam/undetected-chromedriver))  
- Grok account(s)

### Before first run / after Cloudflare blocks

Stock Selenium + remote debugging is easy for **Cloudflare** (and similar) to classify as automation.

1. Run the prep script once (or again after a block):

   ```bat
   cd /d "%USERPROFILE%\Desktop\v3r_nexus"
   python v3r_prepare_browser.py
   ```

   This removes the automation Chrome profile under `%TEMP%\chrome_debug`, clears old Selenium caches, **`pip install`s** dependencies including **undetected-chromedriver**, and tries **winget** to install Chrome if it is missing.  
   If files are locked, close Chrome or re-run with **`--kill-chrome`** (closes **all** Chrome windows).

2. The agent defaults to **`"use_undetected_chrome": true`** in `config.json` (merged on load). That launches Chrome through **undetected-chromedriver** instead of attaching to a plain debug Chrome session.

3. Optional `config.json` keys: **`chrome_binary_path`** (non-standard Chrome install), **`chrome_version_main`** (e.g. `146` if auto-detection fails), **`use_undetected_chrome": false`** only for troubleshooting (legacy remote-debug attach).

**Automatic anti-bot recovery (optional):** If the open page looks like a **Cloudflare / “checking your browser”** interstitial, the agent can **quit the browser, delete `%TEMP%\chrome_debug`, and start a new session** (same idea as the profile step in `v3r_prepare_browser.py`). It does **not** run `pip` or `winget` for you. Defaults: **`auto_recover_cloudflare_block`: true**, **`max_cf_auto_recoveries`: 3** per streak; after that it **pauses** so you can run the full prep script or fix IP/site issues. Successful Grok sessions reset the streak counter.

## Installation

1. Copy this folder to a permanent location (for example `%USERPROFILE%\Desktop\v3r_nexus`).

2. **Optional:** Run `install.bat` once. It creates `%USERPROFILE%\Desktop\v3r_nexus` subfolders, copies `command_headers_template.txt` to `command_headers.txt` if missing, and, when `v3r_agent.exe` exists beside the script, adds a **Startup** shortcut.

3. From source:

   ```bat
   cd /d "%USERPROFILE%\Desktop\v3r_nexus"
   python -m pip install -r requirements.txt
   python v3r_agent.py
   ```

4. **PyInstaller** (example):

   ```bat
   pyinstaller --noconsole --onefile -n v3r_agent v3r_agent.py
   ```

   Place `v3r_agent.exe` in the nexus folder (or next to `install.bat`) if you use the Startup shortcut path from `install.bat`.

## First run and account setup

On the first run, if `config.json` does not exist under:

`%USERPROFILE%\Desktop\v3r_nexus\`

a **Tkinter** dialog asks for up to five Grok accounts (email, password, optional nickname). Credentials are stored in **plain text** in `config.json` together with paths and options. If `config.json` already exists, the dialog is skipped.

### Google sign-in flow

Grok is opened with **Sign in** (`a[href="/sign-in"]`) → **Login with Google** → the Google account row whose `data-email` matches the account’s **email** in `config.json`. The stored **password** is not used for this OAuth path; each rotated account must appear in Google’s chooser for the Chrome profile you use (often already signed into Google). When the header **profile image** (`img[alt="pfp"]`) is present, the agent treats the session as logged in.

## Automatic folders and files

The app uses:

| Path | Purpose |
|------|--------|
| `config.json` | Accounts, poll interval, download folder, limits |
| `state.json` | Monitoring state and resume data |
| `command_headers.txt` | Instruction-name → command template map |
| `logs\agent.log` | Log file |
| `temp\latest_image.png` | Last downloaded image |

## Editing `command_headers.txt`

Each non-comment line:

```text
\ps  |  {data}
```

- Left of `|`: header name (slashes trimmed).  
- Right of `|`: shell command template; `{data}` is replaced with the JSON `data` field from the decoded instruction.

Comment lines start with `#` or `=`.

## Tray menu

- **Start Monitoring** — begin the main loop (also **Ctrl+Shift+S**).  
- **Stop Monitoring** — stop and close the Selenium session (**Ctrl+Shift+X**).  
- **Pause** — pause logical processing (**Ctrl+Shift+P**).  
- **Resume** — clear paused flag.  
- **Cycle Account** — move to the next configured account and reset usage (reconnects browser).  
- **View Log** — open `logs\agent.log` with the default handler (typically Notepad).  
- **Register startup (registry)** — writes `V3RAgent` under  
  `HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run`.  
- **Exit** — stop monitoring and quit the tray app.

## Global hotkey: Insert

The **Insert** key toggles pause/resume while the app runs. This uses the `keyboard` library; on some Windows setups **administrator rights** may be required for low-level hooks. If hotkeys fail, see `agent.log` for a warning.

## Chrome and Selenium

The agent tries to attach to Chrome at **127.0.0.1:9222** (remote debugging). If nothing is listening, it starts Chrome with:

`--remote-debugging-port=9222` and a debug user-data directory under `%TEMP%\chrome_debug`.

ChromeDriver is resolved by **Selenium Manager** (bundled with Selenium 4.6+) unless `chrome_driver_path` is set in `config.json`.

## Instruction image and JSON

Decoded PNG text should be JSON like:

```json
{
  "instruction": "ps",
  "data": "Get-Location",
  "id": "optional-id"
}
```

## Account rotation and rate limits

- After **messages_per_account_limit** sends (default **5**), the agent switches to the next account and forces a new browser session.  
- If the page suggests a rate limit (“rate limit”, “try again later”, etc.), it rotates immediately (or waits **one hour** if only one account exists).  
- After a full cycle through **all** accounts at the message limit, it waits **one hour** and resets to the first account.

## Error recovery

Basic patterns (e.g. missing Python module → `pip install`, retry with admin variants) are handled in code. After **three** consecutive failures, monitoring **pauses** and a message box is shown; use **Resume** when ready.

## Security warning

`config.json` holds **plain-text passwords**. Restrict file permissions and machine access. Admin and elevated paths may trigger **UAC**.

## Troubleshooting

| Issue | What to try |
|--------|-------------|
| Many Grok tabs keep opening | Usually **Chrome was spawned repeatedly** with a grok.com startup URL, or **Sign in** used `target="_blank"`. The agent now opens **`about:blank`** when launching Chrome and goes to **https://grok.com/sign-in** in the same tab. If the UI is still slow, raise **`grok_page_settle_seconds`** in `config.json` (default `4`). |
| `ChromeDriver only supports Chrome version …` (mismatch) or long pauses on start | Uses **Selenium Manager** only (`Service()` — no webdriver-manager). If it still fails, delete **`%USERPROFILE%\.cache\selenium`**, upgrade **`pip install -U selenium`**, or set **`chrome_driver_path`** to a **Chrome for Testing** `chromedriver.exe` matching your Chrome major version: [chrome-for-testing](https://googlechromelabs.github.io/chrome-for-testing/). If attach says **chrome not reachable**, the agent now **starts Chrome on port 9222 first**, then attaches (avoids ~60s stalls when nothing was listening). |
| No tray icon | Run `pythonw.exe v3r_agent.py` or ensure PyInstaller was not built with a broken icon path. |
| Login selectors fail | Grok’s DOM changes; update selectors in `v3r_agent.py` (`login_to_grok`, image CSS). |
| Download never completes | Confirm Chrome’s download folder matches `download_folder` in `config.json` (default: Downloads). |
| `keyboard` hotkeys fail | Run as Administrator once or use only tray Pause/Resume. |
| Bitonal decode garbage | Ensure the PNG is 512×512 and encoding matches the agreed bit layout (black=0, white=1, MSB-first bytes, UTF-8). |

## License

Use at your own risk; automating third‑party sites may violate their terms of service.
