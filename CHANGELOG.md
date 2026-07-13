# Changelog

rubin turns Logic Pro into an AI-playable instrument, and more broadly is a
zero-dependency toolkit for making music as files instead of DAW clicks. Two
parallel engines — **MIDI** and **audio** — each with generate / transform /
render / analyze, all standard-library only.

## 1.5.0

The audio engine, and the bridge between the two.

- **`wave_edit`** — a from-scratch WAV slicer/arranger: decode 16/24/32-bit
  PCM (fast, via `audioop`), slice / pitch / reverse / fade / pan / normalize
  / soft-limit, and arrange clips on a tempo grid into a rendered mix.
- **`cut_samples`** — cut audio samples into a song: chop, tune to a note
  (`pitch_to`/`from_note`), and repeat patterns compactly. Deterministic,
  no DAW region UI.
- **Synthesis** — `Clip.tone` / `Clip.noise` generate sound from nothing;
  `--cut-demo` renders a track with no external files.
- **`render_midi_audio`** — bounce any `.mid` to WAV with a built-in synth
  (sine voices + noise/tone drums), so composed MIDI can be layered with
  sample cuts. This connects the two engines.
- **`catalog_samples`** — report each sample's pitch / key / kind
  (pitched / sub / noise) so they can be tuned and placed.
- **`analyze_audio`** — RMS energy contour to *see* a render's dynamics
  without hearing it; the audio counterpart to `analyze_midi`.
- **`reveal_in_finder`** — the reliable route to place audio in Logic
  (drag-drop), since Logic's audio import isn't scriptable.
- Full audio-pipeline integration test: compose → render → cut → analyze.
- Recovered from an empty-`server.py` commit (a truncating-write bug);
  added module-integrity tests so it can never ship again.

## 1.4.0 and earlier

The MIDI engine and Logic control.

- **Composition**: `compose_midi` with notes, CC, pitch bends, key,
  time signature, tempo maps, swing, and downbeat-anchored humanization.
  Shorthands: `progression` (pad / bass / arp / melody styles, voice-led),
  `drums` (half_time / four_on_floor / boom_bap / trap), and `song`
  (full arrangement from a chord loop + section plan, staged entrances).
- **Analysis**: `analyze_midi` / `describe_midi` — key, chord, and swing
  detection (all abstain rather than guess), density contour, and warnings
  (register clashes, low-end conflicts, flat velocities).
- **Transcription**: `transcribe_audio` via Spotify's basic-pitch,
  content-hash cached; `suggest_accompaniment` turns any source into
  ready compose args that fit its key, feel, and registers.
- **Logic control**: `open_midi_as_project` / `import_midi` (abort-safe —
  never blind-types a path), patch discovery across ~6,200 factory patches +
  channel strips + Surge presets, `load_patch` verified against the channel
  strip, click-free `select_track`, and `logic_status` state reporting.
- Packaged as `rubin-mcp` (pip-installable, console entry point); CI across
  Linux/macOS × Python 3.9/3.12.
