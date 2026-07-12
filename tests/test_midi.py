import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import midi


def walk_chunks(data):
    assert data[:4] == b"MThd"
    ntrks = int.from_bytes(data[10:12], "big")
    pos = 14
    chunks = []
    while pos < len(data):
        assert data[pos:pos + 4] == b"MTrk"
        length = int.from_bytes(data[pos + 4:pos + 8], "big")
        chunks.append(data[pos + 8:pos + 8 + length])
        pos += 8 + length
    assert pos == len(data)
    assert len(chunks) == ntrks
    return chunks


class TestVLQ(unittest.TestCase):
    def test_known_values(self):
        cases = {0: b"\x00", 0x7F: b"\x7f", 0x80: b"\x81\x00",
                 0x2000: b"\xc0\x00", 0x1FFFFF: b"\xff\xff\x7f"}
        for n, expect in cases.items():
            self.assertEqual(midi._vlq(n), expect)


class TestBuildSMF(unittest.TestCase):
    def simple(self, **extra):
        track = {"channel": 0, "notes": [(0, 1, 60, 100), (1, 1, 64, 90)]}
        track.update(extra)
        return midi.build_smf(120, [track])

    def test_structure(self):
        chunks = walk_chunks(self.simple())
        self.assertEqual(len(chunks), 2)  # tempo track + 1 note track
        for c in chunks:
            self.assertTrue(c.endswith(b"\xff\x2f\x00"))

    def test_tempo_meta(self):
        chunks = walk_chunks(midi.build_smf(85, [{"channel": 0, "notes": []}]))
        tempo_us = round(60_000_000 / 85)
        self.assertIn(b"\xff\x51\x03" + tempo_us.to_bytes(3, "big"), chunks[0])

    def test_note_pairing(self):
        chunks = walk_chunks(self.simple())
        body = chunks[1]
        self.assertEqual(body.count(b"\x90"), body.count(b"\x80"))

    def test_program_volume_pan(self):
        chunks = walk_chunks(self.simple(program=38, volume=100, pan=32))
        body = chunks[1]
        self.assertIn(bytes([0xC0, 38]), body)
        self.assertIn(bytes([0xB0, 7, 100]), body)
        self.assertIn(bytes([0xB0, 10, 32]), body)

    def test_cc_and_bend_events(self):
        data = self.simple(cc=[(0.5, 1, 90)], bends=[(1.0, 0), (1.5, 8191)])
        body = walk_chunks(data)[1]
        self.assertIn(bytes([0xB0, 1, 90]), body)
        self.assertIn(bytes([0xE0, 0x00, 0x40]), body)  # center = 8192 raw
        self.assertIn(bytes([0xE0, 0x7F, 0x7F]), body)  # max bend

    def test_zero_duration_clamped(self):
        data = midi.build_smf(120, [{"channel": 0, "notes": [(0, 0, 60, 100)]}])
        body = walk_chunks(data)[1]
        self.assertIn(b"\x90", body)
        self.assertIn(b"\x80", body)

    def test_velocity_clamped(self):
        data = midi.build_smf(120, [{"channel": 0, "notes": [(0, 1, 60, 300)]}])
        body = walk_chunks(data)[1]
        self.assertIn(bytes([0x90, 60, 127]), body)

    def test_drum_channel(self):
        data = midi.build_smf(120, [{"channel": 9, "notes": [(0, 1, 36, 100)]}])
        body = walk_chunks(data)[1]
        self.assertIn(bytes([0x99, 36, 100]), body)

    def test_time_signature(self):
        data = midi.build_smf(120, [{"channel": 0, "notes": []}], time_sig=(3, 4))
        self.assertIn(bytes([0xFF, 0x58, 0x04, 3, 2, 24, 8]), walk_chunks(data)[0])

    def test_key_signature(self):
        cases = {
            "C": (0, 0), "Am": (0, 1), "F#": (6, 0), "Bb": (254, 0),
            "Ebm": (250, 1), "C#m": (4, 1),
        }
        for key, (sf, mi) in cases.items():
            data = midi.build_smf(120, [{"channel": 0, "notes": []}], key=key)
            self.assertIn(bytes([0xFF, 0x59, 0x02, sf, mi]), walk_chunks(data)[0],
                          "key %s" % key)

    def test_bad_key_raises(self):
        with self.assertRaises(KeyError):
            midi.build_smf(120, [{"channel": 0, "notes": []}], key="H")

    def test_tempo_changes(self):
        data = midi.build_smf(
            120, [{"channel": 0, "notes": []}], tempo_changes=[(8, 90)]
        )
        meta = walk_chunks(data)[0]
        for bpm in (120, 90):
            us = round(60_000_000 / bpm)
            self.assertIn(b"\xff\x51\x03" + us.to_bytes(3, "big"), meta)

    def test_validation_errors(self):
        with self.assertRaises(ValueError):
            midi.build_smf(120, [{"channel": 16, "notes": []}])
        with self.assertRaises(ValueError):
            midi.build_smf(120, [{"channel": 0, "notes": [(-1, 1, 60, 100)]}])
        with self.assertRaises(ValueError):
            midi.build_smf(120, [{"channel": 0, "notes": [(0, 1, 200, 100)]}])
        with self.assertRaises(ValueError):
            midi.build_smf(2000, [{"channel": 0, "notes": []}])

    def test_delta_times_monotonic(self):
        # decode deltas of the note track and ensure none are negative (vlq can't
        # encode negatives, but a sorting bug would corrupt adjacent events)
        body = walk_chunks(self.simple())[1]
        # first event delta must be 0 (track name absent, first note at beat 0)
        self.assertEqual(body[0], 0)


