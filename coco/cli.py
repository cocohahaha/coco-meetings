"""coco command-line entry point."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

from . import ai
from .config import MEMORY_FILE, ensure_dirs, load_config, save_config
from .ingest import import_transcript_file
from .library import (AUDIO_EXTS, TRANSCRIPT_EXTS, create_meeting,
                      delete_meeting, find_meeting, list_meetings, search_library)
from .recorder import list_devices, record_blocking
from .templates import TEMPLATES
from .transcriber import transcribe_meeting


def _p(msg: str) -> None:
    print(msg, flush=True)


# ---------- subcommands ----------

def cmd_record(args):
    title = args.title or f"recording-{dt.datetime.now().strftime('%H%M')}"
    mtg = create_meeting(title, source="recording")
    out = mtg.path / "audio.wav"
    _p(f"▶ New meeting: {mtg.id}")
    record_blocking(out, device=args.device)
    _p("✓ Recording saved, transcribing…")
    transcribe_meeting(mtg, model=args.model, progress=_p)
    _p(f"✓ Transcription done → {mtg.transcript_md}")


def cmd_transcribe(args):
    for f in args.files:
        src = Path(f).expanduser()
        if not src.exists():
            _p(f"✗ File not found: {src}")
            continue
        title = args.title or src.stem
        if src.suffix.lower() in TRANSCRIPT_EXTS:
            try:
                mtg = import_transcript_file(src, title, source="transcript import")
            except (ValueError, UnicodeError) as e:
                _p(f"✗ Could not import transcript {src.name}: {e}")
                continue
            _p(f"✓ Transcript imported → {mtg.transcript_md}")
            if load_config().get("auto_memory", True):
                try:
                    ai.update_longterm(mtg)
                except ai.AIError as e:
                    _p(f"  (memory extraction skipped: {e})")
            continue
        mtg = create_meeting(title, audio_path=src, source="import", move=args.move)
        _p(f"▶ Imported: {mtg.id}")
        transcribe_meeting(mtg, model=args.model, progress=_p)
        _p(f"✓ Transcription done → {mtg.transcript_md}")


def cmd_list(args):
    meetings = list_meetings()
    if not meetings:
        _p("Library is empty. Run `coco transcribe <audio>` to import, or drag-drop in the web UI.")
        return
    for m in meetings:
        s = m.summary()
        mark = {"done": "✓", "transcribing": "…", "error": "✗"}.get(s["status"], "·")
        reports = f"  reports[{','.join(s['reports'])}]" if s["reports"] else ""
        _p(f"{mark} {s['id']}  {s['duration'] or '--'}{reports}")


def cmd_show(args):
    mtg = find_meeting(args.key)
    text = mtg.transcript_text()
    _p(text if text else f"{mtg.id} has no transcript yet (status: {mtg.meta.get('status')})")


def cmd_ask(args):
    if args.refs:
        meetings = [find_meeting(r) for r in args.refs]
    else:
        meetings = [m for m in list_meetings() if m.transcript_md.exists()][:1]
        if not meetings:
            _p("No transcribed meetings in the library.")
            return
        _p(f"(No meeting given; defaulting to the most recent: {meetings[0].id})")
    _p("🤔 Thinking…")
    _p("\n" + ai.ask(args.question, meetings))


def cmd_report(args):
    mtg = find_meeting(args.key)
    _p(f"▶ Generating \"{args.template}\" report: {mtg.id} …")
    path, content = ai.generate_report(mtg, args.template)
    _p("\n" + content)
    _p(f"\n✓ Saved → {path}")


def cmd_templates(args):
    for tid, t in TEMPLATES.items():
        _p(f"{tid:<10} {t['label']['en']} — {t['desc']['en']}")


def cmd_brief(args):
    date = args.date or dt.date.today().isoformat()
    _p(f"▶ Generating daily brief for {date}…")
    path, content = ai.daily_brief(date)
    _p("\n" + content)
    _p(f"\n✓ Saved → {path}")
    if load_config().get("auto_memory", True):
        try:
            _p("▶ Merging brief into long-term memory…")
            ai.memorize_brief(Path(path), force=True)
            _p("✓ Merged")
        except ai.AIError as e:
            _p(f"✗ Memory merge failed (the brief itself is saved): {e}")


def cmd_memory(args):
    ensure_dirs()
    if args.add:
        with MEMORY_FILE.open("a", encoding="utf-8") as f:
            f.write(f"- {args.add}\n")
        _p(f"✓ Added to global memory: {args.add}")
    else:
        _p(MEMORY_FILE.read_text(encoding="utf-8"))
        _p(f"(Edit this file to maintain memory: {MEMORY_FILE})")


def cmd_delete(args):
    mtg = find_meeting(args.key)
    if not args.yes:
        ans = input(f"Delete \"{mtg.id}\"? Moves to trash library/_trash [y/N] ")
        if ans.strip().lower() not in ("y", "yes"):
            _p("Cancelled")
            return
    dest = delete_meeting(mtg)
    _p(f"✓ Moved to trash: {dest}")


def cmd_search(args):
    results = search_library(args.query)
    if not results:
        _p(f"No matches for \"{args.query}\"")
        return
    for r in results:
        _p(f"\n● {r['id']}")
        for m in r["matches"]:
            _p(f"  [{m['where']}] {m['line']}")


def cmd_track(args):
    focus = args.focus or ""
    _p(f"▶ Cross-meeting tracking ({focus or 'all meetings'}): follow-through / shifts / open issues…")
    path, content = ai.track(focus)
    _p("\n" + content)
    _p(f"\n✓ Saved → {path}")


def cmd_memorize(args):
    if args.all:
        meetings = [m for m in list_meetings() if m.transcript_md.exists()]
        meetings.reverse()  # accumulate in chronological order
    else:
        if not args.key:
            _p("Usage: coco memorize <meeting>  or  coco memorize --all")
            sys.exit(1)
        meetings = [find_meeting(args.key)]
    for m in meetings:
        if m.meta.get("memorized_at") and not args.force:
            _p(f"· Skipped (already extracted): {m.id}")
            continue
        _p(f"▶ Extracting long-term memory: {m.id} …")
        try:
            out = ai.update_longterm(m, force=args.force)
            _p("✓ Merged" if out else "· Skipped (transcript too short)")
        except ai.AIError as e:
            _p(f"✗ {e}")
    if args.all:
        from .config import BRIEFS_DIR
        for b in sorted(BRIEFS_DIR.glob("*.md")):
            _p(f"▶ Merging daily brief: {b.stem} …")
            try:
                out = ai.memorize_brief(b, force=args.force)
                _p("✓ Merged" if out else "· Skipped (already merged)")
            except ai.AIError as e:
                _p(f"✗ {e}")


def cmd_watch(args):
    folder = Path(args.folder).expanduser()
    if not folder.is_dir():
        _p(f"✗ Not a folder: {folder}")
        sys.exit(1)
    state_file = folder / ".coco_seen.json"
    seen = set(json.loads(state_file.read_text()) if state_file.exists() else [])
    if not state_file.exists():
        # First run: ignore existing files, only watch for new ones afterwards
        seen = {f.name for f in folder.iterdir()
                if f.suffix.lower() in AUDIO_EXTS}
        state_file.write_text(json.dumps(sorted(seen)))
        _p(f"(First watch, ignoring {len(seen)} existing files)")
    _p(f"👀 Watching {folder} (scan every 15s, Ctrl+C to quit)")
    try:
        while True:
            for f in sorted(folder.iterdir()):
                if f.suffix.lower() not in AUDIO_EXTS or f.name in seen:
                    continue
                # wait until the file is done writing (size stable across two checks)
                size = f.stat().st_size
                time.sleep(2)
                if f.stat().st_size != size:
                    continue
                seen.add(f.name)
                state_file.write_text(json.dumps(sorted(seen)))
                _p(f"▶ New file: {f.name}")
                try:
                    mtg = create_meeting(f.stem, audio_path=f, source=f"watch:{folder.name}")
                    transcribe_meeting(mtg, model=args.model, progress=_p)
                    _p(f"✓ {mtg.transcript_md}")
                except Exception as e:
                    _p(f"✗ Failed: {e}")
            time.sleep(15)
    except KeyboardInterrupt:
        _p("\nStopped watching")


def cmd_devices(args):
    _p(list_devices())
    _p("\nMicrophone uses \":N\" (colon + audio device index); see current config with `coco config`")


def cmd_web(args):
    import uvicorn
    from .server import app
    port = args.port or load_config()["port"]
    _p(f"🌐 coco web → http://127.0.0.1:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def cmd_config(args):
    cfg = load_config()
    if args.key and args.value is not None:
        old = cfg.get(args.key)
        val = args.value
        if isinstance(old, int):
            val = int(val)
        elif isinstance(old, list):
            val = json.loads(val)
        cfg[args.key] = val
        save_config(cfg)
        _p(f"✓ {args.key} = {val}")
    else:
        _p(json.dumps(cfg, ensure_ascii=False, indent=2))


# ---------- entry ----------

def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="coco",
        description="coco — local meeting recording / transcription / AI analysis tool",
    )
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("record", help="record and auto-transcribe (Ctrl+C to stop)")
    p.add_argument("title", nargs="?", help="meeting title")
    p.add_argument("--model", help="whisper model: turbo (default) / large")
    p.add_argument("--device", help="ffmpeg audio device, e.g. :0")
    p.set_defaults(func=cmd_record)

    p = sub.add_parser("transcribe", help="transcribe audio/video, or import a finished transcript (.txt/.md/.srt/.vtt/.json)")
    p.add_argument("files", nargs="+")
    p.add_argument("--title", help="meeting title (defaults to the file name)")
    p.add_argument("--model", help="whisper model: turbo (default) / large")
    p.add_argument("--move", action="store_true", help="move instead of copy the source file")
    p.set_defaults(func=cmd_transcribe)

    p = sub.add_parser("list", help="list the meeting library")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("show", help="show one meeting's transcript")
    p.add_argument("key", help="meeting id or title keyword")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("ask", help="ask about meeting content (defaults to the most recent)")
    p.add_argument("question")
    p.add_argument("refs", nargs="*", help="meetings to reference (one or more)")
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("report", help="generate an analysis report from a template")
    p.add_argument("key", help="meeting id or title keyword")
    p.add_argument("-t", "--template", default="minutes",
                   choices=list(TEMPLATES), help="template (default: minutes)")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("templates", help="list available analysis templates")
    p.set_defaults(func=cmd_templates)

    p = sub.add_parser("brief", help="generate the daily brief")
    p.add_argument("date", nargs="?", help="date YYYY-MM-DD (default: today)")
    p.set_defaults(func=cmd_brief)

    p = sub.add_parser("memory", help="view/append global memory")
    p.add_argument("add", nargs="?", help="text to append")
    p.set_defaults(func=cmd_memory)

    p = sub.add_parser("delete", help="delete a meeting (move to trash library/_trash)")
    p.add_argument("key", help="meeting id or title keyword")
    p.add_argument("-y", "--yes", action="store_true", help="skip confirmation")
    p.set_defaults(func=cmd_delete)

    p = sub.add_parser("search", help="full-text search across all transcripts and reports")
    p.add_argument("query")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("track", help="cross-meeting tracking: follow-through / shifts / open issues")
    p.add_argument("focus", nargs="?", help="person/project/client to focus on (blank = global)")
    p.set_defaults(func=cmd_track)

    p = sub.add_parser("memorize", help="extract long-term memory from meetings (auto after transcription)")
    p.add_argument("key", nargs="?", help="meeting id or title keyword")
    p.add_argument("--all", action="store_true", help="process all not-yet-extracted meetings")
    p.add_argument("--force", action="store_true", help="re-extract even already-extracted ones")
    p.set_defaults(func=cmd_memorize)

    p = sub.add_parser("watch", help="watch a folder; new audio is transcribed automatically")
    p.add_argument("folder")
    p.add_argument("--model", help="whisper model")
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("devices", help="list recording devices")
    p.set_defaults(func=cmd_devices)

    p = sub.add_parser("web", help="start the local web interface")
    p.add_argument("--port", type=int)
    p.set_defaults(func=cmd_web)

    p = sub.add_parser("config", help="view/change configuration")
    p.add_argument("key", nargs="?")
    p.add_argument("value", nargs="?")
    p.set_defaults(func=cmd_config)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return
    ensure_dirs()
    try:
        args.func(args)
    except (LookupError, RuntimeError, FileNotFoundError, ai.AIError) as e:
        _p(f"✗ {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
