import os
import subprocess
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from fastapi import HTTPException

CHROME_DEBUG_URL = "http://127.0.0.1:9222/json/version"
DEFAULT_CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
DEFAULT_USER_DATA_DIR = r"C:\chrome-debug"


def _is_chrome_debug_ready() -> bool:
    try:
        with urlopen(CHROME_DEBUG_URL, timeout=1) as response:
            return response.status == 200
    except URLError:
        return False
    except Exception:
        return False


def ensure_chrome_debug_browser():
    if _is_chrome_debug_ready():
        return {
            "started": False,
            "message": "Chrome remote debugging is already running.",
        }

    chrome_path = os.getenv("CHROME_PATH", DEFAULT_CHROME_PATH)
    user_data_dir = os.getenv("CHROME_DEBUG_USER_DATA_DIR", DEFAULT_USER_DATA_DIR)

    if not Path(chrome_path).exists():
        raise HTTPException(
            status_code=500,
            detail=(
                "Chrome was not found at the configured path. Set CHROME_PATH or "
                "start Chrome manually with remote debugging enabled."
            ),
        )

    Path(user_data_dir).mkdir(parents=True, exist_ok=True)

    subprocess.Popen(
        [
            chrome_path,
            "--remote-debugging-port=9222",
            f'--user-data-dir={user_data_dir}',
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    deadline = time.time() + 15
    while time.time() < deadline:
        if _is_chrome_debug_ready():
            return {
                "started": True,
                "message": "Chrome remote debugging started.",
            }
        time.sleep(0.5)

    raise HTTPException(
        status_code=500,
        detail="Chrome was launched but remote debugging did not become ready in time.",
    )