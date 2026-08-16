# Reel Pipeline

Automatically monitors target Facebook Pages for new Reels, ranks them by engagement, and posts the best one to your Facebook Page on a schedule.

---

## Setup

### 1. Supabase — create the queue table

In the Supabase SQL editor, run:

```sql
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
```

### 2. Facebook App — get a Page access token

1. Go to [developers.facebook.com](https://developers.facebook.com) → create an app (type: Business)
2. Add the **Pages API** product
3. Request permissions: `pages_manage_posts`, `pages_read_engagement`
4. In Graph API Explorer, generate a **long-lived Page access token** for your page
5. Note your App ID and App Secret

### 3. GitHub Secrets

Go to your repo → Settings → Secrets and variables → Actions → New repository secret.

Add all of these:

| Secret name | Value |
|---|---|
| `SUPABASE_URL` | Your Supabase project URL |
| `SUPABASE_KEY` | Your Supabase `service_role` key (not anon) |
| `TARGET_PAGES` | JSON array of page URLs: `["https://www.facebook.com/page1/reels/", "..."]` |
| `FB_PAGE_ID` | Your Facebook Page's numeric ID |
| `FB_ACCESS_TOKEN` | Long-lived Page access token |
| `FB_APP_ID` | Your Facebook App ID (for token refresh) |
| `FB_APP_SECRET` | Your Facebook App Secret (for token refresh) |
| `GH_PAT` | GitHub Personal Access Token with `secrets:write` scope (for token refresh) |

### 4. Adjust posting times

In `.github/workflows/publisher.yml`, update the cron schedules to your preferred posting times (in UTC).

---

## How it works

```
Every 2 hours:   Watcher  → scrapes target pages → upserts reels into Supabase
At post times:   Ranker   → rescores all queued reels
                 Publisher → picks best score → downloads via yt-dlp → posts via Graph API
Every Sunday:    TokenRefresh → refreshes FB token → updates GitHub secret automatically
```

## Scoring formula

```
score = (views × 1 + likes × 50 + comments × 150) / 2^(age_hours / 24)
```

Comments are weighted highest (150×) because they signal the strongest engagement.
Score decays by half every 24 hours so newer reels are preferred over equally-engaged older ones.

Tweak `W_VIEWS`, `W_LIKES`, `W_COMMENTS`, and `HALF_LIFE_HOURS` in `pipeline/ranker.py`.

---

## Manual runs

Both workflows can be triggered manually from the **Actions** tab in GitHub.
The publisher workflow has a **dry run** option that scores and picks but doesn't post.

---

## Caveats

- Facebook's DOM changes frequently — the Playwright selectors in `watcher.py` will need maintenance.
- `yt-dlp` handles most Facebook Reel URLs but may break after Facebook updates. Keep it updated (`pip install -U yt-dlp`).
- GitHub Actions free tier gives 2,000 Linux minutes/month. At 2-hour polling intervals the watcher uses ~720 min/month, leaving headroom for publisher runs.
- This pipeline scrapes public pages only. Never automate login — that's a fast ban.
