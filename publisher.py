"""
Publisher — downloads best reel in HD, posts it twice:
1. As a regular YouTube video (original aspect ratio, full quality)
2. As a YouTube Short (letterboxed to 9:16)
Groq generates separate optimized titles for each.
"""
import os
import io
import subprocess
import tempfile
import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from ranker import rescore_and_get_best
from db import mark_posted, mark_skipped, get_client

YOUTUBE_CLIENT_ID     = os.environ["YOUTUBE_CLIENT_ID"]
YOUTUBE_CLIENT_SECRET = os.environ["YOUTUBE_CLIENT_SECRET"]
YOUTUBE_REFRESH_TOKEN = os.environ["YOUTUBE_REFRESH_TOKEN"]
GDRIVE_REFRESH_TOKEN  = os.environ.get("GDRIVE_REFRESH_TOKEN")
GROQ_API_KEY          = os.environ.get("GROQ_API_KEY")
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
    try:
        drive = get_drive_client()
        drive.files().delete(fileId=file_id).execute()
        print(f"[Publisher] Cleaned up Drive file {file_id}")
    except Exception as e:
        print(f"[Publisher] Drive cleanup failed: {e}")


def clean_url(url: str) -> str:
    return url.split("?")[0].split("&")[0]


def download_reel_hd(reel_url: str, output_path: str) -> bool:
    """Download in best available quality — prefers 1080p, falls back to best."""
    cmd = [
        "yt-dlp", "--quiet", "--no-warnings",
        # Try 1080p first, fall back to best available
        "-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "-o", output_path,
        "--max-filesize", "500m",
        "--socket-timeout", "30",
        "--no-check-certificates",
        clean_url(reel_url),
    ]
    print(f"[Publisher] Downloading HD: {clean_url(reel_url)}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        print(f"[Publisher] HD download failed, trying fallback:\n{result.stderr}")
        # Fallback to simple best
        cmd_fallback = [
            "yt-dlp", "--quiet", "--no-warnings",
            "-f", "mp4/best", "-o", output_path,
            "--max-filesize", "500m", "--socket-timeout", "30",
            "--no-check-certificates",
            clean_url(reel_url),
        ]
        result2 = subprocess.run(cmd_fallback, capture_output=True, text=True, timeout=180)
        if result2.returncode != 0:
            print(f"[Publisher] Fallback also failed:\n{result2.stderr}")
            return False
    return True


def enhance_quality(input_path: str, output_path: str) -> bool:
    """
    Re-encode to improve quality:
    - Scale up to 1080p if smaller
    - CRF 18 for high quality (lower = better, 18 is near-lossless)
    - High quality audio at 192k
    """
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", "scale='if(gt(iw,1920),1920,iw)':'if(gt(ih,1080),1080,ih)':flags=lanczos",
        "-c:v", "libx264", "-crf", "18", "-preset", "fast",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        output_path,
    ]
    print("[Publisher] Enhancing quality...")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        print(f"[Publisher] Quality enhancement failed:\n{result.stderr}")
        return False
    return True


def letterbox_to_9x16(input_path: str, output_path: str) -> bool:
    """Pad video to 9:16 with black bars, keeping original content untouched."""
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", "scale=w=1080:h=1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,setsar=1",
        "-t", "179",  # trim to 2:59 max for Shorts
        "-c:v", "libx264", "-crf", "18", "-preset", "fast",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        output_path,
    ]
    print("[Publisher] Letterboxing to 9:16 for Short...")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        print(f"[Publisher] Letterbox failed:\n{result.stderr}")
        return False
    return True


