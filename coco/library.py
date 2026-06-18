"""Meeting library: one folder per meeting under library/, with audio, transcript, reports."""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
from pathlib import Path

from .config import LIBRARY_DIR, TRASH_DIR, ensure_dirs

AUDIO_EXTS = {".m4a", ".mp3", ".wav", ".aiff", ".aif", ".flac", ".ogg",
              ".opus", ".webm", ".mp4", ".mov", ".mkv", ".amr", ".wma"}

# Already-finished transcripts exported from other tools, imported without re-transcribing.
TRANSCRIPT_EXTS = {".txt", ".md", ".markdown", ".srt", ".vtt", ".json"}


def _slug(title: str) -> str:
    s = re.sub(r"[\\/:*?\"<>|\s]+", "-", title.strip())
    return s.strip("-")[:60] or "untitled"


class Meeting:
    def __init__(self, path: Path):
        self.path = path
        self.id = path.name

    @property
    def meta_file(self) -> Path:
        return self.path / "meta.json"

    @property
    def meta(self) -> dict:
        if self.meta_file.exists():
            return json.loads(self.meta_file.read_text(encoding="utf-8"))
        return {}

    def save_meta(self, **kwargs) -> None:
        m = self.meta
        m.update(kwargs)
        self.meta_file.write_text(
            json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @property
    def title(self) -> str:
        return self.meta.get("title", self.id)

    @property
    def date(self) -> str:
        """Effective date (YYYY-MM-DD): a hand-corrected meta.date wins, else the created
        date, else inferred from the folder-name prefix. Used for display, sorting,
        date filtering and daily-brief grouping."""
        d = self.meta.get("date")
        if d:
            return d
        created = self.meta.get("created", "")
        if created:
            return created[:10]
        m = re.match(r"\d{4}-\d{2}-\d{2}", self.id)
        return m.group(0) if m else ""

    @property
    def audio_file(self) -> Path | None:
        for f in sorted(self.path.iterdir()):
            if f.suffix.lower() in AUDIO_EXTS:
                return f
        return None

    @property
    def transcript_md(self) -> Path:
        return self.path / "transcript.md"

    @property
    def transcript_json(self) -> Path:
        return self.path / "transcript.json"

    @property
    def reports_dir(self) -> Path:
        return self.path / "reports"

    def transcript_text(self) -> str:
        if self.transcript_md.exists():
            return self.transcript_md.read_text(encoding="utf-8")
        return ""

    def reports(self) -> list[Path]:
        if not self.reports_dir.exists():
            return []
        return sorted(self.reports_dir.glob("*.md"))

    def summary(self) -> dict:
        m = self.meta
        return {
            "id": self.id,
            "title": self.title,
            "created": m.get("created", ""),
            "date": self.date,
            "duration": m.get("duration", ""),
            "source": m.get("source", ""),
            "status": m.get("status", "new"),  # new|transcribing|done|error
            "has_transcript": self.transcript_md.exists(),
            "reports": [p.stem for p in self.reports()],
        }


def create_meeting(title: str, audio_path: Path | None = None,
                   source: str = "import", move: bool = False) -> Meeting:
    ensure_dirs()
    date = dt.date.today().isoformat()
    base = f"{date}-{_slug(title)}"
    folder = LIBRARY_DIR / base
    n = 1
    while folder.exists():
        n += 1
        folder = LIBRARY_DIR / f"{base}-{n}"
    folder.mkdir(parents=True)
    mtg = Meeting(folder)
    mtg.save_meta(
        title=title,
        created=dt.datetime.now().isoformat(timespec="seconds"),
        source=source,
        status="new",
    )
    if audio_path is not None:
        dest = folder / ("audio" + audio_path.suffix.lower())
        if move:
            shutil.move(str(audio_path), dest)
        else:
            shutil.copy2(audio_path, dest)
    return mtg


def list_meetings() -> list[Meeting]:
    ensure_dirs()
    out = []
    for p in LIBRARY_DIR.iterdir():
        if p.is_dir() and not p.name.startswith("_") and (p / "meta.json").exists():
            out.append(Meeting(p))
    # Newest first by effective date (re-sorts when you correct a recording date),
    # then by created time and id within the same day.
    out.sort(key=lambda m: (m.date, m.meta.get("created", ""), m.id), reverse=True)
    return out


def find_meeting(key: str) -> Meeting:
    """Exact id match, or fuzzy substring match on title/id; error on ambiguity."""
    meetings = list_meetings()
    for m in meetings:
        if m.id == key:
            return m
    hits = [m for m in meetings if key in m.id or key in m.title]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise LookupError(f"Meeting not found: {key} (run `coco list` to see all)")
    names = ", ".join(m.id for m in hits[:8])
    raise LookupError(f"\"{key}\" matches multiple meetings: {names}; use a more specific name")


def meetings_on(date: str) -> list[Meeting]:
    return [m for m in list_meetings() if m.date == date]


def delete_meeting(mtg: Meeting) -> Path:
    """Soft delete: move the whole meeting folder into library/_trash; recover by hand."""
    if mtg.meta.get("status") == "transcribing":
        raise RuntimeError("This meeting is transcribing; delete it after that finishes")
    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d%H%M%S")
    dest = TRASH_DIR / f"{mtg.id}~{stamp}"
    shutil.move(str(mtg.path), dest)
    return dest


def search_library(query: str, per_meeting: int = 4, limit: int = 50) -> list[dict]:
    """Case-insensitive full-text search across all transcripts and reports."""
    q = query.strip().lower()
    if not q:
        return []
    results = []
    for m in list_meetings():
        sources = []
        if m.transcript_md.exists():
            sources.append(("transcript", m.transcript_md))
        sources += [(p.stem, p) for p in m.reports()]
        matches = []
        for label, path in sources:
            for line in path.read_text(encoding="utf-8").splitlines():
                if q in line.lower():
                    matches.append({"where": label, "line": line.strip()[:120]})
                    if len(matches) >= per_meeting:
                        break
            if len(matches) >= per_meeting:
                break
        if matches:
            results.append({"id": m.id, "title": m.title,
                            "created": m.meta.get("created", ""),
                            "matches": matches})
        if len(results) >= limit:
            break
    return results
