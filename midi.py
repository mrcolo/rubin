"""Minimal Standard MIDI File (SMF) writer. No dependencies.

Beats are quarter notes, floats allowed. Resolution is PPQ ticks per beat.
"""

PPQ = 480


def _vlq(n):
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    return bytes(reversed(out))


def _track_chunk(events):
    """events: list of (abs_tick, priority, data_bytes). Lower priority sorts first at equal tick."""
    events = sorted(events, key=lambda e: (e[0], e[1]))
    out = bytearray()
    last = 0
    for tick, _prio, data in events:
        out += _vlq(tick - last)
        out += data
        last = tick
    out += _vlq(0) + b"\xff\x2f\x00"
    return b"MTrk" + len(out).to_bytes(4, "big") + bytes(out)


# circle of fifths: name -> sharps (negative = flats)
_KEY_SF = {"C": 0, "G": 1, "D": 2, "A": 3, "E": 4, "B": 5, "F#": 6, "C#": 7,
           "F": -1, "Bb": -2, "Eb": -3, "Ab": -4, "Db": -5, "Gb": -6, "Cb": -7}
# relative majors for minor keys: Am has the same signature as C
_MINOR_TO_MAJOR = {"A": "C", "E": "G", "B": "D", "F#": "A", "C#": "E", "G#": "B",
                   "D#": "F#", "A#": "C#", "D": "F", "G": "Bb", "C": "Eb",
                   "F": "Ab", "Bb": "Db", "Eb": "Gb", "Ab": "Cb"}


def key_signature_meta(key):
    """FF 59 meta from a key name: 'C', 'F#', 'Bb', 'Am', 'C#m'..."""
    key = key.strip()
    minor = key.endswith("m")
    root = key[:-1] if minor else key
    root = root[0].upper() + root[1:]
    sf = _KEY_SF[_MINOR_TO_MAJOR[root] if minor else root]  # KeyError = bad key
    return bytes([0xFF, 0x59, 0x02, sf & 0xFF, 1 if minor else 0])


def _swing_start(start, swing, unit):
    """Shift offbeat starts DAW-style: 50 = straight, 62 = classic MPC feel.

    `unit` is the swung subdivision in beats (0.5 = 8ths, 0.25 = 16ths).
    Only notes sitting on the offbeat of a unit pair are moved.
    """
    if not swing or abs(swing - 50) < 0.01:
        return start
    pair = unit * 2
    pos = start % pair
    if abs(pos - unit) < 1e-6:  # exactly on the offbeat
        return start - unit + pair * (float(swing) / 100.0)
    return start