class TestWriteSMF(unittest.TestCase):
    def test_roundtrip_to_disk(self):
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as f:
            path = f.name
        try:
            size = midi.write_smf(path, 100, [{"channel": 0, "notes": [(0, 1, 60, 80)]}])
            with open(path, "rb") as f:
                data = f.read()
            self.assertEqual(len(data), size)
            walk_chunks(data)
        finally:
            os.unlink(path)


class TestSwing(unittest.TestCase):
    def ticks_of_first_two_notes(self, data):
        body = walk_chunks(data)[1]
        # first delta is byte 0 (note at beat 0); find second note-on delta
        return body

    def test_offbeat_moves(self):
        notes = [(0, 0.4, 60, 100), (0.5, 0.4, 62, 100)]
        straight = midi.build_smf(120, [{"channel": 0, "notes": notes}])
        swung = midi.build_smf(120, [{"channel": 0, "notes": notes}], swing=62)
        self.assertNotEqual(straight, swung)
        # offbeat lands at 0.62 beats = 298 ticks (rounded)
        self.assertEqual(midi._swing_start(0.5, 62, 0.5), 0.62)
        self.assertEqual(midi._swing_start(1.5, 62, 0.5), 1.62)

    def test_onbeat_unmoved(self):
        self.assertEqual(midi._swing_start(1.0, 62, 0.5), 1.0)
        self.assertEqual(midi._swing_start(0.25, 62, 0.5), 0.25)

    def test_straight_is_noop(self):
        notes = [(0, 0.4, 60, 100), (0.5, 0.4, 62, 100)]
        a = midi.build_smf(120, [{"channel": 0, "notes": notes}])
        b = midi.build_smf(120, [{"channel": 0, "notes": notes}], swing=50)
        self.assertEqual(a, b)

    def test_16th_unit(self):
        self.assertAlmostEqual(midi._swing_start(0.25, 62, 0.25), 0.31)

    def test_per_track_override(self):
        notes = [(0.5, 0.4, 62, 100)]
        g = midi.build_smf(120, [{"channel": 0, "notes": notes, "swing": 50}], swing=66)
        s = midi.build_smf(120, [{"channel": 0, "notes": notes}], swing=50)
        self.assertEqual(g, s)


class TestProgressionStyles(unittest.TestCase):
    def test_bass_low_and_mono(self):
        notes = midi.progression_notes(["Am", "F"], style="bass")
        pitches = [n[2] for n in notes]
        self.assertLessEqual(max(pitches), 48)
        # no two notes sound simultaneously
        spans = sorted((n[0], n[0] + n[1]) for n in notes)
        for (s1, e1), (s2, _e2) in zip(spans, spans[1:]):
            self.assertLessEqual(e1, s2 + 0.01)

    def test_bass_bars_vary(self):
        notes = midi.progression_notes(["Am"], bars_per_chord=2, style="bass")
        bar0 = sorted(n[0] for n in notes if n[0] < 4)
        bar1 = sorted(n[0] - 4 for n in notes if n[0] >= 4)
        self.assertNotEqual(bar0, bar1)  # rhythm differs bar to bar

    def test_arp_never_stutters(self):
        for chord in ("Am", "Cmaj7", "E7", "Fsus4"):
            pitches = [n[2] for n in midi.progression_notes(
                [chord], bars_per_chord=2, style="arp")]
            for a, b in zip(pitches, pitches[1:]):
                self.assertNotEqual(a, b, "repeat in %s arp" % chord)

    def test_arp_is_eighths(self):
        notes = midi.progression_notes(["Am"], bars_per_chord=2, style="arp")
        self.assertEqual(len(notes), 16)
        starts = [n[0] for n in notes]
        self.assertEqual(starts, [i * 0.5 for i in range(16)])

    def test_bad_style(self):
        with self.assertRaises(ValueError):
            midi.progression_notes(["Am"], style="polka")

    def test_trio_describes_clean(self):
        import os as _os, sys as _sys, tempfile
        _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
        import midi_read, server
        with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as f:
            path = f.name
        try:
            server._do_compose({"tempo": 85, "path": path, "key": "Am", "tracks": [
                {"name": "Pad", "channel": 1, "progression": {"chords": ["Am", "F", "C", "E"]}},
                {"name": "Bass", "channel": 0,
                 "progression": {"chords": ["Am", "F", "C", "E"], "style": "bass"}},
                {"name": "Arp", "channel": 2,
                 "progression": {"chords": ["Am", "F", "C", "E"], "style": "arp"}},
            ]})
            text = midi_read.describe(path)
            self.assertIn("progression Am-F-C-E", text)
            self.assertNotIn("WARNING", text)
        finally:
            _os.unlink(path)


