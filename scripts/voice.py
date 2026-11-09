"""Synthesise the demo narration locally with Piper (free, offline once downloaded).

    python scripts/voice.py --model .media/voice/en_US-ryan-high.onnx

Reads the lines from scripts/record_demo.cjs, writes one WAV per line plus
durations.json, which the recorder uses to hold each caption for as long as it
takes to say.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LINE = re.compile(r'^  await say\((".*")\);$', re.M)


def clip_name(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12] + ".wav"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--out", default=str(ROOT / ".media" / "voice"))
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    source = (ROOT / "scripts" / "record_demo.cjs").read_text(encoding="utf-8")
    durations: dict[str, float] = {}
    for raw in LINE.findall(source):
        text = json.loads(raw)
        target = out / clip_name(text)
        subprocess.run([sys.executable, "-m", "piper", "-m", args.model, "-f", str(target),
                        "--sentence-silence", "0.25"],
                       input=text.replace("MORPH", "Morph").encode("utf-8"), check=True)
        with wave.open(str(target)) as clip:
            durations[text] = round(clip.getnframes() / clip.getframerate(), 2)
        print(f"{durations[text]:5.2f}s  {text}")
    (out / "durations.json").write_text(json.dumps(durations, indent=2), encoding="utf-8")
    print(f"total {sum(durations.values()):.1f}s of speech")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
