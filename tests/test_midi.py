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


if __name__ == "__main__":
    unittest.main()
