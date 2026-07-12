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


def build_smf(tempo_bpm, tracks, time_sig=(4, 4)):
    """tracks: [{name, channel, program?, volume?, pan?,
                 notes: [(start_beats, dur_beats, pitch, vel), ...],
                 cc: [(beat, controller, value), ...],
                 bends: [(beat, value), ...]}]  # bend value -8192..8191

    Returns SMF format-1 bytes. Channel 9 is the GM drum channel.
    """
    chunks = []
    tempo_us = round(60_000_000 / float(tempo_bpm))
    num, den = time_sig
    den_pow = {1: 0, 2: 1, 4: 2, 8: 3, 16: 4}[den]
    meta = [
        (0, 0, b"\xff\x51\x03" + tempo_us.to_bytes(3, "big")),
        (0, 0, bytes([0xFF, 0x58, 0x04, num, den_pow, 24, 8])),
    ]
    chunks.append(_track_chunk(meta))

    for t in tracks:
        ch = int(t["channel"]) & 0x0F
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
        for note in t["notes"]:
            start, dur, pitch, vel = note
            on = round(float(start) * PPQ)
            off = round((float(start) + float(dur)) * PPQ)
            if off <= on:
                off = on + 1
            pitch = int(pitch) & 0x7F
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


def write_smf(path, tempo_bpm, tracks, time_sig=(4, 4)):
    data = build_smf(tempo_bpm, tracks, time_sig)
    with open(path, "wb") as f:
        f.write(data)
    return len(data)
