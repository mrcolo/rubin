import array
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from rubin.wave_edit import Clip, Arrangement, write_wav, cut_arrange, _soft_clip


def make_tone(path, seconds=0.5, rate=44100, ch=2):
    import math, wave
    n = int(seconds * rate)
    ints = array.array("h")
    for i in range(n):
        v = int(0.5 * 32767 * math.sin(2 * math.pi * 220 * i / rate))
        for _ in range(ch):
            ints.append(v)
    w = wave.open(path, "wb"); w.setnchannels(ch); w.setsampwidth(2)
    w.setframerate(rate); w.writeframes(ints.tobytes()); w.close()


class TestWaveEdit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.tone = os.path.join(self.tmp, "tone.wav")
        make_tone(self.tone)

    def test_load_and_props(self):
        c = Clip.load(self.tone)
        self.assertAlmostEqual(c.duration, 0.5, places=2)
        self.assertEqual(c.ch, 2)

    def test_slice(self):
        c = Clip.load(self.tone).slice(0, 0.1)
        self.assertAlmostEqual(c.duration, 0.1, places=2)

    def test_gain_and_pitch(self):
        c = Clip.load(self.tone)
        self.assertEqual(len(c.gain(0.5).s), len(c.s))
        up = c.pitch(12)  # octave up -> half the frames
        self.assertAlmostEqual(up.duration, c.duration / 2, places=1)

    def test_reverse_roundtrip(self):
        c = Clip.load(self.tone)
        self.assertEqual(len(c.reverse().reverse().s), len(c.s))

    def test_arrangement_render_length(self):
        c = Clip.load(self.tone)
        arr = Arrangement(tempo=120).add(c, at_beat=0).add(c, at_beat=2)
        # 2 beats at 120 BPM = 1.0s, + 0.5s clip = 1.5s
        self.assertAlmostEqual(arr.render().duration, 1.5, places=1)

    def test_write_roundtrip(self):
        c = Clip.load(self.tone)
        out = os.path.join(self.tmp, "o.wav")
        write_wav(out, c)
        self.assertAlmostEqual(Clip.load(out).duration, 0.5, places=2)

    def test_repeat_places_multiple(self):
        out = os.path.join(self.tmp, "rep.wav")
        # a 0.1s chop repeated 4x every 1 beat at 120 BPM (=0.5s apart)
        r = cut_arrange([
            {"file": self.tone, "at_beat": 0, "end": 0.1,
             "repeat": {"times": 4, "every": 1}},
        ], tempo=120, out_path=out)
        self.assertEqual(r["events"], 1)  # one event spec
        c = Clip.load(out)
        # last chop starts at beat 3 = 1.5s, +0.1 = ~1.6s total
        self.assertAlmostEqual(c.duration, 1.6, places=1)

    def test_cut_arrange(self):
        out = os.path.join(self.tmp, "song.wav")
        r = cut_arrange([
            {"file": self.tone, "at_beat": 0, "end": 0.2},
            {"file": self.tone, "at_beat": 1, "pitch": 5, "gain": 0.8},
        ], tempo=140, out_path=out)
        self.assertEqual(r["events"], 2)
        self.assertTrue(os.path.isfile(out))


class TestSoftLimit(unittest.TestCase):
    def test_quiet_transparent(self):
        for x in (0.0, 0.3, -0.5, 0.79):
            self.assertEqual(_soft_clip(x), x)

    def test_loud_capped_and_symmetric(self):
        self.assertLessEqual(abs(_soft_clip(9.0)), 1.0)
        self.assertGreater(abs(_soft_clip(9.0)), 0.9)
        self.assertEqual(_soft_clip(-1.3), -_soft_clip(1.3))

    def test_monotonic_above_knee(self):
        self.assertGreaterEqual(_soft_clip(1.5), _soft_clip(1.0))

    def test_limit_write_runs(self):
        c = Clip.load(self.tone) if hasattr(self, "tone") else None
        import tempfile, os
        d = tempfile.mkdtemp()
        make_tone(os.path.join(d, "t.wav"))
        src = Clip.load(os.path.join(d, "t.wav")).gain(3.0)  # deliberately hot
        out = os.path.join(d, "lim.wav")
        write_wav(out, src, limit=True)
        self.assertTrue(os.path.isfile(out))


class TestPitchTo(unittest.TestCase):
    def test_pitch_to_computes_shift(self):
        import os
        d = tempfile.mkdtemp()
        make_tone(os.path.join(d, "t.wav"), seconds=0.4)
        out = os.path.join(d, "tuned.wav")
        # tune a "D1" sample up to "E1" = +2 semitones (shorter/higher)
        r = cut_arrange([
            {"file": os.path.join(d, "t.wav"), "at_beat": 0,
             "pitch_to": "E1", "from_note": "D1"},
        ], tempo=140, out_path=out)
        base = Clip.load(os.path.join(d, "t.wav")).duration
        tuned = Clip.load(out).duration
        self.assertLess(tuned, base)  # pitched up -> shorter

    def test_note_to_midi_matches_pitch(self):
        from rubin.midi import note_to_midi
        self.assertEqual(note_to_midi("E1"), 40)
        self.assertEqual(note_to_midi("A3"), 69)


class TestPan(unittest.TestCase):
    def test_hard_left_silences_right(self):
        import array
        c = Clip(array.array("f", [0.5, 0.5] * 50), 44100, 2)  # 0.5 exact in float32
        L = c.pan(-1.0)
        self.assertTrue(all(L.s[i] == 0 for i in range(1, len(L.s), 2)))   # right
        self.assertTrue(all(L.s[i] == 0.5 for i in range(0, len(L.s), 2)))  # left

    def test_center_unchanged(self):
        import array
        c = Clip(array.array("f", [0.3, -0.2] * 20), 44100, 2)
        self.assertEqual(list(c.pan(0).s), list(c.s))

    def test_mono_untouched(self):
        import array
        c = Clip(array.array("f", [0.5] * 30), 44100, 1)
        self.assertEqual(list(c.pan(0.7).s), list(c.s))

    def test_cut_arrange_with_pan(self):
        import os, tempfile
        d = tempfile.mkdtemp()
        make_tone(os.path.join(d, "t.wav"), seconds=0.2)
        out = os.path.join(d, "panned.wav")
        r = cut_arrange([
            {"file": os.path.join(d, "t.wav"), "at_beat": 0, "pan": -0.8},
            {"file": os.path.join(d, "t.wav"), "at_beat": 1, "pan": 0.8},
        ], tempo=140, out_path=out)
        self.assertEqual(r["events"], 2)
        self.assertTrue(os.path.isfile(out))


class TestNormalizeAndValidation(unittest.TestCase):
    def test_normalize_peaks_at_target(self):
        import array
        c = Clip(array.array("f", [0.1, -0.1] * 100), 44100, 2).normalize()
        self.assertAlmostEqual(max(abs(x) for x in c.s), 0.95, places=2)

    def test_normalize_silence_safe(self):
        import array
        c = Clip(array.array("f", [0.0] * 40), 44100, 2)
        self.assertEqual(list(c.normalize().s), list(c.s))

    def test_missing_file_raises_before_render(self):
        with self.assertRaises(ValueError) as ctx:
            cut_arrange([{"file": "/does/not/exist.wav", "at_beat": 0}],
                        out_path="/tmp/never.wav")
        self.assertIn("missing", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
