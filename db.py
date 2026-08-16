"""
Supabase database layer.
Table: reels
  id          bigint (auto)
  reel_url    text (unique)
  source_page text
  views       bigint
  likes       bigint
  comments    bigint
  score       float8
  status      text  ('queued' | 'posted' | 'skipped')
  scraped_at  timestamptz
  posted_at   timestamptz

Run this SQL once in the Supabase SQL editor to create the table:

  CREATE TABLE reels (
    id          bigserial PRIMARY KEY,
    reel_url    text UNIQUE NOT NULL,
    source_page text,
    views       bigint DEFAULT 0,
    likes       bigint DEFAULT 0,
    comments    bigint DEFAULT 0,
    score       float8 DEFAULT 0,
    status      text   DEFAULT 'queued',
    scraped_at  timestamptz DEFAULT now(),
    posted_at   timestamptz
  );

  CREATE INDEX reels_status_score_idx ON reels (status, score DESC);
"""
import os
from datetime import datetime, timezone
from supabase import create_client, Client

_client: Client | None = None


def get_client() -> Client:
    global _client
    if _client is None:
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_KEY"]
        _client = create_client(url, key)
    return _client


def upsert_reel(
    reel_url: str,
    source_page: str,
    views: int = 0,
    likes: int = 0,
    comments: int = 0,
):
    """Insert a new reel or update metrics if it already exists."""
    sb = get_client()
    sb.table("reels").upsert(
        {
            "reel_url": reel_url,
            "source_page": source_page,
            "views": views,
            "likes": likes,
            "comments": comments,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        },
        on_conflict="reel_url",
        # Only update metrics if the new values are higher (don't overwrite with zeros)
        ignore_duplicates=False,
    ).execute()


def update_scores(scored_rows: list[dict]):
    """Bulk-update the score column for a list of {id, score} dicts."""
    sb = get_client()
    for row in scored_rows:
        sb.table("reels").update({"score": row["score"]}).eq("id", row["id"]).execute()


def get_queued_reels() -> list[dict]:
    """Return all reels with status='queued', ordered by score desc."""
    sb = get_client()
    resp = (
        sb.table("reels")
        .select("*")
        .eq("status", "queued")
        .order("score", desc=True)
        .execute()
    )
    return resp.data or []


def mark_posted(reel_id: int):
    sb = get_client()
    sb.table("reels").update(
        {
            "status": "posted",
            "posted_at": datetime.now(timezone.utc).isoformat(),
        }
    ).eq("id", reel_id).execute()


def mark_skipped(reel_id: int):
    sb = get_client()
    sb.table("reels").update({"status": "skipped"}).eq("id", reel_id).execute()


def mark_ready(reel_id: int):
    """Mark a reel as pre-downloaded and ready to post."""
    sb = get_client()
    sb.table("reels").update({"status": "ready"}).eq("id", reel_id).execute()
