"""coco local web server (FastAPI, binds to 127.0.0.1 only)."""
from __future__ import annotations

import datetime as dt
import io
import re
import shutil
import threading
import uuid
import zipfile
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from . import ai
from .config import (BRIEFS_DIR, LONGTERM_FILE, LONGTERM_PLACEHOLDER,
                     MEMORY_FILE, MEMORY_PLACEHOLDER, TRACKING_DIR, TRASH_DIR,
                     ensure_dirs, load_config, save_config)
from .library import (AUDIO_EXTS, Meeting, create_meeting, delete_meeting,
                      find_meeting, list_meetings, search_library)
from .recorder import Recorder
from .templates import TEMPLATES
from .transcriber import transcribe_meeting

app = FastAPI(title="coco", docs_url=None, redoc_url=None)
recorder = Recorder()
JOBS: dict[str, dict] = {}  # jid -> {status, detail, meeting_id, error}
STATIC = Path(__file__).parent / "static"
TRANSCRIBE_LOCK = threading.Lock()  # serialize transcription to avoid loading two models at once


def _err(e: Exception, code: int = 400):
    raise HTTPException(status_code=code, detail=str(e))


def _trash_file(path: Path, dest_stem: str) -> Path:
    """Soft-delete a single .md file: move into trash library/_trash, recoverable by hand."""
    if not path.exists():
        _err(FileNotFoundError(f"File not found: {path.name}"), 404)
    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d%H%M%S")
    dest = TRASH_DIR / f"{dest_stem}~{stamp}.md"
    shutil.move(str(path), dest)
    return dest


def _start_transcribe_job(mtg: Meeting, model: str | None = None) -> str:
    jid = uuid.uuid4().hex[:8]
    JOBS[jid] = {"status": "running", "detail": "Queued…", "meeting_id": mtg.id}

    def work():
        try:
            if not mtg.path.exists():
                raise FileNotFoundError("Meeting was deleted while queued")
            with TRANSCRIBE_LOCK:
                JOBS[jid].update(detail="Transcribing…")
                transcribe_meeting(
                    mtg, model=model,
                    progress=lambda msg: JOBS[jid].update(detail=msg),
                )
            if load_config().get("auto_memory", True):
                try:
                    JOBS[jid].update(detail="Extracting long-term memory…")
                    ai.update_longterm(mtg)
                except Exception as e:  # memory failure must not affect the transcript
                    mtg.save_meta(memory_error=str(e))
            JOBS[jid].update(status="done", detail="Transcription done")
        except Exception as e:
            JOBS[jid].update(status="error", detail=str(e))

    threading.Thread(target=work, daemon=True).start()
    return jid


@app.on_event("startup")
def resume_interrupted():
    """After a restart, re-queue transcription jobs that were interrupted or still queued.

    Covers two cases: killed mid-transcription (transcribing), killed while queued (new).
    """
    for m in list_meetings():
        if (m.meta.get("status") in ("new", "transcribing")
                and not m.transcript_md.exists() and m.audio_file):
            _start_transcribe_job(m)


# ---------- pages ----------

@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


# ---------- library ----------

@app.get("/api/meetings")
def api_meetings():
    return [m.summary() for m in list_meetings()]


@app.get("/api/meetings/{mid}")
def api_meeting(mid: str):
    try:
        m = find_meeting(mid)
    except LookupError as e:
        _err(e, 404)
    reports = [
        {"name": p.stem, "content": p.read_text(encoding="utf-8")}
        for p in m.reports()
    ]
    return {**m.summary(), "transcript": m.transcript_text(), "report_list": reports}


@app.delete("/api/meetings/{mid}")
def api_delete_meeting(mid: str):
    try:
        m = find_meeting(mid)
    except LookupError as e:
        _err(e, 404)
    try:
        dest = delete_meeting(m)
    except RuntimeError as e:
        _err(e)
    return {"ok": True, "trash": str(dest)}


