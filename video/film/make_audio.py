"""Narration (Kokoro, open source, local) and the music bed (synthesised here)."""
import json, sys
import numpy as np
import soundfile as sf
from kokoro_onnx import Kokoro

root = sys.argv[1]          # video/film
models = sys.argv[2]        # directory holding the Kokoro model files
SR = 48000

kokoro = Kokoro(f"{models}/kokoro-v1.0.onnx", f"{models}/voices-v1.0.bin")
lines = json.load(open(f"{root}/narration.json"))
for line in lines:
    samples, rate = kokoro.create(line["text"], voice="af_heart", speed=1.0, lang="en-us")
    sf.write(f"{root}/public/{line['id']}.wav", samples, rate)
    line["dur"] = round(len(samples) / rate, 2)
    print(line["id"], line["at"], line["dur"], round(line["at"] + line["dur"], 1))
json.dump(lines, open(f"{root}/narration.json", "w"), indent=1)

# ── music: slow warm chords, a quiet pulse, a lift under the approvals ──────
T = 85.5
t = np.arange(int(T * SR)) / SR
note = lambda n: 440.0 * 2 ** ((n - 69) / 12)
chords = [[53, 57, 60, 64], [57, 60, 64, 67], [48, 55, 59, 64], [55, 59, 62, 64]]   # Fmaj7 Am7 Cmaj7 G6
bar = T / 8
left = np.zeros_like(t); right = np.zeros_like(t)
for i in range(8):
    start = i * bar
    env = np.clip((t - start) / 2.5, 0, 1) * np.clip((start + bar + 2.5 - t) / 2.5, 0, 1)
    env = np.where((t >= start) & (t <= start + bar + 2.5), env, 0) ** 1.5
    for n in chords[i % 4]:
        f = note(n)
        left += env * (np.sin(2 * np.pi * f * 0.998 * t) + 0.35 * np.sin(2 * np.pi * 2 * f * t)) * 0.11
        right += env * (np.sin(2 * np.pi * f * 1.002 * t) + 0.35 * np.sin(2 * np.pi * 2 * f * 1.001 * t)) * 0.11
    bass = note(chords[i % 4][0] - 12)
    left += env * np.sin(2 * np.pi * bass * t) * 0.16
    right += env * np.sin(2 * np.pi * bass * t) * 0.16
# a soft arpeggio from 8s, a little brighter from 49.5s to 61s
step = 60 / 96 / 2
k = 0
while 8 + k * step < 79:
    at = 8 + k * step
    chord = chords[int(at // bar) % 4]
    f = note(chord[[0, 2, 1, 3, 2, 1][k % 6]] + 12)
    lift = 1.9 if 49.5 <= at <= 61 else 1.0
    i0 = int(at * SR); n = int(0.9 * SR)
    seg = np.arange(n) / SR
    pluck = np.sin(2 * np.pi * f * seg) * np.exp(-seg * 6) * 0.045 * lift
    pan = 0.5 + 0.35 * np.sin(k * 0.9)
    left[i0:i0 + n] += pluck[: len(left[i0:i0 + n])] * (1 - pan)
    right[i0:i0 + n] += pluck[: len(right[i0:i0 + n])] * pan
    k += 1
fade = np.clip(t / 2.0, 0, 1) * np.clip((T - t) / 3.5, 0, 1)
mix = np.stack([left, right], axis=1) * fade[:, None]
mix /= np.max(np.abs(mix)) * 1.4
sf.write(f"{root}/public/music.wav", mix, SR)
print("music ok")
