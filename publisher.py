"""
Publisher — picks the best queued reel and posts to YouTube as a Short.
Checks Google Drive first for a pre-downloaded file (from prescorer),
falls back to downloading fresh if not available.
"""
import os
import io
import subprocess
import tempfile
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from ranker import rescore_and_get_best
from db import mark_posted, mark_skipped, get_client

YOUTUBE_CLIENT_ID     = os.environ["YOUTUBE_CLIENT_ID"]
YOUTUBE_CLIENT_SECRET = os.environ["YOUTUBE_CLIENT_SECRET"]
YOUTUBE_REFRESH_TOKEN = os.environ["YOUTUBE_REFRESH_TOKEN"]
GDRIVE_REFRESH_TOKEN  = os.environ.get("GDRIVE_REFRESH_TOKEN")
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


def get_drive_client():
    creds = Credentials(
        token=None,
        refresh_token=GDRIVE_REFRESH_TOKEN,
        client_id=YOUTUBE_CLIENT_ID,
        client_secret=YOUTUBE_CLIENT_SECRET,
        token_uri="https://oauth2.googleapis.com/token",
    )
    return build("drive", "v3", credentials=creds)


def get_ready_reel() -> dict | None:
    """Check if prescorer already prepped a ready reel."""
    sb = get_client()
    resp = (
        sb.table("reels")
        .select("*")
        .eq("status", "ready")
        .order("score", desc=True)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


def download_from_drive(file_id: str, output_path: str) -> bool:
    """Download pre-scored video from Google Drive."""
    try:
        drive = get_drive_client()
        request = drive.files().get_media(fileId=file_id)
        with open(output_path, "wb") as f:
            downloader = MediaIoBaseDownload(f, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
        print("[Publisher] Got pre-downloaded reel from Drive.")
        return True
    except Exception as e:
        print(f"[Publisher] Drive download failed: {e}")
        return False


def delete_from_drive(file_id: str):
    """Delete the temp file from Drive after posting."""
    try:
        drive = get_drive_client()
        drive.files().delete(fileId=file_id).execute()
        print(f"[Publisher] Cleaned up Drive file {file_id}")
    except Exception as e:
        print(f"[Publisher] Drive cleanup failed: {e}")


def clean_url(url: str) -> str:
    return url.split("?")[0].split("&")[0]


def download_reel(reel_url: str, output_path: str) -> bool:
    cmd = [
        "yt-dlp", "--quiet", "--no-warnings",
        "-f", "mp4/best", "-o", output_path,
        "--max-filesize", "100m", "--socket-timeout", "30",
        "--no-check-certificates",
        clean_url(reel_url),
    ]
    print(f"[Publisher] Downloading fresh: {clean_url(reel_url)}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        print(f"[Publisher] yt-dlp failed:\n{result.stderr}")
        return False
    return True


def upload_to_youtube(youtube, video_path: str, description: str) -> str | None:
    body = {
        "snippet": {
            "title": "#Shorts",
            "description": description,
            "tags": ["shorts", "news", "bangladesh"],
            "categoryId": "25",
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaFileUpload(
        video_path, mimetype="video/mp4",
        resumable=True, chunksize=1024 * 1024 * 5,
    )
    print("[Publisher] Uploading to YouTube...")
    request = youtube.videos().insert(
        part="snippet,status", body=body, media_body=media,
    )
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"[Publisher] {int(status.progress() * 100)}%")
    video_id = response.get("id")
    print(f"[Publisher] Done → https://www.youtube.com/shorts/{video_id}")
    return video_id


def main():
    print("[Publisher] Starting...")

    best = get_ready_reel()
    use_drive = best is not None

    if not best:
        best = rescore_and_get_best()

    if not best:
        print("[Publisher] Queue is empty — nothing to post.")
        return

    print(f"[Publisher] Reel id={best['id']} score={best['score']:.1f} {'(pre-scored)' if use_drive else ''}")

    if DRY_RUN:
        print(f"[Publisher] DRY RUN — would post: {best['reel_url']}")
        return

    youtube = get_youtube_client()

    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = f"{tmpdir}/reel.mp4"
        downloaded = False

        if use_drive and best.get("drive_file_id"):
            downloaded = download_from_drive(best["drive_file_id"], video_path)

        if not downloaded:
            downloaded = download_reel(best["reel_url"], video_path)

        if not downloaded:
            print("[Publisher] All download methods failed — skipping.")
            mark_skipped(best["id"])
            return

        description = f"Source: {best['source_page']}\n\n#Shorts #News #Bangladesh"
        video_id = upload_to_youtube(youtube, video_path, description)

        if video_id:
            mark_posted(best["id"])
            if use_drive and best.get("drive_file_id"):
                delete_from_drive(best["drive_file_id"])
        else:
            print("[Publisher] Upload failed — reel stays queued.")


if __name__ == "__main__":
    main()
