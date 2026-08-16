"""
Publisher — picks the best queued reel and posts it to your Facebook page.
Runs on GitHub Actions at your scheduled posting times.
"""
import os
import subprocess
import tempfile
import requests
from ranker import rescore_and_get_best
from db import mark_posted, mark_skipped

FB_PAGE_ID     = os.environ["FB_PAGE_ID"]
FB_ACCESS_TOKEN = os.environ["FB_ACCESS_TOKEN"]
DRY_RUN        = os.environ.get("DRY_RUN", "false").lower() == "true"

GRAPH_BASE = "https://graph.facebook.com/v19.0"


def download_reel(reel_url: str, output_path: str) -> bool:
    """
    Use yt-dlp to download the reel to a local file.
    yt-dlp handles Facebook URLs natively.
    Pass cookies-from-browser if the reel is behind a login wall.
    """
    cmd = [
        "yt-dlp",
        "--quiet",
        "--no-warnings",
        "-f", "mp4",                    # prefer mp4
        "-o", output_path,
        "--max-filesize", "100m",       # FB reel limit is ~4GB but keep it sane
        "--socket-timeout", "30",
        reel_url,
    ]
    print(f"[Publisher] Downloading: {reel_url}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        print(f"[Publisher] yt-dlp failed:\n{result.stderr}")
        return False
    return True


def upload_video_to_facebook(video_path: str, description: str = "") -> str | None:
    """
    Upload a video file to the page using the Graph API resumable upload.
    Returns the video ID on success, None on failure.

    For files under ~100MB the simple (non-resumable) upload works fine.
    For larger files you'd need the chunked upload API — out of scope here.
    """
    url = f"{GRAPH_BASE}/{FB_PAGE_ID}/videos"
    print(f"[Publisher] Uploading to Facebook...")

    with open(video_path, "rb") as f:
        resp = requests.post(
            url,
            data={
                "access_token": FB_ACCESS_TOKEN,
                "description": description,
                "published": "true",       # set to false to schedule instead
            },
            files={"source": ("reel.mp4", f, "video/mp4")},
            timeout=300,
        )

    if resp.status_code == 200 and "id" in resp.json():
        video_id = resp.json()["id"]
        print(f"[Publisher] Uploaded. Video ID: {video_id}")
        return video_id
    else:
        print(f"[Publisher] Upload failed: {resp.status_code} {resp.text}")
        return None


def main():
    print("[Publisher] Starting...")
    best = rescore_and_get_best()

    if not best:
        print("[Publisher] Queue is empty — nothing to post.")
        return

    print(f"[Publisher] Selected reel id={best['id']} score={best['score']:.1f}")

    if DRY_RUN:
        print(f"[Publisher] DRY RUN — would post: {best['reel_url']}")
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = f"{tmpdir}/reel.mp4"

        # Step 1: download
        if not download_reel(best["reel_url"], video_path):
            print("[Publisher] Download failed — marking as skipped.")
            mark_skipped(best["id"])
            return

        # Step 2: upload & publish
        video_id = upload_video_to_facebook(
            video_path,
            description="",  # add your caption logic here
        )

        if video_id:
            mark_posted(best["id"])
            print(f"[Publisher] Done. Posted video ID {video_id}.")
        else:
            print("[Publisher] Upload failed — reel stays queued for next run.")


if __name__ == "__main__":
    main()