@app.delete("/api/meetings/{mid}/reports/{name}")
def api_delete_report(mid: str, name: str):
    try:
        m = find_meeting(mid)
    except LookupError as e:
        _err(e, 404)
    if "/" in name or ".." in name:
        _err(ValueError("Invalid report name"))
    dest = _trash_file(m.reports_dir / f"{name}.md", f"report~{m.id}~{name}")
    return {"ok": True, "trash": str(dest)}


@app.get("/api/search")
def api_search(q: str = ""):
    return search_library(q)


@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...), title: str = Form(""),
                     model: str = Form("")):
    suffix = Path(file.filename or "audio").suffix.lower()
    if suffix not in AUDIO_EXTS:
        _err(ValueError(f"Unsupported file type: {suffix}"))
    ensure_dirs()
    tmp = Path("/tmp") / f"coco_upload_{uuid.uuid4().hex[:6]}{suffix}"
    tmp.write_bytes(await file.read())
    mtg = create_meeting(title or Path(file.filename).stem,
                         audio_path=tmp, source="upload", move=True)
    jid = _start_transcribe_job(mtg, model or None)
    return {"meeting_id": mtg.id, "job": jid}


class ImportBody(BaseModel):
    path: str
    title: str = ""
    model: str = ""


@app.post("/api/import")
def api_import(body: ImportBody):
    """Import a local file or folder (a folder imports every audio/video inside it)."""
    p = Path(body.path.strip().strip("'\"")).expanduser()
    if not p.exists():
        _err(FileNotFoundError(f"Path does not exist: {p}"))
    if p.is_dir():
        files = [f for f in sorted(p.iterdir())
                 if f.suffix.lower() in AUDIO_EXTS and not f.name.startswith(".")]
        if not files:
            _err(ValueError(f"No recognizable audio/video files in the folder: {p}"))
    else:
        if p.suffix.lower() not in AUDIO_EXTS:
            _err(ValueError(f"Unsupported file type: {p.suffix}"))
        files = [p]
    ensure_dirs()
    imported = []
    for f in files:
        mtg = create_meeting(body.title if (body.title and len(files) == 1) else f.stem,
                             audio_path=f, source="local import")
        imported.append({"meeting_id": mtg.id,
                         "job": _start_transcribe_job(mtg, body.model or None)})
    return {"imported": imported, "count": len(imported)}


# ---------- recording ----------

class RecordStart(BaseModel):
    title: str = ""


@app.post("/api/record/start")
def api_record_start(body: RecordStart):
    if recorder.active:
        _err(RuntimeError("A recording is already in progress"))
    title = body.title or f"recording-{dt.datetime.now().strftime('%H%M')}"
    mtg = create_meeting(title, source="recording")
    try:
        recorder.start(mtg.path / "audio.wav", title)
    except RuntimeError as e:
        _err(e)
    recorder.meeting_id = mtg.id
    return {"meeting_id": mtg.id}


@app.post("/api/record/stop")
def api_record_stop():
    try:
        recorder.stop()
    except RuntimeError as e:
        _err(e)
    mtg = find_meeting(recorder.meeting_id)
    jid = _start_transcribe_job(mtg)
    return {"meeting_id": mtg.id, "job": jid}


@app.get("/api/record/status")
def api_record_status():
    return {
        "active": recorder.active,
        "elapsed": recorder.elapsed(),
        "title": recorder.title if recorder.active else "",
    }


# ---------- job status ----------

@app.get("/api/jobs/{jid}")
def api_job(jid: str):
    job = JOBS.get(jid)
    if not job:
        _err(LookupError("Job not found"), 404)
    return job


# ---------- AI ----------

class AskBody(BaseModel):
    question: str
    ids: list[str] = []
    all: bool = False  # ask across all transcribed meetings


@app.post("/api/ask")
def api_ask(body: AskBody):
    try:
        if body.all:
            meetings = [m for m in list_meetings() if m.transcript_md.exists()]
        elif body.ids:
            meetings = [find_meeting(i) for i in body.ids]
        else:
            meetings = [m for m in list_meetings() if m.transcript_md.exists()][:1]
        if not meetings:
            raise ai.AIError("No transcribed meetings in the library")
        return {"answer": ai.ask(body.question, meetings)}
    except (LookupError, ai.AIError) as e:
        _err(e)


