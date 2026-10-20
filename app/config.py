from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "corrections.db"
MODEL = "grok-4.7"
SAP_MODE = "mock"


def load_dotenv(path: Path | None = None) -> None:
    env_path = path or (ROOT / ".env")
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_api_key() -> str | None:
    load_dotenv()
    key = os.environ.get("XAI_API_KEY", "").strip()
    if key:
        return key
    try:
        import streamlit as st

        secret = st.secrets.get("XAI_API_KEY", "")
    except Exception:
        return None
    secret = str(secret).strip()
    return secret or None
