"""Audio -> MIDI transcription cache, powered by Spotify's basic-pitch.

basic-pitch lives in its own venv (.venv-bp) so the MCP server stays
dependency-free; transcription shells out to its CLI. Results are cached
content-addressed (sha256 of the audio) under ~/.cache/rubin/midi with an
index recording where each MIDI came from.
"""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile

CACHE_DIR = os.path.expanduser("~/.cache/rubin/midi")
INDEX_PATH = os.path.join(CACHE_DIR, "index.json")

_BP_CANDIDATES = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".venv-bp", "bin", "basic-pitch"),
    shutil.which("basic-pitch") or "",
]

AUDIO_EXTS = {".wav", ".mp3", ".aif", ".aiff", ".flac", ".m4a", ".ogg"}


def _bp_bin():
    for cand in _BP_CANDIDATES:
        if cand and os.path.isfile(cand):
            return cand
    raise RuntimeError(
        "basic-pitch not installed. Run: python3 -m venv .venv-bp && "
        ".venv-bp/bin/pip install basic-pitch (in the rubin directory)"
    )


def _load_index():
    try:
        with open(INDEX_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_index(index):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(INDEX_PATH, "w") as f:
        json.dump(index, f, indent=1)


def _sha256(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def transcribe(audio_path, label=None):
    """Transcribe an audio file to MIDI; returns the cache entry dict.

    Cached by audio content hash — re-transcribing the same audio is free.
    """
    audio_path = os.path.expanduser(audio_path)
    if not os.path.isfile(audio_path):
        raise ValueError("no such audio file: %s" % audio_path)
    if os.path.splitext(audio_path)[1].lower() not in AUDIO_EXTS:
        raise ValueError(
            "unsupported extension %s (want one of %s)"
            % (os.path.splitext(audio_path)[1], sorted(AUDIO_EXTS))
        )

    digest = _sha256(audio_path)
    index = _load_index()
    if digest in index and os.path.isfile(index[digest]["midi"]):
        return index[digest]

    midi_path = os.path.join(CACHE_DIR, digest[:16] + ".mid")
    os.makedirs(CACHE_DIR, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        p = subprocess.run(
            [_bp_bin(), tmp, audio_path, "--save-midi"],
            capture_output=True, text=True, timeout=600,
        )
        if p.returncode != 0:
            raise RuntimeError("basic-pitch failed: %s" % (p.stderr.strip()[-500:]))
        produced = [f for f in os.listdir(tmp) if f.endswith(".mid")]
        if not produced:
            raise RuntimeError("basic-pitch produced no MIDI")
        shutil.move(os.path.join(tmp, produced[0]), midi_path)

    entry = {
        "midi": midi_path,
        "source": os.path.abspath(audio_path),
        "label": label or os.path.splitext(os.path.basename(audio_path))[0],
        "sha256": digest,
    }
    try:
        from rubin import midi_read

        entry["summary"] = midi_read.describe(midi_path)
    except Exception:
        pass  # a summary is a nicety; the transcription itself succeeded
    index[digest] = entry
    _save_index(index)
    return entry


def list_transcriptions(query=None):
    """List cached transcriptions, optionally filtered by label/source substring."""
    q = (query or "").lower()
    out = []
    for entry in _load_index().values():
        hay = (entry.get("label", "") + " " + entry.get("source", "")).lower()
        if q and q not in hay:
            continue
        out.append(entry)
    return out
