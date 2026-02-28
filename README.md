# ⬡ JobHunter v4

A self-hosted job search dashboard that aggregates listings from multiple sources, AI-matches them to your resume, and syncs with a Google Sheet tracker. Built for entry-level software engineering roles in Indianapolis, West Lafayette, and remote.

---

## What It Does

- **Scrapes jobs** from Adzuna (free, 250/day) for general role searches and JSearch/RapidAPI (200/month) for targeted company boards
- **AI-matches** each listing to your resume using Purdue's GenAI API, scoring 0–100 with reasons
- **Tracks applications** with status labels (Applied, Interview, Offer, Rejected) and freeform notes
- **Syncs two-ways** with a Google Sheet — your email-update script keeps writing to the sheet as-is, and JobHunter stays in sync
- **Multi-user** — each username gets a separate job list, saved jobs, and profile. API keys are shared

---

## Quick Start

```bash
# 1. Copy this folder to your server

# 2. Run migration (safe to re-run, creates DB + tables)
python3 migrate.py

# 3. Install dependencies
pip install -r requirements.txt

# 4. Start the server
chmod +x run.sh
./run.sh

# 5. Open in browser
http://localhost:5000
# or from another machine:
http://YOUR_SERVER_IP:5000
```

On first launch, log in with any username — new accounts are created automatically with default locations (Indianapolis, West Lafayette, Plainfield).

---

## API Keys — What You Need

### Adzuna (Primary Search Source) — Free
Used for all general role searches. 250 requests/day, resets at midnight.

