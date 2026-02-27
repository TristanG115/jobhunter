# ⬡ JobHunter

A personal job aggregator for Tristan Gooding — scrapes relevant jobs, AI-matches them to your resume, and presents them in a clean dashboard with filtering, saving, and a map view.

---

## Quick Start

```bash
# 1. Clone / copy this folder to your server

# 2. Run setup (creates venv, installs deps, starts server)
chmod +x run.sh
./run.sh

# 3. Open in browser
http://localhost:5000
# or from another machine:
http://YOUR_SERVER_IP:5000
```

---

## First-Time Setup (in the UI)

1. Go to **Settings** in the sidebar
2. Enter your **OpenAI API key** (from Purdue's GenAI portal)
3. Enter your **JSearch API key** from RapidAPI (free tier = 200 req/month)
4. Click **Save Keys**
5. Hit **Scrape Now** in the bottom left — takes 2–5 minutes

### Getting API Keys

**JSearch (RapidAPI):**
1. Go to https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch
2. Subscribe → Free plan (200 requests/month, enough for ~1-2 scrapes)
3. Copy the `X-RapidAPI-Key` from the request headers section

**Purdue OpenAI Key:**
- Visit Purdue's GenAI program portal
- Generate/copy your API key — works exactly like an OpenAI key (starts with `sk-`)

---

## Running as a Background Service (recommended for your 24/7 server)

```bash
# Edit the service file with your actual username and path
nano jobhunter.service

# Install the service
sudo cp jobhunter.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable jobhunter
sudo systemctl start jobhunter

# Check status
sudo systemctl status jobhunter
```

### Auto-scrape via Cron (optional)
Add to crontab (`crontab -e`) to scrape every day at 8am:
```
0 8 * * * curl -X POST http://localhost:5000/api/scrape
```

---

## Accessing From Outside Your Network

If you want to access from your laptop away from home, options:
- **Tailscale** (easiest): Install on server + laptop, use Tailscale IP
- **Nginx reverse proxy**: Proxy port 80/443 → 5000 with your domain
- **SSH tunnel**: `ssh -L 5000:localhost:5000 user@yourserver`

---

## Features

| Feature | Details |
|---|---|
| **AI Match Score** | GPT-4o-mini scores 0–100 vs your resume |
| **Job Cards** | Title, company, location, salary, work type, match reasons |
| **Filtering** | By work type, min score, keyword search |
| **Sorting** | Best match, newest, salary, title |
| **Map View** | Leaflet.js map with color-coded markers |
| **Save Jobs** | Bookmark interesting listings |
| **Hide Jobs** | Remove irrelevant ones from view |
| **Persistent DB** | SQLite — survives restarts, only new jobs on re-scrape |
| **Scrape Log** | Live progress during scraping |

---

## Search Targets (pre-configured for Tristan)

The scraper searches for:
- Software engineer / AI / ML engineer / backend developer / data engineer / cloud engineer
- In: Indianapolis, IN | West Lafayette, IN | Remote (USA)
- Entry-level, new grad, and intern roles included

To modify searches, edit `scraper.py` → `SEARCH_QUERIES` list.

---

## Stack

- **Backend**: Python + Flask
- **Database**: SQLite (file: `data/jobs.db`)
- **Job Source**: JSearch API via RapidAPI
- **AI Matching**: OpenAI GPT-4o-mini
- **Map**: Leaflet.js (free, no key needed)
- **Frontend**: Vanilla HTML/CSS/JS (no build step needed)
