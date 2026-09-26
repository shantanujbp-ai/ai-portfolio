"""
Central place for paths and settings so every script agrees on where
things live.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent

DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
WAREHOUSE_DIR = DATA_DIR / "warehouse"

for _dir in (RAW_DIR, WAREHOUSE_DIR):
    _dir.mkdir(parents=True, exist_ok=True)

# Read from Streamlit secrets if available, otherwise fall back to .env
try:
    import streamlit as st
    ANTHROPIC_API_KEY = st.secrets.get("ANTHROPIC_API_KEY", os.environ.get("ANTHROPIC_API_KEY", ""))
except Exception:
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

if not ANTHROPIC_API_KEY:
    raise RuntimeError(
        "No ANTHROPIC_API_KEY found. Add it to Streamlit secrets or a .env file."
    )
