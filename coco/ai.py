"""AI analysis layer: calls the local `claude` CLI (claude -p), no API key needed."""
from __future__ import annotations

import datetime as dt
import os
import subprocess
import threading

from .config import (BRIEFS_DIR, LONGTERM_FILE, MEMORY_FILE, TRACKING_DIR,
                     load_config)
from .library import Meeting, list_meetings, meetings_on
from .templates import (BRIEF_PROMPT, CHAT_SYSTEM, LONGTERM_PROMPT,
                        TRACK_PROMPT, TEMPLATES, template_label)

MAX_CONTEXT_CHARS = 400_000  # cap the transcript volume injected into claude
MAX_LONGTERM_INJECT = 30_000  # cap on long-term memory injected into an analysis
MEMORY_LOCK = threading.Lock()  # serialize long-term memory read/modify/write


class AIError(RuntimeError):
    pass


PROOFREAD_PROMPT = (
    "Below is a meeting transcript produced by automatic (Whisper) speech recognition. "
    "It may contain homophone errors, mis-spelled proper nouns, and punctuation/segmentation "
    "issues. Proofread it line by line:\n"
    "1. Only fix obvious recognition / proper-noun / punctuation errors; do not rewrite what "
    "was said and do not add or remove information\n"
    "2. Strictly preserve every [timestamp] and the original line structure and line count\n"
    "3. Leave the title and metadata lines at the top of the file untouched\n"
    "4. For names, brands and terms, follow the spellings given in global memory\n"
    "5. Keep the original language of the transcript; do not translate\n"
    "Output the proofread Markdown in full, with no explanation or preamble."
)


def proofread(mtg: "Meeting") -> str:
    """AI proofread: fix homophones/proper nouns/punctuation, back up raw to transcript.raw.md."""
    text = mtg.transcript_text()
    if not text:
        raise AIError(f"Meeting {mtg.id} has no transcript yet")
    if len(text) > 60_000:
        raise AIError("Transcript exceeds 60k characters; whole-file proofread is not "
                      "supported (edit or split it first)")
    out = run_claude(
        f"{_memory_block()}{PROOFREAD_PROMPT}\n\n<transcript>\n{text}\n</transcript>",
        timeout=1200,
    )
    if len(out) < len(text) * 0.5:
        raise AIError("Proofread output looks wrong (more than half shorter than the "
                      "original); aborted, the original is untouched")
    raw = mtg.path / "transcript.raw.md"
    if not raw.exists():
        raw.write_text(text, encoding="utf-8")  # back up the original machine transcript only
    mtg.transcript_md.write_text(out.rstrip() + "\n", encoding="utf-8")
    mtg.save_meta(proofread_at=dt.datetime.now().isoformat(timespec="seconds"))
    return out


def run_claude(prompt: str, timeout: int = 900) -> str:
    cfg = load_config()
    cmd = [cfg["claude_bin"], "-p", "--output-format", "text",
           *cfg.get("claude_extra_args", [])]
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    try:
        proc = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True,
            timeout=timeout, env=env,
        )
    except FileNotFoundError:
        raise AIError(f"claude CLI not found ({cfg['claude_bin']}); make sure it is "
                      "installed and logged in")
    except subprocess.TimeoutExpired:
        raise AIError(f"claude timed out (>{timeout}s)")
    if proc.returncode != 0:
        raise AIError(f"claude call failed: {(proc.stderr or proc.stdout).strip()[:500]}")
    return proc.stdout.strip()


def _memory_block() -> str:
    parts = []
    if MEMORY_FILE.exists():
        text = MEMORY_FILE.read_text(encoding="utf-8").strip()
        if text:
            parts.append(f"<global_memory>\n{text}\n</global_memory>")
    if LONGTERM_FILE.exists():
        text = LONGTERM_FILE.read_text(encoding="utf-8").strip()
        if text:
            parts.append(f"<long_term_memory note=\"accumulated automatically from past "
                         f"meetings\">\n{text[:MAX_LONGTERM_INJECT]}\n</long_term_memory>")
    return "\n\n".join(parts) + "\n\n" if parts else ""


def _context_block(meetings: list[Meeting]) -> str:
    parts, total = [], 0
    for m in meetings:
        t = m.transcript_text()
        if not t:
            continue
        if total + len(t) > MAX_CONTEXT_CHARS:
            t = t[: MAX_CONTEXT_CHARS - total] + "\n…(transcript too long, truncated)"
        parts.append(f"<meeting id=\"{m.id}\">\n{t}\n</meeting>")
        total += len(t)
        if total >= MAX_CONTEXT_CHARS:
            break
    return "\n\n".join(parts)


def ask(question: str, meetings: list[Meeting]) -> str:
    ctx = _context_block(meetings)
    if not ctx:
        raise AIError("The referenced meeting has no transcript yet")
    prompt = f"{CHAT_SYSTEM}\n\n{_memory_block()}{ctx}\n\n<question>\n{question}\n</question>"
    return run_claude(prompt)


