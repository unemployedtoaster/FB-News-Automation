"""
Ranker — computes an engagement score for each queued reel.

Score formula:
  raw   = (views * W_VIEWS) + (likes * W_LIKES) + (comments * W_COMMENTS)
  score = raw / age_decay

age_decay = 2 ^ (hours_since_scraped / HALF_LIFE_HOURS)
  → score halves every HALF_LIFE_HOURS hours, so fresher reels are preferred
    when engagement is otherwise equal.

Weights are intentionally not equal:
  - Comments signal the strongest reaction (people bothered to type something)
  - Likes are mid-tier passive engagement
  - Views are high-volume but low-signal
"""
from datetime import datetime, timezone
from db import get_queued_reels, update_scores

W_VIEWS    = 1.0
W_LIKES    = 50.0
W_COMMENTS = 150.0
HALF_LIFE_HOURS = 24.0  # score halves every 24h — tweak to taste


def age_decay(scraped_at_iso: str) -> float:
    scraped = datetime.fromisoformat(scraped_at_iso)
    if scraped.tzinfo is None:
        scraped = scraped.replace(tzinfo=timezone.utc)
    hours = (datetime.now(timezone.utc) - scraped).total_seconds() / 3600
    return 2 ** (hours / HALF_LIFE_HOURS)


def compute_score(views: int, likes: int, comments: int, scraped_at: str) -> float:
    raw = (views * W_VIEWS) + (likes * W_LIKES) + (comments * W_COMMENTS)
    if raw == 0:
        return 0.0
    return raw / age_decay(scraped_at)


def rescore_and_get_best() -> dict | None:
    """
    Rescore all queued reels, persist the scores, and return the top one.
    Returns None if the queue is empty.
    """
    reels = get_queued_reels()
    if not reels:
        return None

    scored = []
    for r in reels:
        r["score"] = compute_score(
            r.get("views", 0),
            r.get("likes", 0),
            r.get("comments", 0),
            r.get("scraped_at", datetime.now(timezone.utc).isoformat()),
        )
        scored.append(r)
        print(
            f"  [Ranker] id={r['id']} score={r['score']:.1f} "
            f"v={r['views']} l={r['likes']} c={r['comments']} "
            f"url={r['reel_url']}"
        )

    update_scores([{"id": r["id"], "score": r["score"]} for r in scored])

    best = max(scored, key=lambda r: r["score"])
    print(f"[Ranker] Best: id={best['id']} score={best['score']:.1f} → {best['reel_url']}")
    return best
