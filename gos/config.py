from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("GOS_DATA_DIR", BASE_DIR / "data"))
LOG_DIR = Path(os.environ.get("GOS_LOG_DIR", BASE_DIR / "logs"))
PLAYER_DIR = DATA_DIR / "players"

HOST = os.environ.get("GOS_HOST", "0.0.0.0")
PORT = int(os.environ.get("GOS_PORT", "8080"))

# The client builds URLs as <base>/frontend/<endpoint>.
# This value is what 9004 can advertise back to the client.
PUBLIC_BASE_URL = os.environ.get("GOS_PUBLIC_BASE_URL", f"http://127.0.0.1:{PORT}/frontend/")

# musicMod.setDownloadUrl(A0_3.MusicUrl) in the action-9006 callback stores
# this and prefixes every "vocal_music/<lang>/<npc>/<id>.mp3" voice download.
# It must end in "/" and must be an absolute URL, otherwise network.download
# gets a relative path ("Malformed URL") and the story/tutorial voice loop
# never completes -> the first story dialog hangs on a black screen.
_public_root = PUBLIC_BASE_URL.split("/frontend/")[0] if "/frontend/" in PUBLIC_BASE_URL else PUBLIC_BASE_URL.rstrip("/")
MUSIC_BASE_URL = os.environ.get("GOS_MUSIC_BASE_URL", _public_root + "/music/")

SERVER_ID = int(os.environ.get("GOS_SERVER_ID", "1"))
SERVER_NAME = os.environ.get("GOS_SERVER_NAME", "Dev Server")

# PostgreSQL mirror for players/sessions (empty string disables it).
# Credentials are supplied by GOS_PG_DSN in the service environment; the
# committed default never contains a password.
PG_DSN = os.environ.get("GOS_PG_DSN", "dbname=gos_dev user=gos_dev host=127.0.0.1")

for d in (DATA_DIR, LOG_DIR, PLAYER_DIR):
    d.mkdir(parents=True, exist_ok=True)
