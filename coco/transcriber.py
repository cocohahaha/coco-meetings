"""Local Whisper transcription (mlx-whisper, native Apple Silicon acceleration)."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from .config import load_config, resolve_model_repo, setup_hf_endpoint
from .library import Meeting


def _fmt_ts(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def transcribe_file(audio: Path, model: str | None = None,
                    language: str | None = None,
                    progress=lambda msg: None) -> dict:
    """Transcribe an audio/video file -> {text, segments, duration, model}."""
    cfg = load_config()
    model = model or cfg["whisper_model"]
    # language: explicit arg > config; "auto" / empty means let Whisper detect it
    lang = language if language is not None else cfg.get("language")
    if not lang or lang == "auto":
        lang = None
    setup_hf_endpoint(cfg)
    repo = resolve_model_repo(model)

    progress(f"Loading model {model} (first run downloads it automatically; large is ~3GB)…")
    import mlx_whisper  # lazy import: loading mlx is slow

    progress("Transcribing…")
    kwargs = {}
    # Bias recognition with user-supplied proper nouns / names, if any. This is
    # language-neutral: Whisper detects the spoken language on its own.
    if cfg.get("initial_prompt_extra"):
        kwargs["initial_prompt"] = cfg["initial_prompt_extra"]
    result = mlx_whisper.transcribe(
        str(audio),
        path_or_hf_repo=repo,
        language=lang,
        verbose=None,
        # Long-audio anti-repeat / anti-hallucination: do not feed the previous
        # segment back in as a condition for the next one.
        condition_on_previous_text=False,
        **kwargs,
    )
    segments = [
        {"start": round(s["start"], 2), "end": round(s["end"], 2),
         "text": s["text"].strip()}
        for s in result.get("segments", [])
        if s["text"].strip()
    ]
    duration = segments[-1]["end"] if segments else 0
    return {
        "text": result.get("text", "").strip(),
        "segments": segments,
        "duration": duration,
        "model": model,
        "language": result.get("language", lang),
    }


def transcribe_meeting(mtg: Meeting, model: str | None = None,
                       progress=lambda msg: None) -> None:
    """Transcribe meeting audio, write transcript.md / transcript.json, update meta."""
    audio = mtg.audio_file
    if audio is None:
        raise FileNotFoundError(f"Meeting {mtg.id} has no audio file")
    mtg.save_meta(status="transcribing")
    try:
        result = transcribe_file(audio, model=model, progress=progress)
    except Exception as e:
        mtg.save_meta(status="error", error=str(e))
        raise

    mtg.transcript_json.write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    lines = [
        f"# {mtg.title}",
        "",
        f"- Date: {mtg.meta.get('created', '')}",
        f"- Duration: {_fmt_ts(result['duration'])}",
        f"- Source: {mtg.meta.get('source', '')} (transcribed with {result['model']})",
        f"- Language: {result.get('language') or 'auto'}",
        "",
        "## Transcript",
        "",
    ]
    for s in result["segments"]:
        lines.append(f"[{_fmt_ts(s['start'])}] {s['text']}")
    mtg.transcript_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    mtg.save_meta(
        status="done",
        duration=_fmt_ts(result["duration"]),
        whisper_model=result["model"],
        language=result.get("language") or "auto",
        transcribed_at=dt.datetime.now().isoformat(timespec="seconds"),
        error=None,
    )
