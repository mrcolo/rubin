"""Zero-dependency WAV slicer / arranger.

The audio counterpart to midi.py: decode PCM WAV (16/24/32-bit), cut and
transform clips, and arrange them on a tempo grid into a rendered mix. Pure
stdlib — samples are kept as interleaved floats in an `array('f')`.

This is how rubin "cuts samples to make a song": deterministic file edits,
no DAW UI. Output is a standard 16-bit WAV any DAW imports.
"""

import array
import audioop
import math
import wave


class Clip:
    def __init__(self, samples, rate, channels):
        self.s = samples          # array('f'), interleaved, normalized [-1, 1]
        self.rate = rate
        self.ch = channels

    @property
    def frames(self):
        return len(self.s) // self.ch

    @property
    def duration(self):
        return self.frames / self.rate

    # ---- construction -------------------------------------------------
    @staticmethod
    def load(path):
        w = wave.open(path, "rb")
        ch, sw, rate, nf = (w.getnchannels(), w.getsampwidth(),
                            w.getframerate(), w.getnframes())
        raw = w.readframes(nf)
        w.close()
        if sw not in (1, 2, 3, 4):
            raise ValueError("unsupported sample width %d bytes" % sw)
        # audioop does the bit-depth conversion to 32-bit in one C call
        # (the per-sample Python decode was the slow path, esp. for 24-bit)
        ints = array.array("i")
        ints.frombytes(audioop.lin2lin(raw, sw, 4))
        scale = 1.0 / 2147483648.0
        out = array.array("f", [x * scale for x in ints])
        return Clip(out, rate, ch)

    @staticmethod
    def silence(seconds, rate=44100, channels=2):
        return Clip(array.array("f", bytes(4 * channels * int(seconds * rate))),
                    rate, channels)

    def copy(self):
        return Clip(array.array("f", self.s), self.rate, self.ch)

    @staticmethod
    def tone(freq, seconds, rate=44100, channels=2, amp=0.7):
        """A sine tone — a from-scratch sound source for the arranger."""
        n = int(seconds * rate)
        step = 2 * math.pi * freq / rate
        out = array.array("f", bytes(4 * channels * n))
        for i in range(n):
            v = amp * math.sin(step * i)
            for c in range(channels):
                out[i * channels + c] = v
        return Clip(out, rate, channels)

    @staticmethod
    def noise(seconds, rate=44100, channels=2, amp=0.6):
        """White noise (deterministic LCG) — snares, risers, textures."""
        n = int(seconds * rate)
        out = array.array("f", bytes(4 * channels * n))
        seed = 12345
        for i in range(n):
            seed = (1103515245 * seed + 12345) & 0x7FFFFFFF
            v = amp * (seed / 0x3FFFFFFF - 1.0)
            for c in range(channels):
                out[i * channels + c] = v
        return Clip(out, rate, channels)

    # ---- edits (return new clips; originals untouched) ----------------
    def slice(self, start_s, end_s=None):
        a = max(0, int(start_s * self.rate)) * self.ch
        b = (self.frames if end_s is None else int(end_s * self.rate)) * self.ch
        b = min(b, len(self.s))
        return Clip(self.s[a:b], self.rate, self.ch)

    def gain(self, factor):
        return Clip(array.array("f", [x * factor for x in self.s]), self.rate, self.ch)

    def normalize(self, peak=0.95):
        """Scale so the clip's own peak sits at `peak` — evens out one-shots
        recorded at inconsistent levels before arranging."""
        hi = 0.0
        for x in self.s:
            a = x if x >= 0 else -x
            if a > hi:
                hi = a
        if hi == 0:
            return self.copy()
        return self.gain(peak / hi)

    def pan(self, p):
        """Balance a stereo clip: p in [-1,1], -1 = hard left, 0 = center,
        +1 = hard right. Mono clips are returned unchanged."""
        if self.ch != 2 or p == 0:
            return self.copy()
        lg = min(1.0, 1.0 - p)
        rg = min(1.0, 1.0 + p)
        out = array.array("f", self.s)
        for i in range(0, len(out), 2):
            out[i] *= lg
            out[i + 1] *= rg
        return Clip(out, self.rate, self.ch)

    def reverse(self):
        out = array.array("f", self.s)
        # reverse frame order, keep channel interleave
        fr = self.frames
        for i in range(fr):
            for c in range(self.ch):
                out[i * self.ch + c] = self.s[(fr - 1 - i) * self.ch + c]
        return Clip(out, self.rate, self.ch)

    def fade(self, in_ms=0, out_ms=0):
        out = array.array("f", self.s)
        fin = int(in_ms / 1000 * self.rate)
        fout = int(out_ms / 1000 * self.rate)
        fr = self.frames
        for i in range(min(fin, fr)):
            g = i / fin
            for c in range(self.ch):
                out[i * self.ch + c] *= g
        for i in range(min(fout, fr)):
            g = i / fout
            f = fr - 1 - i
            for c in range(self.ch):
                out[f * self.ch + c] *= g
        return Clip(out, self.rate, self.ch)

    def pitch(self, semitones):
        """Naive resample: shifts pitch AND speed (classic sampler/chop sound)."""
        if semitones == 0:
            return self.copy()
        ratio = 2 ** (semitones / 12.0)   # up = faster/shorter
        new_frames = int(self.frames / ratio)
        out = array.array("f", bytes(4 * self.ch * new_frames))
        for i in range(new_frames):
            src = i * ratio
            j = int(src)
            frac = src - j
            for c in range(self.ch):
                a = self.s[j * self.ch + c] if j * self.ch + c < len(self.s) else 0.0
                k = (j + 1) * self.ch + c
                b = self.s[k] if k < len(self.s) else a
                out[i * self.ch + c] = a + (b - a) * frac
        return Clip(out, self.rate, self.ch)


