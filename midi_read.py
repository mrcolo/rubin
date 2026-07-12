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
    all_notes = [n for t in tracks for n in t["notes"]]
    key, conf = guess_key(all_notes)
    if key:
        out["key_guess"] = key
        out["key_confidence"] = conf
    if all_notes and max(n[0] + n[1] for n in all_notes) >= 32:
        # only meaningful past ~8 bars; short clips are one window anyway
        out["density_curve"] = density_curve(tracks)
    warns = arrangement_warnings(tracks)
    if warns:
        out["warnings"] = warns
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
        entry = {
            "name": t["name"],
            "notes": len(notes),
            "low": pitch_name(min(pitches)),
            "high": pitch_name(max(pitches)),
            "mean_velocity": round(sum(vels) / len(vels)),
            "length_beats": round(span, 2),
            "notes_per_beat": round(len(notes) / span, 2) if span else 0,
            "max_polyphony": poly,
        }
        # per-track key only where it's meaningful (enough tonal variety —
        # skips drum tracks and one-note parts)
        if len(notes) >= 8 and len({n[2] % 12 for n in notes}) >= 5:
            tkey, tconf = guess_key(notes)
            if tconf >= 0.6:  # low correlation = atonal content (e.g. drums)
                entry["key_guess"] = tkey
                entry["key_confidence"] = tconf
        sw = guess_swing(notes)
        if sw is not None:
            entry["swing_guess"] = sw
        out["tracks"].append(entry)
    return out


# Krumhansl-Kessler key profiles (major / minor)
_KS_MAJOR = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
_KS_MINOR = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]


def _correlate(hist, profile):
    n = 12
    mh, mp = sum(hist) / n, sum(profile) / n
    num = sum((hist[i] - mh) * (profile[i] - mp) for i in range(n))
    dh = sum((h - mh) ** 2 for h in hist) ** 0.5
    dp = sum((p - mp) ** 2 for p in profile) ** 0.5
    return num / (dh * dp) if dh and dp else 0.0


def guess_key(notes):
    """Estimate key from notes [(start, dur, pitch, vel)] via pitch-class
    profile correlation (duration-weighted). Returns (name, confidence)."""
    hist = [0.0] * 12
    for _s, dur, pitch, _v in notes:
        hist[pitch % 12] += max(float(dur), 0.1)
    if not any(hist):
        return None, 0.0
    best = (None, -2.0)
    for root in range(12):
        rot = hist[root:] + hist[:root]
        for profile, suffix in ((_KS_MAJOR, ""), (_KS_MINOR, "m")):
            r = _correlate(rot, profile)
            if r > best[1]:
                best = (NOTE_NAMES[root] + suffix, r)
    return best[0], round(best[1], 3)


def guess_swing(notes):
    """Estimate 8th-note swing from offbeat placement. Returns percent
    (50 = straight, ~62 = MPC feel) or None when there's no consistent
    offbeat cluster (e.g. straight-16th content or too few offbeats)."""
    fracs = sorted(s % 1.0 for s, _d, _p, _v in notes if 0.4 <= (s % 1.0) <= 0.8)
    if len(fracs) < 6:
        return None
    mid = len(fracs) // 2
    med = fracs[mid] if len(fracs) % 2 else (fracs[mid - 1] + fracs[mid]) / 2
    mean = sum(fracs) / len(fracs)
    spread = (sum((f - mean) ** 2 for f in fracs) / len(fracs)) ** 0.5
    if spread > 0.04:  # not one cluster: mixed subdivisions, skip
        return None
    return round(med * 100)


def density_curve(tracks, beats_per_bar=4, window_bars=4):
    """Energy contour: per window of bars, total notes/beat and how many
    tracks are active. Reveals arrangement structure (intro/verse/chorus)."""
    all_notes = [(n, ti) for ti, t in enumerate(tracks) for n in t["notes"]]
    if not all_notes:
        return []
    window = beats_per_bar * window_bars
    end = max(n[0] + n[1] for n, _ in all_notes)
    curve = []
    w = 0
    while w * window < end:
        lo, hi = w * window, (w + 1) * window
        in_win = [(n, ti) for n, ti in all_notes if lo <= n[0] < hi]
        curve.append({
            "bar": w * window_bars + 1,
            "notes_per_beat": round(len(in_win) / window, 2),
            "active_tracks": len({ti for _, ti in in_win}),
        })
        w += 1
    return curve


def arrangement_warnings(tracks):
    """Flag arrangement problems the ear would find: parts crowding the
    same register, multiple parts fighting over the low end, and
    robotically flat velocities."""
    named = []
    for i, t in enumerate(tracks):
        if len(t["notes"]) >= 8:
            pitches = sorted(n[2] for n in t["notes"])
            named.append((t["name"] or "track %d" % (i + 1), pitches))
    warnings = []
    for a in range(len(named)):
        for b in range(a + 1, len(named)):
            (na, pa), (nb, pb) = named[a], named[b]
            lo = max(pa[0], pb[0])
            hi = min(pa[-1], pb[-1])
            if hi <= lo:
                continue
            overlap = hi - lo
            smaller_span = max(1, min(pa[-1] - pa[0], pb[-1] - pb[0]))
            # share of each part's notes inside the shared range
            in_a = sum(1 for p in pa if lo <= p <= hi) / len(pa)
            in_b = sum(1 for p in pb if lo <= p <= hi) / len(pb)
            if overlap / smaller_span > 0.7 and min(in_a, in_b) > 0.6:
                warnings.append(
                    "'%s' and '%s' crowd the same register (%s-%s) — "
                    "separate them or thin one out" % (na, nb, pitch_name(lo), pitch_name(hi)))
    low_owners = [n for n, p in named
                  if sum(1 for x in p if x < 48) / len(p) > 0.25]
    if len(low_owners) > 1:
        warnings.append(
            "multiple parts live below C2 (%s) — only one should own the low end"
            % ", ".join("'%s'" % n for n in low_owners))
    for i, t in enumerate(tracks):
        vels = [n[3] for n in t["notes"]]
        if len(vels) < 12:
            continue
        mean = sum(vels) / len(vels)
        sd = (sum((v - mean) ** 2 for v in vels) / len(vels)) ** 0.5
        if sd < 2.0:
            warnings.append(
                "'%s' has flat velocities (stddev %.1f) — humanize with "
                "accents and ghost notes" % (t["name"] or "track %d" % (i + 1), sd))
    return warnings