def generate_report(mtg: Meeting, template: str) -> "tuple[str, str]":
    """Generate a templated report, write to the meeting's reports/, return (path, content)."""
    if template not in TEMPLATES:
        raise AIError(f"Unknown template: {template} (available: {', '.join(TEMPLATES)})")
    text = mtg.transcript_text()
    if not text:
        raise AIError(f"Meeting {mtg.id} has no transcript yet")
    prompt = (
        f"{CHAT_SYSTEM}\n\n{_memory_block()}"
        f"<meeting id=\"{mtg.id}\">\n{text[:MAX_CONTEXT_CHARS]}\n</meeting>\n\n"
        f"<task>\n{TEMPLATES[template]['prompt']}\n</task>\n\n"
        "Output the Markdown report body directly, with no pleasantries or preamble."
    )
    content = run_claude(prompt)
    mtg.reports_dir.mkdir(exist_ok=True)
    stamp = dt.datetime.now().strftime("%H%M")
    path = mtg.reports_dir / f"{template}-{stamp}.md"
    path.write_text(f"# {mtg.title} · {template_label(template)}\n\n{content}\n",
                    encoding="utf-8")
    return str(path), content


def _merge_longterm(source_id: str, text: str, kind: str = "meeting transcript") -> str:
    """Merge one source (meeting transcript / daily brief) into memory/longterm.md."""
    with MEMORY_LOCK:
        old = (LONGTERM_FILE.read_text(encoding="utf-8")
               if LONGTERM_FILE.exists() else "")
        prompt = (
            f"{LONGTERM_PROMPT}\n\n"
            f"<current_long_term_memory>\n{old.strip() or '(still empty)'}\n"
            f"</current_long_term_memory>\n\n"
            f"<new_source kind=\"{kind}\" id=\"{source_id}\">\n"
            f"{text[:100_000]}\n</new_source>"
        )
        out = run_claude(prompt, timeout=1200)
        if "## " not in out:
            raise AIError("Long-term memory output has an unexpected format; not updated")
        if len(old) > 2000 and len(out) < len(old) * 0.3:
            raise AIError("Long-term memory output is far shorter than before (likely lost "
                          "information); not updated")
        if old.strip():
            LONGTERM_FILE.with_suffix(".bak.md").write_text(old, encoding="utf-8")
        LONGTERM_FILE.write_text(out.rstrip() + "\n", encoding="utf-8")
    return out


def update_longterm(mtg: Meeting, force: bool = False) -> str:
    """Extract long-term memory from one meeting and merge into memory/longterm.md.

    Returns the updated full text; returns "" when skipped. Each meeting is extracted
    once (meta.memorized_at); the previous version is backed up to longterm.bak.md.
    """
    if mtg.meta.get("memorized_at") and not force:
        return ""
    text = mtg.transcript_text()
    if len(text) < 200:  # too short (test / empty transcript), nothing worth extracting
        mtg.save_meta(memorized_at="skipped-too-short")
        return ""
    out = _merge_longterm(mtg.id, text)
    mtg.save_meta(memorized_at=dt.datetime.now().isoformat(timespec="seconds"))
    return out


BRIEFS_MEMORIZED = LONGTERM_FILE.parent / ".briefs_memorized.json"


def memorize_brief(path, force: bool = False) -> str:
    """Merge a daily brief into long-term memory. Briefs often hold cross-meeting
    action-item roundups and strategic insight.

    Merged briefs are recorded in memory/.briefs_memorized.json; repeated calls skip
    (force redoes it).
    """
    import json as _json
    from pathlib import Path as _Path
    path = _Path(path)
    done = set(_json.loads(BRIEFS_MEMORIZED.read_text(encoding="utf-8"))
               if BRIEFS_MEMORIZED.exists() else [])
    if path.name in done and not force:
        return ""
    text = path.read_text(encoding="utf-8")
    if len(text) < 200:
        return ""
    out = _merge_longterm(f"daily-brief-{path.stem}", text, kind="daily brief")
    done.add(path.name)
    BRIEFS_MEMORIZED.write_text(_json.dumps(sorted(done), ensure_ascii=False),
                                encoding="utf-8")
    return out


def track(focus: str = "") -> "tuple[str, str]":
    """Cross-meeting tracking: follow-through, shifts in position, recurring open issues."""
    meetings = [m for m in list_meetings() if m.transcript_md.exists()]
    if len(meetings) < 2:
        raise AIError("Cross-meeting tracking needs at least two transcribed meetings")
    ctx = _context_block(list(reversed(meetings)))  # chronological order
    focus_line = (f"\nThis tracking focuses on: {focus}. Mention other content only when "
                  f"relevant to it.\n" if focus.strip() else "")
    prompt = f"{_memory_block()}{TRACK_PROMPT}{focus_line}\n\n{ctx}"
    content = run_claude(prompt, timeout=1200)
    TRACKING_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d-%H%M")
    name = f"{stamp}-{focus.strip()[:20]}" if focus.strip() else stamp
    path = TRACKING_DIR / f"{name}.md"
    title = f"# Cross-meeting tracking · {focus.strip() or 'All meetings'} · {stamp}"
    content = _dedupe_title(content)
    path.write_text(f"{title}\n\n{content}\n", encoding="utf-8")
    return str(path), content


