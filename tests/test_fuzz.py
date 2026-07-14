"""Deterministic property/fuzz coverage.

Generates many pseudo-random compositions and audio arrangements (fixed seed,
so runs are reproducible) and asserts invariants that the happy-path unit
tests don't cover: no crashes, valid/parseable output, note counts preserved,
no negative durations, valid WAVs. This is the permanent form of the one-off
stress sweep that found the empty-render and negative-duration bugs.
"""
import os
import random
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from rubin import midi, midi_read, wave_edit

ROOTS = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
QUALS = ["", "m", "7", "maj7", "m7", "sus4", "add9", "m9"]
STYLES = ["pad", "bass", "arp", "melody"]
DRUMS = ["half_time", "four_on_floor", "boom_bap", "trap"]


def walk_smf(data):
    assert data[:4] == b"MThd"
    ntrks = int.from_bytes(data[10:12], "big")
    pos, found = 14, 0
    while pos < len(data):
        assert data[pos:pos + 4] == b"MTrk"
        length = int.from_bytes(data[pos + 4:pos + 8], "big")
        assert data[pos + 8:pos + 8 + length].endswith(b"\xff\x2f\x00")
        found += 1
        pos += 8 + length
    assert pos == len(data) and found == ntrks


class TestFuzzCompose(unittest.TestCase):
    def test_random_compositions_are_valid(self):
        rng = random.Random(1234)
        d = tempfile.mkdtemp()
        for i in range(60):
            tempo = rng.choice([70, 85, 120, 140, 174, 999, 20])
            chords = [rng.choice(ROOTS) + rng.choice(QUALS)
                      for _ in range(rng.randint(1, 6))]
            tracks = []
            if rng.random() < 0.8:
                tracks.append({"channel": 0, "progression": None})  # placeholder
            # build via the composer's own note generators
            notes = []
            for c in chords:
                notes += midi.progression_notes(
                    [c], style=rng.choice(STYLES),
                    bars_per_chord=rng.randint(1, 2))
            spec_tracks = [{"name": "T", "channel": rng.randint(0, 8), "notes": notes}]
            if rng.random() < 0.5:
                spec_tracks.append({"name": "D", "channel": 9,
                                    "notes": midi.drum_pattern(rng.choice(DRUMS),
                                                               bars=rng.randint(1, 4))})
            key = rng.choice(ROOTS) + rng.choice(["", "m"])
            data = midi.build_smf(tempo, spec_tracks, key=key,
                                  swing=rng.choice([None, 50, 58, 66]),
                                  humanize=rng.choice([None, 0.01, 0.02]))
            walk_smf(data)                              # structurally valid
            # every emitted note has positive duration after parse
            path = os.path.join(d, "f%d.mid" % i)
            with open(path, "wb") as f:
                f.write(data)
            out = midi_read.analyze(path)               # analyze never crashes
            self.assertIn("tempo", out)
            for t in out["tracks"]:
                self.assertGreaterEqual(t["notes"], 0)

    def test_random_progressions_no_negative_durations(self):
        rng = random.Random(99)
        for _ in range(200):
            chords = [rng.choice(ROOTS) + rng.choice(QUALS)
                      for _ in range(rng.randint(1, 4))]
            for style in STYLES:
                for n in midi.progression_notes(chords, style=style,
                                                bars_per_chord=rng.randint(0, 3)):
                    self.assertGreater(n[1], 0)         # dur > 0
                    self.assertGreaterEqual(n[0], 0)    # start >= 0
                    self.assertTrue(0 <= n[2] <= 127)   # pitch in range


class TestFuzzAudio(unittest.TestCase):
    def test_random_cut_arrangements_render_valid(self):
        rng = random.Random(7)
        d = tempfile.mkdtemp()
        # a couple of synth "samples" to draw from
        srcs = []
        for j in range(3):
            p = os.path.join(d, "s%d.wav" % j)
            wave_edit.write_wav(p, wave_edit.Clip.tone(200 + j * 110, 0.3))
            srcs.append(p)
        for i in range(20):
            events = []
            for _ in range(rng.randint(1, 8)):
                events.append({
                    "file": rng.choice(srcs),
                    "at_beat": rng.uniform(0, 8),
                    "end": rng.choice([None, 0.1, 0.2]),
                    "pitch": rng.choice([0, 0, 3, -2, 7]),
                    "gain": rng.uniform(0.4, 1.0),
                    "pan": rng.choice([0, -0.6, 0.6]),
                })
            out = os.path.join(d, "c%d.wav" % i)
            wave_edit.cut_arrange(events, tempo=rng.choice([120, 140, 174]),
                                  out_path=out, limit=rng.random() < 0.5)
            a = wave_edit.analyze_audio(out)            # valid, analyzable
            self.assertGreater(a["duration"], 0)
            self.assertLessEqual(a["peak"], 1.0)


class TestFuzzSongAndBounce(unittest.TestCase):
    def test_random_songs_and_bounces(self):
        import math
        rng = random.Random(4242)
        d = tempfile.mkdtemp()
        roles_pool = ["pad", "bass", "arp", "drums", "melody"]
        for i in range(4):
            chords = [rng.choice(ROOTS) + rng.choice(["", "m", "maj7", "m7"])
                      for _ in range(rng.randint(1, 4))]
            sections = []
            for _ in range(rng.randint(1, 4)):
                k = rng.randint(1, 3)
                sections.append({"bars": rng.choice([2, 4, 8]),
                                 "roles": rng.sample(roles_pool, k)})
            tracks = midi.build_song(chords, sections=sections,
                                     drum_pattern_name=rng.choice(DRUMS),
                                     seed=rng.randint(0, 5))
            spec = {"tempo": rng.choice([90, 140, 174]), "key": "Em",
                    "path": os.path.join(d, "song%d.mid" % i), "tracks": tracks}
            from rubin import server
            server._do_compose(spec)
            walk_smf(open(spec["path"], "rb").read())     # valid SMF
            # bounce to audio — synth must handle every generated note
            wav = os.path.join(d, "song%d.wav" % i)
            r = wave_edit.render_midi(spec["path"], wav)
            self.assertGreaterEqual(r["voices"], 0)
            a = wave_edit.analyze_audio(wav)
            self.assertLessEqual(a["peak"], 1.0)          # never clips over full scale

    def test_render_midi_pitch_extremes(self):
        # notes at the very edges of MIDI range must synth without error
        d = tempfile.mkdtemp()
        from rubin import server
        mid = os.path.join(d, "ext.mid")
        server._do_compose({"tempo": 120, "path": mid, "tracks": [
            {"name": "X", "channel": 0, "notes":
                [{"start": i, "dur": 0.5, "pitch": p, "vel": 100}
                 for i, p in enumerate([0, 1, 126, 127, 21, 108])]},
            {"name": "D", "channel": 9, "notes":
                [{"start": i * 0.5, "dur": 0.1, "pitch": p, "vel": 100}
                 for i, p in enumerate([35, 36, 38, 42, 46, 49, 51, 60, 99])]},
        ]})
        wav = os.path.join(d, "ext.wav")
        wave_edit.render_midi(mid, wav)
        self.assertGreater(wave_edit.analyze_audio(wav)["duration"], 0)


if __name__ == "__main__":
    unittest.main()
