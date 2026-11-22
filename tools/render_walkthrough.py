"""Rebuild the AI-narrated walkthrough. Install Pillow and edge-tts in a separate venv.

python tools/render_walkthrough.py --tts
Requires ffmpeg and ffprobe on PATH. TTS uses Microsoft's network service;
only the public walkthrough text is sent. This is not a personal voice recording.
"""
from __future__ import annotations

import argparse
import subprocess
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
SLIDES = [
    ("01 / THE ASSESSMENT", "A small AI platform.\nClear operational boundaries.", ["FastAPI: login / chat / health / metrics", "JWT + RBAC · Redis · PostgreSQL", "Docker + Kubernetes deployment examples", "Implemented features ≠ a live 500 RPS claim"]),
    ("02 / REQUEST FLOW", "Identity before inference.", ["Login → signed, expiring access token", "JWT validation → role check → Redis admission", "Logical LLM gateway → provider → response", "Persist metadata, not raw conversations", "Admin metrics · user chat · read-only denied"]),
    ("03 / RELIABILITY", "Bound the work.\nObserve the outcome.", ["Async HTTP + total deadline + bounded retries", "Optional model / endpoint fallback", "Shared rate limits + expiring concurrency leases", "Redis outage: fail closed", "Latency · usage · errors · correlation IDs"]),
    ("04 / DEPLOYMENT & SCALE", "API capacity is not\nprovider capacity.", ["Nginx → stateless API replicas → managed state", "Migration job before rollout; readiness probes", "100 RPS × 5 seconds = 500 in-flight calls", "500 RPS × 5 seconds = 2,500 in-flight calls", "Plan RPM / TPM / concurrency / spend budgets"]),
    ("05 / MIGRATION", "Move state first.\nShift traffic gradually.", ["Measure baseline; define recovery objectives", "Managed PostgreSQL + Redis; compatible schema", "Private validation → canary → gradual cutover", "Rollback needs data compatibility", "OIDC for identity; ECS or K8s for operations"]),
    ("06 / VERIFICATION", "Evidence, not promises.", ["Source + tests + configuration + runbooks", "Verification report separates tested / untested", "Cache opt-in; per-user isolation", "Durable job queue is a documented extension", "Next: provider checks, quotas, restore + load tests"]),
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tts", action="store_true")
    args = parser.parse_args()
    sections = (DOCS / "walkthrough.md").read_text().split("## Slide ")[1:]
    narration = [section.split("\n\n", 1)[1].strip() for section in sections]
    (DOCS / "narration.txt").write_text("\n\n".join(narration) + "\n")
    audio = DOCS / "walkthrough-audio.mp3"
    if args.tts:
        subprocess.run(["edge-tts", "--voice", "en-US-GuyNeural", "--file", str(DOCS / "narration.txt"), "--write-media", str(audio)], check=True)
    duration = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(audio)], text=True).strip())
    if duration > 299:
        raise SystemExit(f"Narration is {duration:.1f}s: shorten to below five minutes")
    out = DOCS / "slides"
    out.mkdir(exist_ok=True)
    words = sum(len(n.split()) for n in narration)
    timing = []
    for index, ((eyebrow, title, bullets), spoken) in enumerate(zip(SLIDES, narration, strict=True), 1):
        image = Image.new("RGB", (1600, 900), "#091322")
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 16, 900), fill="#4bd5c5")
        draw.text((85, 65), "AI / QA PLATFORM", font=ImageFont.truetype(BOLD, 25), fill="#4bd5c5")
        draw.text((85, 117), eyebrow, font=ImageFont.truetype(FONT, 22), fill="#aebfd3")
        draw.multiline_text((85, 182), title, font=ImageFont.truetype(BOLD, 57), fill="#edf5ff", spacing=13)
        y = 380
        for bullet in bullets:
            draw.ellipse((91, y + 10, 101, y + 20), fill="#4bd5c5")
            for line in textwrap.wrap(bullet, width=77):
                draw.text((125, y), line, font=ImageFont.truetype(FONT, 29), fill="#cbd9ea")
                y += 45
            y += 12
        draw.line((85, 807, 1515, 807), fill="#2a3e56", width=2)
        draw.text((85, 833), "AI-narrated walkthrough | Source and verification details in the repository", font=ImageFont.truetype(FONT, 19), fill="#8ba9c6")
        draw.text((1430, 833), f"{index} / 6", font=ImageFont.truetype(FONT, 19), fill="#8ba9c6")
        slide = out / f"{index:02}.png"
        image.save(slide)
        timing.extend([f"file '{slide.as_posix()}'", f"duration {duration * len(spoken.split()) / words:.6f}"])
    timing.append(f"file '{slide.as_posix()}'")
    manifest = out / "timeline.ffconcat"
    manifest.write_text("\n".join(timing) + "\n")
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning", "-f", "concat", "-safe", "0", "-i", str(manifest), "-i", str(audio), "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage", "-r", "12", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-t", f"{duration:.3f}", "-movflags", "+faststart", str(DOCS / "walkthrough.mp4")], check=True)
    print(f"Rendered {duration:.1f}s walkthrough: {DOCS / 'walkthrough.mp4'}")


if __name__ == "__main__":
    main()