EDIT_PLAN_PROMPT = (
    "You are coco's correction assistant. The user describes, in plain language, a fix to "
    "apply across their meeting library: a misrecognized name or term, wrong wording in "
    "minutes, or a fact to remember. Output ONLY a JSON object (no markdown fence, no "
    "commentary) with exactly this shape:\n"
    '{"replacements": [{"find": "<exact literal text>", "replace": "<corrected text>", '
    '"scope": "all"}],\n'
    ' "memory_append": "<0-3 markdown bullet lines for global memory: name mappings, '
    'canonical spellings; or empty string>",\n'
    ' "hotwords": ["<correct spellings of proper nouns, to bias future transcription>"],\n'
    ' "note": "<one short sentence for the user, in the user\'s language>"}\n'
    "Rules:\n"
    "1. find must be an exact literal substring visible in the material below (or an "
    "obvious ASR variant the user cites); never regex, never approximate\n"
    "2. Prefer several precise replacements (each misspelling variant separately) over one "
    "broad one; never use a find string so short it would hit unrelated text\n"
    "3. scope is \"all\" (whole library) or a meeting id when the fix belongs to one "
    "meeting only\n"
    "4. If the instruction is a question rather than a correction, return an empty "
    "replacements list and explain in note\n"
    "5. memory_append lines must be durable facts (e.g. \"- 'Li Zong' in transcripts = "
    "黎总, CFO at client X\"), not restatements of the edit"
)


def propose_edits(instruction: str, mtg: "Meeting | None" = None) -> dict:
    """Turn a plain-language correction into a structured edit plan (not applied here)."""
    material = _memory_block()
    if mtg is not None:
        t = mtg.transcript_text()
        if t:
            material += f"<meeting id=\"{mtg.id}\">\n{t[:40_000]}\n</meeting>\n\n"
    prompt = (f"{EDIT_PLAN_PROMPT}\n\n{material}"
              f"<instruction>\n{instruction}\n</instruction>")
    out = run_claude(prompt, timeout=600)
    plan = _parse_json_object(out)
    reps = []
    for r in plan.get("replacements") or []:
        find = str(r.get("find") or "")
        if not find.strip() or len(find.strip()) < 2:
            continue  # too short / empty: would hit unrelated text
        reps.append({"find": find, "replace": str(r.get("replace") or ""),
                     "scope": str(r.get("scope") or "all")})
    return {
        "replacements": reps,
        "memory_append": str(plan.get("memory_append") or "").strip(),
        "hotwords": [str(h).strip() for h in (plan.get("hotwords") or []) if str(h).strip()],
        "note": str(plan.get("note") or "").strip(),
    }


def _parse_json_object(out: str) -> dict:
    """Parse a JSON object from model output, tolerating markdown fences around it."""
    import json as _json
    s = out.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1].rsplit("```", 1)[0]
    start, end = s.find("{"), s.rfind("}")
    if start < 0 or end <= start:
        raise AIError(f"Correction plan is not valid JSON: {out[:200]}")
    try:
        obj = _json.loads(s[start:end + 1])
    except _json.JSONDecodeError as e:
        raise AIError(f"Correction plan is not valid JSON ({e}): {out[:200]}")
    if not isinstance(obj, dict):
        raise AIError("Correction plan is not a JSON object")
    return obj


def _dedupe_title(content: str) -> str:
    """If the model output starts with its own top-level heading, drop it so it does not
    duplicate the canonical title we prepend."""
    stripped = content.lstrip("\n")
    if stripped.startswith("# "):
        _, _, rest = stripped.partition("\n")
        return rest.lstrip("\n")
    return content


def daily_brief(date: str | None = None) -> "tuple[str, str]":
    """Summarize all of one day's meetings into a daily brief, return (path, content)."""
    date = date or dt.date.today().isoformat()
    meetings = [m for m in meetings_on(date) if m.transcript_md.exists()]
    if not meetings:
        raise AIError(f"No transcribed meetings on {date}")
    ctx = _context_block(list(reversed(meetings)))  # chronological order
    prompt = f"{_memory_block()}{BRIEF_PROMPT}\n\nDate: {date}\n\n{ctx}"
    title = f"# Daily brief · {date}"
    content = _dedupe_title(run_claude(prompt))
    BRIEFS_DIR.mkdir(parents=True, exist_ok=True)
    path = BRIEFS_DIR / f"{date}.md"
    path.write_text(f"{title}\n\n{content}\n", encoding="utf-8")
    return str(path), content
