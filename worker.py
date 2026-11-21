
import os
import sys
import subprocess
import requests
from pathlib import Path

API_URL = os.environ["J360_API_URL"]
API_SECRET = os.environ["J360_API_SECRET"]
WORK_DIR = Path("work")
WORK_DIR.mkdir(exist_ok=True)

def api_request(action, payload=None):
    response = requests.post(
        API_URL,
        json={"action": action, **(payload or {})},
        headers={"X-J360-Secret": API_SECRET},
        timeout=60
    )
    response.raise_for_status()
    return response.json()

def download_video(url, target):
    with requests.get(url, stream=True, timeout=180) as r:
        r.raise_for_status()
        with open(target, "wb") as f:
            for chunk in r.iter_content(1024 * 1024):
                if chunk:
                    f.write(chunk)

def render_video(source, output, title, logo):
    # Keep original footage; fit inside vertical canvas.
    safe_title = title.replace("\\", "\\\\").replace("'", "\\'")
    cmd = [
        "ffmpeg", "-y",
        "-i", str(source),
        "-i", str(logo),
        "-filter_complex",
        (
            "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease,"
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black,"
            "setsar=1[base];"
            "[1:v]scale=180:-1[logo];"
            "[base][logo]overlay=35:35,"
            "drawbox=x=0:y=0:w=iw:h=150:color=0x101820@0.85:t=fill,"
            "drawbox=x=0:y=1450:w=iw:h=400:color=0x101820@0.85:t=fill,"
            "drawbox=x=0:y=1850:w=iw:h=70:color=0x087448@0.98:t=fill[v]"
        ),
        "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast",
        "-crf", "23", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-t", "60", "-movflags", "+faststart",
        str(output)
    ]
    subprocess.run(cmd, check=True)

def main():
    print("Jonokotha 360 video worker starting")

    # Queue API will be connected after hosting API is installed.
    result = api_request("claim")

    if not result.get("video"):
        print("No approved video available")
        return

    item = result["video"]
    source = WORK_DIR / "source.mp4"
    output = WORK_DIR / "rendered.mp4"
    logo = WORK_DIR / "logo.png"

    download_video(item["video_url"], source)
    download_video(os.environ["J360_LOGO_URL"], logo)

    render_video(source, output, item["title"], logo)

    with open(output, "rb") as f:
        upload = requests.post(
            API_URL,
            headers={"X-J360-Secret": API_SECRET},
            data={"action": "publish"},
            files={"video": ("jonokotha360.mp4", f, "video/mp4")},
            timeout=900
        )
        upload.raise_for_status()

    print("Video processing completed")

if __name__ == "__main__":
    main()
