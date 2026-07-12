#!/usr/bin/env python3
"""rubin: a minimal, dependency-free MCP server for Logic Pro.

Philosophy: don't fight Logic's UI. Compose real .mid files (fully reliable),
then use one small scripted action to import them into the open project.

MCP stdio transport: newline-delimited JSON-RPC 2.0.
Run `server.py --demo` to compose and import a demo beat without MCP.
"""

import json
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import midi as midilib  # noqa: E402
import midi_read  # noqa: E402
import logic_ctl  # noqa: E402
import patches  # noqa: E402
import transcribe as transcribe_mod  # noqa: E402

DEFAULT_OUT_DIR = os.path.expanduser("~/Desktop")

NOTE_SCHEMA = {
    "type": "object",
    "properties": {
        "start": {"type": "number", "description": "Start position in beats (quarter notes) from 0"},
        "dur": {"type": "number", "description": "Duration in beats"},
        "pitch": {"type": "integer", "description": "MIDI pitch 0-127 (60 = middle C)"},
        "vel": {"type": "integer", "description": "Velocity 1-127"},
    },
    "required": ["start", "dur", "pitch", "vel"],
}

CC_SCHEMA = {
    "type": "object",
    "properties": {
        "beat": {"type": "number", "description": "Position in beats"},
        "controller": {"type": "integer", "description": "CC number 0-127 (1 mod, 64 sustain, 11 expression)"},
        "value": {"type": "integer", "description": "0-127"},
    },
    "required": ["beat", "controller", "value"],
}

BEND_SCHEMA = {
    "type": "object",
    "properties": {
        "beat": {"type": "number", "description": "Position in beats"},
        "value": {"type": "integer", "description": "-8192 to 8191 (0 = center)"},
    },
    "required": ["beat", "value"],
}

TRACK_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "Track name shown in Logic"},
        "channel": {"type": "integer", "description": "MIDI channel 0-15. Use 9 for drums (GM drum map)"},
        "program": {
            "type": "integer",
            "description": (
                "Optional GM program 0-127. Logic maps this to a software instrument "
                "on import, so it selects the initial sound"
            ),
        },
        "volume": {"type": "integer", "description": "Optional initial CC7 volume 0-127"},
        "pan": {"type": "integer", "description": "Optional initial CC10 pan 0-127 (64 = center)"},
        "swing": {"type": "number", "description": "Optional per-track swing override (50-75)"},
        "drums": {
            "type": "object",
            "description": (
                "Shorthand: a full drum groove on this track (set channel 9). "
                "Patterns: half_time (dark R&B), four_on_floor (house), "
                "boom_bap (hip-hop), trap"
            ),
            "properties": {
                "pattern": {"type": "string",
                            "enum": ["half_time", "four_on_floor", "boom_bap", "trap"]},
                "bars": {"type": "integer", "description": "Default 8"},
                "start_bar": {"type": "integer", "description": "Delay entrance: pattern starts at this bar (0-based, default 0)"},
                "fills": {"type": "boolean", "description": "Snare-roll fills every 4th bar (default true)"},
            },
            "required": ["pattern"],
        },
        "progression": {
            "type": "object",
            "description": (
                "Shorthand: sustained chord voicings generated from names "
                "(e.g. Am, F, Cmaj7, E7, Dm9, Gsus4) — merged with any notes"
            ),
            "properties": {
                "chords": {"type": "array", "items": {"type": "string"}},
                "bars_per_chord": {"type": "integer", "description": "Default 2"},
                "start_bar": {"type": "integer", "description": "Delay entrance: first chord starts at this bar (0-based, default 0)"},
                "repeat": {"type": "integer", "description": "Play the chord list this many times (default 1)"},
                "style": {"type": "string", "enum": ["pad", "bass", "arp"],
                          "description": "pad = sustained voicings (default), bass = root groove, arp = 8th cycle"},
                "octave": {"type": "integer", "description": "Root octave (defaults per style)"},
                "vel": {"type": "integer", "description": "Base velocity (defaults per style)"},
            },
            "required": ["chords"],
        },
        "notes": {"type": "array", "items": NOTE_SCHEMA, "description": "May be empty when progression is given"},
        "cc": {"type": "array", "items": CC_SCHEMA, "description": "Optional controller automation"},
        "bends": {"type": "array", "items": BEND_SCHEMA, "description": "Optional pitch bends"},
    },
    "required": ["channel", "notes"],
}

