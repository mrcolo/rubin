# logic-composer

A minimal, dependency-free MCP server for getting music into Logic Pro.

## Why

UI-automation-first tools (clicking Logic's interface for every action) proved
unreliable — silent failures, garbage state reads. This server flips the
approach: **compose real Standard MIDI Files** (deterministic, no UI involved),
then use the smallest possible scripted step to get them into Logic.

## Tools

| Tool | What it does |
|------|--------------|
| `compose_midi` | Write a .mid file from note data (tempo, tracks, notes) |
| `open_midi_as_project` | Open a .mid in Logic as a new project (most reliable; no dialogs) |
| `import_midi` | File > Import > MIDI File into the open project at bar 1 (UI-scripted) |
| `compose_and_import` | Both steps in one call |
| `transport` | play / stop / record / go_to_beginning via key commands |
| `answer_dialog` | Click through a Logic sheet/dialog (e.g. tempo-import prompt) |
| `logic_status` | Is Logic running + front window title |

Note format: `{start, dur, pitch, vel}` — start/dur in beats (quarter notes),
pitch 0–127 (60 = middle C), velocity 1–127. Channel 9 is GM drums
(36 kick, 38 snare, 39 clap, 42 closed hat, 46 open hat).

## Install

```sh
claude mcp add --scope user logic-composer -- /usr/bin/python3 ~/dev/logic-mcp/server.py
```

Requires macOS Accessibility + Automation permissions for the host app
(Claude / terminal) — only for `import_midi`, `transport`, `answer_dialog`.
`compose_midi` and `open_midi_as_project` need no special permissions.

## Demo

```sh
python3 server.py --demo               # compose Weeknd-style beat + open in Logic
python3 server.py --demo --write-only  # just write ~/Desktop/weeknd_beat.mid
```

## Files

- `server.py` — MCP stdio server (hand-rolled JSON-RPC, no SDK)
- `midi.py` — Standard MIDI File writer
- `logic_ctl.py` — AppleScript/System Events control of Logic
- `demo_beat.py` — 8-bar dark R&B demo (85 BPM, Am–F–C–E)
