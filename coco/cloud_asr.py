"""Cloud transcription via Alibaba Bailian (DashScope) realtime ASR.

Used when this machine cannot run mlx-whisper (Intel Mac, weak hardware) or when
asr_backend is set to "cloud". The audio is converted to 16 kHz mono mp3 with
ffmpeg, then streamed to paraformer-realtime-v2 over the DashScope SDK — no
public URL or OSS bucket is needed, and sentence-level timestamps are preserved
so the transcript format stays identical to the local pipeline.

Note: unlike the local pipeline, the audio does leave the machine (it is sent to
Alibaba Cloud under the company's Bailian account).
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from .config import load_config

# Languages paraformer-realtime-v2 accepts as hints. Other languages (fr, de, …)
# must use the local backend.
PARAFORMER_LANGS = {"zh", "en", "ja", "yue", "ko"}


class CloudASRError(RuntimeError):
    pass


def _api_key(cfg: dict) -> str:
    key = cfg.get("dashscope_api_key") or os.environ.get("DASHSCOPE_API_KEY", "")
    if not key:
        raise CloudASRError(
            "Cloud transcription needs a Bailian API key: set the DASHSCOPE_API_KEY "
            "environment variable or \"dashscope_api_key\" in coco.config.json"
        )
    return key


def _prepare_audio(src: Path, tmpdir: Path) -> Path:
    """Extract/convert to 16 kHz mono mp3 (small upload, format paraformer accepts)."""
    out = tmpdir / "audio.mp3"
    proc = subprocess.run(
        ["ffmpeg", "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
         "-b:a", "64k", str(out)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0 or not out.exists():
        raise CloudASRError(f"ffmpeg conversion failed: {(proc.stderr or '')[-300:]}")
    return out


def _language_hints(cfg: dict, language: str | None) -> list[str]:
    lang = language if language is not None else cfg.get("language")
    if not lang or lang == "auto":
        hints = cfg.get("cloud_language_hints") or ["zh", "en"]
        return [h for h in hints if h in PARAFORMER_LANGS] or ["zh", "en"]
    if lang not in PARAFORMER_LANGS:
        raise CloudASRError(
            f"Cloud model paraformer-realtime-v2 supports {'/'.join(sorted(PARAFORMER_LANGS))} "
            f"only; for \"{lang}\" use the local model (asr_backend: local)"
        )
    return [lang]


def transcribe_file_cloud(audio: Path, language: str | None = None,
                          progress=lambda msg: None) -> dict:
    """Transcribe via Bailian -> same shape as the local pipeline:
    {text, segments, duration, model, language}."""
    cfg = load_config()
    model = cfg.get("cloud_asr_model", "paraformer-realtime-v2")
    hints = _language_hints(cfg, language)
    try:
        import dashscope
        from dashscope.audio.asr import Recognition
    except ImportError:
        raise CloudASRError(
            "The dashscope package is not installed; run: pip install dashscope"
        )
    dashscope.api_key = _api_key(cfg)

    with tempfile.TemporaryDirectory() as td:
        progress("Converting audio for cloud transcription…")
        mp3 = _prepare_audio(audio, Path(td))
        size_mb = mp3.stat().st_size / 1e6
        progress(f"Streaming to Bailian ({model}, {size_mb:.0f} MB)…")
        kwargs = {"language_hints": hints}
        if cfg.get("vocabulary_id"):
            kwargs["vocabulary_id"] = cfg["vocabulary_id"]
        rec = Recognition(model=model, format="mp3", sample_rate=16000,
                          callback=None, **kwargs)
        result = rec.call(str(mp3))

    if getattr(result, "status_code", None) != 200:
        msg = getattr(result, "message", "") or "unknown error"
        raise CloudASRError(f"Bailian transcription failed: {msg}")
    sentences = result.get_sentence() or []
    segments = []
    for s in sentences:
        text = (s.get("text") or "").strip()
        if not text:
            continue
        begin = s.get("begin_time") or 0
        end = s.get("end_time") or begin
        segments.append({"start": round(begin / 1000, 2),
                         "end": round(end / 1000, 2), "text": text})
    if not segments:
        raise CloudASRError("Bailian returned an empty transcript (silent audio, or an "
                            "unsupported language for this model)")
    return {
        "text": " ".join(s["text"] for s in segments),
        "segments": segments,
        "duration": segments[-1]["end"],
        "model": model,
        "language": (language if language not in (None, "auto") else
                     "+".join(hints)),
    }
