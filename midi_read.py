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
        chan_counts = {}
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
                    chan_counts[ch] = chan_counts.get(ch, 0) + 1
                elif kind == 0x80 or (kind == 0x90 and d2 == 0):
                    stack = open_notes.get((ch, d1))
                    if stack:
                        start, vel = stack.pop(0)
                        notes.append(
                            (start / ppq, (tick - start) / ppq, d1, vel)
                        )
        notes.sort()
        channel = max(chan_counts, key=chan_counts.get) if chan_counts else None
        tracks.append({"name": name, "notes": notes, "channel": channel})
        pos = end
    return ppq, tempo_bpm, tracks


def pitch_name(p):
    return "%s%d" % (NOTE_NAMES[p % 12], p // 12 - 2)


def spelled_pitch_name(p, prefer_flats):
    """pitch_name, respelled to flats in flat-key contexts (Eb2, not D#2)."""
    name = NOTE_NAMES[p % 12]
    if prefer_flats:
        name = _SHARP_TO_FLAT.get(name, name)
    return "%s%d" % (name, p // 12 - 2)


def analyze(path):
    """Summarize a .mid: per-track range, note count, density, polyphony."""
    with open(path, "rb") as f:
        ppq, tempo, tracks = parse_smf(f.read())
    out = {"tempo": round(tempo, 2), "tracks": []}
    all_notes = [n for t in tracks for n in t["notes"]]
    key, conf = guess_key(all_notes)
    if key:
        flats = respell(key, True) in FLAT_KEYS
        key = respell(key, flats)
        out["key_guess"] = key
        out["key_confidence"] = conf
    if all_notes and max(n[0] + n[1] for n in all_notes) >= 32:
        # only meaningful past ~8 bars; short clips are one window anyway
        out["density_curve"] = density_curve(tracks)
    chords = guess_chords(tracks)
    if any(c and c != "?" for c in chords):
        flats = out.get("key_guess") in FLAT_KEYS
        out["chords"] = [respell(c, flats) if c else c for c in chords]
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
        flats = out.get("key_guess") in FLAT_KEYS
        entry = {
            "name": t["name"],
            "channel": t.get("channel"),
            "notes": len(notes),
            "low": spelled_pitch_name(min(pitches), flats),
            "high": spelled_pitch_name(max(pitches), flats),
            "low_pitch": min(pitches),
            "high_pitch": max(pitches),
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
    # size by last onset, not last ring-out: the curve counts note starts,
    # so a sustained tail must not fabricate an empty trailing window
    end = max(n[0] for n, _ in all_notes) + 1e-9
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
        if t.get("channel") == 9:
            continue  # drum pitches are kit pieces, not register
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


def describe(path):
    """One-paragraph human summary of a .mid, built from analyze()."""
    a = analyze(path)
    bits = []
    tracks = a["tracks"]
    total = sum(t["notes"] for t in tracks)
    length = max((t["length_beats"] for t in tracks), default=0)
    bits.append("%d track(s), %d notes, ~%d bars at %g BPM" % (
        len(tracks), total, round(length / 4), a["tempo"]))
    if a.get("key_guess"):
        bits.append("key %s (%.0f%% confident)" % (a["key_guess"], a["key_confidence"] * 100))
    swings = {t["swing_guess"] for t in tracks if "swing_guess" in t and t["swing_guess"] > 52}
    if swings:
        bits.append("swung feel (~%d%%)" % max(swings))
    chords = a.get("chords")
    if chords:
        seq, last = [], object()
        for c in chords:
            if c != last:
                seq.append(c or "-")
                last = c
        bits.append("progression %s" % "-".join(seq[:12]))
    curve = a.get("density_curve")
    if curve and len(curve) >= 3:
        vals = [c["notes_per_beat"] for c in curve]
        if max(vals) > 2.5 * (min(vals) + 0.01):
            bits.append("dynamic arrangement (density %.1f-%.1f notes/beat)" % (min(vals), max(vals)))
        else:
            bits.append("flat energy contour - consider distinct sections")
    parts = "; ".join(
        "%s: %s-%s%s" % (t["name"] or "?", t["low"], t["high"],
                         " (poly %d)" % t["max_polyphony"] if t["max_polyphony"] > 1 else "")
        for t in tracks[:6])
    text = ". ".join(bits) + ". Parts - " + parts + "."
    for w in a.get("warnings", []):
        text += " WARNING: %s." % w
    return text


_CHORD_TEMPLATES = [
    ("", (0, 4, 7)),        # major
    ("m", (0, 3, 7)),       # minor
    ("7", (0, 4, 7, 10)),   # dominant 7th
    ("maj7", (0, 4, 7, 11)),
    ("m7", (0, 3, 7, 10)),
]


def guess_chords(tracks, beats_per_bar=4, max_bars=64):
    """Per-bar chord names from all non-drum tracks (duration-weighted
    pitch-class mass vs. triad/7th templates). '?' where nothing fits well;
    'A?' = root evident but too few pitch classes to judge quality (e.g. a
    bare bassline). None entries trimmed from the tail."""
    notes = [n for t in tracks if t.get("channel") != 9 for n in t["notes"]]
    if not notes:
        return []
    end = min(max(n[0] + n[1] for n in notes), beats_per_bar * max_bars)
    chords = []
    bar = 0
    while bar * beats_per_bar < end:
        lo, hi = bar * beats_per_bar, (bar + 1) * beats_per_bar
        hist = [0.0] * 12
        bass_pitch = None
        for s, d, p, _v in notes:
            s2, e2 = max(s, lo), min(s + d, hi)
            if e2 > s2:
                hist[p % 12] += e2 - s2
                if bass_pitch is None or p < bass_pitch:
                    bass_pitch = p
        total = sum(hist)
        if total == 0:
            chords.append(None)
            bar += 1
            continue
        meaningful = [pc for pc in range(12) if hist[pc] > total * 0.05]
        if len(meaningful) < 3:
            # a root alone (e.g. a bassline) can't reveal chord quality —
            # report the root with an explicit unknown marker, don't guess
            root = max(range(12), key=lambda pc: hist[pc])
            chords.append(NOTE_NAMES[root] + "?")
            bar += 1
            continue
        best = ("?", 0.0)
        for root in range(12):
            for suffix, template in _CHORD_TEMPLATES:
                tones = [hist[(root + iv) % 12] for iv in template]
                mass = sum(tones)
                # coverage penalizes templates with absent tones, so an
                # inverted triad isn't claimed by a 7th chord it subsets
                coverage = sum(1 for x in tones if x > total * 0.05) / len(template)
                score = (mass / total) * coverage
                if bass_pitch is not None and bass_pitch % 12 == root:
                    score += 0.05  # bass note on the root is strong evidence
                if score > best[1]:
                    best = (NOTE_NAMES[root] + suffix, score)
        chords.append(best[0] if best[1] >= 0.65 else "?")
        bar += 1
    while chords and chords[-1] is None:
        chords.pop()
    return chords


def _stock_progression(key):
    """A dependable progression transposed into `key`:
    minor -> i-VI-III-V (Am-F-C-E shape), major -> I-V-vi-IV (C-G-Am-F)."""
    flats = {"Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#", "Cb": "B"}
    minor = bool(key) and key.endswith("m")
    root_name = (key[:-1] if minor else key) if key else "A"
    root_name = flats.get(root_name, root_name)
    if root_name not in NOTE_NAMES:
        root_name, minor = "A", True  # unrecognized: safe dark default
    root = NOTE_NAMES.index(root_name)
    if not key:
        minor = True
    if minor:
        steps = [(0, "m"), (8, ""), (3, ""), (7, "")]
    else:
        steps = [(0, ""), (7, ""), (9, "m"), (5, "")]
    flats = key in FLAT_KEYS if key else False
    return [respell(NOTE_NAMES[(root + iv) % 12] + q, flats) for iv, q in steps]


def suggest_accompaniment(path):
    """Ready-to-use compose_midi arguments that complement this file:
    same tempo/key/feel, progression from detected chords, and only the
    roles whose register the source doesn't already occupy."""
    a = analyze(path)
    notes = []
    tempo = a["tempo"]
    key = a.get("key_guess")

    # progression: collapse per-bar chords to changes + bars per chord
    chords_raw = [c for c in a.get("chords", []) if c]
    prog, runs = [], []
    for c in chords_raw:
        if prog and c == prog[-1]:
            runs[-1] += 1
        else:
            prog.append(c)
            runs.append(1)
    clean = [c for c in prog if not c.endswith("?")]
    if prog and len(clean) == len(prog):
        bars_per_chord = max(1, round(sum(runs) / len(runs)))
        chords = prog
        notes.append("progression taken from the source (%s)" % "-".join(chords))
    elif prog and all(c.endswith("?") for c in prog):
        # roots known (e.g. a bassline): assume diatonic quality from key
        minor_root = key[:-1] if key and key.endswith("m") else None
        chords = [c[:-1] + ("m" if minor_root and c[:-1] == minor_root else "")
                  for c in prog]
        bars_per_chord = max(1, round(sum(runs) / len(runs)))
        notes.append("roots from the source; qualities assumed from key %s" % key)
    else:
        chords = _stock_progression(key)
        notes.append("chords ambiguous in source; using a stock %s progression (%s)"
                     % (key or "A minor", "-".join(chords)))
        bars_per_chord = 2

    # which registers does the source occupy?
    low = mid = high = False
    for t in a["tracks"]:
        lo, hi = t["low_pitch"], t["high_pitch"]
        low = low or lo < 48
        mid = mid or (lo <= 71 and hi >= 48)
        high = high or hi >= 72
    tracks = []
    if not low:
        tracks.append({"name": "Bass", "channel": 0, "program": 38,
                       "progression": {"chords": chords, "style": "bass",
                                       "bars_per_chord": bars_per_chord}})
    if not mid:
        tracks.append({"name": "Pad", "channel": 1, "program": 89,
                       "progression": {"chords": chords,
                                       "bars_per_chord": bars_per_chord}})
    if not high:
        tracks.append({"name": "Arp", "channel": 2, "program": 81,
                       "progression": {"chords": chords, "style": "arp",
                                       "bars_per_chord": bars_per_chord}})
    # drums: only if the source doesn't already have them; pattern chosen
    # from the source's tempo and feel
    if any(t.get("channel") == 9 for t in a["tracks"]):
        notes.append("source already has drums; not adding another kit")
    else:
        swung = any(t.get("swing_guess", 50) > 54 for t in a["tracks"])
        if tempo >= 130:
            pattern = "trap"          # 130-160, straight 16ths, half-time feel
        elif tempo >= 118 and not swung:
            pattern = "four_on_floor"  # house range
        elif swung and tempo < 110:
            pattern = "boom_bap"       # swung hip-hop
        else:
            pattern = "half_time"      # slow dark R&B default
        notes.append("drum pattern '%s' chosen from tempo %g and %s feel"
                     % (pattern, tempo, "swung" if swung else "straight"))
        tracks.append({"name": "Drums", "channel": 9,
                       "drums": {"pattern": pattern,
                                 "bars": max(8, len(chords) * bars_per_chord)}})
    out = {"tempo": tempo, "tracks": tracks, "humanize": 0.02}
    if key:
        out["key"] = key
    swings = [t["swing_guess"] for t in a["tracks"]
              if t.get("swing_guess", 50) > 52]
    if swings:
        out["swing"] = max(swings)
        notes.append("matched the source's swing (%d%%)" % out["swing"])
    out["suggestion_notes"] = notes
    return out


_SHARP_TO_FLAT = {"C#": "Db", "D#": "Eb", "F#": "Gb", "G#": "Ab", "A#": "Bb"}
# keys whose conventional spelling uses flats (majors + relative minors)
FLAT_KEYS = {"F", "Bb", "Eb", "Ab", "Db", "Gb",
             "Dm", "Gm", "Cm", "Fm", "Bbm", "Ebm"}


def respell(name, prefer_flats):
    """'A#m' -> 'Bbm' when the key context reads in flats."""
    if not prefer_flats or not name:
        return name
    root = name[:2] if len(name) > 1 and name[1] == "#" else name[:1]
    if root in _SHARP_TO_FLAT:
        return _SHARP_TO_FLAT[root] + name[len(root):]
    return name
