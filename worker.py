import os
import sys
import subprocess
import textwrap
import requests
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

API_URL = os.environ["J360_API_URL"].strip()
API_SECRET = os.environ["J360_API_SECRET"].strip()
FB_PAGE_ID = os.environ["FB_PAGE_ID"].strip()
FB_PAGE_ACCESS_TOKEN = os.environ["FB_PAGE_ACCESS_TOKEN"].strip()
FB_GRAPH_VERSION = os.environ.get("FB_GRAPH_VERSION", "v23.0").strip()
LOGO_URL = os.environ["J360_LOGO_URL"].strip()

WORK_DIR = Path("work")
WORK_DIR.mkdir(exist_ok=True)
TIMEOUT = 900
MAX_VIDEO_SECONDS = 60


def api_request(action, payload=None):
    """claim uses JSON; complete/fail are sent as form fields for the PHP API."""
    payload = payload or {}
    headers = {"X-J360-Secret": API_SECRET}
    if action == "claim":
        response = requests.post(
            API_URL, json={"action": action, **payload},
            headers=headers, timeout=60
        )
    else:
        response = requests.post(
            API_URL, data={"action": action, **payload},
            headers=headers, timeout=60
        )
    response.raise_for_status()
    try:
        result = response.json()
    except ValueError:
        raise RuntimeError(f"API returned non-JSON response: {response.text[:500]}")
    if result.get("success") is False or result.get("error"):
        raise RuntimeError(str(result.get("error") or result))
    return result


