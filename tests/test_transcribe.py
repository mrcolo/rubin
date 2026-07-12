import json
import os
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import midi
import transcribe


class TranscribeHarness(unittest.TestCase):
    """Run transcribe() against a stub basic-pitch binary."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        # a valid .mid the stub will "produce"
        self.fake_mid = os.path.join(self.tmp, "result.mid")
        midi.write_smf(self.fake_mid, 100,
                       [{"channel": 0, "notes": [(0, 1, 60, 90 + i) for i in range(3)]}])
        self.counter = os.path.join(self.tmp, "runs")
        stub = os.path.join(self.tmp, "basic-pitch")
        with open(stub, "w") as f:
            f.write(
                "#!/bin/sh\n"
                "echo run >> %s\n"
                "cp %s \"$1/out_basic_pitch.mid\"\n" % (self.counter, self.fake_mid))
        os.chmod(stub, stat.S_IRWXU)
        self.audio = os.path.join(self.tmp, "take.wav")
        with open(self.audio, "wb") as f:
            f.write(b"RIFF-fake-audio-content")
        self._old = (transcribe._BP_CANDIDATES, transcribe.CACHE_DIR, transcribe.INDEX_PATH)
        transcribe._BP_CANDIDATES = [stub]
        transcribe.CACHE_DIR = os.path.join(self.tmp, "cache")
        transcribe.INDEX_PATH = os.path.join(transcribe.CACHE_DIR, "index.json")

    def tearDown(self):
        transcribe._BP_CANDIDATES, transcribe.CACHE_DIR, transcribe.INDEX_PATH = self._old

    def runs(self):
        try:
            return len(open(self.counter).read().splitlines())
        except OSError:
            return 0

    def test_transcribe_and_cache_hit(self):
        entry = transcribe.transcribe(self.audio, label="hum")
        self.assertTrue(os.path.isfile(entry["midi"]))
        self.assertEqual(entry["label"], "hum")
        self.assertIn("BPM", entry.get("summary", ""))
        self.assertEqual(self.runs(), 1)
        again = transcribe.transcribe(self.audio)
        self.assertEqual(again["midi"], entry["midi"])
        self.assertEqual(self.runs(), 1)  # cache hit: stub not re-run

    def test_list_filter(self):
        transcribe.transcribe(self.audio, label="dark groove")
        self.assertTrue(transcribe.list_transcriptions("dark"))
        self.assertFalse(transcribe.list_transcriptions("polka"))

    def test_bad_extension(self):
        with self.assertRaises(ValueError):
            transcribe.transcribe(self.fake_mid)  # .mid is not audio

    def test_missing_file(self):
        with self.assertRaises(ValueError):
            transcribe.transcribe(os.path.join(self.tmp, "nope.wav"))


if __name__ == "__main__":
    unittest.main()