class Arrangement:
    """Place clips at absolute times (or beats) and render a mix."""

    def __init__(self, tempo=140.0, rate=44100, channels=2):
        self.tempo = float(tempo)
        self.rate = rate
        self.ch = channels
        self.events = []   # (start_frame, clip)

    def beat_to_s(self, beat):
        return beat * 60.0 / self.tempo

    def add(self, clip, at_beat=None, at_s=None, gain=1.0):
        if at_s is None:
            at_s = self.beat_to_s(at_beat or 0)
        c = clip if gain == 1.0 else clip.gain(gain)
        self.events.append((int(at_s * self.rate), c))
        return self

    def render(self):
        if not self.events:
            return Clip(array.array("f", []), self.rate, self.ch)
        end = max(start + c.frames for start, c in self.events)
        mix = array.array("f", bytes(4 * self.ch * end))
        for start, c in self.events:
            base = start * self.ch
            cs = c.s
            for i in range(len(cs)):
                mix[base + i] += cs[i]
        return Clip(mix, self.rate, self.ch)


def _soft_clip(x, knee=0.8):
    """tanh-style soft saturation above `knee`; linear below (transparent for
    quiet content, tames loud transients without a hard-clip edge)."""
    a = x if x >= 0 else -x
    if a <= knee:
        return x
    over = (a - knee) / (1.0 - knee)
    shaped = knee + (1.0 - knee) * math.tanh(over)
    return shaped if x >= 0 else -shaped


def write_wav(path, clip, peak=0.89, limit=False):
    """Write a clip to 16-bit WAV, normalized so the peak sits at `peak`.

    With `limit=True`, apply soft-clip limiting first so a single loud
    transient doesn't peak-normalize the whole mix into quietness — useful
    for dense sample stacks. Quiet content is unaffected either way.
    """
    s = clip.s
    if limit:
        s = array.array("f", [_soft_clip(x) for x in s])
    hi = 0.0
    for x in s:
        a = x if x >= 0 else -x
        if a > hi:
            hi = a
    scale = (peak / hi) if hi > 0 else 1.0
    ints = array.array("h", bytes(2 * len(s)))
    for i in range(len(s)):
        v = int(s[i] * scale * 32767)
        ints[i] = 32767 if v > 32767 else (-32768 if v < -32768 else v)
    w = wave.open(path, "wb")
    w.setnchannels(clip.ch)
    w.setsampwidth(2)
    w.setframerate(clip.rate)
    w.writeframes(ints.tobytes())
    w.close()
    return clip.duration


