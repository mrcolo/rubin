"""Guards against the empty-file / truncated-module class of bug.

On 2026-07-13 rubin/server.py was committed EMPTY (a truncating write:
`open(f,"w").write(open(f).read()...)` opens/zeroes the file before the
inner read). The MCP kept running from the loaded module, so nothing failed
until a fresh import. These tests fail loudly if any module is empty or has
lost a load-bearing symbol — CI would have caught it.
"""
import importlib
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, "rubin")

# module -> (min source lines, symbols that must exist)
EXPECTED = {
    "midi": (60, ["build_smf", "write_smf", "chord_pitches", "progression_notes",
                  "drum_pattern", "melody_notes", "build_song"]),
    "midi_read": (60, ["parse_smf", "analyze", "describe", "guess_key",
                       "guess_chords", "guess_swing", "suggest_accompaniment"]),
    "patches": (40, ["find_patches", "find_channel_strips", "find_surge_presets"]),
    "logic_ctl": (60, ["import_midi", "import_audio", "load_patch", "select_track",
                       "list_tracks", "selected_strip_name", "project_state",
                       "reveal_in_finder", "list_audio_units"]),
    "transcribe": (40, ["transcribe", "catalog_folder", "list_transcriptions"]),
    "server": (200, ["main", "check", "verify", "serve", "handle_tool",
                     "_do_compose", "TOOLS"]),
    "demo_beat": (20, ["weeknd_beat"]),
    "wave_edit": (80, ["Clip", "Arrangement", "write_wav", "cut_arrange", "demo"]),
}


class TestModuleIntegrity(unittest.TestCase):
    def test_modules_non_empty_and_complete(self):
        for mod, (min_lines, symbols) in EXPECTED.items():
            path = os.path.join(PKG, mod + ".py")
            with open(path) as f:
                lines = f.read().splitlines()
            self.assertGreaterEqual(
                len(lines), min_lines,
                "%s.py has %d lines (< %d) — truncated?" % (mod, len(lines), min_lines))
            m = importlib.import_module("rubin." + mod)
            for sym in symbols:
                self.assertTrue(hasattr(m, sym),
                                "rubin.%s is missing %s" % (mod, sym))

    def test_server_exposes_all_tools(self):
        from rubin import server
        names = {t["name"] for t in server.TOOLS}
        # a representative spread across every tool family
        for required in ("compose_midi", "analyze_midi", "load_patch",
                         "import_midi", "catalog_samples", "reveal_in_finder",
                         "suggest_accompaniment", "find_patches"):
            self.assertIn(required, names)
        self.assertGreaterEqual(len(names), 20)


if __name__ == "__main__":
    unittest.main()
