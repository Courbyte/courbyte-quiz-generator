# Courbyte Arena – AI Quiz Video Generator

Automatically generates AI-written quiz videos and distributes them across social
media, on a schedule or on demand.

**Live production URL:** `https://courbyte-app.duckdns.org`

---

## 📁 Project Structure

```
courbyte-quiz-generator/
├── server.py                  # Flask app: routes, video generation, scheduler
├── templates/
│   └── index.html             # Dashboard markup
├── static/
│   ├── css/style.css          # Dashboard styling
│   └── js/app.js              # Dashboard behavior
├── requirements.txt
├── .env                        # Real secrets (never commit this)
├── .env.example                 # Template of every variable needed
├── .gitignore
├── .dockerignore
├── Dockerfile                   # For Render (or any Docker-based host)
├── list_voices.py                # Utility: lists installed pyttsx3 voices
├── schedule.json                 # Auto-created: your scheduled topics
├── calm_music.mp3                # Optional: background music
├── logo.png                      # Optional: watermark image
├── outro.mp4                     # Optional: pre-made outro clip (keeps its own audio)
└── supabase/
    └── functions/
        └── cleanup-old-videos/
            └── index.ts           # Deployed separately via Supabase CLI
```

**Note:** there is no persistent local `videos/` folder. MoviePy still writes a
temporary file on disk to encode into, but it's written to the system temp
directory and deleted automatically right after a successful Supabase upload.
Supabase is the only permanent storage for generated videos.

---

## 🚀 Local Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```
Also install at the OS level: **FFmpeg**, and if using the default TTS engine,
**espeak-ng** on Linux (Windows uses its built-in SAPI5 voices instead).
ImageMagick is **not** required — all text is drawn with PIL, not MoviePy's
TextClip.

### 2. Supabase setup
Create a project at supabase.com, then run in the SQL Editor:

```sql
CREATE TABLE IF NOT EXISTS public.users (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  username TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  pin_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS public.quiz_questions (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  topic TEXT UNIQUE NOT NULL,
  questions_json JSONB NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.quiz_questions ENABLE ROW LEVEL SECURITY;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

INSERT INTO public.users (username, password_hash, pin_hash)
VALUES (
  'yourusername',
  encode(digest('YourActualPassword', 'sha256'), 'hex'),
  encode(digest('123456', 'sha256'), 'hex')
);
```
Then create a **Storage bucket** named `quiz-videos`, toggle **Public bucket ON**.

### 3. Get your API keys
- **Groq** (free): console.groq.com
- **Supabase**: Settings → API → the **service_role** key specifically (not `anon`)
- **Make.com** (optional, for live credit tracking): Profile → API → token with `scenarios:read` scope only

### 4. Fill in `.env`
```ini
SUPABASE_URL=https://xxxxxxxxxxxx.supabase.co
SUPABASE_SERVICE_KEY=your_service_role_key
SUPABASE_BUCKET=quiz-videos
GROQ_API_KEY=gsk_...
MAKE_WEBHOOK_URL=https://hook.eu1.make.com/xxxxxxxxxxxxxxxxxxxxxxxx
MAKE_API_TOKEN=your_make_api_token
MAKE_ZONE=eu1
MAKE_TEAM_ID=your_team_id
MAKE_CREDIT_LIMIT=1000
SECRET_TOKEN=some_long_random_string
KEEP_LOCAL_VIDEOS=false

# TTS engine choice - see "Voice" section below
TTS_ENGINE=pyttsx3
TTS_VOICE_ID=
POLLY_VOICE_ID=Joanna
POLLY_REGION=us-east-1
```

### 5. Run it
```bash
python3 server.py
```
Open `http://localhost:5000`.

---

## 🔐 Login

- **Username + password** for the dashboard. Password must contain an uppercase
  letter, lowercase letter, number, and special symbol (8+ chars).
- **6-digit PIN** is separate, only used to confirm deleting a scheduled day.
- Both stored as SHA-256 hashes in Supabase, never plaintext.
- Password field has a lock/unlock icon to toggle visibility.
- Login shows a spinner then a success toast; logout shows its own toast.

---

## 🎬 Video Generation — structure of each video

1. **Opening hook** — spoken/on-screen: *"Can you score X out of X in this
   quiz?"* where X is automatically the number of questions.
2. **Per question:** question read aloud → 5-second countdown beep (5→1) → a
   dedicated answer-reveal card where the correct option is spoken.
3. **Outro** — your `outro.mp4` if present (keeps its own original audio),
   otherwise an auto-generated brand card.
4. **Follow/Subscribe screen** — final call-to-action after the outro, two
   button-style graphics with a spoken prompt.

All screens use the brand palette (`#00cccc` / `#99ffff` aqua on a dark
gradient with a soft glow).

