# Agent setup guide

This file is for a coding agent (Claude Code or similar) asked to set up and run **coco**
on the user's machine. Follow it top to bottom. coco is a fully local meeting tool:
record/import audio → local Whisper transcription → AI analysis via the user's `claude`
CLI. No data leaves the machine; there is no API key to configure.

## 0. Hard constraint — check first

coco transcribes with `mlx-whisper`, which **only runs on Apple Silicon Macs (M1+)**.
Before doing anything, confirm the platform:

```bash
uname -m   # must print "arm64"
```

If it is not `arm64` (Intel Mac, Windows, Linux), stop and tell the user: coco's
transcription engine will not run here. Do not try to swap in another engine unless the
user explicitly asks.

## 1. Verify prerequisites, install what is missing

```bash
command -v python3 || echo "MISSING: python3 (brew install python)"
command -v ffmpeg  || echo "MISSING: ffmpeg (brew install ffmpeg)"
command -v claude  || echo "MISSING: claude CLI (https://docs.claude.com/claude-code)"
```

- `python3` and `ffmpeg` are required. Install any that are missing (Homebrew is the
  simplest path on macOS).
- The `claude` CLI is required for AI features (reports, chat, brief, tracking) and must
  be logged in. Transcription alone works without it — if `claude` is missing, set the
  tool up anyway and note to the user that AI features need Claude Code.

## 2. Set up and start

The launcher bootstraps its own virtual environment and installs dependencies on the
first run, so this is all that is needed:

```bash
./run.sh
```

This creates `.venv`, installs `requirements.txt`, starts the web server on
http://127.0.0.1:8765, and opens the browser. The first transcription downloads a
Whisper model automatically (~1.5GB for the default `turbo`, ~3GB for `large`).

If you prefer to set up without launching the browser:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
PYTHONPATH="$(pwd)" .venv/bin/python -m coco web   # serve, or use other CLI subcommands
```

## 3. Verify it works

```bash
# the server should answer with HTML
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8765/   # expect 200

# the CLI should run and list subcommands
./bin/coco --help
./bin/coco templates
```

For an end-to-end check, ask the user for a short audio/video file and run
`./bin/coco transcribe <file>`, then `./bin/coco list`.

## 4. Things to know

- **Importing finished transcripts**: besides audio/video, the user can drop in a
  transcript already produced elsewhere (`.txt`, `.md`, `.srt`, `.vtt`, or Whisper-style
  `.json`) via Upload, drag-drop, Local path, or `./bin/coco transcribe file.srt`. It is
  imported without re-transcribing, and every analysis feature works on it. Subtitle and
  JSON timestamps are preserved as `[mm:ss]` lines.
- **Languages**: the UI is English/French (top-bar switch). Transcription auto-detects
  the spoken language; AI analysis answers in the meeting's language. To force a
  transcription language: `./bin/coco config language fr` (or `en`, `zh`, …).
- **Privacy / git**: `library/`, `memory/` and `coco.config.json` hold the user's
  recordings and notes and are git-ignored. Never commit them, never move them out of
  the machine.
- **Config**: `./bin/coco config` prints current settings; `./bin/coco config <key> <value>`
  changes one. Useful keys: `whisper_model` (turbo/large), `language`, `claude_extra_args`.
- **Logs**: `logs/coco.log` when started via `run.sh`.
- **System audio**: capturing the other side of a call needs a virtual audio device
  (e.g. BlackHole); then `./bin/coco devices` and set `audio_device`.

See `README.md` for the full feature list and CLI reference.
