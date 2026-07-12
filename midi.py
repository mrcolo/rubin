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


def _humanize_start(start, i, amount, beats_per_bar=4):
    """Deterministic micro-timing drift, up to +/- `amount` beats.

    Bar downbeats stay anchored — players drift inside the bar but land
    the one together.
    """
    if not amount or start % beats_per_bar < 1e-9:
        return start
    frac = (((i * 7919) % 13) - 6) / 6.0  # -1..1, deterministic per index
    return max(0.0, start + frac * float(amount))


def build_smf(tempo_bpm, tracks, time_sig=(4, 4), key=None, tempo_changes=None,
              swing=None, swing_unit=0.5, humanize=None):
    """tracks: [{name, channel, program?, volume?, pan?,
                 notes: [(start_beats, dur_beats, pitch, vel), ...],
                 cc: [(beat, controller, value), ...],
                 bends: [(beat, value), ...]}]  # bend value -8192..8191

    `key` is a name like 'Am' or 'Eb'; `tempo_changes` is [(beat, bpm), ...]
    applied after the initial tempo. `swing` (50-75, 50 = straight) moves
    offbeat notes late, DAW-style; `swing_unit` is the subdivision in beats
    (0.5 = 8th swing, 0.25 = 16th swing); tracks may override with their own
    `swing`. `humanize` (beats, e.g. 0.02) adds deterministic micro-timing
    drift to non-downbeat notes; tracks may override. Returns SMF format-1
    bytes. Channel 9 is the GM drum channel.
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
        track_hum = t.get("humanize", humanize)
        for ni, note in enumerate(t["notes"]):
            start, dur, pitch, vel = note
            if float(start) < 0:
                raise ValueError("negative note start: %r" % (note,))
            start = _swing_start(float(start), track_swing, swing_unit)
            start = _humanize_start(start, ni, track_hum, time_sig[0])
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
              swing=None, swing_unit=0.5, humanize=None):
    data = build_smf(tempo_bpm, tracks, time_sig, key=key, tempo_changes=tempo_changes,
                     swing=swing, swing_unit=swing_unit, humanize=humanize)
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


def _nearest_pitch(pc, target):
    """The pitch with class `pc` closest to `target`."""
    return min((pc + 12 * k for k in range(11)), key=lambda p: abs(p - target))


def progression_notes(chords, bars_per_chord=2, beats_per_bar=4, octave=None,
                      vel=None, gap=0.25, style="pad", voice_lead=True):
    """Notes for a chord progression in one of three styles:

    - "pad": sustained voicings (default), one per chord, voice-led so
      each voice moves to its nearest chord tone instead of jumping in
      parallel (disable with voice_lead=False)
    - "bass": monophonic root groove with octave pickups, low register
    - "arp": 8th-note up-down cycle over the chord tones, an octave up

    Velocities are gently varied so nothing reads as robotic.
    """
    if style not in ("pad", "bass", "arp"):
        raise ValueError("unknown progression style %r" % style)
    octave = octave if octave is not None else {"pad": 3, "bass": 0, "arp": 4}[style]
    vel = vel if vel is not None else {"pad": 68, "bass": 98, "arp": 56}[style]
    notes = []
    span = bars_per_chord * beats_per_bar

    def hv(ci, i, base):  # humanized velocity
        return max(1, min(127, base + ((ci * 5 + i * 3) % 7) - 3))

    prev_voicing = None
    for ci, name in enumerate(chords):
        start = ci * span
        pitches = chord_pitches(name, octave)
        if style == "pad":
            if voice_lead and prev_voicing:
                center = sum(prev_voicing) / len(prev_voicing)
                pitches = sorted(_nearest_pitch(p % 12, center) for p in pitches)
            prev_voicing = pitches
            for vi, pitch in enumerate(pitches):
                notes.append((start, span - gap, pitch, hv(ci, vi, vel)))
        elif style == "bass":
            root = pitches[0]
            for bar in range(bars_per_chord):
                b = start + bar * beats_per_bar
                last = bar == bars_per_chord - 1
                notes.append((b, beats_per_bar - 1.25, root, hv(ci, bar, vel)))
                notes.append((b + beats_per_bar - 1, 0.5, root, hv(ci, bar + 1, vel - 12)))
                notes.append((b + beats_per_bar - 0.5, 0.5,
                              root + (12 if last else 0), hv(ci, bar + 2, vel - 16)))
        else:  # arp
            # true up-down traversal: [0,1,2,1] for triads, [0,1,2,3,2,1]
            # for 7ths — no stuttered repeats at the turnaround
            cycle = list(range(len(pitches))) + list(range(len(pitches) - 2, 0, -1))
            steps = int(span / 0.5)
            for i in range(steps):
                pitch = pitches[cycle[i % len(cycle)]]
                notes.append((start + i * 0.5, 0.45, pitch,
                              hv(ci, i, vel + (6 if i % 4 == 0 else 0))))
    return notes


KICK, SNARE, CLAP, CHAT, OHAT, CRASH = 36, 38, 39, 42, 46, 49


def drum_pattern(pattern, bars=8, fills=True):
    """Drum notes (for channel 9) in a named style:

    - "half_time": dark R&B/Weeknd — snare on 3, sparse kick, 8th hats
    - "four_on_floor": house — kick every beat, open-hat offbeats, clap 2+4
    - "boom_bap": hip-hop — kick 1 and the and-of-2, snare 2+4
    - "trap": half-time with 16th hats and roll ornaments

    Ghost notes, alternating-bar variation, and velocity humanization are
    built in; `fills` adds a snare roll into every 4th bar.
    """
    if pattern not in ("half_time", "four_on_floor", "boom_bap", "trap"):
        raise ValueError("unknown drum pattern %r" % pattern)
    notes = []

    def hv(bar, i, base):
        return max(1, min(127, base + ((bar * 13 + i * 7) % 9) - 4))

    for bar in range(bars):
        t = bar * 4.0
        if pattern == "half_time":
            notes.append((t, 0.4, KICK, hv(bar, 0, 112)))
            notes.append((t + 1.5, 0.4, KICK, hv(bar, 1, 103)))
            if bar % 2 == 1:
                notes.append((t + 3.25, 0.4, KICK, hv(bar, 2, 97)))
            notes.append((t + 2, 0.4, SNARE, hv(bar, 3, 106)))
            notes.append((t + 2, 0.4, CLAP, hv(bar, 4, 92)))
            if bar % 4 == 1:
                notes.append((t + 3.75, 0.2, SNARE, 34))
            openh = bar % 4 == 3
            for i in range(8):
                pos = i * 0.5
                if openh and pos == 3.5:
                    continue
                notes.append((t + pos, 0.2, CHAT, hv(bar, i, 84 if i % 2 == 0 else 52)))
            if openh:
                notes.append((t + 3.5, 0.6, OHAT, 74))
        elif pattern == "four_on_floor":
            for beat in range(4):
                notes.append((t + beat, 0.4, KICK, hv(bar, beat, 114)))
            notes.append((t + 1, 0.4, CLAP, hv(bar, 4, 96)))
            notes.append((t + 3, 0.4, CLAP, hv(bar, 5, 96)))
            for i in range(4):
                notes.append((t + i + 0.5, 0.4, OHAT, hv(bar, i, 78)))
            for i in range(8):
                notes.append((t + i * 0.5, 0.15, CHAT, hv(bar, i, 46)))
        elif pattern == "boom_bap":
            notes.append((t, 0.4, KICK, hv(bar, 0, 112)))
            notes.append((t + 2.5, 0.4, KICK, hv(bar, 1, 104)))
            if bar % 2 == 1:
                notes.append((t + 1.75, 0.3, KICK, hv(bar, 2, 88)))
            notes.append((t + 1, 0.4, SNARE, hv(bar, 3, 108)))
            notes.append((t + 3, 0.4, SNARE, hv(bar, 4, 106)))
            for i in range(8):
                notes.append((t + i * 0.5, 0.2, CHAT, hv(bar, i, 80 if i % 2 == 0 else 54)))
        else:  # trap
            notes.append((t, 0.4, KICK, hv(bar, 0, 114)))
            notes.append((t + (0.75 if bar % 2 == 0 else 1.5), 0.4, KICK, hv(bar, 1, 102)))
            notes.append((t + 2.75, 0.4, KICK, hv(bar, 2, 98)))
            notes.append((t + 2, 0.4, SNARE, hv(bar, 3, 110)))
            for i in range(16):
                pos = i * 0.25
                base = 72 if i % 4 == 0 else 48
                notes.append((t + pos, 0.1, CHAT, hv(bar, i, base)))
            if bar % 2 == 1:  # 32nd roll ornament into beat 4
                for j in range(4):
                    notes.append((t + 3.5 + j * 0.125, 0.08, CHAT, 60 + j * 8))
        if fills and bar % 4 == 3:
            for j, pos in enumerate((3.0, 3.25, 3.5, 3.625, 3.75, 3.875)):
                notes.append((t + pos, 0.1, SNARE, 58 + j * 10))
    return notes