### Voice — two engines available
```ini
TTS_ENGINE=pyttsx3   # free, offline, works everywhere, but voice differs by OS
                     # (Windows SAPI5 vs Linux espeak-ng) and sounds robotic on Linux
TTS_ENGINE=polly     # Amazon Polly Neural voice - natural/human sounding, and
                     # sounds IDENTICAL regardless of which server it runs on.
                     # Small free tier for 12 months, then pay-per-character
                     # (usage volume here is tiny - a few sentences per video,
                     # twice a day).
```
For Polly: either set `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` in `.env`
(local testing), or — used in production here — attach an **IAM Role** with
`AmazonPollyReadOnlyAccess` to the EC2 instance, so no keys are stored anywhere.

Every spoken line in a video (hook, questions, answers, fallback outro,
follow/subscribe) goes through the same `make_tts()` function, so the voice
stays consistent throughout one video regardless of which engine is active.

### Avoiding repeat topics
Both "Suggest Topics" and the automatic scheduler's fallback topic selection
check Supabase for every topic already generated and tell Groq to avoid all of
them, only falling back to a small static list as a last resort.

### Per-slot auto-post toggle
A bullhorn icon next to each slot's "Posted" checkbox — bright = webhook fires
after generating, dim = video still generates and uploads to Supabase, but no
webhook is sent for that one.

---

## 📅 Scheduling — how automatic generation actually works

1. Select a day on the dashboard → click **Suggest Topics** → Groq fills both
   the 9:00 and 18:00 slots with fresh topics. This saves immediately,
   server-side - no extra click needed for that specific action (though click
   **Save Schedule** after any manual edits, like toggling auto-post).
2. At 9:00 and 18:00 every day, Flask's **APScheduler** cron job fires, checks
   `schedule.json` for that slot's topic, and - if one is filled in - runs the
   full pipeline automatically: generate quiz → render video → upload to
   Supabase → fire the Make.com webhook. Completely hands-off once scheduled.
3. If a slot is empty, the scheduler asks Groq for a fresh, never-used topic
   instead of leaving that slot idle.
4. **This only runs while the server process is alive.** On the current AWS
   EC2 setup with `Restart=always` in the systemd service, this is reliable -
   no sleep/wake risk like a free-tier PaaS host would have.

Make.com's side is **not** schedule-based at all - it's a webhook listener,
reacting instantly whenever Flask sends it a payload. Keep "Immediately as
data arrives" toggled ON in the scenario.

---

## 📡 Social Posting Pipeline

- Flask sends a webhook (`video_url`, `topic`, `filename`) to Make's Custom
  Webhook trigger.