def download_file(url, target, max_bytes=None):
    if not url or not url.startswith(("https://", "http://")):
        raise RuntimeError("A valid public HTTP/HTTPS download URL is missing")
    total = 0
    with requests.get(url, stream=True, timeout=(30, 180)) as response:
        response.raise_for_status()
        with open(target, "wb") as output:
            for chunk in response.iter_content(1024 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if max_bytes and total > max_bytes:
                    raise RuntimeError("Downloaded file exceeds the allowed size")
                output.write(chunk)
    if not target.exists() or target.stat().st_size == 0:
        raise RuntimeError("Downloaded file is empty")


def find_bengali_font():
    candidates = [
        "/usr/share/fonts/truetype/noto/NotoSansBengali-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansBengaliUI-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSerifBengali-Regular.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return path
    # Fallback search for Noto Bengali fonts installed by the workflow.
    matches = list(Path("/usr/share/fonts").rglob("*Bengali*Regular*.ttf"))
    if matches:
        return str(matches[0])
    raise RuntimeError("Bengali font not found. Install fonts-noto-core on the GitHub runner.")


def load_font(path, size):
    # RAQM is required for correct Bengali shaping/ligatures.
    try:
        return ImageFont.truetype(path, size, layout_engine=ImageFont.Layout.RAQM)
    except Exception as exc:
        raise RuntimeError(
            "Pillow/RAQM Bengali text shaping is unavailable on the runner."
        ) from exc


def wrap_bengali(draw, text, font, max_width):
    words = str(text or "").strip().split()
    if not words:
        return ["সর্বশেষ সংবাদ"]
    lines, current = [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def make_overlay(title, logo_path, overlay_path):
    canvas = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    # Header panel and footer strip.
    draw.rectangle((0, 0, 1080, 365), fill=(10, 22, 31, 225))
    draw.rectangle((0, 1780, 1080, 1920), fill=(8, 116, 72, 245))

    # Logo, kept within the top-left corner.
    logo = Image.open(logo_path).convert("RGBA")
    logo.thumbnail((220, 125), Image.Resampling.LANCZOS)
    canvas.alpha_composite(logo, (42, 30))

    font_path = find_bengali_font()
    title_font = load_font(font_path, 58)
    small_font = load_font(font_path, 36)

    headline = str(title or "").strip() or "সর্বশেষ সংবাদ"
    lines = wrap_bengali(draw, headline, title_font, 990)
    # Limit title height while preserving readable Bengali glyphs.
    if len(lines) > 3:
        lines = lines[:3]
        last = lines[-1]
        while last and draw.textbbox((0, 0), last + "…", font=title_font)[2] > 990:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"

    y = 185
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=title_font)
        line_h = bbox[3] - bbox[1]
        draw.text(((1080 - (bbox[2] - bbox[0])) / 2, y), line,
                  font=title_font, fill=(255, 255, 255, 255), stroke_width=1,
                  stroke_fill=(0, 0, 0, 150))
        y += line_h + 14

    footer = "জনকথা 360  |  সত্যের পক্ষে"
    bbox = draw.textbbox((0, 0), footer, font=small_font)
    draw.text(((1080 - (bbox[2] - bbox[0])) / 2, 1820),
              footer, font=small_font, fill=(255, 255, 255, 255))

    canvas.save(overlay_path)


def render_video(source, output, title, logo):
    overlay = WORK_DIR / "overlay.png"
    make_overlay(title, logo, overlay)

    # Blurred full-screen background + centered full-frame original footage.
    filter_graph = (
        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,boxblur=24:8[bg];"
        "[0:v]scale=1080:1450:force_original_aspect_ratio=decrease[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2[base];"
        "[base][1:v]overlay=0:0:format=auto[v]"
    )
    command = [
        "ffmpeg", "-hide_banner", "-y",
        "-i", str(source), "-i", str(overlay),
        "-filter_complex", filter_graph,
        "-map", "[v]", "-map", "0:a?",
        "-t", str(MAX_VIDEO_SECONDS),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart", str(output)
    ]
    subprocess.run(command, check=True)
    if not output.exists() or output.stat().st_size < 10000:
        raise RuntimeError("FFmpeg did not create a valid output video")


def facebook_upload(video_path, title, description):
    url = f"https://graph.facebook.com/{FB_GRAPH_VERSION}/{FB_PAGE_ID}/videos"
    data = {
        "access_token": FB_PAGE_ACCESS_TOKEN,
        "title": str(title or "জনকথা 360"),
        "description": str(description or title or "জনকথা 360"),
    }
    with open(video_path, "rb") as video_file:
        response = requests.post(
            url, data=data,
            files={"source": ("jonokotha360.mp4", video_file, "video/mp4")},
            timeout=TIMEOUT
        )
    try:
        result = response.json()
    except ValueError:
        raise RuntimeError(f"Facebook returned non-JSON response: {response.text[:500]}")
    if not response.ok or result.get("error"):
        raise RuntimeError(f"Facebook upload failed: {result}")
    video_id = result.get("id")
    if not video_id:
        raise RuntimeError(f"Facebook response did not include video ID: {result}")
    return str(video_id)


def main():
    print("Jonokotha 360 worker starting")
    claimed = api_request("claim")
    item = claimed.get("video")
    if not item:
        print("No approved video available")
        return

    queue_id = item.get("id")
    if not queue_id:
        raise RuntimeError("Claim API response has no video.id")

    source = WORK_DIR / "source-video"
    output = WORK_DIR / "jonokotha360-rendered.mp4"
    logo = WORK_DIR / "logo.png"
    published_video_id = None

    try:
        download_file(item.get("video_url"), source, max_bytes=350 * 1024 * 1024)
        download_file(LOGO_URL, logo, max_bytes=15 * 1024 * 1024)

        title = item.get("title") or "সর্বশেষ সংবাদ"
        summary = item.get("summary") or title
        render_video(source, output, title, logo)

        published_video_id = facebook_upload(output, title, summary)

        # PHP endpoint's complete handler reads regular POST form fields.
        api_request("complete", {
            "id": str(queue_id),
            "facebook_video_id": published_video_id,
        })
        print(f"Published successfully. Facebook video ID: {published_video_id}")

    except Exception as exc:
        error_message = str(exc)[:1800]
        print(f"Worker failed for queue ID {queue_id}: {error_message}", file=sys.stderr)
        try:
            api_request("fail", {
                "id": str(queue_id),
                "error": error_message,
                "error_text": error_message,
            })
        except Exception as api_exc:
            print(f"Could not report failure to PHP API: {api_exc}", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