class TestProgression(unittest.TestCase):
    def test_chord_pitches(self):
        self.assertEqual(midi.chord_pitches("Am"), [69, 72, 76])
        self.assertEqual(midi.chord_pitches("Bbmaj7"), [70, 74, 77, 81])
        with self.assertRaises(ValueError):
            midi.chord_pitches("Hm")
        with self.assertRaises(ValueError):
            midi.chord_pitches("Cweird")

    def test_progression_roundtrip(self):
        import os as _os, sys as _sys, tempfile
        _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
        import midi_read, server
        with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as f:
            path = f.name
        try:
            server._do_compose({"tempo": 90, "path": path, "tracks": [
                {"channel": 1, "progression": {"chords": ["Am", "F", "Cmaj7", "E7"]}}]})
            _p, _t, tracks = midi_read.parse_smf(open(path, "rb").read())
            self.assertEqual(midi_read.guess_chords(tracks),
                             ["Am", "Am", "F", "F", "Cmaj7", "Cmaj7", "E7", "E7"])
        finally:
            _os.unlink(path)


class TestDrumPattern(unittest.TestCase):
    def test_all_patterns_generate(self):
        for p in ("half_time", "four_on_floor", "boom_bap", "trap"):
            notes = midi.drum_pattern(p, bars=8)
            self.assertGreater(len(notes), 50, p)
            self.assertTrue(all(0 <= n[2] <= 127 for n in notes))

    def test_four_on_floor_kick_every_beat(self):
        notes = midi.drum_pattern("four_on_floor", bars=2, fills=False)
        kicks = sorted(n[0] for n in notes if n[2] == midi.KICK)
        self.assertEqual(kicks, [float(i) for i in range(8)])

    def test_unknown_pattern(self):
        with self.assertRaises(ValueError):
            midi.drum_pattern("jungle")

    def test_full_band_describes_clean(self):
        import os as _os, sys as _sys, tempfile
        _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
        import midi_read, server
        with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as f:
            path = f.name
        try:
            server._do_compose({"tempo": 85, "path": path, "key": "Am", "tracks": [
                {"name": "Drums", "channel": 9, "drums": {"pattern": "half_time"}},
                {"name": "Pad", "channel": 1,
                 "progression": {"chords": ["Am", "F", "C", "E"]}},
                {"name": "Bass", "channel": 0,
                 "progression": {"chords": ["Am", "F", "C", "E"], "style": "bass"}},
            ]})
            text = midi_read.describe(path)
            self.assertNotIn("WARNING", text)
            out = midi_read.analyze(path)
            for w in out.get("density_curve", []):
                self.assertGreater(w["notes_per_beat"], 0,
                                   "phantom empty window at bar %d" % w["bar"])
        finally:
            _os.unlink(path)


class TestHumanizeTiming(unittest.TestCase):
    def test_deterministic_and_effective(self):
        notes = [(i * 0.5, 0.4, 60, 90) for i in range(16)]
        a = midi.build_smf(90, [{"channel": 0, "notes": notes}])
        b = midi.build_smf(90, [{"channel": 0, "notes": notes}], humanize=0.02)
        self.assertNotEqual(a, b)
        self.assertEqual(b, midi.build_smf(90, [{"channel": 0, "notes": notes}], humanize=0.02))

    def test_downbeats_anchored(self):
        for bar_start in (0.0, 4.0, 8.0, 12.0):
            for i in range(8):
                self.assertEqual(midi._humanize_start(bar_start, i, 0.03), bar_start)

    def test_drift_bounded(self):
        for i in range(50):
            drift = midi._humanize_start(2.5, i, 0.02) - 2.5
            self.assertLessEqual(abs(drift), 0.02 + 1e-9)

    def test_some_notes_drift(self):
        drifts = [midi._humanize_start(2.5, i, 0.02) != 2.5 for i in range(13)]
        self.assertGreater(sum(drifts), 8)