- **Facebook Pages**, **Instagram for Business**, **YouTube** (via an HTTP
  download step first, since YouTube's module needs binary data not a URL) —
  all native Make modules.
- **TikTok** — posted via **Zernio**, a dedicated Make connector for TikTok,
  wired directly into the scenario. No Buffer/Zapier/Google Sheets bridge
  needed for this platform.
- **X (Twitter)** — currently removed from the pipeline; no native Make module
  exists since Make discontinued X support in 2025.
- **Instagram note:** occasionally interrupted by a Facebook security
  checkpoint on the connected account; currently bypassed via a "Skip" route
  so it doesn't block the rest of the run. Reconnecting Make's Facebook
  connection from scratch would likely resolve this properly.
- Module references in Make must point at the actual webhook module's real ID
  — recreating the webhook changes its ID and breaks every `{{ID.field}}`
  reference downstream until remapped.

---

## 📊 Make.com Credit Tracking

`/api/make-credits` calls Make's `scenarios/consumptions` endpoint, sums
centicredits used this billing period, and shows remaining credits on the
dashboard's circular gauge. Falls back to `—` gracefully if not configured.

---

## 🧹 Supabase 7-Day Auto-Delete

An Edge Function (`supabase/functions/cleanup-old-videos/index.ts`) checks the
`quiz-videos` bucket and deletes anything older than 7 days. Deployed with:
```bash
supabase functions deploy cleanup-old-videos
```
Scheduled via `pg_cron` (requires `pg_cron` and `pg_net` extensions enabled):
```sql
select cron.schedule(
  'cleanup-old-videos-daily',
  '0 3 * * *',
  $$ select net.http_post(
    url := 'https://<project-ref>.supabase.co/functions/v1/cleanup-old-videos',
    headers := '{"Authorization": "Bearer <service_role_key>"}'::jsonb
  ) $$
);
```

---

## ☁️ Production Deployment (current live setup)

Deployed on **AWS EC2**, chosen after Oracle Cloud's signup fraud-detection
repeatedly rejected the account (a known, common issue unrelated to any
mistake on our end). AWS was already available and, as a real always-on VM,
avoids the sleep/wake problem free PaaS tiers like Render have.

### Architecture
```
[ Client Browser / Webhooks / API ]
        │
        ▼ (Ports 80 / 443)
[ AWS Security Group ]
        │
        ▼
[ Nginx Web Server ]
  (SSL termination via Let's Encrypt)
        │
        ▼ (Internal port 5000)
[ Flask App / Gunicorn WSGI ]
```

- **Cloud infrastructure:** AWS EC2, Ubuntu 22.04 LTS
- **Reverse proxy:** Nginx (SSL termination)
- **App server:** Gunicorn running the Flask app in a virtualenv on `localhost:5000`
- **Process management:** systemd service (`courbyte.service`), `Restart=always`
  for auto-recovery on crash or reboot
- **DNS:** DuckDNS free subdomain — `courbyte-app.duckdns.org`
- **SSL:** Let's Encrypt via Certbot, auto-renewing via systemd timer

### Security Group (AWS EC2 inbound rules)

| Type | Protocol | Port | Source | Purpose |
|---|---|---|---|---|
| SSH | TCP | 22 | your IP (restrict this) | Admin terminal access |
| HTTP | TCP | 80 | 0.0.0.0/0 | ACME challenge (Certbot) + HTTP→HTTPS redirect |
| HTTPS | TCP | 443 | 0.0.0.0/0 | Secure public traffic |

**⚠️ Open item:** port 5000 was originally left open to `0.0.0.0/0` for direct
dev access. Since Nginx now reverse-proxies everything through 80/443 with
SSL, that direct port-5000 exposure is unnecessary and should be closed (or at
minimum restricted to your own IP) to avoid bypassing Nginx's SSL entirely.

Internal Linux firewall: `sudo ufw allow 'Nginx Full'`

### Verification commands
```bash
sudo systemctl status nginx
sudo systemctl status courbyte
curl -i https://courbyte-app.duckdns.org/
sudo journalctl -u courbyte -f
```

### Alternative path considered: Render
Documented here in case of future migration - Render requires no credit card
at signup (unlike Oracle/AWS/GCP), using the included `Dockerfile` (installs
FFmpeg, espeak-ng, fonts-dejavu-core). Tradeoff: free tier sleeps after 15 min
idle, requiring an external pinger (StatusCake, free, 5-min checks, allows
commercial use) to keep the scheduler reliable. Start command would be:
`gunicorn server:app --bind 0.0.0.0:$PORT --timeout 300` (the long timeout is
necessary - gunicorn's default 30s timeout would otherwise kill in-progress
video generation requests).

### Domain name note
Free permanent domains (the old Freenom .tk/.ml model) effectively no longer
exist as a reliable option - that service shut down in 2024. DuckDNS's free
subdomain (used here) was the practical realistic alternative.

---

## 🐛 Bugs Fixed Along the Way (for reference)

- **TTS engine hang** — reusing one global `pyttsx3` engine instance could
  silently deadlock on Windows. Fixed by creating a fresh engine per call.
- **TTS race condition** — `runAndWait()` could return before the WAV file
  finished writing, causing intermittent MoviePy read errors. Fixed with a
  file-size check and short retries.
- **MoviePy 1.x vs 2.x syntax** — `.loop()`/`.volumex()` replaced with
  `.with_effects([afx.AudioLoop(...), afx.MultiplyVolume(...)])`.
- **Countdown beep volume** — lowered from `0.8` to `0.25` amplitude.
- **Countdown length** — changed from 12 seconds to 5, counting 5→1.
- **Font path** — added Linux fallback paths (the original Windows-only path
  would otherwise crash every frame render on a Linux server).
- **Make.com module ID mismatch** — recreating the webhook module gave it a
  new ID; every downstream `{{1.field}}` reference had to be repointed.

---

## 📜 Server Endpoints Reference

- `GET /` — Dashboard UI
- `POST /api/login` — Authentication
- `GET /api/schedule` / `POST /api/schedule` — Fetch / save schedule
- `POST /api/delete-day` — Delete a scheduled day (requires PIN)
- `POST /api/generate-slot` — Generate video for one specific slot
- `POST /api/generate-questions` — Generate quiz questions only (preview)
- `POST /api/suggest-topics` — AI topic suggestions for selected days
- `POST /api/create-video` — Manual video creation from reviewed questions
- `GET /api/make-credits` — Live Make.com credit usage

---

## ⚠️ Known Open Items

- **Port 5000 exposed publicly** on the EC2 Security Group — should be closed
  or restricted now that Nginx handles all public traffic (see above).
- **X (Twitter) posting** — removed from the pipeline for now.
- **Instagram checkpoint** — currently bypassed via "Skip," not fully resolved.
- **Exposed Supabase service_role key** — pasted into chat during debugging
  more than once; rotating it (Supabase → Settings → API → regenerate) is
  recommended, updating `.env` and the `pg_cron` job afterward.

---

## 🛠️ Utility Scripts

- **`list_voices.py`** — lists installed `pyttsx3` voices and their IDs (run
  locally for Windows voices, or on the server for Linux voices).

Several one-off debugging scripts were created during setup
(`test_supabase_login.py`, `check_key_role.py`, `hash_credentials.py`, etc.) —
diagnostic tools only, not part of the running app, safe to delete.