class TrackBody(BaseModel):
    focus: str = ""


@app.post("/api/track")
def api_track(body: TrackBody):
    try:
        path, content = ai.track(body.focus)
        return {"path": path, "content": content, "name": Path(path).stem}
    except ai.AIError as e:
        _err(e)


@app.delete("/api/tracking/{name}")
def api_delete_tracking(name: str):
    if "/" in name or ".." in name:
        _err(ValueError("Invalid file name"))
    dest = _trash_file(TRACKING_DIR / f"{name}.md", f"tracking~{name}")
    return {"ok": True, "trash": str(dest)}


class ReportBody(BaseModel):
    id: str
    template: str


@app.post("/api/report")
def api_report(body: ReportBody):
    try:
        mtg = find_meeting(body.id)
        path, content = ai.generate_report(mtg, body.template)
        return {"path": path, "content": content}
    except (LookupError, ai.AIError) as e:
        _err(e)


class BriefBody(BaseModel):
    date: str = ""


@app.post("/api/brief")
def api_brief(body: BriefBody):
    try:
        path, content = ai.daily_brief(body.date or None)
    except ai.AIError as e:
        _err(e)
    if load_config().get("auto_memory", True):
        def merge():
            try:  # the brief was just regenerated and changed; force a re-merge
                ai.memorize_brief(Path(path), force=True)
            except Exception:
                pass
        threading.Thread(target=merge, daemon=True).start()
    return {"path": path, "content": content, "date": Path(path).stem}


@app.get("/api/briefs")
def api_briefs():
    if not BRIEFS_DIR.exists():
        return []
    return [
        {"date": p.stem, "content": p.read_text(encoding="utf-8")}
        for p in sorted(BRIEFS_DIR.glob("*.md"), reverse=True)
    ]


@app.delete("/api/briefs/{date}")
def api_delete_brief(date: str):
    if "/" in date or ".." in date:
        _err(ValueError("Invalid date"))
    dest = _trash_file(BRIEFS_DIR / f"{date}.md", f"daily-brief~{date}")
    return {"ok": True, "trash": str(dest)}


# ---------- config / transcript edit / proofread ----------

@app.get("/api/config")
def api_config():
    return {"whisper_model": load_config()["whisper_model"]}


class ConfigBody(BaseModel):
    whisper_model: str


@app.post("/api/config")
def api_config_save(body: ConfigBody):
    if body.whisper_model not in ("turbo", "large"):
        _err(ValueError("Model must be turbo or large"))
    cfg = load_config()
    cfg["whisper_model"] = body.whisper_model
    save_config(cfg)
    return {"ok": True, "whisper_model": body.whisper_model}


class TranscriptBody(BaseModel):
    content: str


class MeetingMetaBody(BaseModel):
    title: str | None = None
    date: str | None = None  # YYYY-MM-DD, hand-corrected recording date


@app.post("/api/meetings/{mid}/meta")
def api_save_meeting_meta(mid: str, body: MeetingMetaBody):
    """Change a meeting's title / correct its recording date (folder is not renamed, id stays stable)."""
    try:
        m = find_meeting(mid)
    except LookupError as e:
        _err(e, 404)
    updates: dict = {}
    if body.title is not None:
        t = body.title.strip()
        if not t:
            _err(ValueError("Title cannot be empty"))
        updates["title"] = t
    if body.date is not None:
        d = body.date.strip()
        try:
            dt.date.fromisoformat(d)  # validates both format and a real date
        except ValueError:
            _err(ValueError("Date must be YYYY-MM-DD and a valid date"))
        updates["date"] = d
    if not updates:
        _err(ValueError("No fields to update"))
    m.save_meta(**updates)
    return {"ok": True, **m.summary()}


