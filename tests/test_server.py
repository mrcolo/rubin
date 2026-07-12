import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(ROOT, "server.py")


def rpc(messages):
    """Pipe JSON-RPC messages through the server, return responses keyed by id."""
    stdin = "".join(json.dumps(m) + "\n" for m in messages)
    p = subprocess.run(
        [sys.executable, SERVER], input=stdin, capture_output=True, text=True, timeout=30
    )
    out = {}
    for line in p.stdout.splitlines():
        msg = json.loads(line)
        out[msg["id"]] = msg
    return out


class TestProtocol(unittest.TestCase):
    def test_initialize_and_list(self):
        out = rpc([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "t", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ])
        self.assertEqual(out[1]["result"]["serverInfo"]["name"], "rubin")
        self.assertEqual(out[1]["result"]["protocolVersion"], "2025-06-18")
        names = {t["name"] for t in out[2]["result"]["tools"]}
        self.assertEqual(
            names,
            {"compose_midi", "import_midi", "compose_and_import", "open_midi_as_project",
             "transport", "select_track", "find_patches", "load_patch", "list_tracks",
             "transcribe_audio", "list_transcriptions", "analyze_midi",
             "answer_dialog", "save_project", "list_plugins", "find_channel_strips",
             "find_surge_presets", "logic_status"},
        )

    def test_compose_call(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.mid")
            out = rpc([
                {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"protocolVersion": "2025-06-18"}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                 "params": {"name": "compose_midi", "arguments": {
                     "tempo": 92, "path": path,
                     "tracks": [{"channel": 0,
                                 "notes": [{"start": 0, "dur": 1, "pitch": 60, "vel": 100}],
                                 "cc": [{"beat": 0, "controller": 1, "value": 64}],
                                 "bends": [{"beat": 0.5, "value": 2000}]}]}}},
            ])
            res = out[2]["result"]
            self.assertFalse(res["isError"])
            self.assertTrue(os.path.isfile(path))
            with open(path, "rb") as f:
                self.assertEqual(f.read(4), b"MThd")

    def test_tool_error_is_result_not_crash(self):
        out = rpc([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "import_midi", "arguments": {"path": "/nonexistent.mid"}}},
            {"jsonrpc": "2.0", "id": 3, "method": "ping"},
        ])
        self.assertTrue(out[2]["result"]["isError"])
        self.assertEqual(out[3]["result"], {})  # server survived the error

    def test_unknown_method(self):
        out = rpc([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "id": 2, "method": "bogus/method"},
        ])
        self.assertEqual(out[2]["error"]["code"], -32601)


class TestCheckFlag(unittest.TestCase):
    def test_check_outputs_json(self):
        p = subprocess.run([sys.executable, SERVER, "--check"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0)
        status = json.loads(p.stdout)
        self.assertIn("logic_installed", status)
        self.assertIn("factory_patches", status)
        self.assertIn("transcription", status)


if __name__ == "__main__":
    unittest.main()
