"""Royalty-free background music: synthesized at runtime with numpy (no third-party rights at all).

Mood-dependent ambient pad + soft pulse. Output 48 kHz stereo WAV. Deterministic per seed.
"""
import numpy as np, soundfile as sf

SR = 48000
PROG = {  # chord roots (semitones from A2) and qualities
    "green": [(0, "maj"), (7, "maj"), (9, "min"), (5, "maj")],
    "neutral": [(0, "min7"), (5, "min7"), (3, "maj7"), (10, "maj")],
    "red": [(0, "min"), (8, "maj"), (3, "maj"), (10, "maj")],
}
Q = {"maj": [0, 4, 7, 12], "min": [0, 3, 7, 12], "min7": [0, 3, 7, 10], "maj7": [0, 4, 7, 11]}


def _lp(x, a):
    y = np.empty_like(x); acc = 0.0
    # one-pole low-pass, vectorised in blocks via cumulative filter approximation
    from scipy.signal import lfilter  # noqa
    return lfilter([1 - a], [1, -a], x)


def make_music(seconds, mood, out_wav, seed=0, bpm=None):
    rng = np.random.default_rng(seed)
    bpm = bpm or {"green": 96, "neutral": 88, "red": 76}[mood]
    n = int(seconds * SR); t = np.arange(n) / SR
    bar = 4 * 60 / bpm
    base = 110.0 * 2 ** (rng.integers(-2, 3) / 12)
    pad = np.zeros(n)
    for i in range(int(seconds / bar) + 2):
        root, q = PROG[mood][i % 4]
        s0, s1 = int(i * bar * SR), int(min(n, (i + 1) * bar * SR + 0.3 * SR))
        if s0 >= n: break
        tt = t[s0:s1] - t[s0]
        env = np.minimum(1, tt / 0.6) * np.exp(-tt / (bar * 1.6))
        for iv in Q[q]:
            f = base * 2 ** ((root + iv) / 12)
            for det in (-0.12, 0.12):
                pad[s0:s1] += env * (np.sin(2 * np.pi * f * (1 + det / 100) * tt) + 0.25 * np.sin(4 * np.pi * f * tt)) / 8
    # soft pulse: low "kick" on each beat + filtered noise tick on off-beats
    beat = 60 / bpm
    pulse = np.zeros(n)
    for k in range(int(seconds / beat) + 1):
        s0 = int(k * beat * SR); L = int(0.25 * SR)
        if s0 >= n: break
        tt = np.arange(min(L, n - s0)) / SR
        pulse[s0:s0 + len(tt)] += 0.5 * np.sin(2 * np.pi * (55 + 60 * np.exp(-tt * 30)) * tt) * np.exp(-tt * 14)
        s1 = int((k + .5) * beat * SR)
        if s1 < n:
            L2 = min(int(0.05 * SR), n - s1)
            pulse[s1:s1 + L2] += 0.05 * rng.standard_normal(L2) * np.exp(-np.arange(L2) / SR * 80)
    try:
        pad = _lp(pad, 0.92)
    except Exception:
        pass
    mix = 0.7 * pad + (0.35 if mood != "red" else 0.2) * pulse
    fade = np.minimum(1, np.minimum(t / 1.0, (seconds - t) / 1.5))
    mix *= np.clip(fade, 0, 1)
    mix /= max(1e-6, np.abs(mix).max()) / 0.8
    st = np.stack([mix, np.roll(mix, int(0.012 * SR))], axis=1)  # cheap stereo width
    sf.write(out_wav, st.astype(np.float32), SR)
    return out_wav