@app.post("/api/meetings/{mid}/transcript")
def api_save_transcript(mid: str, body: TranscriptBody):
    try:
        m = find_meeting(mid)
    except LookupError as e:
        _err(e, 404)
    if not body.content.strip():
        _err(ValueError("Content is empty, not saved"))
    m.transcript_md.write_text(body.content.rstrip() + "\n", encoding="utf-8")
    m.save_meta(edited_at=dt.datetime.now().isoformat(timespec="seconds"))
    return {"ok": True}


@app.post("/api/proofread/{mid}")
def api_proofread(mid: str):
    try:
        m = find_meeting(mid)
        content = ai.proofread(m)
        return {"content": content}
    except (LookupError, ai.AIError) as e:
        _err(e)


# ---------- downloads (.md export) ----------

def _md_download(path: Path, filename: str):
    if not path.exists():
        _err(FileNotFoundError(f"File not found: {path.name}"), 404)
    return FileResponse(path, media_type="text/markdown; charset=utf-8",
                        filename=filename)


@app.get("/api/download/brief/{date}")
def dl_brief(date: str):
    return _md_download(BRIEFS_DIR / f"{date}.md", f"daily-brief-{date}.md")


@app.get("/api/download/tracking/{name}")
def dl_tracking(name: str):
    if "/" in name or ".." in name:
        _err(ValueError("Invalid file name"))
    return _md_download(TRACKING_DIR / f"{name}.md", f"cross-meeting-tracking-{name}.md")


@app.get("/api/download/meeting/{mid}/transcript")
def dl_transcript(mid: str):
    try:
        m = find_meeting(mid)
    except LookupError as e:
        _err(e, 404)
    return _md_download(m.transcript_md, f"{m.title}-transcript.md")


MEMORY_FILES = {"content": (MEMORY_FILE, "global-memory", MEMORY_PLACEHOLDER),
                "longterm": (LONGTERM_FILE, "long-term-memory", LONGTERM_PLACEHOLDER)}


@app.get("/api/download/memory/{which}")
def dl_memory(which: str):
    if which not in MEMORY_FILES:
        _err(ValueError("which must be content or longterm"))
    path, label, _ = MEMORY_FILES[which]
    ensure_dirs()
    return _md_download(path, f"{label}.md")


@app.get("/api/download/meeting/{mid}/report/{name}")
def dl_report(mid: str, name: str):
    try:
        m = find_meeting(mid)
    except LookupError as e:
        _err(e, 404)
    if "/" in name or ".." in name:
        _err(ValueError("Invalid report name"))
    return _md_download(m.reports_dir / f"{name}.md", f"{m.title}-{name}.md")


# ---------- batch export (multiple .md packed into a zip) ----------

def _report_label(name: str) -> str:
    # minutes-1355 -> minutes 13:55, matching the tab display
    return re.sub(r"-(\d{2})(\d{2})$", r" \1:\2", name)


@app.get("/api/export/manifest")
def api_export_manifest():
    """List every exportable .md so the export dialog can tick them one by one."""
    meetings = []
    for m in list_meetings():
        files = []
        if m.transcript_md.exists():
            files.append({"type": "transcript", "name": "", "label": "Transcript"})
        for p in m.reports():
            files.append({"type": "report", "name": p.stem,
                          "label": _report_label(p.stem)})
        if files:
            meetings.append({"id": m.id, "title": m.title,
                             "date": m.date, "files": files})
    briefs = ([{"name": p.stem, "label": p.stem}
               for p in sorted(BRIEFS_DIR.glob("*.md"), reverse=True)]
              if BRIEFS_DIR.exists() else [])
    tracking = ([{"name": p.stem, "label": p.stem}
                 for p in sorted(TRACKING_DIR.glob("*.md"), reverse=True)]
                if TRACKING_DIR.exists() else [])
    return {"meetings": meetings, "briefs": briefs, "tracking": tracking}


def _safe_seg(s: str) -> str:
    """Sanitize into a safe single path segment for the zip (strip separators and edge dots)."""
    return re.sub(r"[\\/]+", "_", s).strip().strip(".") or "untitled"