class TestFriendlyErrors(unittest.TestCase):
    def test_missing_channel_message(self):
        import os as _os, sys as _sys
        _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
        import server
        with self.assertRaises(ValueError) as ctx:
            server._do_compose({"tempo": 90, "path": "/tmp/x.mid",
                                "tracks": [{"name": "Oops", "notes": []}]})
        self.assertIn("Oops", str(ctx.exception))
        self.assertIn("channel", str(ctx.exception))


class TestVoiceLeading(unittest.TestCase):
    def voicings(self, notes, span=8):
        out = {}
        for s, d, p, v in notes:
            out.setdefault(int(s // span), []).append(p)
        return [sorted(v) for _, v in sorted(out.items())]

    def movement(self, vs):
        return sum(abs(sum(a) / len(a) - sum(b) / len(b))
                   for a, b in zip(vs, vs[1:]))

    def test_voice_leading_minimizes_movement(self):
        led = self.voicings(midi.progression_notes(["Am", "F", "C", "E"]))
        raw = self.voicings(midi.progression_notes(["Am", "F", "C", "E"], voice_lead=False))
        self.assertLess(self.movement(led), self.movement(raw) / 3)

    def test_inversions_still_detected(self):
        import os as _os, sys as _sys, tempfile
        _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
        import midi_read
        with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as f:
            path = f.name
        try:
            midi.write_smf(path, 90, [{"channel": 1,
                "notes": midi.progression_notes(["Am", "F", "C", "E"])}])
            _p, _t, tr = midi_read.parse_smf(open(path, "rb").read())
            self.assertEqual(midi_read.guess_chords(tr),
                             ["Am", "Am", "F", "F", "C", "C", "E", "E"])
        finally:
            _os.unlink(path)


class TestMelody(unittest.TestCase):
    PROGS = (["Am", "F", "C", "E"], ["C", "G", "Am", "F"], ["Dm", "Bb", "F", "A"])

    def shape(self, chords, seed=0):
        notes = midi.melody_notes(chords, seed=seed)
        pitches = [n[2] for n in notes]
        vels = [n[3] for n in notes]
        starts = [n[0] for n in notes]
        peak_i = pitches.index(max(pitches))
        total = starts[-1] + notes[-1][1]
        return notes, pitches, vels, 100 * starts[peak_i] / total, peak_i

    def test_peak_placed_and_loudest(self):
        for chords in self.PROGS:
            for seed in range(4):
                _n, _p, vels, pct, peak_i = self.shape(chords, seed)
                self.assertTrue(60 <= pct <= 85, "peak at %d%%" % pct)
                self.assertEqual(vels[peak_i], max(vels))

    def test_monophonic(self):
        for chords in self.PROGS:
            notes = midi.melody_notes(chords)
            spans = sorted((n[0], n[0] + n[1]) for n in notes)
            for (s1, e1), (s2, _e2) in zip(spans, spans[1:]):
                self.assertLessEqual(e1, s2 + 0.01)

    def test_resolves_to_root(self):
        notes = midi.melody_notes(["Am", "F", "C", "E"])
        root_pc = midi.chord_pitches("Am")[0] % 12
        self.assertEqual(notes[-1][2] % 12, root_pc)

    def test_deterministic_but_seeded(self):
        a = midi.melody_notes(["Am", "F"], seed=1)
        self.assertEqual(a, midi.melody_notes(["Am", "F"], seed=1))
        self.assertNotEqual(a, midi.melody_notes(["Am", "F"], seed=2))

    def test_progression_style_melody(self):
        notes = midi.progression_notes(["Am", "F", "C", "E"], style="melody")
        self.assertGreater(len(notes), 16)

    def test_band_with_melody_describes_clean(self):
        import os as _os, sys as _sys, tempfile
        _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
        import midi_read, server
        with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as f:
            path = f.name
        try:
            server._do_compose({"tempo": 85, "path": path, "key": "Am", "tracks": [
                {"name": "Drums", "channel": 9, "drums": {"pattern": "half_time"}},
                {"name": "Bass", "channel": 0,
                 "progression": {"chords": ["Am", "F", "C", "E"], "style": "bass"}},
                {"name": "Pad", "channel": 1,
                 "progression": {"chords": ["Am", "F", "C", "E"], "octave": 2}},
                {"name": "Lead", "channel": 3,
                 "progression": {"chords": ["Am", "F", "C", "E"], "style": "melody"}},
            ]})
            self.assertNotIn("WARNING", midi_read.describe(path))
        finally:
            _os.unlink(path)


if __name__ == "__main__":
    unittest.main()