def cut_arrange(events, tempo=140.0, out_path=None, limit=False):
    """Build a song from sample slices. `events` is a list of dicts:
      {file, at_beat, start?, end?, pitch?, gain?, reverse?, fade_in?,
       fade_out?, pan? (-1..1), normalize?, pitch_to?/from_note?, repeat?:{times, every}}
    Each loads `file`, optionally slices [start,end] seconds, pitches by
    `pitch` semitones, reverses, fades (ms), and places it at `at_beat`.
    Renders a normalized 16-bit WAV to out_path. Returns {path, duration, events}."""
    import os
    # pre-flight: fail cleanly on any missing file BEFORE rendering anything
    missing = sorted({os.path.expanduser(ev["file"]) for ev in events
                      if not os.path.isfile(os.path.expanduser(ev["file"]))})
    if missing:
        raise ValueError("missing sample file(s): %s" % ", ".join(missing))
    cache = {}
    arr = Arrangement(tempo=tempo)
    for ev in events:
        path = os.path.expanduser(ev["file"])
        if path not in cache:
            cache[path] = Clip.load(path)
        c = cache[path]
        if ev.get("normalize"):
            c = c.normalize()
        if "start" in ev or "end" in ev:
            c = c.slice(ev.get("start", 0), ev.get("end"))
        semis = ev.get("pitch", 0)
        if ev.get("pitch_to") and ev.get("from_note"):
            from rubin.midi import note_to_midi
            semis = note_to_midi(ev["pitch_to"]) - note_to_midi(ev["from_note"])
        if semis:
            c = c.pitch(semis)
        if ev.get("reverse"):
            c = c.reverse()
        if ev.get("fade_in") or ev.get("fade_out"):
            c = c.fade(ev.get("fade_in", 0), ev.get("fade_out", 0))
        if ev.get("pan"):
            c = c.pan(ev["pan"])
        gain = ev.get("gain", 1.0)
        if rpt := ev.get("repeat"):
            times = int(rpt.get("times", 1))
            every = float(rpt.get("every", 1))
            for k in range(times):
                arr.add(c, at_beat=ev.get("at_beat", 0) + k * every, gain=gain)
        else:
            arr.add(c, at_beat=ev.get("at_beat", 0), gain=gain)
    out_path = os.path.expanduser(out_path or "~/Desktop/cut_arrangement.wav")
    dur = write_wav(out_path, arr.render(), limit=limit)
    return {"path": out_path, "duration": round(dur, 2), "events": len(events)}


def demo(out_path=None):
    """Render a short from-scratch cut without external files: a synthesized
    sub + kick + noise-snare in a half-time dubstep pattern. Exercises the
    whole engine (synthesis -> slice/fade -> arrange -> mix -> write)."""
    import os, tempfile
    d = tempfile.mkdtemp()
    kick = Clip.tone(55, 0.18).fade(1, 120)
    sub = Clip.tone(41.2, 0.5).fade(3, 60)          # E1 sub
    snare = Clip.noise(0.2).fade(1, 90)
    kp = os.path.join(d, "kick.wav"); write_wav(kp, kick)
    sp = os.path.join(d, "sub.wav"); write_wav(sp, sub)
    np_ = os.path.join(d, "snare.wav"); write_wav(np_, snare)
    events = []
    for bar in range(8):
        b = bar * 4
        events.append({"file": sp, "at_beat": b, "gain": 0.9})
        events.append({"file": kp, "at_beat": b, "gain": 1.0})
        events.append({"file": kp, "at_beat": b + 1.5, "gain": 0.85})
        events.append({"file": np_, "at_beat": b + 2, "gain": 0.8})
    out_path = os.path.expanduser(out_path or "~/Desktop/cut_demo.wav")
    return cut_arrange(events, tempo=140, out_path=out_path, limit=True)


