"""Import already-finished transcripts from other tools, without re-transcribing.

Supports plain text and Markdown (.txt / .md / .markdown), subtitle files with a
time axis (.srt / .vtt), and Whisper-style JSON (.json). Subtitle and JSON cues with
timestamps are rendered into the same `[mm:ss] text` shape as native transcripts, so
every downstream feature (reports, chat, daily brief, tracking, memory) works on them
unchanged.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

from .library import Meeting, create_meeting
from .transcriber import _fmt_ts


def _ts_to_seconds(ts: str) -> float:
    """Parse 'HH:MM:SS,mmm' / 'HH:MM:SS.mmm' / 'MM:SS' into seconds."""
    ts = ts.strip().replace(",", ".")
    try:
        parts = [float(p) for p in ts.split(":")]
    except ValueError:
        return 0.0
    if len(parts) == 3:
        h, m, s = parts
    elif len(parts) == 2:
        h, m, s = 0.0, parts[0], parts[1]
    elif len(parts) == 1:
        h, m, s = 0.0, 0.0, parts[0]
    else:
        return 0.0
    return h * 3600 + m * 60 + s


def _parse_subtitles(raw: str) -> list[dict]:
    """Parse .srt / .vtt cues into [{start, end, text}]."""
    segments = []
    for block in re.split(r"\n\s*\n", raw):
        lines = [l for l in block.splitlines() if l.strip()]
        if not lines:
            continue
        ti = next((i for i, l in enumerate(lines) if "-->" in l), -1)
        if ti < 0:
            continue
        m = re.search(r"([\d:.,]+)\s*-->\s*([\d:.,]+)", lines[ti])
        if not m:
            continue
        text = " ".join(lines[ti + 1:]).strip()
        text = re.sub(r"<[^>]+>", "", text)  # drop VTT inline tags like <c> or <00:00:01.000>
        if text:
            segments.append({"start": round(_ts_to_seconds(m.group(1)), 2),
                             "end": round(_ts_to_seconds(m.group(2)), 2),
                             "text": text})
    return segments


def _parse_json(raw: str) -> dict:
    """Parse Whisper-style JSON: a dict with 'segments' and/or 'text', or a list of segments."""
    data = json.loads(raw)
    segs, text = None, ""
    if isinstance(data, dict):
        if isinstance(data.get("segments"), list):
            segs = data["segments"]
        text = (data.get("text") or "").strip()
    elif isinstance(data, list):
        segs = data
    segments = []
    for s in (segs or []):
        if not isinstance(s, dict):
            continue
        t = (s.get("text") or "").strip()
        if not t:
            continue
        st, en = s.get("start"), s.get("end")
        segments.append({"start": round(float(st), 2) if st is not None else None,
                         "end": round(float(en), 2) if en is not None else None,
                         "text": t})
    if not segments and not text:
        raise ValueError("JSON has no recognizable 'segments' or 'text'")
    return {"segments": segments, "text": text}


def build_imported(raw: str, ext: str) -> dict:
    """Normalize a raw transcript file into {segments, text} by extension."""
    ext = ext.lower()
    if ext in (".srt", ".vtt"):
        segments = _parse_subtitles(raw)
        if not segments:
            raise ValueError(f"No subtitle cues found in the {ext} file")
        return {"segments": segments, "text": "\n".join(s["text"] for s in segments)}
    if ext == ".json":
        r = _parse_json(raw)
        if not r["text"]:
            r["text"] = "\n".join(s["text"] for s in r["segments"])
        return r
    # .txt / .md / .markdown — plain text, used as-is
    text = raw.strip()
    if not text:
        raise ValueError("File is empty")
    return {"segments": [], "text": text}


def import_transcript_file(path: Path, title: str | None = None,
                           source: str = "transcript import") -> Meeting:
    """Create a meeting from an already-finished transcript file (no audio, no transcription)."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    data = build_imported(raw, path.suffix)
    title = title or path.stem
    fmt = path.suffix.lstrip(".").lower()
    segments = data["segments"]

    duration = ""
    if segments and any(s.get("start") is not None for s in segments):
        body = [(f"[{_fmt_ts(s['start'])}] {s['text']}" if s.get("start") is not None
                 else s["text"]) for s in segments]
        ends = [s["end"] for s in segments if s.get("end") is not None]
        if ends:
            duration = _fmt_ts(max(ends))
    else:
        body = [data["text"]]

    mtg = create_meeting(title, source=source)
    header = [f"# {title}", "", f"- Date: {mtg.meta.get('created', '')}"]
    if duration:
        header.append(f"- Duration: {duration}")
    header += [f"- Source: imported transcript ({fmt})", "", "## Transcript", ""]
    mtg.transcript_md.write_text("\n".join(header + body).rstrip() + "\n", encoding="utf-8")
    mtg.transcript_json.write_text(json.dumps({
        "text": data["text"], "segments": segments, "duration": duration,
        "model": "imported", "language": "imported", "source_format": fmt,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    mtg.save_meta(status="done", duration=duration, whisper_model="imported",
                  imported_at=dt.datetime.now().isoformat(timespec="seconds"), error=None)
    return mtg
