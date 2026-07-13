"""Zero-dependency WAV slicer / arranger.

The audio counterpart to midi.py: decode PCM WAV (16/24/32-bit), cut and
transform clips, and arrange them on a tempo grid into a rendered mix. Pure
stdlib — samples are kept as interleaved floats in an `array('f')`.

This is how rubin "cuts samples to make a song": deterministic file edits,
no DAW UI. Output is a standard 16-bit WAV any DAW imports.
"""

import array
import struct
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
        out = array.array("f", bytes(4 * ch * nf))
        n = ch * nf
        if sw == 2:
            ints = struct.unpack("<%dh" % n, raw)
            scale = 1.0 / 32768.0
            for i in range(n):
                out[i] = ints[i] * scale
        elif sw == 3:
            scale = 1.0 / 8388608.0
            for i in range(n):
                b0, b1, b2 = raw[3 * i], raw[3 * i + 1], raw[3 * i + 2]
                v = b0 | (b1 << 8) | (b2 << 16)
                if v & 0x800000:
                    v -= 0x1000000
                out[i] = v * scale
        elif sw == 4:
            ints = struct.unpack("<%di" % n, raw)
            scale = 1.0 / 2147483648.0
            for i in range(n):
                out[i] = ints[i] * scale
        else:
            raise ValueError("unsupported sample width %d bytes" % sw)
        return Clip(out, rate, ch)

    @staticmethod
    def silence(seconds, rate=44100, channels=2):
        return Clip(array.array("f", bytes(4 * channels * int(seconds * rate))),
                    rate, channels)

    def copy(self):
        return Clip(array.array("f", self.s), self.rate, self.ch)

    # ---- edits (return new clips; originals untouched) ----------------
    def slice(self, start_s, end_s=None):
        a = max(0, int(start_s * self.rate)) * self.ch
        b = (self.frames if end_s is None else int(end_s * self.rate)) * self.ch
        b = min(b, len(self.s))
        return Clip(self.s[a:b], self.rate, self.ch)

    def gain(self, factor):
        return Clip(array.array("f", [x * factor for x in self.s]), self.rate, self.ch)

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


def write_wav(path, clip, peak=0.89):
    """Write a clip to 16-bit WAV, normalizing so the peak sits at `peak`."""
    s = clip.s
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


def cut_arrange(events, tempo=140.0, out_path=None):
    """Build a song from sample slices. `events` is a list of dicts:
      {file, at_beat, start?, end?, pitch?, gain?, reverse?, fade_in?, fade_out?}
    Each loads `file`, optionally slices [start,end] seconds, pitches by
    `pitch` semitones, reverses, fades (ms), and places it at `at_beat`.
    Renders a normalized 16-bit WAV to out_path. Returns {path, duration, events}."""
    import os
    cache = {}
    arr = Arrangement(tempo=tempo)
    for ev in events:
        path = os.path.expanduser(ev["file"])
        if path not in cache:
            cache[path] = Clip.load(path)
        c = cache[path]
        if "start" in ev or "end" in ev:
            c = c.slice(ev.get("start", 0), ev.get("end"))
        if ev.get("pitch"):
            c = c.pitch(ev["pitch"])
        if ev.get("reverse"):
            c = c.reverse()
        if ev.get("fade_in") or ev.get("fade_out"):
            c = c.fade(ev.get("fade_in", 0), ev.get("fade_out", 0))
        arr.add(c, at_beat=ev.get("at_beat", 0), gain=ev.get("gain", 1.0))
    out_path = os.path.expanduser(out_path or "~/Desktop/cut_arrangement.wav")
    dur = write_wav(out_path, arr.render())
    return {"path": out_path, "duration": round(dur, 2), "events": len(events)}
