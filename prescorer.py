"""
Pre-scorer — runs 30 min before each publish time.
Rescores the queue, picks the winner, downloads it,
and uploads to Google Drive so the publisher can post instantly.
"""
import os
import subprocess
import tempfile
from db import get_queued_reels, update_scores, get_client
from ranker import compute_score
from datetime import datetime, timezone
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

YOUTUBE_CLIENT_ID     = os.environ["YOUTUBE_CLIENT_ID"]
YOUTUBE_CLIENT_SECRET = os.environ["YOUTUBE_CLIENT_SECRET"]
GDRIVE_REFRESH_TOKEN  = os.environ["GDRIVE_REFRESH_TOKEN"]
GDRIVE_FOLDER_ID      = os.environ["GDRIVE_FOLDER_ID"]


def get_drive_client():
    creds = Credentials(
        token=None,
        refresh_token=GDRIVE_REFRESH_TOKEN,
        client_id=YOUTUBE_CLIENT_ID,
        client_secret=YOUTUBE_CLIENT_SECRET,
        token_uri="https://oauth2.googleapis.com/token",
    )
    return build("drive", "v3", credentials=creds)


def rescore(reels: list[dict]) -> list[dict]:
    for r in reels:
        r["score"] = compute_score(
            r.get("views", 0),
            r.get("likes", 0),
            r.get("comments", 0),
            r.get("scraped_at", datetime.now(timezone.utc).isoformat()),
        )
    update_scores([{"id": r["id"], "score": r["score"]} for r in reels])
    return sorted(reels, key=lambda r: r["score"], reverse=True)


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
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        print(f"[PreScorer] yt-dlp failed: {result.stderr}")
        return False
    return True


def delete_old_files(drive):
    """Clean up any leftover files from previous runs."""
    resp = drive.files().list(
        q=f"'{GDRIVE_FOLDER_ID}' in parents and trashed=false",
        fields="files(id, name)",
    ).execute()
    for f in resp.get("files", []):
        drive.files().delete(fileId=f["id"]).execute()
        print(f"[PreScorer] Deleted old file: {f['name']}")


def upload_to_drive(drive, video_path: str, reel_id: int) -> str | None:
    """Upload video to Google Drive and return the file ID."""
    file_metadata = {
        "name": f"reel_{reel_id}.mp4",
        "parents": [GDRIVE_FOLDER_ID],
    }
    media = MediaFileUpload(
        video_path,
        mimetype="video/mp4",
        resumable=True,
        chunksize=1024 * 1024 * 5,
    )
    file = drive.files().create(
        body=file_metadata,
        media_body=media,
        fields="id",
    ).execute()
    file_id = file.get("id")
    print(f"[PreScorer] Uploaded to Drive. File ID: {file_id}")
    return file_id


def save_drive_file_id(reel_id: int, file_id: str):
    """Store the Drive file ID and mark reel as ready in Supabase."""
    sb = get_client()
    sb.table("reels").update({
        "status": "ready",
        "drive_file_id": file_id,
    }).eq("id", reel_id).execute()


def main():
    print("[PreScorer] Starting...")
    reels = get_queued_reels()

    if not reels:
        print("[PreScorer] Queue empty — nothing to prep.")
        return

    scored = rescore(reels)
    best = scored[0]
    print(f"[PreScorer] Best reel id={best['id']} score={best['score']:.1f} → {best['reel_url']}")

    drive = get_drive_client()
    delete_old_files(drive)

    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = f"{tmpdir}/reel.mp4"
        if not download_reel(best["reel_url"], video_path):
            print("[PreScorer] Download failed — publisher will handle it at post time.")
            return

        file_id = upload_to_drive(drive, video_path, best["id"])
        if file_id:
            save_drive_file_id(best["id"], file_id)
            print(f"[PreScorer] Reel id={best['id']} is ready.")
        else:
            print("[PreScorer] Drive upload failed.")


if __name__ == "__main__":
    main()
