"""Weeknd-style demo beat: 8 bars, 85 BPM, A minor (Am - F - C - E).

Dark half-time R&B: kick + clap/snare, syncopated hats with 16th fills,
sub-ish synth bass, warm pad chords, and a sparse pluck arp on top.
"""

# (label, bass_root, pad_voicing, arp_notes) — each chord lasts 2 bars
PROG = [
    ("Am", 33, [57, 60, 64, 69], [69, 72, 76, 81]),
    ("F", 29, [53, 57, 60, 65], [65, 69, 72, 77]),
    ("C", 36, [52, 55, 60, 64], [67, 72, 76, 79]),
    ("E", 28, [52, 56, 59, 64], [64, 68, 71, 76]),
]

KICK, SNARE, CLAP, CHAT, OHAT = 36, 38, 39, 42, 46


def weeknd_beat():
    drums, bass, pad, arp = [], [], [], []

    for bar in range(8):
        t = bar * 4.0

        # Kick: half-time anchor, extra pickup on even-numbered bars
        drums.append((t + 0.0, 0.4, KICK, 112))
        drums.append((t + 1.5, 0.4, KICK, 104))
        if bar % 2 == 1:
            drums.append((t + 3.25, 0.4, KICK, 98))

        # Snare + clap layered on beat 3 (half-time backbeat)
        drums.append((t + 2.0, 0.4, SNARE, 106))
        drums.append((t + 2.0, 0.4, CLAP, 92))

        # Hats: 8ths, accented on downbeats; 16th fill into bars 4 and 8
        open_hat_bar = bar in (1, 5)
        for i in range(8):
            pos = i * 0.5
            if open_hat_bar and pos == 3.5:
                continue  # open hat replaces this one
            vel = 88 if i % 2 == 0 else 56
            drums.append((t + pos, 0.2, CHAT, vel))
        if open_hat_bar:
            drums.append((t + 3.5, 0.6, OHAT, 76))
        if bar in (3, 7):
            drums.append((t + 3.25, 0.12, CHAT, 66))
            drums.append((t + 3.75, 0.12, CHAT, 70))

    for block, (_label, root, voicing, arp_notes) in enumerate(PROG):
        b = block * 8.0  # 2 bars per chord

        # Bass: long root, syncopated pickups, octave flick
        bass += [
            (b + 0.0, 2.75, root, 102),
            (b + 3.0, 0.5, root, 88),
            (b + 3.5, 0.5, root + 12, 84),
            (b + 4.0, 1.5, root, 100),
            (b + 6.0, 0.5, root, 86),
            (b + 6.5, 1.5, root, 96),
        ]

        # Pad: sustained chord across the 2 bars
        for pitch in voicing:
            pad.append((b, 7.75, pitch, 68))

        # Arp: 8th-note up-down cycle, quiet, slight downbeat accent
        cycle = [0, 1, 2, 3, 2, 1]
        for i in range(16):
            pitch = arp_notes[cycle[i % 6]]
            vel = 62 if i % 4 == 0 else 54
            arp.append((b + i * 0.5, 0.45, pitch, vel))

    return {
        "tempo": 85,
        "name": "weeknd_beat",
        "tracks": [
            {"name": "Drums", "channel": 9, "notes": drums},
            {"name": "Synth Bass", "channel": 0, "program": 38, "notes": bass},
            {"name": "Warm Pad", "channel": 1, "program": 89, "notes": pad},
            {"name": "Pluck Arp", "channel": 2, "program": 81, "notes": arp},
        ],
    }