def build_smf(tempo_bpm, tracks, time_sig=(4, 4), key=None, tempo_changes=None,
              swing=None, swing_unit=0.5):
    """tracks: [{name, channel, program?, volume?, pan?,
                 notes: [(start_beats, dur_beats, pitch, vel), ...],
                 cc: [(beat, controller, value), ...],
                 bends: [(beat, value), ...]}]  # bend value -8192..8191

    `key` is a name like 'Am' or 'Eb'; `tempo_changes` is [(beat, bpm), ...]
    applied after the initial tempo. `swing` (50-75, 50 = straight) moves
    offbeat notes late, DAW-style; `swing_unit` is the subdivision in beats
    (0.5 = 8th swing, 0.25 = 16th swing); tracks may override with their own
    `swing`. Returns SMF format-1 bytes. Channel 9 is the GM drum channel.
    """
    chunks = []
    num, den = time_sig
    den_pow = {1: 0, 2: 1, 4: 2, 8: 3, 16: 4}[den]

    def tempo_event(beat, bpm):
        if not 4 <= float(bpm) <= 999:
            raise ValueError("tempo out of range 4-999: %r" % bpm)
        us = round(60_000_000 / float(bpm))
        return (round(float(beat) * PPQ), 0, b"\xff\x51\x03" + us.to_bytes(3, "big"))

    meta = [
        tempo_event(0, tempo_bpm),
        (0, 0, bytes([0xFF, 0x58, 0x04, num, den_pow, 24, 8])),
    ]
    if key:
        meta.append((0, 0, key_signature_meta(key)))
    for beat, bpm in tempo_changes or []:
        meta.append(tempo_event(beat, bpm))
    chunks.append(_track_chunk(meta))

    for t in tracks:
        ch = int(t["channel"])
        if not 0 <= ch <= 15:
            raise ValueError("channel out of range 0-15: %r" % ch)
        ev = []
        name = t.get("name")
        if name:
            nb = str(name).encode("utf-8")
            ev.append((0, 0, b"\xff\x03" + _vlq(len(nb)) + nb))
        if t.get("program") is not None:
            ev.append((0, 0, bytes([0xC0 | ch, int(t["program"]) & 0x7F])))
        if t.get("volume") is not None:
            ev.append((0, 0, bytes([0xB0 | ch, 7, max(0, min(127, int(t["volume"])))])))
        if t.get("pan") is not None:
            ev.append((0, 0, bytes([0xB0 | ch, 10, max(0, min(127, int(t["pan"])))])))
        track_swing = t.get("swing", swing)
        for note in t["notes"]:
            start, dur, pitch, vel = note
            if float(start) < 0:
                raise ValueError("negative note start: %r" % (note,))
            start = _swing_start(float(start), track_swing, swing_unit)
            pitch = int(pitch)
            if not 0 <= pitch <= 127:
                raise ValueError("pitch out of range 0-127: %r" % (note,))
            on = round(float(start) * PPQ)
            off = round((float(start) + float(dur)) * PPQ)
            if off <= on:
                off = on + 1
            vel = max(1, min(127, int(vel)))
            # note-offs (prio 1) sort before note-ons (prio 2) at the same tick
            ev.append((on, 2, bytes([0x90 | ch, pitch, vel])))
            ev.append((off, 1, bytes([0x80 | ch, pitch, 0])))
        for beat, controller, value in t.get("cc") or []:
            tick = round(float(beat) * PPQ)
            data = bytes([0xB0 | ch, int(controller) & 0x7F, max(0, min(127, int(value)))])
            ev.append((tick, 0, data))
        for beat, value in t.get("bends") or []:
            tick = round(float(beat) * PPQ)
            raw = max(0, min(16383, int(value) + 8192))
            ev.append((tick, 0, bytes([0xE0 | ch, raw & 0x7F, (raw >> 7) & 0x7F])))
        chunks.append(_track_chunk(ev))

    header = (
        b"MThd"
        + (6).to_bytes(4, "big")
        + (1).to_bytes(2, "big")
        + len(chunks).to_bytes(2, "big")
        + PPQ.to_bytes(2, "big")
    )
    return header + b"".join(chunks)


def write_smf(path, tempo_bpm, tracks, time_sig=(4, 4), key=None, tempo_changes=None,
              swing=None, swing_unit=0.5):
    data = build_smf(tempo_bpm, tracks, time_sig, key=key, tempo_changes=tempo_changes,
                     swing=swing, swing_unit=swing_unit)
    with open(path, "wb") as f:
        f.write(data)
    return len(data)


_PC = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "F": 5,
       "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9, "A#": 10,
       "Bb": 10, "B": 11}
_QUALITIES = {"": (0, 4, 7), "m": (0, 3, 7), "7": (0, 4, 7, 10),
              "maj7": (0, 4, 7, 11), "m7": (0, 3, 7, 10), "dim": (0, 3, 6),
              "sus4": (0, 5, 7), "add9": (0, 4, 7, 14), "m9": (0, 3, 7, 14)}


def chord_pitches(name, octave=3):
    """'Am' -> [69, 72, 76] (A3 C4 E4). Root placed in octave `octave`."""
    name = name.strip()
    root = name[:2] if len(name) > 1 and name[1] in "#b" else name[:1]
    root = root[0].upper() + root[1:]
    quality = name[len(root):]
    if root not in _PC:
        raise ValueError("unknown chord root: %r" % name)
    if quality not in _QUALITIES:
        raise ValueError("unknown chord quality %r (know: %s)"
                         % (quality, sorted(_QUALITIES)))
    base = 12 * (octave + 2) + _PC[root]
    return [base + iv for iv in _QUALITIES[quality]]


def progression_notes(chords, bars_per_chord=2, beats_per_bar=4, octave=3,
                      vel=68, gap=0.25):
    """Sustained chord notes for a progression — one voicing per chord,
    gently varied velocities, small gap before each change."""
    notes = []
    span = bars_per_chord * beats_per_bar
    for ci, name in enumerate(chords):
        start = ci * span
        for vi, pitch in enumerate(chord_pitches(name, octave)):
            notes.append((start, span - gap, pitch,
                          max(1, min(127, vel + ((ci * 5 + vi * 3) % 7) - 3))))
    return notes
