"""
Pre-scorer — runs 30 min before each publish time.
Rescores the queue, picks the winner, downloads it,
and uploads the video file to Supabase Storage so the
publisher can skip the download step and post instantly.

Supabase Storage bucket needed: create one called 'reels' in your
Supabase dashboard (Storage → New bucket → name: reels → public: false)
"""
import os
import subprocess
import tempfile
from db import get_queued_reels, update_scores, get_client
from ranker import compute_score
from datetime import datetime, timezone


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
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        print(f"[PreScorer] yt-dlp failed: {result.stderr}")
        return False
    return True


def upload_to_supabase_storage(video_path: str, reel_id: int) -> str | None:
    """Upload video to Supabase Storage and return the storage path."""
    sb = get_client()
    storage_path = f"ready/{reel_id}.mp4"

    with open(video_path, "rb") as f:
        data = f.read()

    try:
        # Remove old file if exists
        sb.storage.from_("reels").remove([storage_path])
    except Exception:
        pass

    resp = sb.storage.from_("reels").upload(
        storage_path,
        data,
        {"content-type": "video/mp4", "upsert": "true"},
    )

    if resp:
        print(f"[PreScorer] Uploaded to storage: {storage_path}")
        # Mark the reel as pre-downloaded in the db
        sb.table("reels").update({"status": "ready"}).eq("id", reel_id).execute()
        return storage_path
    return None


def main():
    print("[PreScorer] Starting...")
    reels = get_queued_reels()

    if not reels:
        print("[PreScorer] Queue empty — nothing to prep.")
        return

    scored = rescore(reels)
    best = scored[0]
    print(f"[PreScorer] Best reel id={best['id']} score={best['score']:.1f} → {best['reel_url']}")

    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = f"{tmpdir}/reel.mp4"
        if not download_reel(best["reel_url"], video_path):
            print("[PreScorer] Download failed — publisher will download at post time instead.")
            return

        path = upload_to_supabase_storage(video_path, best["id"])
        if path:
            print(f"[PreScorer] Reel id={best['id']} is ready to post.")
        else:
            print("[PreScorer] Storage upload failed — publisher will handle it.")


if __name__ == "__main__":
    main()
