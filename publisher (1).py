"""
Publisher — picks the best queued reel, downloads it, and uploads to YouTube as a Short.
Runs on GitHub Actions at your scheduled posting times.
"""
import os
import subprocess
import tempfile
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from ranker import rescore_and_get_best
from db import mark_posted, mark_skipped

YOUTUBE_CLIENT_ID     = os.environ["YOUTUBE_CLIENT_ID"]
YOUTUBE_CLIENT_SECRET = os.environ["YOUTUBE_CLIENT_SECRET"]
YOUTUBE_REFRESH_TOKEN = os.environ["YOUTUBE_REFRESH_TOKEN"]
DRY_RUN               = os.environ.get("DRY_RUN", "false").lower() == "true"


def get_youtube_client():
    creds = Credentials(
        token=None,
        refresh_token=YOUTUBE_REFRESH_TOKEN,
        client_id=YOUTUBE_CLIENT_ID,
        client_secret=YOUTUBE_CLIENT_SECRET,
        token_uri="https://oauth2.googleapis.com/token",
    )
    return build("youtube", "v3", credentials=creds)


def download_reel(reel_url: str, output_path: str) -> bool:
    cmd = [
        "yt-dlp",
        "--quiet",
        "--no-warnings",
        "-f", "mp4",
        "-o", output_path,
        "--max-filesize", "100m",
        "--socket-timeout", "30",
        reel_url,
    ]
    print(f"[Publisher] Downloading: {reel_url}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        print(f"[Publisher] yt-dlp failed:\n{result.stderr}")
        return False
    return True


def upload_to_youtube(youtube, video_path: str, title: str, description: str) -> str | None:
    """
    Upload video as a YouTube Short.
    Shorts require vertical video (9:16) under 60 seconds.
    We add #Shorts to the title to signal it to YouTube's algorithm.
    """
    body = {
        "snippet": {
            "title": title[:100],  # YT title limit
            "description": description,
            "tags": ["shorts", "news", "bangladesh"],
            "categoryId": "25",  # News & Politics
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(
        video_path,
        mimetype="video/mp4",
        resumable=True,
        chunksize=1024 * 1024 * 5,  # 5MB chunks
    )

    print("[Publisher] Uploading to YouTube...")
    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"[Publisher] Upload progress: {int(status.progress() * 100)}%")

    video_id = response.get("id")
    print(f"[Publisher] Uploaded. Video ID: {video_id}")
    print(f"[Publisher] URL: https://www.youtube.com/shorts/{video_id}")
    return video_id


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

    youtube = get_youtube_client()

    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = f"{tmpdir}/reel.mp4"

        if not download_reel(best["reel_url"], video_path):
            print("[Publisher] Download failed — marking as skipped.")
            mark_skipped(best["id"])
            return

        title = f"#Shorts"
        description = f"Source: {best['source_page']}\n\n#Shorts #News #Bangladesh"

        video_id = upload_to_youtube(youtube, video_path, title, description)

        if video_id:
            mark_posted(best["id"])
            print(f"[Publisher] Done. https://www.youtube.com/shorts/{video_id}")
        else:
            print("[Publisher] Upload failed — reel stays queued.")


if __name__ == "__main__":
    main()
