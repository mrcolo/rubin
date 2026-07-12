"""Minimal Standard MIDI File reader + analyzer. No dependencies.

Counterpart to midi.py's writer: parses notes back out of any .mid (ours or
basic-pitch's) and summarizes what an instrument part sounds like — range,
density, polyphony — so compositions can be informed by transcribed audio.
"""

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _read_vlq(data, pos):
    val = 0
    while True:
        b = data[pos]
        pos += 1
        val = (val << 7) | (b & 0x7F)
        if not b & 0x80:
            return val, pos


def parse_smf(data):
    """Return (ppq, tempo_bpm, tracks) where tracks are
    [{"name": str, "notes": [(start_beats, dur_beats, pitch, vel)]}]."""
    if data[:4] != b"MThd":
        raise ValueError("not a MIDI file")
    ntrks = int.from_bytes(data[10:12], "big")
    ppq = int.from_bytes(data[12:14], "big")
    if ppq & 0x8000:
        raise ValueError("SMPTE time division not supported")
    tempo_bpm = 120.0
    tracks = []
    pos = 14
    for _ in range(ntrks):
        if data[pos:pos + 4] != b"MTrk":
            raise ValueError("bad chunk at %d" % pos)
        length = int.from_bytes(data[pos + 4:pos + 8], "big")
        end = pos + 8 + length
        p = pos + 8
        tick = 0
        running = 0
        name = ""
        open_notes = {}
        notes = []
        while p < end:
            delta, p = _read_vlq(data, p)
            tick += delta
            status = data[p]
            if status & 0x80:
                p += 1
                if status < 0xF0:
                    running = status
            else:
                status = running
            if status == 0xFF:
                meta = data[p]
                mlen, p2 = _read_vlq(data, p + 1)
                body = data[p2:p2 + mlen]
                if meta == 0x03 and not name:
                    name = body.decode("utf-8", "replace")
                elif meta == 0x51 and mlen == 3:
                    tempo_bpm = 60_000_000 / int.from_bytes(body, "big")
                p = p2 + mlen
            elif status in (0xF0, 0xF7):
                slen, p2 = _read_vlq(data, p)
                p = p2 + slen
            else:
                kind = status & 0xF0
                ch = status & 0x0F
                if kind in (0x80, 0x90, 0xA0, 0xB0, 0xE0):
                    d1, d2 = data[p], data[p + 1]
                    p += 2
                elif kind in (0xC0, 0xD0):
                    d1, d2 = data[p], 0
                    p += 1
                else:
                    raise ValueError("bad status 0x%02x at %d" % (status, p))
                if kind == 0x90 and d2 > 0:
                    open_notes.setdefault((ch, d1), []).append((tick, d2))
                elif kind == 0x80 or (kind == 0x90 and d2 == 0):
                    stack = open_notes.get((ch, d1))
                    if stack:
                        start, vel = stack.pop(0)
                        notes.append(
                            (start / ppq, (tick - start) / ppq, d1, vel)
                        )
        notes.sort()
        tracks.append({"name": name, "notes": notes})
        pos = end
    return ppq, tempo_bpm, tracks


def pitch_name(p):
    return "%s%d" % (NOTE_NAMES[p % 12], p // 12 - 2)


def analyze(path):
    """Summarize a .mid: per-track range, note count, density, polyphony."""
    with open(path, "rb") as f:
        ppq, tempo, tracks = parse_smf(f.read())
    out = {"tempo": round(tempo, 2), "tracks": []}
    for t in tracks:
        notes = t["notes"]
        if not notes:
            continue
        pitches = [n[2] for n in notes]
        vels = [n[3] for n in notes]
        span = max(n[0] + n[1] for n in notes) - notes[0][0]
        # polyphony: max simultaneous notes at any note-on
        events = sorted(
            [(n[0], 1) for n in notes] + [(n[0] + n[1], -1) for n in notes],
            key=lambda e: (e[0], e[1]),
        )
        poly = cur = 0
        for _, d in events:
            cur += d
            poly = max(poly, cur)
        out["tracks"].append({
            "name": t["name"],
            "notes": len(notes),
            "low": pitch_name(min(pitches)),
            "high": pitch_name(max(pitches)),
            "mean_velocity": round(sum(vels) / len(vels)),
            "length_beats": round(span, 2),
            "notes_per_beat": round(len(notes) / span, 2) if span else 0,
            "max_polyphony": poly,
        })
    return out
