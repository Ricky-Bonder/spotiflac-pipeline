"""Shared config loader for Python scripts in spotiflac-pipeline.

Sourced via `from _common import *` at the top of each bin/*.py script.
Reads ~/.config/spotiflac-pipeline/spotiflac.env (if present) into the
process environment, then exposes typed Path/str/int constants for all
SPOTIFLAC_* keys with defaults.
"""

import os
from pathlib import Path

_CONFIG_FILE = Path(os.environ.get(
    "SPOTIFLAC_CONFIG_FILE",
    str(Path.home() / ".config/spotiflac-pipeline/spotiflac.env"),
))


def _load_env_file(p: Path) -> None:
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if v.startswith(('"', "'")) and v.endswith(v[0]):
            v = v[1:-1]
        v = os.path.expandvars(v.replace("$HOME", os.path.expanduser("~")))
        os.environ.setdefault(k.strip(), v)


_load_env_file(_CONFIG_FILE)


def _env_path(key: str, default: str) -> Path:
    return Path(os.path.expanduser(os.environ.get(key, default))).resolve()


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, str(default)))
    except ValueError:
        return default


MUSIC_ROOT          = _env_path("SPOTIFLAC_MUSIC_ROOT",   "~/Music")
OUTPUT_DIR          = _env_path("SPOTIFLAC_OUTPUT_DIR",   str(MUSIC_ROOT / "spotiflac"))
SPOTDL_ROOT         = _env_path("SPOTIFLAC_SPOTDL_ROOT",  str(MUSIC_ROOT / "spotdl"))
STATE_DIR           = _env_path("SPOTIFLAC_STATE_DIR",    "~/.local/state/spotiflac-pipeline")
VENV                = _env_path("SPOTIFLAC_VENV",         "~/.local/share/spotiflac-pipeline/venv")

LIBRARY_DIR         = OUTPUT_DIR / "_library"
PLAYLISTS_DIR       = OUTPUT_DIR / "_playlists"

TELEGRAM_BOT_TOKEN  = os.environ.get("SPOTIFLAC_TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID    = os.environ.get("SPOTIFLAC_TELEGRAM_CHAT_ID", "")

MIN_FREE_DISK_GB    = _env_int("SPOTIFLAC_MIN_FREE_DISK_GB", 50)
MIN_FREE_MEM_MB     = _env_int("SPOTIFLAC_MIN_FREE_MEM_MB",  200)

OLD_PLAYLISTS_JSON  = os.environ.get("SPOTIFLAC_OLD_PLAYLISTS_JSON", "")
LIKES_ALIASES       = set(
    a.strip() for a in os.environ.get("SPOTIFLAC_LIKES_ALIASES", "Liked Songs").split(",")
    if a.strip()
)

STATE_DIR.mkdir(parents=True, exist_ok=True)


def notify(msg: str) -> None:
    """Send a Telegram message if configured; always logs to state-dir notify.log."""
    import urllib.parse
    import urllib.request
    (STATE_DIR / "notify.log").open("a").write(f"[notify] {msg}\n")
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    data = urllib.parse.urlencode({"chat_id": TELEGRAM_CHAT_ID, "text": msg}).encode()
    try:
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data=data, timeout=10,
        )
    except Exception:
        pass
