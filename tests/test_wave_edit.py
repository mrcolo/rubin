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


if __name__ == "__main__":
    unittest.main()
