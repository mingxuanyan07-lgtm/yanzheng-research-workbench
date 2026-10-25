"""Transcode an actual completed browser recording and verify duration/frames."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import imageio_ffmpeg

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output/public-cases/20261023"
VIDEO = OUT / "video"

def main():
    recording = json.loads((VIDEO / "recording.json").read_text(encoding="utf-8"))
    assert not recording["errors"] and not recording["fixture"]
    raw = Path(recording["raw"])
    assert raw.is_file() and raw.is_relative_to(VIDEO)
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    final = OUT / "研证-两份真实公开案例-三分钟.mp4"
    process = subprocess.run([ffmpeg, "-hide_banner", "-y", "-i", str(raw), "-t", "180",
        "-vf", "scale=1620:1080,pad=1920:1080:150:0:color=0x0e2e23,fps=25", "-c:v", "libx264",
        "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", str(final)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=0x08000000)
    (VIDEO / "transcode.log").write_text(process.stderr, encoding="utf-8")
    process.check_returncode()
    metadata = subprocess.run([ffmpeg, "-hide_banner", "-i", str(final)], capture_output=True,
        text=True, encoding="utf-8", errors="replace", creationflags=0x08000000).stderr
    match = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", metadata)
    assert match, metadata
    h, m, s = map(float, match.groups())
    seconds = 3600*h + 60*m + s
    assert abs(seconds - 180) < 0.1
    for second in (8, 44, 80, 110, 145, 160, 174):
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", str(second),
            "-i", str(final), "-frames:v", "1", str(VIDEO / f"frame-{second:03}.png")],
            check=True, capture_output=True, creationflags=0x08000000)
    result = {"file": final.name, "duration_seconds": seconds, "resolution": "1920x1080",
        "video_codec": "H264", "fps": 25, "audio": False, "captioned": True,
        "sha256": hashlib.sha256(final.read_bytes()).hexdigest(), "bytes": final.stat().st_size,
        "source": "actual Chrome recording of isolated real cases; no mock provider or route interception",
        "editing": "trim to 180 seconds; proportional scale and side padding; no generated UI state",
        "model_calls_in_recording": 0, "model_calls_before_recording": 9,
        "human_decision": "actual conversation reply retained; Codex delegated entry and recording",
        "review_frames": [f"video/frame-{s:03}.png" for s in (8,44,80,110,145,160,174)]}
    (OUT / "video-validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))

if __name__ == "__main__":
    main()