def _resolve_export_item(it: "ExportItem") -> "tuple[Path | None, str]":
    """Safely resolve one export item into (disk path, relative path inside zip).
    Invalid items return (None, '').

    Every name forbids path separators and ..; meeting paths are resolved only through
    find_meeting on existing meetings, preventing path traversal.
    """
    name = it.name or ""
    if "/" in name or "\\" in name or ".." in name:
        return None, ""
    if it.type in ("transcript", "report"):
        try:
            m = find_meeting(it.id)
        except LookupError:
            return None, ""
        folder = _safe_seg(f"{m.date}-{m.title}")
        if it.type == "transcript":
            return m.transcript_md, f"{folder}/transcript.md"
        return m.reports_dir / f"{name}.md", f"{folder}/{_safe_seg(_report_label(name))}.md"
    if it.type == "brief":
        return BRIEFS_DIR / f"{name}.md", f"daily-briefs/{_safe_seg(name)}.md"
    if it.type == "tracking":
        return TRACKING_DIR / f"{name}.md", f"cross-meeting-tracking/{_safe_seg(name)}.md"
    return None, ""


class ExportItem(BaseModel):
    type: str  # transcript | report | brief | tracking
    id: str = ""
    name: str = ""


class ExportBody(BaseModel):
    items: list[ExportItem]


@app.post("/api/export")
def api_export(body: ExportBody):
    """Pack the selected .md files into a zip and return it."""
    if not body.items:
        _err(ValueError("No files selected"))
    buf = io.BytesIO()
    used: set[str] = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for it in body.items:
            path, arc = _resolve_export_item(it)
            if path is None or not path.exists():
                continue
            base, n = arc, 1
            while arc in used:  # different meetings may produce the same arc; avoid overwriting
                n += 1
                stem, dot, ext = base.rpartition(".")
                arc = f"{stem}-{n}.{ext}" if dot else f"{base}-{n}"
            used.add(arc)
            zf.write(path, arcname=arc)
    if not used:
        _err(ValueError("None of the selected files exist"))
    buf.seek(0)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    fname = f"coco-export-{stamp}.zip"
    disp = f"attachment; filename={fname}; filename*=UTF-8''{quote(fname)}"
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": disp})


# ---------- templates / memory ----------

@app.get("/api/templates")
def api_templates():
    return [{"id": k, "label": v["label"], "desc": v["desc"]}
            for k, v in TEMPLATES.items()]


@app.get("/api/memory")
def api_memory():
    ensure_dirs()
    return {"content": MEMORY_FILE.read_text(encoding="utf-8"),
            "longterm": LONGTERM_FILE.read_text(encoding="utf-8")}


class MemoryBody(BaseModel):
    content: str | None = None
    longterm: str | None = None


def _backup_then_write(path: Path, new: str) -> bool:
    """Before overwriting, back up the old content as .bak.md in the same dir (single-slot undo).

    Returns whether a backup was actually written (no backup if content is unchanged).
    """
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    backed = bool(old.strip()) and old != new
    if backed:
        path.with_suffix(".bak.md").write_text(old, encoding="utf-8")
    path.write_text(new, encoding="utf-8")
    return backed


@app.post("/api/memory")
def api_memory_save(body: MemoryBody):
    ensure_dirs()
    with ai.MEMORY_LOCK:
        if body.content is not None:
            _backup_then_write(MEMORY_FILE, body.content)
        if body.longterm is not None:
            _backup_then_write(LONGTERM_FILE, body.longterm)
    return {"ok": True}


class MemoryClearBody(BaseModel):
    which: str  # content | longterm


@app.post("/api/memory/clear")
def api_memory_clear(body: MemoryClearBody):
    if body.which not in MEMORY_FILES:
        _err(ValueError("which must be content or longterm"))
    ensure_dirs()
    path, _, placeholder = MEMORY_FILES[body.which]
    with ai.MEMORY_LOCK:
        backed = _backup_then_write(path, placeholder)
    return {"ok": True, "content": placeholder,
            "backup": str(path.with_suffix(".bak.md")) if backed else None}


@app.exception_handler(Exception)
def on_error(request, exc):
    return JSONResponse(status_code=500, content={"detail": str(exc)})
