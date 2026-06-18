"""coco global configuration: paths, defaults, model mapping."""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # project root
LIBRARY_DIR = ROOT / "library"
BRIEFS_DIR = LIBRARY_DIR / "_briefs"
TRACKING_DIR = LIBRARY_DIR / "_tracking"
TRASH_DIR = LIBRARY_DIR / "_trash"
MEMORY_FILE = ROOT / "memory" / "memory.md"
LONGTERM_FILE = ROOT / "memory" / "longterm.md"  # AI-maintained long-term memory
CONFIG_FILE = ROOT / "coco.config.json"

# whisper model alias -> HuggingFace repo
MODEL_REPOS = {
    "large": "mlx-community/whisper-large-v3-mlx",
    "turbo": "mlx-community/whisper-large-v3-turbo",
    "tiny": "mlx-community/whisper-tiny",  # for quick self-checks only
}

MEMORY_PLACEHOLDER = (
    "# Global memory\n\n"
    "<!-- Everything here is injected into every AI analysis: names, terms, "
    "company background, personal preferences, etc. -->\n"
)
LONGTERM_PLACEHOLDER = (
    "# Long-term memory\n\n"
    "<!-- Maintained automatically by coco: after each transcription it extracts "
    "people, projects, commitments and terms from the meeting. You can edit this "
    "by hand; the next update merges on top of your edits. -->\n"
)

DEFAULTS = {
    "whisper_model": "turbo",   # turbo (fast) | large (most accurate) | tiny (self-check)
    "language": "auto",         # auto = detect per file; or an ISO code like "en", "fr", "zh"
    "initial_prompt_extra": "",  # proper nouns / names appended to the transcription prompt
    "beam_size": 0,             # 0 = greedy (fast). Set 5 for beam search: more accurate, slower
    "audio_device": ":0",       # ffmpeg avfoundation audio input device (see: coco devices)
    "claude_bin": "claude",
    "claude_extra_args": [],    # e.g. ["--model", "claude-sonnet-4-6"]
    "auto_memory": True,        # auto-extract long-term memory after each transcription

    "port": 8765,
    "hf_endpoint": "",          # leave empty = use default; set a mirror URL if needed
}


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    return cfg


def save_config(cfg: dict) -> None:
    keys = {k: cfg[k] for k in DEFAULTS if k in cfg}
    CONFIG_FILE.write_text(
        json.dumps(keys, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def resolve_model_repo(name: str) -> str:
    return MODEL_REPOS.get(name, name)  # also accept a raw HF repo name


def ensure_dirs() -> None:
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    BRIEFS_DIR.mkdir(parents=True, exist_ok=True)
    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not MEMORY_FILE.exists():
        MEMORY_FILE.write_text(MEMORY_PLACEHOLDER, encoding="utf-8")
    if not LONGTERM_FILE.exists():
        LONGTERM_FILE.write_text(LONGTERM_PLACEHOLDER, encoding="utf-8")


def setup_hf_endpoint(cfg: dict | None = None) -> None:
    """Model download source: honor an explicit env var or config value if set."""
    if os.environ.get("HF_ENDPOINT"):
        return
    cfg = cfg or load_config()
    if cfg.get("hf_endpoint"):
        os.environ["HF_ENDPOINT"] = cfg["hf_endpoint"]
