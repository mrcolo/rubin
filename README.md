# rubin

A minimal, dependency-free MCP server that turns Logic Pro into an
AI-playable instrument: compose MIDI, load any factory patch (Alchemy
included), and drive the transport.

![A rubin-composed 40-bar arrangement playing in Logic Pro — five tracks
composed as MIDI, Alchemy patches loaded via the Library, piano roll showing
the lead hook](docs/rubin-in-logic.png)

*Everything in this screenshot was placed by rubin: the five tracks and
their regions (composed as a Standard MIDI File), the Alchemy patches on
each channel strip (loaded through the Library), tempo, and key.*

## Why it works

UI-automation-first tools (clicking Logic's interface for every note) fail
silently and read garbage state. rubin flips the approach:

- **Composition is a file, not clicks.** Notes, CC automation, pitch bends,
  tempo, and time signature are written into a real Standard MIDI File —
  deterministic, testable, no UI involved. Logic maps each track's GM program
  to a software instrument on import.
- **Patch discovery is a disk read, not a guess.** Logic's ~6,000 factory
  patches live on disk with the synth engine recorded inside each bundle;
  `find_patches` filters them by name, category, or engine (`Alchemy`,
  `Retro Synth`, `ES2`, `Sculpture`...).
- **UI scripting is a last resort, and verified.** Only patch loading,
  import, and transport touch the UI — anchored to stable accessibility
  landmarks (the Library's "Search Sounds" field, track-header fields), with
  `list_tracks` reading real state back so nothing fails silently.

## Tools

| Tool | What it does |
|------|--------------|
| `compose_midi` | Write a .mid from notes / CC / bends / volume / pan per track |
| `open_midi_as_project` | Open a .mid in Logic as a new project (no dialogs, most reliable) |
| `import_midi` | File > Import > MIDI File into the open project at bar 1 |
| `compose_and_import` | Both steps in one call |
| `transcribe_audio` | Audio → MIDI via [basic-pitch](https://github.com/spotify/basic-pitch), content-hash cached |
| `list_transcriptions` | Browse the transcription cache |
| `analyze_midi` | Summarize any .mid: key + swing detection, range, density, polyphony, tempo |
| `find_patches` | Search the on-disk factory patch index (name / category / engine) |
| `find_channel_strips` | Discover factory FX-chain settings (.cst) — names for the Setting menu; not Library-loadable |
| `select_track` | Select track N (1-based) |
| `load_patch` | Load a Library patch onto the selected track; returns the loaded name |
| `list_tracks` | Read back `[{name, patch}]` from the open project |
| `save_project` | Save the project (Cmd+S); reports the sheet if the project is unnamed |
| `transport` | play / stop / record / go_to_beginning |
| `answer_dialog` | Click through a Logic sheet/dialog (e.g. tempo-import prompt) |
| `logic_status` | Is Logic running + front window title |

Note format: `{start, dur, pitch, vel}` — start/dur in beats, pitch 0–127
(60 = middle C), velocity 1–127. Channel 9 is GM drums (36 kick, 38 snare,
39 clap, 42 closed hat, 46 open hat). Tracks also accept `cc`
(`{beat, controller, value}`), `bends` (`{beat, value}` −8192…8191),
`program`, `volume`, `pan`. Compositions accept `time_sig` (`[3, 4]`),
`key` (`"Am"`, `"Eb"`, `"F#m"`), `tempo_changes` (`[{beat, bpm}]`), and
`swing` (50 = straight, ~62 = MPC feel; `swing_unit` 0.5/0.25 for
8th/16th swing; per-track override supported).

### Example: an Alchemy pad in three calls

```
find_patches {category: "Synthesizer/Pad", plugin: "Alchemy"}
  → [{"name": "Drifting Away", ...}, ...]
select_track {index: 3}
load_patch {query: "Drifting Away"}
  → Loaded patch 'Drifting Away' on the selected track
```

## Ears: audio in, MIDI out

`transcribe_audio` turns any recording — a hummed melody, a guitar take, a
sample — into MIDI with Spotify's basic-pitch, cached by content hash under
`~/.cache/rubin/midi` so repeat calls are free. `analyze_midi` then reads the
result back (rubin has its own SMF parser) and reports what the instrument
plays: pitch range, note density, polyphony. That loop — hear it, read it,
compose against it — is how rubin learns what source material sounds like.

## Install

```sh
claude mcp add --scope user rubin -- /usr/bin/python3 ~/dev/rubin/server.py
# optional, for transcribe_audio:
cd ~/dev/rubin && python3 -m venv .venv-bp && .venv-bp/bin/pip install basic-pitch
```

Requires macOS Accessibility + Automation permissions for the host app
(Claude / terminal) — only for the UI-touching tools. `compose_midi`,
`find_patches`, and `open_midi_as_project` need no permissions at all.

## Test & demo

```sh
python3 -m unittest discover -s tests   # 22 tests, no Logic needed for most
python3 server.py --demo                # compose + open a demo beat in Logic
python3 server.py --demo --write-only   # just write the .mid
```

## Files

- `server.py` — MCP stdio server (hand-rolled JSON-RPC, no SDK)
- `midi.py` — Standard MIDI File writer
- `midi_read.py` — SMF parser + per-track analyzer
- `transcribe.py` — basic-pitch wrapper + content-addressed cache
- `patches.py` — factory patch index reader
- `logic_ctl.py` — the minimal AppleScript/AX layer
- `demo_beat.py` — 8-bar dark R&B demo (85 BPM, Am–F–C–E)
- `tests/` — unit + protocol tests

## Known limits

- Patch loading, track selection, and import drive Logic's UI; they're
  anchored to stable AX landmarks (bounded subtree scans, never the whole
  window — real projects have thousands of AX elements) but a future Logic
  redesign could move them. `list_tracks` exists so failures are visible,
  not silent.
- Logic auto-renames a track to its patch name after `load_patch` unless the
  track was named manually first.
- Tested on Logic Pro X 10.x/11 ("Logic Pro X.app" and "Logic Pro.app" are
  both handled), macOS 14+.
