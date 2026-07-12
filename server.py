#!/usr/bin/env python3
"""logic-composer: a minimal, dependency-free MCP server for Logic Pro.

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
import logic_ctl  # noqa: E402

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

TRACK_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "Track name shown in Logic"},
        "channel": {"type": "integer", "description": "MIDI channel 0-15. Use 9 for drums (GM drum map)"},
        "program": {"type": "integer", "description": "Optional GM program 0-127 (hint for initial patch)"},
        "notes": {"type": "array", "items": NOTE_SCHEMA},
    },
    "required": ["channel", "notes"],
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
    for t in args["tracks"]:
        tracks.append(
            {
                "name": t.get("name"),
                "channel": t["channel"],
                "program": t.get("program"),
                "notes": [
                    n if isinstance(n, (list, tuple))
                    else (n["start"], n["dur"], n["pitch"], n["vel"])
                    for n in t["notes"]
                ],
            }
        )
    size = midilib.write_smf(path, args["tempo"], tracks)
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

    if name == "answer_dialog":
        prefer = tuple(args.get("prefer") or ("Import Tempo", "Import", "Yes", "OK"))
        clicked = logic_ctl.answer_dialog(prefer)
        return "Clicked '%s'" % clicked if clicked else "No dialog present"

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
                        "serverInfo": {"name": "logic-composer", "version": "1.0.0"},
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


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--demo":
        import demo_beat

        write_only = "--write-only" in sys.argv
        args = demo_beat.weeknd_beat()
        path, size = _do_compose(args)
        print("Composed %s (%d bytes)" % (path, size))
        if not write_only:
            clicked, buttons = _do_import(path)
            print("Import sent. Dialog buttons: %s clicked: %s" % (buttons, clicked))
        return
    serve()


if __name__ == "__main__":
    main()