# GM drum note -> (synth kind, freq). Rough but recognizable.
_DRUM_MAP = {
    36: ("tone", 55), 35: ("tone", 50),           # kicks
    38: ("noise", None), 40: ("noise", None),      # snares
    39: ("noise", None),                           # clap
    42: ("noise_hi", None), 44: ("noise_hi", None),
    46: ("noise_hi", None), 49: ("noise_hi", None), 51: ("noise_hi", None),
}


def _note_clip(pitch, dur_s, vel, rate, drum=False):
    amp = max(0.05, min(1.0, vel / 127.0)) * 0.5
    if drum:
        kind, freq = _DRUM_MAP.get(pitch, ("noise_hi", None))
        if kind == "tone":
            return Clip.tone(freq, min(dur_s, 0.22), rate, amp=amp).fade(1, 120)
        hp = min(dur_s, 0.18 if kind == "noise" else 0.06)
        return Clip.noise(hp, rate, amp=amp * (1.0 if kind == "noise" else 0.6)).fade(1, 40)
    freq = 440.0 * (2 ** ((pitch - 69) / 12.0))
    rel = min(int(dur_s * 1000), 90)
    return Clip.tone(freq, dur_s, rate, amp=amp).fade(4, rel)


def render_midi(midi_path, out_path=None, rate=44100):
    """Bounce a .mid to audio with rubin's built-in synth: sine voices for
    pitched tracks, noise/tone hits for channel-9 drums. No DAW or plugin —
    lets a composed MIDI arrangement be mixed with sample cuts."""
    import os
    from rubin import midi_read
    with open(os.path.expanduser(midi_path), "rb") as f:
        ppq, tempo, tracks = midi_read.parse_smf(f.read())
    arr = Arrangement(tempo=tempo, rate=rate)
    for t in tracks:
        drum = t.get("channel") == 9
        for start, dur, pitch, vel in t["notes"]:
            dur_s = arr.beat_to_s(dur)
            if dur_s < 0.02:
                dur_s = 0.02
            arr.add(_note_clip(pitch, dur_s, vel, rate, drum), at_beat=start)
    out_path = os.path.expanduser(out_path or "~/Desktop/midi_render.wav")
    dur = write_wav(out_path, arr.render(), limit=True)
    return {"path": out_path, "duration": round(dur, 2),
            "voices": sum(len(t["notes"]) for t in tracks), "tempo": tempo}


def analyze_audio(path, window_s=0.5):
    """RMS energy contour of a WAV so a session can 'see' its dynamics without
    hearing it — the audio analog of analyze_midi's density curve. Returns
    duration, peak, overall RMS, and per-window [{t, rms}] energy over time."""
    import os, math
    c = Clip.load(os.path.expanduser(path))
    n = c.frames
    win = max(1, int(window_s * c.rate))
    contour = []
    peak = 0.0
    sq_total = 0.0
    for w0 in range(0, n, win):
        s = 0.0
        cnt = 0
        for f in range(w0, min(w0 + win, n)):
            for ch in range(c.ch):
                v = c.s[f * c.ch + ch]
                s += v * v
                a = v if v >= 0 else -v
                if a > peak:
                    peak = a
                cnt += 1
        if cnt:
            sq_total += s
            contour.append({"t": round(w0 / c.rate, 2),
                            "rms": round(math.sqrt(s / cnt), 4)})
    total_samples = n * c.ch
    overall = round(math.sqrt(sq_total / total_samples), 4) if total_samples else 0.0
    return {"duration": round(c.duration, 2), "peak": round(peak, 4),
            "rms": overall, "contour": contour}
