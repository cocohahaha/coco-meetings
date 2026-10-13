# coco — local meeting recording / transcription / AI analysis

Record or import audio, transcribe it with Whisper (locally) or Alibaba Bailian
(cloud), then turn it into AI analysis reports, daily briefs and cross-meeting
tracking. The AI layer calls the `claude` CLI you already have.

In the default local mode everything stays on your machine and no audio leaves your
laptop. Machines that cannot run the local model (Intel Mac, Windows, weak hardware)
can switch transcription to the cloud backend instead — audio is then sent to Alibaba
Cloud under your Bailian account (see "Cloud transcription" below).

The interface is bilingual (English / Français, switch in the top bar). Transcription
auto-detects the spoken language, and every AI analysis answers in the language of the
meeting itself — a French meeting yields a French report, an English meeting an English
one.

## Requirements

- **Apple Silicon Mac** (M1 or later) for local transcription: `mlx-whisper` is built
  for Apple Silicon. On Intel Macs / Windows / Linux, or on machines too weak to run
  it comfortably, use cloud transcription instead (see below) — everything else works
  the same.
- **ffmpeg** — `brew install ffmpeg`
- **Claude Code CLI** — needed for the AI features (reports, chat, daily brief,
  tracking). Install and log in once: https://docs.claude.com/claude-code
  Transcription alone works without it.
- Python 3.9+ (the bundled scripts create their own virtual environment).

## Quick start

If you use a coding agent (Claude Code, etc.), the fastest path is to hand it this
repo and the included `AGENTS.md` — it tells the agent exactly how to set up and run
the tool. Otherwise:

```bash
git clone <this-repo-url> coco
cd coco
./run.sh          # first run creates the venv, installs deps, starts the web UI
```

`./run.sh` opens http://127.0.0.1:8765 in your browser. The first run downloads the
Whisper model (the default `turbo` model is ~1.5GB, `large` ~3GB). You can also
double-click `coco.command` in Finder instead of using the terminal.

Three ways to add a meeting in the web UI: **drag a file into the window**, the "Upload"
button (multi-select), or "Local path" (paste a file, or a folder to batch-import
everything inside it). Audio and video are transcribed locally; an already-finished
transcript exported from another tool (`.txt`, `.md`, `.srt`, `.vtt`, or Whisper-style
`.json`) is imported as-is, with no re-transcription. Files queue up and finish one by
one. Transcripts, reports and daily briefs all download as `.md`.

## Features

- **Import or record** : transcribe audio/video locally, or bring in a finished
  transcript from another tool (`.txt`, `.md`, `.srt`, `.vtt`, or Whisper-style `.json`)
  and run all the same analysis on it. Subtitle and JSON timestamps are kept.
- **Transcript view** — edit the text in place (later AI analysis uses your edited
  version), or run **AI proofread** to fix recognition errors, proper nouns and
  punctuation without rewriting content (the raw machine transcript is backed up to
  `transcript.raw.md`).
