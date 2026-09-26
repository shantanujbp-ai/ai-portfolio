"""
Central place for paths and settings so every script agrees on where
things live. Mirrors the same pattern as the AI-Agent-Project.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent

DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"          # synthetic CSVs from generate_synthetic_data.py
WAREHOUSE_DIR = DATA_DIR / "warehouse"  # the DuckDB file lives here

for _dir in (RAW_DIR, WAREHOUSE_DIR):
    _dir.mkdir(parents=True, exist_ok=True)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