def generate_titles(source_page: str) -> tuple[str, str]:
    """Generate separate titles for the long video and the Short using Groq."""
    source_name = source_page.rstrip("/").split("/")[-1]

    if not GROQ_API_KEY:
        return (
            f"Breaking News from {source_name} | Bangladesh News",
            f"Breaking News #Shorts #News #Bangladesh",
        )

    prompt = f"""You are a YouTube title writer for a Bangladeshi news channel called "Cold Hard Feed".
The content comes from the Facebook page "{source_name}".

Generate TWO titles:
1. LONG_TITLE: For a regular YouTube video. Engaging, descriptive, 60-80 chars, no hashtags.
2. SHORT_TITLE: For a YouTube Short. Punchy, under 60 chars, end with #Shorts #News #Bangladesh.

Return ONLY this format, nothing else:
LONG_TITLE: <title here>
SHORT_TITLE: <title here>"""

    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "llama3-8b-8192",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 150,
                "temperature": 0.7,
            },
            timeout=15,
        )
        content = resp.json()["choices"][0]["message"]["content"].strip()
        long_title = "Breaking News from Bangladesh"
        short_title = "Breaking News #Shorts #News #Bangladesh"
        for line in content.splitlines():
            if line.startswith("LONG_TITLE:"):
                long_title = line.replace("LONG_TITLE:", "").strip()[:100]
            elif line.startswith("SHORT_TITLE:"):
                short_title = line.replace("SHORT_TITLE:", "").strip()[:100]
        print(f"[Publisher] Long title: {long_title}")
        print(f"[Publisher] Short title: {short_title}")
        return long_title, short_title
    except Exception as e:
        print(f"[Publisher] Groq failed: {e}")
        return (
            f"Breaking News | {source_name} | Bangladesh",
            f"Breaking News #Shorts #News #Bangladesh",
        )


def upload_to_youtube(youtube, video_path: str, title: str, description: str, is_short: bool = False) -> str | None:
    tags = ["news", "bangladesh", "breaking news"]
    if is_short:
        tags += ["shorts", "youtubeshorts"]

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": "25",
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaFileUpload(
        video_path, mimetype="video/mp4",
        resumable=True, chunksize=1024 * 1024 * 10,  # 10MB chunks for speed
    )
    label = "Short" if is_short else "Video"
    print(f"[Publisher] Uploading {label} to YouTube...")
    request = youtube.videos().insert(
        part="snippet,status", body=body, media_body=media,
    )
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"[Publisher] {label} {int(status.progress() * 100)}%")
    video_id = response.get("id")
    url = f"https://www.youtube.com/shorts/{video_id}" if is_short else f"https://www.youtube.com/watch?v={video_id}"
    print(f"[Publisher] {label} done → {url}")
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
        raw_path    = f"{tmpdir}/raw.mp4"
        hd_path     = f"{tmpdir}/hd.mp4"
        short_path  = f"{tmpdir}/short.mp4"
        downloaded  = False

        # Step 1: get the video
        if use_drive and best.get("drive_file_id"):
            downloaded = download_from_drive(best["drive_file_id"], raw_path)
        if not downloaded:
            downloaded = download_reel_hd(best["reel_url"], raw_path)
        if not downloaded:
            print("[Publisher] All download methods failed — skipping.")
            mark_skipped(best["id"])
            return

        # Step 2: enhance quality for long video
        if not enhance_quality(raw_path, hd_path):
            print("[Publisher] Quality enhancement failed — using raw.")
            hd_path = raw_path

        # Step 3: letterbox for Short
        short_ok = letterbox_to_9x16(hd_path, short_path)
        if not short_ok:
            short_path = hd_path  # fallback

        # Step 4: generate titles
        source = best.get("source_page", "")
        long_title, short_title = generate_titles(source)
        description = f"Source: {source}\n\n#News #Bangladesh #BreakingNews"
        short_description = f"Source: {source}\n\n#Shorts #News #Bangladesh #BreakingNews"

        # Step 5: upload long video
        video_id = upload_to_youtube(youtube, hd_path, long_title, description, is_short=False)

        # Step 6: upload Short
        short_id = upload_to_youtube(youtube, short_path, short_title, short_description, is_short=True)

        if video_id or short_id:
            mark_posted(best["id"])
            if use_drive and best.get("drive_file_id"):
                delete_from_drive(best["drive_file_id"])
            print(f"[Publisher] Posted video: {video_id} | Short: {short_id}")
        else:
            print("[Publisher] Both uploads failed — reel stays queued.")


if __name__ == "__main__":
    main()
