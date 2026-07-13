# rubin

[![tests](https://github.com/mrcolo/rubin/actions/workflows/test.yml/badge.svg)](https://github.com/mrcolo/rubin/actions/workflows/test.yml)

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
| `analyze_midi` | Full analysis: key/swing/chord detection, density contour, warnings, per-track stats |
| `describe_midi` | The same, as one readable paragraph |
| `suggest_accompaniment` | Analysis → ready compose_midi args that fit a source file (registers, key, feel) |
| `cut_samples` | Cut/arrange WAV sample slices on a tempo grid into a rendered song (no DAW) |
| `render_midi_audio` | Bounce a .mid to WAV with the built-in synth (mix MIDI arrangements with sample cuts) |
| `analyze_audio` | RMS energy contour of a WAV — 'see' a render's dynamics without hearing it |
| `catalog_samples` | Report each sample's pitch, key, and kind (pitched/sub/noise) |
| `reveal_in_finder` | Reveal a file/folder in Finder, ready to drag onto a track |
| `import_audio` | Reveal + drag guidance (Logic audio import isn't scriptable) |
| `find_patches` | Search the on-disk factory patch index (name / category / engine) |
| `find_surge_presets` | Discover installed Surge XT presets (load via Surge's browser) |
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
8th/16th swing; per-track override supported). Tracks can declare a
`progression` (`{chords: ["Am", "F", "Cmaj7", "E7"]}`) instead of raw
notes — voicings are generated (styles: pad / bass / arp / melody — the
latter writes a hook: chord tones on strong beats, one peak, resolves to
the root), with `repeat`
and `start_bar` for staged entrances; `drums: {pattern: "half_time"}`
generates a full groove. `analyze_midi` reads the same chord names back.

### Example: a full backing track from declarations

```
compose_midi {tempo: 85, key: "Am", name: "sketch", tracks: [
  {name: "Pad",   channel: 1, progression: {chords: ["Am","F","C","E"], repeat: 2}},
  {name: "Drums", channel: 9, drums: {pattern: "half_time", bars: 12, start_bar: 4}},
  {name: "Bass",  channel: 0, progression: {chords: ["Am","F","C","E"], style: "bass", start_bar: 4}},
  {name: "Arp",   channel: 2, progression: {chords: ["Am","F","C","E"], style: "arp", start_bar: 8}},
]}
describe_midi {path: "~/Desktop/sketch.mid"}
  → 4 track(s), 283 notes, ~16 bars at 85 BPM. key Am (82% confident).
    progression Am-F-C-E... dynamic arrangement (density 0.4-6.7 notes/beat). ...
```

Staged entrances (`start_bar`) give the arrangement a real build; the
analyzer verifies its own output — key, chords, contour, and warnings for
register clashes or robotic velocities.

### Example: an Alchemy pad in three calls

```
find_patches {category: "Synthesizer/Pad", plugin: "Alchemy"}
  → [{"name": "Drifting Away", ...}, ...]
select_track {index: 3}
load_patch {query: "Drifting Away"}
  → Loaded patch 'Drifting Away' on the selected track
```

## Genre starting points (stock Logic, index-verified)

| Role | Patches (exact `load_patch` names) |
|---|---|
| Future-bass chord stabs | Massive Stack, Chicago Stack, Sparkle Chord (category `EDM Chord`) |
| Vocal-chop feel | Breathy Vox, Starlight Vox, Rhythmic Vox |
| Sub bass | Deep Sub Bass, Full Sub, Super Sub 808 Bass |
| Wobble bass (CC1 = wobble) | Mega Wobble Bass, FM Wobble Bass, Sharp Wobble Bass |
| Dark pads | Drifting Away (Alchemy) |
| Trap/808 kits | Modern 808; dub: Dub Machine |
| Punk/rock | SoCal+ kit, Hard Rock guitar, Punchy Bottom bass |

rubin's CC automation maps onto these: `cc` 1 drives the wobble patches'
filter, `cc` 11 fakes sidechain pump on stabs, bends do scoops and slides.

## Ears: audio in, MIDI out

`transcribe_audio` turns any recording — a hummed melody, a guitar take, a
sample — into MIDI with Spotify's basic-pitch, cached by content hash under
`~/.cache/rubin/midi` so repeat calls are free. `analyze_midi` then reads the
result back (rubin has its own SMF parser) and reports what the instrument
plays: pitch range, note density, polyphony. That loop — hear it, read it,
compose against it — is how rubin learns what source material sounds like.

## Cut samples into a song

rubin edits audio the way it composes MIDI — as a deterministic file
operation, never through a DAW's region UI. `cut_samples` loads WAV files
(16/24/32-bit), slices/pitches/reverses/fades them, and arranges the chops
on a tempo grid into a rendered mix:

```
catalog_samples {folder: "~/Music/Samples/dubstep"}
  → Evil_1: sub, key E · Down_Growl: noise · ...
cut_samples {tempo: 140, out_path: "~/Desktop/drop.wav", events: [
  {file: ".../Evil_1.wav", at_beat: 0, repeat: {times: 8, every: 4}},        # E sub every bar
  {file: ".../Down_Growl.wav", at_beat: 0, end: 0.2, pitch: 3,
   repeat: {times: 32, every: 0.5}},                                          # growl chops
]}
```

`catalog_samples` first tells you each sample's pitch and whether it's tonal
or texture, so you can tune and place them. Output is a standard WAV any DAW
imports (or drag it in via `reveal_in_finder`). `limit: true` soft-clips
dense stacks so one loud hit doesn't thin the mix.

## Install

```sh
# from a clone (development)
claude mcp add --scope user rubin -- /usr/bin/python3 ~/dev/rubin/server.py

# or installed as a package
pip install git+https://github.com/mrcolo/rubin
claude mcp add --scope user rubin -- rubin-mcp
python3 server.py --check   # environment health: Logic, indexes, AUs, transcription
python3 server.py --verify song.mid   # pre-flight: analysis + warnings, exit 1 if dirty
# optional, for transcribe_audio:
cd ~/dev/rubin && python3 -m venv .venv-bp && .venv-bp/bin/pip install basic-pitch
```

Requires macOS Accessibility + Automation permissions for the host app
(Claude / terminal) — only for the UI-touching tools. `compose_midi`,
`find_patches`, and `open_midi_as_project` need no permissions at all.

## Test & demo

```sh
python3 -m unittest discover -s tests   # 76 tests, no Logic needed for most
python3 server.py --demo                # compose + open a demo beat in Logic
python3 server.py --demo --write-only   # just write the .mid
python3 server.py --cut-demo demo.wav   # render a from-scratch sample cut (no external files)
```

## Files

- `server.py` — MCP stdio server (hand-rolled JSON-RPC, no SDK)
- `midi.py` — Standard MIDI File writer
- `midi_read.py` — SMF parser + per-track analyzer
- `transcribe.py` — basic-pitch wrapper + content-addressed cache
- `wave_edit.py` — zero-dependency WAV slicer/arranger (the audio counterpart to midi.py)
- `patches.py` — factory patch index reader
- `logic_ctl.py` — the minimal AppleScript/AX layer
- `demo_beat.py` — 8-bar dark R&B demo (85 BPM, Am–F–C–E)
- `tests/` — unit + protocol tests

See [CHANGELOG.md](CHANGELOG.md) for the full arc.

## Known limits
- `import_audio` is best-effort and often aborts: Logic imports audio through
  a Browser pane, not a file dialog. It is hardened to NEVER type a path into
  the arrange window (a path's letters fire as key commands — `r` starts a
  recording — which once damaged a live session). Drag audio from Finder for
  reliability.
- `import_midi` and `import_audio` both confirm a file panel is frontmost
  before typing a path, and abort with Escape otherwise — they never leak a
  path into the arrange window. `import_midi`'s happy path is live-verified;
  `import_audio` usually aborts (audio import is a Browser pane).


- Patch loading, track selection, and import drive Logic's UI; they're
  anchored to stable AX landmarks (bounded subtree scans, never the whole
  window — real projects have thousands of AX elements) but a future Logic
  redesign could move them. `list_tracks` exists so failures are visible,
  not silent.
- Logic auto-renames a track to its patch name after `load_patch` unless the
  track was named manually first.
- Track-header patch labels can sit in an off-screen column and read stale —
  header text is NOT a reliable patch readback (this once produced a false
  "loading is broken" diagnosis). The selected track's channel-strip name in
  the inspector is ground truth; `load_patch` verifies against it and returns
  VERIFIED or FAILED accordingly.
- Tested on Logic Pro X 10.x/11 ("Logic Pro X.app" and "Logic Pro.app" are
  both handled), macOS 14+.
