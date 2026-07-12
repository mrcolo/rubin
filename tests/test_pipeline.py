"""Capstone: the full audio-to-arrangement pipeline as one chain.

transcribe (stub binary) -> suggest_accompaniment -> compose -> analyze:
the exact four-step workflow the music-production skill teaches, minus the
Logic import at the end. If any module's contract drifts, this breaks.
"""
import os
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import midi
import midi_read
import server
import transcribe


class TestFullPipeline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        # the "performance": a swung Am bassline, as if transcribed from audio
        self.performance = os.path.join(self.tmp, "performance.mid")
        midi.write_smf(self.performance, 92, [{"name": "Take", "channel": 0,
            "notes": midi.progression_notes(["Am", "F", "C", "E"], style="bass")}],
            key="Am", swing=62)
        stub = os.path.join(self.tmp, "basic-pitch")
        with open(stub, "w") as f:
            f.write("#!/bin/sh\ncp %s \"$1/take_basic_pitch.mid\"\n" % self.performance)
        os.chmod(stub, stat.S_IRWXU)
        self.audio = os.path.join(self.tmp, "take.wav")
        with open(self.audio, "wb") as f:
            f.write(b"fake-audio-bytes")
        self._old = (transcribe._BP_CANDIDATES, transcribe.CACHE_DIR, transcribe.INDEX_PATH)
        transcribe._BP_CANDIDATES = [stub]
        transcribe.CACHE_DIR = os.path.join(self.tmp, "cache")
        transcribe.INDEX_PATH = os.path.join(transcribe.CACHE_DIR, "index.json")

    def tearDown(self):
        transcribe._BP_CANDIDATES, transcribe.CACHE_DIR, transcribe.INDEX_PATH = self._old

    def test_audio_to_verified_arrangement(self):
        # 1. "hear" the audio
        entry = transcribe.transcribe(self.audio, label="late night idea")
        # 2. ask what would fit
        suggestion = midi_read.suggest_accompaniment(entry["midi"])
        roles = [t["name"] for t in suggestion["tracks"]]
        self.assertNotIn("Bass", roles)          # source owns the low end
        self.assertEqual(suggestion["swing"], 62)  # feel carried over
        self.assertEqual(suggestion["key"], "Am")
        # 3. compose it
        out_path = os.path.join(self.tmp, "arrangement.mid")
        suggestion["path"] = out_path
        server._do_compose(suggestion)
        # 4. verify: clean, in key, with the source's harmony
        analysis = midi_read.analyze(out_path)
        self.assertNotIn("warnings", analysis)
        self.assertEqual(analysis["key_guess"], "Am")
        self.assertEqual(analysis["chords"][0], "Am")
        text = midi_read.describe(out_path)
        self.assertNotIn("WARNING", text)


if __name__ == "__main__":
    unittest.main()
