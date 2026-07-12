import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import midi
import midi_read


class TestRoundTrip(unittest.TestCase):
    def test_notes_survive_write_read(self):
        notes = [(0, 1, 60, 100), (1, 0.5, 64, 90), (1.5, 2, 67, 80)]
        data = midi.build_smf(92, [{"name": "Keys", "channel": 0, "notes": notes}])
        ppq, tempo, tracks = midi_read.parse_smf(data)
        self.assertEqual(ppq, midi.PPQ)
        self.assertAlmostEqual(tempo, 92, places=1)
        # track 0 is the tempo track; notes live in track 1
        self.assertEqual(tracks[1]["name"], "Keys")
        got = tracks[1]["notes"]
        self.assertEqual(len(got), 3)
        for (es, ed, ep, ev), (gs, gd, gp, gv) in zip(sorted(notes), got):
            self.assertAlmostEqual(gs, es, places=3)
            self.assertAlmostEqual(gd, ed, places=3)
            self.assertEqual(gp, ep)
            self.assertEqual(gv, ev)

    def test_overlapping_same_pitch(self):
        notes = [(0, 4, 60, 100), (1, 1, 60, 90)]
        data = midi.build_smf(120, [{"channel": 0, "notes": notes}])
        _, _, tracks = midi_read.parse_smf(data)
        self.assertEqual(len(tracks[1]["notes"]), 2)

    def test_running_status_and_channel(self):
        data = midi.build_smf(120, [{"channel": 9, "notes": [(0, 1, 36, 100), (1, 1, 38, 90)]}])
        _, _, tracks = midi_read.parse_smf(data)
        self.assertEqual([n[2] for n in tracks[1]["notes"]], [36, 38])

    def test_not_midi_raises(self):
        with self.assertRaises(ValueError):
            midi_read.parse_smf(b"RIFFxxxx")


class TestAnalyze(unittest.TestCase):
    def test_analysis_fields(self):
        import tempfile

        notes = [(i * 0.5, 0.4, 60 + (i % 12), 100) for i in range(16)]
        chord = [(0, 4, p, 80) for p in (48, 52, 55)]
        with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as f:
            path = f.name
        try:
            midi.write_smf(path, 85, [
                {"name": "Lead", "channel": 0, "notes": notes},
                {"name": "Pad", "channel": 1, "notes": chord},
            ])
            out = midi_read.analyze(path)
            self.assertAlmostEqual(out["tempo"], 85, places=1)
            lead = next(t for t in out["tracks"] if t["name"] == "Lead")
            self.assertEqual(lead["notes"], 16)
            self.assertEqual(lead["low"], "C3")
            self.assertEqual(lead["max_polyphony"], 1)
            pad = next(t for t in out["tracks"] if t["name"] == "Pad")
            self.assertEqual(pad["max_polyphony"], 3)
        finally:
            os.unlink(path)


class TestGuessKey(unittest.TestCase):
    def scale(self, pitches, tonic_hold):
        notes = [(i, 1, p, 90) for i, p in enumerate(pitches)]
        notes += [(len(pitches), 4, p, 95) for p in tonic_hold]
        return notes

    def test_a_minor(self):
        key, conf = midi_read.guess_key(
            self.scale([57, 59, 60, 62, 64, 65, 67, 69], [57, 60, 64]))
        self.assertEqual(key, "Am")
        self.assertGreater(conf, 0.8)

    def test_c_major(self):
        key, conf = midi_read.guess_key(
            self.scale([60, 62, 64, 65, 67, 69, 71, 72], [60, 64, 67]))
        self.assertEqual(key, "C")

    def test_empty(self):
        self.assertEqual(midi_read.guess_key([]), (None, 0.0))

    def test_analyze_includes_key(self):
        import tempfile, os as _os, midi
        with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as f:
            path = f.name
        try:
            midi.write_smf(path, 85, [{"channel": 0, "notes":
                self.scale([57, 59, 60, 62, 64, 65, 67, 69], [57, 60, 64])}])
            out = midi_read.analyze(path)
            self.assertEqual(out["key_guess"], "Am")
        finally:
            _os.unlink(path)


    def test_per_track_key(self):
        import tempfile, os as _os, midi
        with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as f:
            path = f.name
        try:
            melodic = self.scale([57, 59, 60, 62, 64, 65, 67, 69], [57, 60, 64])
            drums = [(i * 0.5, 0.2, 42, 80) for i in range(16)]
            midi.write_smf(path, 85, [
                {"name": "Lead", "channel": 0, "notes": melodic},
                {"name": "Hats", "channel": 9, "notes": drums},
            ])
            out = midi_read.analyze(path)
            lead = next(t for t in out["tracks"] if t["name"] == "Lead")
            hats = next(t for t in out["tracks"] if t["name"] == "Hats")
            self.assertEqual(lead["key_guess"], "Am")
            self.assertNotIn("key_guess", hats)  # one pitch class: no guess
        finally:
            _os.unlink(path)


class TestGuessSwing(unittest.TestCase):
    def test_straight_8ths(self):
        notes = [(i * 0.5, 0.3, 60, 90) for i in range(16)]
        self.assertEqual(midi_read.guess_swing(notes), 50)

    def test_swung(self):
        notes = [(i, 0.3, 60, 90) for i in range(8)]
        notes += [(i + 0.62, 0.3, 60, 90) for i in range(8)]
        self.assertEqual(midi_read.guess_swing(notes), 62)

    def test_mixed_16ths_abstains(self):
        notes = [(i * 0.25, 0.2, 60, 90) for i in range(32)]
        self.assertIsNone(midi_read.guess_swing(notes))

    def test_too_few_abstains(self):
        self.assertIsNone(midi_read.guess_swing([(0.5, 0.3, 60, 90)]))

    def test_writer_reader_roundtrip(self):
        import tempfile, os as _os, midi
        with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as f:
            path = f.name
        try:
            notes = [(i * 0.5, 0.3, 60 + i % 4, 90) for i in range(32)]
            midi.write_smf(path, 80, [{"channel": 0, "notes": notes}], swing=62)
            out = midi_read.analyze(path)
            self.assertEqual(out["tracks"][0]["swing_guess"], 62)
        finally:
            _os.unlink(path)


class TestDensityCurve(unittest.TestCase):
    def test_contour_shape(self):
        busy = {"notes": [(b + i * 0.25, 0.2, 60, 90)
                          for b in range(0, 16) for i in range(4)]}
        sparse = {"notes": [(b, 1, 48, 80) for b in range(16, 32, 4)]}
        curve = midi_read.density_curve([busy, sparse])
        self.assertEqual(curve[0]["bar"], 1)
        self.assertGreater(curve[0]["notes_per_beat"], curve[1]["notes_per_beat"])

    def test_in_analyze_for_long_files(self):
        import tempfile, os as _os, sys as _sys, midi
        _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
        import demo_beat, server
        spec = demo_beat.weeknd_beat()
        with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as f:
            spec["path"] = f.name
        try:
            server._do_compose(spec)
            out = midi_read.analyze(spec["path"])
            self.assertIn("density_curve", out)
            self.assertGreaterEqual(len(out["density_curve"]), 2)
        finally:
            _os.unlink(spec["path"])

    def test_empty(self):
        self.assertEqual(midi_read.density_curve([]), [])


if __name__ == "__main__":
    unittest.main()
