"""End-to-end proof that rubin's MIDI and audio engines compose.

compose MIDI -> render_midi to WAV -> synth a sample -> cut_samples layers
the bounce + the sample -> analyze_audio confirms the mix has energy. If any
seam between the two engines breaks, this fails.
"""
import math
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from rubin import server, wave_edit


class TestAudioPipeline(unittest.TestCase):
    def test_compose_render_cut_analyze(self):
        d = tempfile.mkdtemp()
        # 1. compose a short MIDI bed (drums + a bass line)
        mid = os.path.join(d, "bed.mid")
        server._do_compose({"tempo": 140, "key": "Em", "path": mid, "tracks": [
            {"name": "Drums", "channel": 9, "drums": {"pattern": "half_time", "bars": 4}},
            {"name": "Bass", "channel": 0,
             "progression": {"chords": ["Em", "C"], "style": "bass", "repeat": 2}},
        ]})
        # 2. bounce the MIDI to audio with the built-in synth
        bed_wav = os.path.join(d, "bed.wav")
        r = wave_edit.render_midi(mid, bed_wav)
        self.assertGreater(r["voices"], 0)

        # 3. synth a one-shot "sample" to layer on top
        sample = os.path.join(d, "hit.wav")
        wave_edit.write_wav(sample, wave_edit.Clip.noise(0.2).fade(1, 80))

        # 4. cut_samples mixes the whole bounce + the sample hits into one track
        song = os.path.join(d, "song.wav")
        wave_edit.cut_arrange([
            {"file": bed_wav, "at_beat": 0},
            {"file": sample, "at_beat": 0, "repeat": {"times": 4, "every": 4}},
        ], tempo=140, out_path=song, limit=True)

        # 5. analyze the final mix — it must be real, energetic audio
        a = wave_edit.analyze_audio(song)
        self.assertGreater(a["duration"], 3.0)
        self.assertGreater(a["rms"], 0.05)
        self.assertGreater(a["peak"], 0.3)


if __name__ == "__main__":
    unittest.main()