TIME_SIG_SCHEMA = {
    "type": "array",
    "items": {"type": "integer"},
    "description": "Optional [numerator, denominator], default [4, 4]",
}

KEY_SCHEMA = {
    "type": "string",
    "description": "Optional key signature, e.g. 'Am', 'C', 'F#m', 'Eb'",
}

TEMPO_CHANGES_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "beat": {"type": "number", "description": "Position in beats"},
            "bpm": {"type": "number"},
        },
        "required": ["beat", "bpm"],
    },
    "description": "Optional mid-song tempo changes",
}

TOOLS = [
    {
        "name": "compose_midi",
        "description": (
            "Write a Standard MIDI File from note data. Returns the file path. "
            "Beats are quarter notes; channel 9 is drums (GM: 36 kick, 38 snare, "
            "39 clap, 42 closed hat, 46 open hat)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tempo": {"type": "number", "description": "BPM"},
                "tracks": {"type": "array", "items": TRACK_SCHEMA},
                "time_sig": TIME_SIG_SCHEMA,
                "key": KEY_SCHEMA,
                "tempo_changes": TEMPO_CHANGES_SCHEMA,
                "swing": {"type": "number", "description": "50-75; 50 = straight, ~62 = MPC swing (offbeats late)"},
                "swing_unit": {"type": "number", "description": "Swung subdivision in beats: 0.5 = 8ths (default), 0.25 = 16ths"},
                "humanize": {"type": "number", "description": "Micro-timing drift in beats (try 0.015-0.03); downbeats stay anchored"},
                "path": {"type": "string", "description": "Output .mid path (default ~/Desktop/<name>.mid)"},
                "name": {"type": "string", "description": "Base filename if path not given"},
            },
            "required": ["tempo", "tracks"],
        },
    },
    {
        "name": "import_midi",
        "description": (
            "Import a .mid file into the currently open Logic Pro project at bar 1 "
            "(File > Import > MIDI File). Creates one Logic track per MIDI track. "
            "May raise a tempo dialog; call answer_dialog afterwards if reported."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Absolute path to .mid file"}},
            "required": ["path"],
        },
    },
    {
        "name": "compose_and_import",
        "description": "compose_midi + import_midi in one step, including tempo-dialog handling.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tempo": {"type": "number"},
                "tracks": {"type": "array", "items": TRACK_SCHEMA},
                "time_sig": TIME_SIG_SCHEMA,
                "key": KEY_SCHEMA,
                "tempo_changes": TEMPO_CHANGES_SCHEMA,
                "swing": {"type": "number", "description": "50-75; 50 = straight, ~62 = MPC swing (offbeats late)"},
                "swing_unit": {"type": "number", "description": "Swung subdivision in beats: 0.5 = 8ths (default), 0.25 = 16ths"},
                "humanize": {"type": "number", "description": "Micro-timing drift in beats (try 0.015-0.03); downbeats stay anchored"},
                "name": {"type": "string", "description": "Base filename (default 'composition')"},
            },
            "required": ["tempo", "tracks"],
        },
    },
    {
        "name": "open_midi_as_project",
        "description": (
            "Open a .mid file directly in Logic Pro, creating a new project from it. "
            "Most reliable when no project is currently open. One Logic track per MIDI track."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Absolute path to .mid file"}},
            "required": ["path"],
        },
    },
    {
        "name": "transport",
        "description": "Logic transport via key command: play, stop, record, go_to_beginning.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "enum": ["play", "stop", "record", "go_to_beginning"]},
            },
            "required": ["command"],
        },
    },
    {
        "name": "select_track",
        "description": (
            "Select track N (1-based) in the open project via keyboard navigation. "
            "Use before load_patch to target a specific track."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"index": {"type": "integer", "description": "Track number, 1-based"}},
            "required": ["index"],
        },
    },
    {
        "name": "transcribe_audio",
        "description": (
            "Transcribe an audio file (wav/mp3/aiff/flac/m4a/ogg) to MIDI using "
            "Spotify's basic-pitch, cached by content hash under ~/.cache/rubin. "
            "Returns the cache entry plus an analysis of what the instrument "
            "plays (range, density, polyphony). Use the cached .mid with "
            "import_midi/open_midi_as_project, or the analysis to inform new parts."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Audio file path"},
                "label": {"type": "string", "description": "Optional friendly name for the cache"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "list_transcriptions",
        "description": "List cached audio->MIDI transcriptions, optionally filtered by label/source substring.",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
        },
    },
    {
        "name": "analyze_midi",
        "description": (
            "Parse any .mid and summarize per-track content: note count, pitch "
            "range, mean velocity, density (notes/beat), max polyphony, tempo."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "MIDI file path"}},
            "required": ["path"],
        },
    },
    {
        "name": "describe_midi",
        "description": (
            "One-paragraph human summary of a .mid: tracks, length, key, feel, "
            "energy contour, part registers, and any arrangement warnings. "
            "Cheaper to read than analyze_midi's full JSON."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "MIDI file path"}},
            "required": ["path"],
        },
    },
    {
        "name": "suggest_accompaniment",
        "description": (
            "Analyze a .mid (e.g. a transcription) and return ready-to-use "
            "compose_midi arguments that complement it: matching tempo, key, "
            "swing, a progression from its detected chords, and only the roles "
            "(bass/pad/arp/drums) whose register the source doesn't occupy. "
            "Feed the result straight back into compose_midi, then import both."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "MIDI file path"}},
            "required": ["path"],
        },
    },
    {
        "name": "find_patches",
        "description": (
            "Search Logic's factory patch index on disk. Returns exact patch names "
            "loadable with load_patch. Filter by name substring, category path "
            "(e.g. 'Synthesizer/Pad', 'Bass', 'Drum Kit'), and/or synth engine "
            "plugin (e.g. 'Alchemy', 'Retro Synth', 'ES2', 'Sculpture')."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Name substring"},
                "category": {"type": "string", "description": "Category path substring"},
                "plugin": {"type": "string", "description": "Synth engine name, e.g. 'Alchemy'"},
                "limit": {"type": "integer", "description": "Max results (default 25)"},
            },
        },
    },
    {
        "name": "load_patch",
        "description": (
            "Load a Logic Library patch onto the SELECTED track by name — the route "
            "to Alchemy and every other Logic synth/instrument sound. Prefers an "
            "exact name match, else loads the top search result, and returns the "
            "name of the patch actually loaded. Use find_patches to discover exact "
            "names, and list_tracks to verify."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Patch name (exact preferred)"}},
            "required": ["query"],
        },
    },
    {
        "name": "list_tracks",
        "description": (
            "Read the open project's tracks from the UI: returns "
            "[{name, patch}] top to bottom. Use to verify edits."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "answer_dialog",
        "description": (
            "If Logic is showing a sheet/dialog, click the best button. Prefers names "
            "matching `prefer` (default: Import Tempo / Import / Yes / OK), else the "
            "default button. Returns which button was clicked."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"prefer": {"type": "array", "items": {"type": "string"}}},
        },
    },
    {
        "name": "find_channel_strips",
        "description": (
            "Search Logic's factory channel-strip settings (.cst) — complete FX "
            "chains (EQ, compression, sends, spaces). Categories: 'Track/...', "
            "'Bus' (reverbs/delays), 'Output/02 Mastering', 'Instrument'. "
            "Discovery only: the Library search does NOT index .cst names "
            "(verified), so guide the user to load them via the channel strip's "
            "Setting button, or prefer load_patch (patches embed full FX chains)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Name substring"},
                "category": {"type": "string", "description": "Category path substring"},
                "limit": {"type": "integer", "description": "Max results (default 25)"},
            },
        },
    },
    {
        "name": "find_surge_presets",
        "description": (
            "Search installed Surge XT synth presets (.fxp) by name/category "
            "(Basses, Leads, Pads, Keys, Sequences...). Discovery only: presets "
            "load via Surge's own browser inside Logic, so give the user the "
            "preset name and category to pick."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Name substring"},
                "category": {"type": "string", "description": "Category substring"},
                "limit": {"type": "integer", "description": "Max results (default 25)"},
            },
        },
    },
    {
        "name": "list_plugins",
        "description": (
            "List installed Audio Units from the system registry (instruments and "
            "effects, including third-party synths like Surge XT and Dexed). "
            "No UI involved."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "save_project",
        "description": (
            "Save the open project (Cmd+S). If a Save sheet appears (never-saved "
            "project), its buttons are reported — the user should name it, or you "
            "can proceed with answer_dialog."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "logic_status",
        "description": "Report whether Logic Pro is running and the front window title.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _do_compose(args):
    path = args.get("path")
    if not path:
        base = args.get("name", "composition")
        path = os.path.join(DEFAULT_OUT_DIR, base + ".mid")
    path = os.path.expanduser(path)
    tracks = []
    for idx, t in enumerate(args["tracks"]):
        if "channel" not in t:
            raise ValueError(
                "track %d (%s) is missing 'channel' (0-15; 9 = drums)"
                % (idx + 1, t.get("name", "unnamed")))
        drums = t.get("drums")
        drum_notes = midilib.drum_pattern(
            drums["pattern"],
            bars=drums.get("bars", 8),
            fills=drums.get("fills", True),
        ) if drums else []
        if drums and drums.get("start_bar"):
            off = drums["start_bar"] * 4.0
            drum_notes = [(s + off, d, p, v) for s, d, p, v in drum_notes]
        prog = t.get("progression")
        prog_notes = midilib.progression_notes(
            prog["chords"] * max(1, int(prog.get("repeat", 1))),
            bars_per_chord=prog.get("bars_per_chord", 2),
            octave=prog.get("octave"),
            vel=prog.get("vel"),
            style=prog.get("style", "pad"),
        ) if prog else []
        if prog and prog.get("start_bar"):
            off = prog["start_bar"] * 4.0
            prog_notes = [(s + off, d, p, v) for s, d, p, v in prog_notes]
        tracks.append(
            {
                "name": t.get("name"),
                "channel": t["channel"],
                "program": t.get("program"),
                "volume": t.get("volume"),
                "pan": t.get("pan"),
                "swing": t.get("swing"),
                "humanize": t.get("humanize"),
                "notes": [
                    n if isinstance(n, (list, tuple))
                    else (n["start"], n["dur"], n["pitch"], n["vel"])
                    for n in t.get("notes") or []
                ] + prog_notes + drum_notes,
                "cc": [
                    c if isinstance(c, (list, tuple))
                    else (c["beat"], c["controller"], c["value"])
                    for c in t.get("cc") or []
                ],
                "bends": [
                    b if isinstance(b, (list, tuple)) else (b["beat"], b["value"])
                    for b in t.get("bends") or []
                ],
            }
        )
    time_sig = tuple(args.get("time_sig") or (4, 4))
    tempo_changes = [
        tc if isinstance(tc, (list, tuple)) else (tc["beat"], tc["bpm"])
        for tc in args.get("tempo_changes") or []
    ]
    size = midilib.write_smf(
        path, args["tempo"], tracks, time_sig,
        key=args.get("key"), tempo_changes=tempo_changes,
        swing=args.get("swing"), swing_unit=args.get("swing_unit", 0.5),
        humanize=args.get("humanize"),
    )
    return path, size


def _do_import(path):
    logic_ctl.import_midi(path)
    time.sleep(1.0)
    clicked = None
    buttons = logic_ctl.find_dialog_buttons()
    if buttons:
        clicked = logic_ctl.answer_dialog()
    return clicked, buttons


def handle_tool(name, args):
    if name == "compose_midi":
        path, size = _do_compose(args)
        return "Wrote %d bytes to %s" % (size, path)

    if name == "import_midi":
        path = os.path.expanduser(args["path"])
        if not os.path.isfile(path):
            raise ValueError("no such file: %s" % path)
        clicked, buttons = _do_import(path)
        msg = "Import sequence sent for %s." % path
        if clicked:
            msg += " Dialog appeared (buttons: %s); clicked '%s'." % (buttons, clicked)
        return msg

    if name == "compose_and_import":
        path, size = _do_compose(args)
        clicked, buttons = _do_import(path)
        msg = "Composed %s (%d bytes) and sent import sequence." % (path, size)
        if clicked:
            msg += " Dialog appeared (buttons: %s); clicked '%s'." % (buttons, clicked)
        return msg

    if name == "open_midi_as_project":
        path = os.path.expanduser(args["path"])
        if not os.path.isfile(path):
            raise ValueError("no such file: %s" % path)
        logic_ctl.open_midi_as_project(path)
        return "Opened %s in Logic Pro as a new project" % path

    if name == "transport":
        logic_ctl.transport(args["command"])
        return "Sent %s" % args["command"]

    if name == "select_track":
        logic_ctl.select_track(args["index"])
        return "Selected track %d" % args["index"]

    if name == "transcribe_audio":
        entry = transcribe_mod.transcribe(args["path"], label=args.get("label"))
        analysis = midi_read.analyze(entry["midi"])
        return json.dumps({"cache": entry, "analysis": analysis})

    if name == "list_transcriptions":
        return json.dumps(transcribe_mod.list_transcriptions(args.get("query")))

    if name == "analyze_midi":
        path = os.path.expanduser(args["path"])
        if not os.path.isfile(path):
            raise ValueError("no such file: %s" % path)
        return json.dumps(midi_read.analyze(path))

    if name == "describe_midi":
        path = os.path.expanduser(args["path"])
        if not os.path.isfile(path):
            raise ValueError("no such file: %s" % path)
        return midi_read.describe(path)

    if name == "suggest_accompaniment":
        path = os.path.expanduser(args["path"])
        if not os.path.isfile(path):
            raise ValueError("no such file: %s" % path)
        return json.dumps(midi_read.suggest_accompaniment(path))

    if name == "find_patches":
        hits = patches.find_patches(
            query=args.get("query"),
            plugin=args.get("plugin"),
            category=args.get("category"),
            limit=args.get("limit", 25),
        )
        return json.dumps(hits)

    if name == "load_patch":
        loaded = logic_ctl.load_patch(args["query"])
        return "Loaded patch '%s' on the selected track" % loaded

    if name == "list_tracks":
        return json.dumps(logic_ctl.list_tracks())

    if name == "answer_dialog":
        prefer = tuple(args.get("prefer") or ("Import Tempo", "Import", "Yes", "OK"))
        clicked = logic_ctl.answer_dialog(prefer)
        return "Clicked '%s'" % clicked if clicked else "No dialog present"

    if name == "find_channel_strips":
        return json.dumps(patches.find_channel_strips(
            query=args.get("query"),
            category=args.get("category"),
            limit=args.get("limit", 25),
        ))

    if name == "find_surge_presets":
        return json.dumps(patches.find_surge_presets(
            query=args.get("query"),
            category=args.get("category"),
            limit=args.get("limit", 25),
        ))

    if name == "list_plugins":
        return json.dumps(logic_ctl.list_audio_units())

    if name == "save_project":
        buttons = logic_ctl.save_project()
        if buttons:
            return "Save sheet appeared with buttons %s — project may be unnamed" % buttons
        return "Saved"

    if name == "logic_status":
        running = logic_ctl.logic_running()
        title = logic_ctl.front_window_title() if running else None
        return json.dumps({"running": running, "front_window": title})

    raise ValueError("unknown tool: %s" % name)


def _reply(mid, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": result}) + "\n")
    sys.stdout.flush()


def _error(mid, code, message):
    sys.stdout.write(
        json.dumps({"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}) + "\n"
    )
    sys.stdout.flush()


def serve():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        mid = msg.get("id")
        method = msg.get("method")
        try:
            if method == "initialize":
                _reply(
                    mid,
                    {
                        "protocolVersion": (msg.get("params") or {}).get(
                            "protocolVersion", "2024-11-05"
                        ),
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "rubin", "version": "1.2.0"},
                    },
                )
            elif method == "notifications/initialized":
                pass
            elif method == "tools/list":
                _reply(mid, {"tools": TOOLS})
            elif method == "tools/call":
                params = msg.get("params") or {}
                try:
                    text = handle_tool(params.get("name"), params.get("arguments") or {})
                    _reply(mid, {"content": [{"type": "text", "text": text}], "isError": False})
                except Exception as e:  # tool errors go back as tool results
                    sys.stderr.write(traceback.format_exc())
                    _reply(
                        mid,
                        {"content": [{"type": "text", "text": "Error: %s" % e}], "isError": True},
                    )
            elif method == "ping":
                _reply(mid, {})
            elif mid is not None:
                _error(mid, -32601, "method not found: %s" % method)
        except Exception:
            sys.stderr.write(traceback.format_exc())
            if mid is not None:
                _error(mid, -32603, "internal error")


def check():
    """Environment health as JSON: what works, what's missing."""
    import glob

    status = {}
    status["logic_installed"] = os.path.isdir(
        "/Applications/%s.app" % logic_ctl.app_name())
    try:
        status["logic_running"] = logic_ctl.logic_running()
    except Exception as e:
        status["logic_running"] = "error: %s" % e
    status["factory_patches"] = len(patches._build_index())
    status["channel_strips"] = len(patches._build_cst_index())
    try:
        aus = logic_ctl.list_audio_units()
        status["audio_units"] = len(aus)
        status["third_party_synths"] = sorted(
            a["name"] for a in aus
            if a["type"] == "instrument" and a["manufacturer"] != "Apple")
    except Exception as e:
        status["audio_units"] = "error: %s" % e
    bp = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      ".venv-bp", "bin", "basic-pitch")
    status["transcription"] = "ready" if os.path.isfile(bp) else (
        "missing: python3 -m venv .venv-bp && .venv-bp/bin/pip install basic-pitch")
    cache = os.path.expanduser("~/.cache/rubin/midi")
    status["transcription_cache"] = len(glob.glob(os.path.join(cache, "*.mid")))
    print(json.dumps(status, indent=1))


def verify(path):
    """Pre-flight a .mid: full analysis to stdout, exit 1 on warnings."""
    out = midi_read.analyze(os.path.expanduser(path))
    print(json.dumps(out, indent=1))
    warns = out.get("warnings", [])
    if warns:
        sys.stderr.write("%d warning(s) - see 'warnings' above\n" % len(warns))
    return 1 if warns else 0


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "--verify":
        sys.exit(verify(sys.argv[2]))
    if len(sys.argv) > 1 and sys.argv[1] == "--check":
        check()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "--demo":
        import demo_beat

        write_only = "--write-only" in sys.argv
        args = demo_beat.weeknd_beat()
        extra = [a for a in sys.argv[2:] if not a.startswith("--")]
        if extra:
            args["path"] = extra[0]
        path, size = _do_compose(args)
        print("Composed %s (%d bytes)" % (path, size))
        if not write_only:
            clicked, buttons = _do_import(path)
            print("Import sent. Dialog buttons: %s clicked: %s" % (buttons, clicked))
        return
    serve()


if __name__ == "__main__":
    main()