- **Analysis reports** — pick a template and generate a report from the transcript.
- **Chat** — ask anything about a meeting; tick "across all meetings" to ask
  cross-session questions (how someone's position changed, who committed to what).
- **Correction mode (in the chat panel)** — describe a fix in plain language ("'Li
  Zong' is actually 黎总, fix it everywhere") and coco proposes a global find/replace
  plan across transcripts, reports and memory. Nothing is written until you confirm;
  every touched file is backed up to `.bak` first, the mapping is added to global
  memory, and the corrected names feed future transcriptions.
- **Daily brief** — consolidate a day's meetings into one brief, with history per date.
- **Cross-meeting tracking** — follow-through on commitments, shifts in position, and
  recurring unresolved questions, optionally focused on a person/project/client.
- **Global memory** — names, terms and preferences you maintain by hand, injected into
  every analysis.
- **Long-term memory (automatic)** — after each transcription coco extracts people,
  projects, commitments and terms into `memory/longterm.md` and feeds them back into
  later analysis. Any overwrite is backed up to a `.bak.md` first.
- **Search, date filters, batch export to zip, soft-delete to trash** (`library/_trash`,
  recoverable by hand).

## Templates

minutes · action items · emotional arc · tension & disagreement · cognitive bias ·
threads to pull · client follow-up · interview debrief
(edit or add your own in `coco/templates.py`).

## CLI

The web UI covers everything, but a CLI is included:

| Command | What it does |
|---|---|
| `./bin/coco transcribe <files…>` | import audio/video and transcribe (`--model turbo` for the fast model) |
| `./bin/coco list` / `show <meeting>` | browse the library / show one transcript |
| `./bin/coco ask "question" [meetings…]` | ask about meeting content |
| `./bin/coco report <meeting> -t <template>` | generate a report (`coco templates` lists them) |
| `./bin/coco brief [date]` | daily brief for a date |
| `./bin/coco track [focus]` | cross-meeting tracking |
| `./bin/coco search <term>` | full-text search across transcripts and reports |
| `./bin/coco watch <folder>` | watch a folder; new audio is transcribed automatically |
| `./bin/coco config [key value]` | view/change configuration |

Optional alias:
```bash
echo "alias coco=\"$(pwd)/bin/coco\"" >> ~/.zshrc
```

## Cloud transcription (Alibaba Bailian)

For machines that cannot run `mlx-whisper`, transcription can go through Alibaba
Bailian (DashScope) instead — the same account that powers the company `claude` proxy.
Analysis still runs through the `claude` CLI; only the audio-to-text step changes.

1. Get a Bailian API key and expose it: `export DASHSCOPE_API_KEY=sk-…` (or set
   `dashscope_api_key` in `coco.config.json`).
2. Pick "Model: cloud (Bailian)" in the top bar (or set `asr_backend` to `cloud`).
   The default `asr_backend: auto` uses the local model when the machine has one and
   falls back to the cloud otherwise, so on a Windows/Intel machine with a key set,
   things just work.

Details:

- Model: `paraformer-realtime-v2` (config `cloud_asr_model`). Audio is converted to
  16 kHz mono mp3 with ffmpeg and streamed over the DashScope SDK — no public URL or
  OSS bucket involved. Sentence timestamps are kept, so transcripts look identical to
  local ones.
- Languages: zh / en / ja / yue / ko. When `language` is `auto`, recognition is biased
  by `cloud_language_hints` (default `["zh","en"]`). For other languages (fr, de, …)
  use the local backend.
- Hotwords: `initial_prompt_extra` only helps the local model. For the cloud model,
  create a hotword vocabulary in the Bailian console and set its id as
  `vocabulary_id`.
- Privacy: in cloud mode the meeting audio is uploaded to Alibaba Cloud under your
  Bailian account. Billing is per audio duration — see the Bailian console pricing
  page.

## Configuration (`coco config`)

- `asr_backend` — `auto` (default: local when available, else cloud), `local`, or
  `cloud`. Also switchable in the top bar of the web UI.
- `whisper_model` — `turbo` (default, fast) or `large` (most accurate). Also switchable
  in the top bar of the web UI.
- `language` : `auto` (default; Whisper detects the spoken language per file) or an ISO
  code like `en`, `fr`, `zh` to force one. Also selectable in the top bar of the web UI,
  and the detected language is shown on each transcript. Forcing the known language is
  more reliable than auto-detect for short or noisy clips.
- `initial_prompt_extra` — recurring names/brands/terms (any language) injected into the
  transcription prompt to improve recognition.
- `beam_size` : `0` (default, greedy decoding, fastest) or `5` for beam search, which is
  more accurate at the cost of speed. The single biggest accuracy lever once you are on
  the `large` model.
- `auto_memory` — auto-extract long-term memory after each transcription (default true).
- `claude_extra_args` — extra args for the claude CLI, e.g. `["--model","claude-sonnet-4-6"]`.

## Where data lives (all local)

```
library/<date-title>/   audio + transcript.md/json + reports/*.md
library/_briefs/         daily briefs
library/_tracking/       cross-meeting tracking reports
library/_trash/          trash (deleted meetings, recover by hand)
memory/memory.md         global memory (maintained by hand)
memory/longterm.md       long-term memory (auto-extracted, hand-editable)
coco.config.json         configuration (created on first `coco config` change)
```

These paths are listed in `.gitignore` and are never committed.

## Notes

- On the first recording, macOS asks for microphone permission for your terminal —
  allow it.
- To capture system audio (the other side of a call) you need a virtual audio device
  such as BlackHole; then run `coco devices` and set `audio_device` in the config.
- The web interface binds to `127.0.0.1` only; it is not exposed to the network.
