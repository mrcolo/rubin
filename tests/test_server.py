import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(ROOT, "server.py")
sys.path.insert(0, ROOT)


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
             "find_surge_presets", "describe_midi", "suggest_accompaniment", "logic_status"},
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


class TestVerifyFlag(unittest.TestCase):
    def _write(self, tracks):
        import midi
        f = tempfile.NamedTemporaryFile(suffix=".mid", delete=False)
        f.close()
        midi.write_smf(f.name, 90, tracks)
        return f.name

    def test_clean_file_exits_zero(self):
        path = self._write([{"name": "L", "channel": 0, "notes":
            [(i, 0.5, 70 + i % 12, 90 + ((i * 7) % 9) - 4) for i in range(16)]}])
        try:
            p = subprocess.run([sys.executable, SERVER, "--verify", path],
                               capture_output=True, text=True, timeout=30)
            self.assertEqual(p.returncode, 0)
            self.assertNotIn("warnings", json.loads(p.stdout))
        finally:
            os.unlink(path)

    def test_flat_file_exits_one(self):
        path = self._write([{"name": "R", "channel": 0, "notes":
            [(i, 0.5, 60 + i % 24, 100) for i in range(16)]}])
        try:
            p = subprocess.run([sys.executable, SERVER, "--verify", path],
                               capture_output=True, text=True, timeout=30)
            self.assertEqual(p.returncode, 1)
            self.assertTrue(json.loads(p.stdout)["warnings"])
        finally:
            os.unlink(path)


class TestEntrances(unittest.TestCase):
    def test_staged_build_has_rising_contour(self):
        import midi_read
        with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as f:
            path = f.name
        try:
            import server
            server._do_compose({"tempo": 85, "path": path, "tracks": [
                {"name": "Pad", "channel": 1,
                 "progression": {"chords": ["Am", "F", "C", "E"], "repeat": 2}},
                {"name": "Drums", "channel": 9,
                 "drums": {"pattern": "half_time", "bars": 12, "start_bar": 4}},
                {"name": "Arp", "channel": 2,
                 "progression": {"chords": ["Am", "F", "C", "E"],
                                 "style": "arp", "start_bar": 8}},
            ]})
            out = midi_read.analyze(path)
            curve = out["density_curve"]
            self.assertLess(curve[0]["notes_per_beat"], curve[1]["notes_per_beat"])
            self.assertLess(curve[1]["notes_per_beat"], curve[2]["notes_per_beat"])
            self.assertEqual(curve[0]["active_tracks"], 1)
            self.assertEqual(curve[2]["active_tracks"], 3)
        finally:
            os.unlink(path)

    def test_repeat_doubles_length(self):
        import midi_read, server
        with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as f:
            path = f.name
        try:
            server._do_compose({"tempo": 90, "path": path, "tracks": [
                {"channel": 1, "progression": {"chords": ["Am", "F"], "repeat": 2}}]})
            _p, _t, tracks = midi_read.parse_smf(open(path, "rb").read())
            self.assertEqual(midi_read.guess_chords(tracks),
                             ["Am", "Am", "F", "F", "Am", "Am", "F", "F"])
        finally:
            os.unlink(path)


class TestDemoFlag(unittest.TestCase):
    def test_demo_writes_clean_file_to_given_path(self):
        import midi_read
        with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as f:
            path = f.name
        try:
            p = subprocess.run(
                [sys.executable, SERVER, "--demo", "--write-only", path],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(p.returncode, 0)
            text = midi_read.describe(path)
            self.assertIn("Am", text)
            self.assertNotIn("WARNING", text)
        finally:
            os.unlink(path)


class TestComposeWarnings(unittest.TestCase):
    def test_flat_velocities_surface_in_result(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "flat.mid")
            out = rpc([
                {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"protocolVersion": "2025-06-18"}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                 "params": {"name": "compose_midi", "arguments": {
                     "tempo": 90, "path": path,
                     "tracks": [{"name": "Robo", "channel": 0, "notes":
                         [{"start": i, "dur": 0.5, "pitch": 60 + i % 24, "vel": 100}
                          for i in range(16)]}]}}},
            ])
            text = out[2]["result"]["content"][0]["text"]
            self.assertIn("Arrangement warnings", text)
            self.assertIn("flat velocities", text)

    def test_clean_compose_has_no_warning_suffix(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "clean.mid")
            out = rpc([
                {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"protocolVersion": "2025-06-18"}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                 "params": {"name": "compose_midi", "arguments": {
                     "tempo": 90, "path": path, "humanize": 0.02,
                     "tracks": [{"name": "OK", "channel": 1, "progression":
                         {"chords": ["Am", "F", "C", "E"]}}]}}},
            ])
            text = out[2]["result"]["content"][0]["text"]
            self.assertNotIn("Arrangement warnings", text)


class TestSongShorthand(unittest.TestCase):
    def test_full_song_from_one_declaration(self):
        import midi_read
        with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as f:
            path = f.name
        try:
            import server
            server._do_compose({"tempo": 85, "key": "Am", "path": path,
                                "song": {"chords": ["Am", "F", "C", "E"]}})
            a = midi_read.analyze(path)
            self.assertNotIn("warnings", a)
            self.assertEqual(a["key_guess"], "Am")
            self.assertEqual(len(a["tracks"]), 5)
            curve = a["density_curve"]
            # builds from the intro, strips at the outro
            self.assertLess(curve[0]["active_tracks"], curve[2]["active_tracks"])
            self.assertLess(curve[-1]["active_tracks"], curve[2]["active_tracks"])
        finally:
            os.unlink(path)

    def test_choruses_share_the_hook(self):
        import midi
        tracks = midi.build_song(["Am", "F", "C", "E"])
        lead = next(t for t in tracks if t["name"] == "Lead")["notes"]
        # two choruses at bars 12-19 and 28-35 (beats 48.. and 112..)
        c1 = sorted((round(s - 48, 3), round(d, 3), p) for s, d, p, v in lead if 48 <= s < 80)
        c2 = sorted((round(s - 112, 3), round(d, 3), p) for s, d, p, v in lead if 112 <= s < 144)
        self.assertEqual(c1, c2)
        self.assertGreater(len(c1), 10)


if __name__ == "__main__":
    unittest.main()