1. Go to [developer.adzuna.com](https://developer.adzuna.com)
2. Sign up → Create an app → Copy **App ID** and **App Key**
3. Paste both into Settings → Adzuna API

### JSearch via RapidAPI — 200 req/month free
Used only for targeted company searches (Rolls-Royce, Cummins, Eli Lilly, etc.) — about 15 calls per scrape, so 200/month lasts all month.

1. Go to [rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch](https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch)
2. Subscribe → Free plan
3. Copy the `X-RapidAPI-Key` value → paste into Settings → JSearch API

### Purdue GenAI — Free for Purdue users
Used for AI matching (scoring jobs vs. your resume) and the AI Advisor.

1. Log into the [Purdue GenAI portal](https://genai.rcac.purdue.edu)
2. Click your avatar → Settings → Account → API Keys → Create
3. Paste into Settings → Purdue GenAI Key
4. **Recommended model:** `gpt-oss:120b` (best JSON reliability for structured scoring)
5. API URL: `https://genai.rcac.purdue.edu/api/chat/completions`

---

## Google Sheets Sync (Optional)

Two-way sync with a Google Sheet. Your email-tracking script keeps working as-is — JobHunter reads the status updates it writes and can push back when you change things in the app.

### Setup (~5 minutes)

1. Go to [console.cloud.google.com](https://console.cloud.google.com) → create or select a project
2. Search "Google Sheets API" → **Enable** it
3. Go to **IAM & Admin → Service Accounts → Create Service Account** (any name)
4. Click the account → **Keys → Add Key → JSON** → download the file
5. In JobHunter → **Settings → Google Sheets Sync** → upload the JSON file
6. Copy the service account email shown after upload
7. Open your Google Sheet → **Share** → paste the email → give **Editor** access
8. Copy your Sheet ID from the URL: `docs.google.com/spreadsheets/d/YOUR_SHEET_ID_HERE/edit`
9. Paste into Settings → Sheet ID → click **Test Connection**

### How Sync Works

| Action | Result |
|---|---|
| **Pull from Sheet** | Imports all sheet rows into JobHunter tracker. Updates statuses on existing jobs if the sheet has newer info (email script updates are authoritative). |
| **Push to Sheet** | Updates status + notes on rows that came from the sheet. Appends new rows for jobs you marked Applied in JobHunter but weren't already in the sheet. |
| **Auto-push** (toggle) | Every time you change a status or save notes in JobHunter, it silently pushes that change to the sheet in the background. |

Your sheet columns must be: `Title | Company | Pay | Date Applied | Location | Status | Latest Email Subject | Latest Email Body | Notes`

---

## First-Time Setup Checklist

1. `python3 migrate.py` — run once before first start
2. `pip install -r requirements.txt` — install all deps including Google API libs
3. `./run.sh` — start the server
4. Log in at `localhost:5000`
5. Go to **My Profile** → upload your resume (PDF, DOCX, or TXT)
6. Optionally add **Persistent Context** (standing preferences for AI matching)
7. Go to **Settings** → add API keys (Adzuna required, JSearch and Purdue for AI features)
8. Optionally configure **Google Sheets sync**
9. Hit **Scrape Now** — first scrape takes 5–10 minutes

---

## Features

### Job Discovery
- **Adzuna search** — 28 role queries × your active locations + 6 remote variants. Covers Software Engineer, ML/AI/Data Engineer, DevOps, Systems Analyst, Solutions Engineer, and more entry-level/new-grad titles
- **JSearch company boards** — 15 targeted Indiana employers: Rolls-Royce, Caterpillar, Allison Transmission, Cummins, KSM, Eli Lilly, Salesforce, Purdue University, Raytheon, Carrier, Angi, Corteva, OneAmerica, Genesys, Infosys
- **Pre-filtering** — senior/staff/principal/director titles and off-field roles are removed before AI scoring, saving API calls
- **Deduplication** — same job from multiple queries (same title + company) is collapsed to one listing

### AI Features
- **Match scoring** — each new job is scored 0–100 against your resume with a 1–2 sentence explanation. Uses batch processing with 3-retry logic and 4-strategy JSON parsing for reliability. Unscored jobs get `-1` (shown as `?`) rather than a fake 50
- **Rescore** — re-run AI matching on unscored jobs without touching JSearch at all
- **AI Advisor** — analyzes your resume and returns recommended job titles, target companies, specialized job boards, search keywords, and career advice

### Application Tracking
- **Status labels** on every card: No Status / Interested / Applied / Interview / Offer / Rejected
- **Notes field** — auto-saves after you stop typing (800ms debounce)
- **Tracker page** — status overview with counts, filterable by pipeline stage
- **Sheets sync** — statuses and notes stay in sync with your Google Sheet

### Filtering & Display
- **Search** across title, company, location, and notes
- **Work type filter** — Remote / Hybrid / Onsite
- **Source filter** — Adzuna vs. Sheets import
- **Sort** — Best Match, Newest, Salary, Title A-Z, Company A-Z
- **Min score slider** — hide below a threshold
- **Scored only toggle** — hide unscored jobs
- **NEW badge** — cards from the latest scrape are tagged until you click them
- **Map view** — Leaflet.js with color-coded markers (green=strong, yellow=partial, red=low)

### API Usage Tracking
- **Adzuna gauge** in sidebar — shows today's usage vs 250/day limit, resets daily
- **JSearch gauge** — shows monthly usage vs your configured limit
- **Per-scrape breakdown** in the log — exact Adzuna, JSearch, and AI call counts per run
- **Budget guard** — scrape is blocked if JSearch has fewer than 5 calls remaining; JSearch company searches are skipped automatically if budget is critically low

### Export & Utilities
- **Export CSV** — all jobs or saved-only, includes all fields
- **Save/hide** — bookmark interesting jobs, remove irrelevant ones permanently
- **Detail modal** — full job description, posted date, salary, all metadata

---

## Configuring Search Targets

### Locations
Manage in **Settings → Search Locations**. Each location can be toggled on/off per scrape. Default: Indianapolis (30mi), West Lafayette (25mi), Plainfield (20mi).

Adzuna uses the city name directly. Plainfield is mapped to Indianapolis since Adzuna's coverage there is thin.

### Role Queries
Edit `scraper.py` → `ROLE_QUERIES` to add or remove job titles for Adzuna searches. Currently 28 role variants + 6 remote-specific queries.

### Company Boards
Edit `scraper.py` → `COMPANY_JSEARCH` to add companies to the JSearch targeted list. Each entry uses one JSearch API call per scrape.

---

## Running as a Background Service

```bash
# Edit jobhunter.service with your actual username and absolute path
nano jobhunter.service

# Install and start
sudo cp jobhunter.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable jobhunter
sudo systemctl start jobhunter

# Check it's running
sudo systemctl status jobhunter
```

### Auto-scrape via Cron (optional)

```bash
crontab -e
```

Add this line to scrape daily at 8am:
```
0 8 * * * curl -s -X POST http://localhost:5000/api/scrape
```

> Note: cron-triggered scrapes won't have a logged-in session, so they'll fail the auth check. For scheduled scraping, add a `--user` header or create a simple shell script that handles auth. Alternatively, just scrape manually from the UI each morning.

---

## Accessing From Outside Your Network

- **Tailscale** (easiest): Install on server + laptop, use the Tailscale IP address
- **SSH tunnel**: `ssh -L 5000:localhost:5000 user@yourserver` then open `localhost:5000`
- **Nginx reverse proxy**: Proxy port 80/443 → 5000 with your domain name

---

## File Structure

```
jobhunter/
├── app.py              # Flask routes, DB logic, scrape orchestration
├── scraper.py          # Adzuna + JSearch scrapers, AI matching, JSON parsing
├── sheets_sync.py      # Google Sheets two-way sync module
├── migrate.py          # DB migration script — run once before first start
├── requirements.txt    # Python dependencies
├── run.sh              # Start script (creates venv if needed)
├── jobhunter.service   # systemd service file
├── templates/
│   └── index.html      # Single-page frontend (vanilla JS, no build step)
├── data/
│   └── jobs.db         # SQLite database (auto-created)
├── credentials/
│   └── sheets_credentials.json  # Google service account key (you add this)
└── uploads/            # Temp folder (resumes processed in-memory)
```

---

## Stack

| Layer | Technology |
|---|---|
| Backend | Python 3 + Flask |
| Database | SQLite (`data/jobs.db`) |
| Primary job source | Adzuna API (free, 250/day) |
| Company job source | JSearch via RapidAPI (200/month) |
| AI matching | Purdue GenAI API (`gpt-oss:120b` recommended) |
| Sheets sync | Google Sheets API v4 + service account auth |
| Map | Leaflet.js (free, no key) |
| Frontend | Vanilla HTML/CSS/JS — no build step, no npm |

---

## Troubleshooting

**Scrape finds jobs but no match scores appear**
- Check Purdue API key in Settings
- Make sure you uploaded a resume in My Profile
- Try changing the model to `llama3.3:70b` if `gpt-oss:120b` is unavailable
- Check the scrape log for specific AI error messages

**Adzuna returns no results**
- Verify App ID and App Key are both set correctly (two separate fields)
- Test with a broad query — Adzuna has better coverage in larger metros

**Google Sheets sync fails with 403**
- The service account email needs Editor (not Viewer) access to the sheet
- Re-share the sheet and wait 30 seconds before retrying

**Google Sheets sync fails with 404**
- Double-check the Sheet ID — it's the long alphanumeric string in the URL, not the sheet name

**Jobs from sheet don't match existing JobHunter listings**
- Matching is done by normalizing title + company text. Minor spelling differences (e.g. "Lockheed Martin" vs "Lockheedmartin") will result in a duplicate rather than a match — just hide one
