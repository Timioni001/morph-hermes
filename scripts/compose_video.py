"""Turn the recorded session into a finished MP4.

Captions are burned in from the timings the recorder wrote while it ran, so they
describe what is actually on screen at that moment. Nothing else is altered: no
cuts that change the order of events, no re-staged frames.

    python scripts/compose_video.py --input .media/raw.webm \
        --captions .media/captions.json --output .media/morph-demo.mp4

Caption text is passed to ffmpeg through files rather than inline, because
ffmpeg's filter-string quoting cannot carry an apostrophe and silently
mis-parses the rest of the chain when it meets one.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/inter/Inter-SemiBold.otf",
    "/usr/share/fonts/opentype/inter/Inter-Medium.otf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]
MAX_SECONDS = 90.0
MIN_CAPTION_SECONDS = 0.8


def pick_font() -> str | None:
    for candidate in FONT_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return None


def build_filter(captions: list[dict], font: str | None, width: int, height: int,
                 scratch: Path) -> str:
    parts = [
        f"scale={width}:{height}:force_original_aspect_ratio=decrease",
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:white",
        "fps=30",
        "format=yuv420p",
    ]
    if font:
        for index, caption in enumerate(captions):
            start, end = float(caption["start"]), float(caption["end"])
            if end - start < MIN_CAPTION_SECONDS:
                continue
            path = scratch / f"caption-{index:02d}.txt"
            path.write_text(caption["text"], encoding="utf-8")
            window = f"between(t\\,{start:.2f}\\,{end:.2f})"
            parts.append(f"drawbox=x=0:y=ih-104:w=iw:h=104:"
                         f"color=0x0B1220@0.88:t=fill:enable='{window}'")
            parts.append(f"drawtext=fontfile={font}:textfile={path}:reload=0:"
                         f"fontcolor=white:fontsize=30:x=(w-text_w)/2:y=h-67:"
                         f"enable='{window}'")
    parts.append("fade=t=in:st=0:d=0.6")
    return ",".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--captions", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    if shutil.which("ffmpeg") is None:
        print("ffmpeg is required", file=sys.stderr)
        return 1

    meta = json.loads(Path(args.captions).read_text(encoding="utf-8"))
    captions = meta["captions"]
    width, height = meta.get("width", 1600), meta.get("height", 900)
    duration = captions[-1]["end"] if captions else 0.0
    if duration > MAX_SECONDS:
        print(f"the recording is {duration:.0f}s, over the {MAX_SECONDS:.0f}s budget; trimming",
              file=sys.stderr)

    font = pick_font()
    if font is None:
        print("no usable font found; the video will have no captions", file=sys.stderr)

    with tempfile.TemporaryDirectory(prefix="morph-captions-") as scratch:
        graph = build_filter(captions, font, width, height, Path(scratch))
        script = Path(scratch) / "filter.txt"
        script.write_text(graph, encoding="utf-8")
        voice = Path(args.captions).parent / "voice"
        clips = []
        for caption in captions:
            name = hashlib.sha1(caption["text"].encode("utf-8")).hexdigest()[:12] + ".wav"
            if (voice / name).is_file():
                clips.append((voice / name, float(caption["start"]) + 0.2))
        command = ["ffmpeg", "-y", "-loglevel", "error", "-i", args.input]
        for clip, _start in clips:
            command += ["-i", str(clip)]
        command += ["-t", f"{min(duration + 0.6, MAX_SECONDS):.2f}", "-filter_script:v", str(script)]
        if clips:
            # Each line starts with its caption; one loudness pass over the mix.
            chain = "".join(f"[{i + 1}:a]adelay={int(start * 1000)}:all=1[a{i}];"
                            for i, (_clip, start) in enumerate(clips))
            chain += "".join(f"[a{i}]" for i in range(len(clips)))
            chain += (f"amix=inputs={len(clips)}:normalize=0,"
                      "highpass=f=70,loudnorm=I=-16:TP=-1.5:LRA=11,apad[aout]")
            command += ["-filter_complex", chain, "-map", "0:v", "-map", "[aout]",
                        "-c:a", "aac", "-b:a", "160k", "-ar", "48000"]
        else:
            command += ["-an"]
        command += ["-c:v", "libx264", "-preset", "slow", "-crf", "20",
                    "-pix_fmt", "yuv420p", "-movflags", "+faststart", args.output]
        subprocess.run(command, check=True)

    size = Path(args.output).stat().st_size
    print(f"{args.output}: {size / 1_000_000:.1f} MB, {min(duration, MAX_SECONDS):.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
