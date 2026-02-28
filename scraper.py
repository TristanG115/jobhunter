"""
JobHunter v5 Scraper — Multi-source, zero-cost job aggregation.

Sources (in order of call during a scrape):
  1. The Muse       — no key, 500 req/hr unauthenticated. Tech/startup focus.
  2. Remotive       — no key, generous limits. Remote-only tech jobs.
  3. Greenhouse     — no key, not rate-limited (CDN-cached). Direct company boards.
  4. USAJobs        — free API key (email signup only). Federal/government jobs.
  5. JSearch        — 200 req/month free. Reserved for Indiana company-specific searches.

Rate-limiting strategy:
  - Hard sleep between every request (configurable per source)
  - Check response headers for rate-limit signals and back off automatically
  - Sources that return 429 are skipped gracefully — rest of scrape continues
  - JSearch budget guard: skipped automatically if <5 calls remain this month
"""

import requests
import json
import time
import re
from datetime import datetime

# ─── TITLE PRE-FILTER ─────────────────────────────────────────────────────────

EXCLUDE_TITLE_KEYWORDS = [
    "senior", "sr.", " sr ", "staff ", "principal", "director", "vp ", "vice president",
    "manager", "head of", "lead ", " lead", "architect", "cto", "cso", "chief",
    "surgeon", "physician", "nurse", "dental", "attorney", "lawyer",
    "account executive", "truck driver", "cdl", "warehouse", "hvac",
    "plumber", "electrician", "carpenter", "welder", "forklift",
]

def is_relevant_title(title: str) -> bool:
    t = title.lower()
    return not any(kw in t for kw in EXCLUDE_TITLE_KEYWORDS)

def dedup_by_title_company(jobs: list) -> list:
    seen = set()
    result = []
    for job in jobs:
        key = re.sub(r'[^a-z0-9]', '', (job.get("title", "") + job.get("company", "")).lower())
        if key not in seen:
            seen.add(key)
            result.append(job)
    return result

def _safe_get(url, params=None, headers=None, timeout=20, source=""):
    """HTTP GET with automatic 429 detection and backoff."""
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=timeout)
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 60))
            raise RateLimitError(f"{source} rate limited — retry after {retry_after}s", retry_after)
        resp.raise_for_status()
        return resp
    except RateLimitError:
        raise
    except requests.exceptions.Timeout:
        raise Exception(f"{source} request timed out")
    except requests.exceptions.RequestException as e:
        raise Exception(f"{source} request failed: {e}")

class RateLimitError(Exception):
    def __init__(self, msg, retry_after=60):
        super().__init__(msg)
        self.retry_after = retry_after


# ═══════════════════════════════════════════════════════════════════════════════
# SOURCE 1: THE MUSE  (no key required, 500 req/hr)
# ═══════════════════════════════════════════════════════════════════════════════

MUSE_CATEGORIES = [
    "Software Engineer",
    "Data Science",
    "Data Analytics",
    "IT",
    "QA",
]

MUSE_LEVELS = ["entry level", "mid level"]  # avoid senior

MUSE_BASE = "https://www.themuse.com/api/public/jobs"

def scrape_muse(log_fn):
    """
    Fetch entry/mid-level tech jobs from The Muse.
    No key needed. 500 req/hr limit — we stay well under with sleeps.
    Returns (jobs, api_calls).
    """
    all_jobs = []
    seen_ids = set()
    api_calls = 0

    log_fn("The Muse: fetching entry-level tech roles...")

    for category in MUSE_CATEGORIES:
        for level in MUSE_LEVELS:
            for page in range(0, 3):  # pages 0,1,2 = up to 60 results per combo
                try:
                    resp = _safe_get(
                        MUSE_BASE,
                        params={"category": category, "level": level, "page": page, "descending": "true"},
                        timeout=15,
                        source="Muse"
                    )
                    api_calls += 1
                    data = resp.json()
                    results = data.get("results", [])
                    if not results:
                        break  # no more pages

                    new_count = 0
                    for job in results:
                        job_id = "muse_" + str(job.get("id", ""))
                        if job_id in seen_ids:
                            continue
                        title = (job.get("name") or "").strip()
                        if not title or not is_relevant_title(title):
                            continue
                        seen_ids.add(job_id)

                        company = job.get("company", {}).get("name", "")
                        locations = job.get("locations", [])
                        location_str = ", ".join(loc.get("name", "") for loc in locations) if locations else "Remote"

                        # Work type — check locations and name
                        title_lower = title.lower()
                        loc_lower = location_str.lower()
                        if "remote" in title_lower or "remote" in loc_lower or not locations:
                            work_type = "Remote"
                        elif "hybrid" in title_lower or "hybrid" in loc_lower:
                            work_type = "Hybrid"
                        else:
                            work_type = "Onsite"

                        # Publication date
                        pub = job.get("publication_date", "")

                        # Description from refs
                        refs = job.get("refs", {})
                        apply_url = refs.get("landing_page", "")

                        # Salary — Muse rarely provides it
                        contents = job.get("contents", "")

                        all_jobs.append({
                            "job_id": job_id,
                            "title": title,
                            "company": company,
                            "location": location_str,
                            "lat": None, "lng": None,
                            "work_type": work_type,
                            "salary_min": None, "salary_max": None, "salary_display": "",
                            "description": re.sub(r'<[^>]+>', '', contents)[:2500],
                            "apply_url": apply_url,
                            "company_url": apply_url,
                            "source": "The Muse",
                            "date_posted": pub,
                        })
                        new_count += 1

                    log_fn(f"  Muse [{category} / {level}] page {page}: {new_count} listings")
                    time.sleep(0.5)  # Stay well under 500/hr

                    if len(results) < 20:
                        break  # last page

                except RateLimitError as e:
                    log_fn(f"  Muse rate limited — skipping remaining pages for {category}")
                    time.sleep(min(e.retry_after, 30))
                    break
                except Exception as e:
                    log_fn(f"  Muse error ({category}/{level}/p{page}): {e}")
                    break

    log_fn(f"The Muse: {len(all_jobs)} jobs, {api_calls} calls")
    return all_jobs, api_calls


# ═══════════════════════════════════════════════════════════════════════════════
# SOURCE 2: REMOTIVE  (no key, remote-only, generous limits)
# ═══════════════════════════════════════════════════════════════════════════════

REMOTIVE_CATEGORIES = [
    "software-dev",
    "data",
    "devops",
    "qa",
]

REMOTIVE_BASE = "https://remotive.com/api/remote-jobs"

def scrape_remotive(log_fn):
    """
    Fetch remote tech jobs from Remotive.
    No key. Returns all active remote listings in category.
    Returns (jobs, api_calls).
    """
    all_jobs = []
    seen_ids = set()
    api_calls = 0

    log_fn("Remotive: fetching remote tech roles...")

    for category in REMOTIVE_CATEGORIES:
        try:
            resp = _safe_get(
                REMOTIVE_BASE,
                params={"category": category, "limit": 100},
                timeout=20,
                source="Remotive"
            )
            api_calls += 1
            data = resp.json()
            jobs = data.get("jobs", [])

            new_count = 0
            for job in jobs:
                job_id = "rem_" + str(job.get("id", ""))
                if job_id in seen_ids:
                    continue
                title = (job.get("title") or "").strip()
                if not title or not is_relevant_title(title):
                    continue
                seen_ids.add(job_id)

                # Parse salary
                sal_str = job.get("salary", "") or ""
                sal_min, sal_max = _parse_salary_range(sal_str)
                sal_display = sal_str if sal_str else ""

                # Candidate location restriction
                candidate_loc = job.get("candidate_required_location", "") or "Worldwide"

                # Strip HTML from description
                desc_html = job.get("description", "") or ""
                desc_text = re.sub(r'<[^>]+>', ' ', desc_html)
                desc_text = re.sub(r'\s+', ' ', desc_text).strip()[:2500]

                all_jobs.append({
                    "job_id": job_id,
                    "title": title,
                    "company": job.get("company_name", ""),
                    "location": f"Remote — {candidate_loc}",
                    "lat": None, "lng": None,
                    "work_type": "Remote",
                    "salary_min": sal_min,
                    "salary_max": sal_max,
                    "salary_display": sal_display,
                    "description": desc_text,
                    "apply_url": job.get("url", ""),
                    "company_url": job.get("url", ""),
                    "source": "Remotive",
                    "date_posted": job.get("publication_date", ""),
                })
                new_count += 1

            log_fn(f"  Remotive [{category}]: {new_count} listings")
            time.sleep(1.0)  # Be polite — undocumented limit

        except RateLimitError as e:
            log_fn(f"  Remotive rate limited — waiting {e.retry_after}s then skipping")
            time.sleep(min(e.retry_after, 30))
        except Exception as e:
            log_fn(f"  Remotive error ({category}): {e}")

    log_fn(f"Remotive: {len(all_jobs)} jobs, {api_calls} calls")
    return all_jobs, api_calls


# ═══════════════════════════════════════════════════════════════════════════════
# SOURCE 3: GREENHOUSE COMPANY BOARDS  (no key, not rate-limited)
# ═══════════════════════════════════════════════════════════════════════════════

# board_token is the slug in boards.greenhouse.io/{token}
# These are your Indiana/target employers that use Greenhouse ATS
GREENHOUSE_BOARDS = [
    {"name": "Salesforce",          "token": "salesforce"},
    {"name": "Angi",                "token": "angi"},
    {"name": "Corteva",             "token": "corteva"},
    {"name": "Genesys",             "token": "genesys"},
    {"name": "Rolls-Royce",         "token": "rollsroyce"},
    {"name": "Raytheon",            "token": "raytheon"},
    {"name": "Carrier",             "token": "carrier"},
    {"name": "Lilly",               "token": "lilly"},
    {"name": "Infosys",             "token": "infosys"},
    {"name": "Cummins",             "token": "cummins"},
    {"name": "Allegion",            "token": "allegion"},
    {"name": "Kyndryl",             "token": "kyndryl"},
    {"name": "Anthology",           "token": "anthology"},
    {"name": "Formstack",           "token": "formstack"},
    {"name": "KAR Global",          "token": "karglobal"},
    {"name": "Caliber Collision",   "token": "calibercollision"},
    {"name": "First Internet Bank", "token": "firstinternetbank"},
    {"name": "Exact Sciences",      "token": "exactsciences"},
    {"name": "Resultant",           "token": "resultant"},
    {"name": "Emplify",             "token": "emplify"},
    # Big remote-friendly tech companies
    {"name": "Notion",              "token": "notion"},
    {"name": "Figma",               "token": "figma"},
    {"name": "Stripe",              "token": "stripe"},
    {"name": "Plaid",               "token": "plaid"},
    {"name": "Brex",                "token": "brex"},
    {"name": "Weights & Biases",    "token": "wandb"},
    {"name": "Scale AI",            "token": "scaleai"},
    {"name": "Neon",                "token": "neondatabase"},
]

GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"

def scrape_greenhouse(log_fn):
    """
    Pull jobs directly from company Greenhouse boards.
    Completely free, no auth, CDN-cached so not rate limited.
    Filters by title keywords client-side.
    Returns (jobs, api_calls).
    """
    all_jobs = []
    seen_ids = set()
    api_calls = 0
    failed_boards = []

    log_fn(f"Greenhouse: pulling {len(GREENHOUSE_BOARDS)} company boards...")

    # Keywords that suggest entry-level / junior
    ENTRY_KEYWORDS = [
        "junior", "entry", "associate", "early career", "new grad", "graduate",
        "intern", "apprentice", "i ", " i)", "level 1", "level i", "jr.",
        " 1 ", "entry-level", "recent grad",
    ]

    for board in GREENHOUSE_BOARDS:
        try:
            url = GREENHOUSE_API.format(token=board["token"])
            resp = _safe_get(url, params={"content": "true"}, timeout=15, source=f"Greenhouse/{board['name']}")
            api_calls += 1
            data = resp.json()
            jobs = data.get("jobs", [])

            new_count = 0
            for job in jobs:
                job_id = "gh_" + str(job.get("id", ""))
                if job_id in seen_ids:
                    continue

                title = (job.get("title") or "").strip()
                if not title:
                    continue

                title_lower = title.lower()

                # Only keep entry-level-ish titles
                # (Greenhouse boards mix all seniority, so we filter harder here)
                has_entry_kw = any(kw in title_lower for kw in ENTRY_KEYWORDS)
                has_tech_kw = any(kw in title_lower for kw in [
                    "software", "engineer", "developer", "data", "analyst",
                    "cloud", "devops", "systems", "python", "java", "backend",
                    "frontend", "full stack", "machine learning", "ai ", "ml ",
                    "infrastructure", "platform", "site reliability", "sre",
                    "quality", "qa", "test", "automation", "it ", "technology",
                ])

                if not has_tech_kw:
                    continue
                if not has_entry_kw and not is_relevant_title(title):
                    continue  # Skip obvious senior titles if no entry keyword
                if not is_relevant_title(title):
                    continue

                seen_ids.add(job_id)

                loc = job.get("location", {})
                location_str = loc.get("name", "") if isinstance(loc, dict) else str(loc)
                loc_lower = location_str.lower()
                if "remote" in loc_lower or not location_str:
                    work_type = "Remote"
                elif "hybrid" in loc_lower:
                    work_type = "Hybrid"
                else:
                    work_type = "Onsite"

                # Strip HTML from content
                content_html = job.get("content", "") or ""
                desc_text = re.sub(r'<[^>]+>', ' ', content_html)
                desc_text = re.sub(r'\s+', ' ', desc_text).strip()[:2500]

                apply_url = job.get("absolute_url", "")
                updated = job.get("updated_at", "")

                all_jobs.append({
                    "job_id": job_id,
                    "title": title,
                    "company": board["name"],
                    "location": location_str,
                    "lat": None, "lng": None,
                    "work_type": work_type,
                    "salary_min": None, "salary_max": None, "salary_display": "",
                    "description": desc_text,
                    "apply_url": apply_url,
                    "company_url": f"https://boards.greenhouse.io/{board['token']}",
                    "source": "Greenhouse",
                    "date_posted": updated,
                })
                new_count += 1

            if new_count:
                log_fn(f"  Greenhouse [{board['name']}]: {new_count} listings")
            time.sleep(0.3)  # Polite but fast — CDN cached

        except RateLimitError as e:
            log_fn(f"  Greenhouse [{board['name']}] rate limited — skipping")
            time.sleep(min(e.retry_after, 15))
        except Exception as e:
            failed_boards.append(board["name"])
            # Many tokens won't exist — silent fail is fine
            time.sleep(0.2)

    if failed_boards:
        log_fn(f"  Greenhouse: {len(failed_boards)} boards not found (tokens may be wrong): {', '.join(failed_boards[:5])}")

    log_fn(f"Greenhouse: {len(all_jobs)} jobs, {api_calls} calls")
    return all_jobs, api_calls


# ═══════════════════════════════════════════════════════════════════════════════
# SOURCE 4: USAJOBS  (free key — email registration at developer.usajobs.gov)
# ═══════════════════════════════════════════════════════════════════════════════

USAJOBS_KEYWORDS = [
    "Software Engineer",
    "Data Scientist",
    "Data Analyst",
    "Systems Analyst",
    "IT Specialist",
    "Computer Engineer",
    "Cybersecurity",
    "Cloud Engineer",
    "Information Technology",
]

USAJOBS_BASE = "https://data.usajobs.gov/api/Search"

def scrape_usajobs(api_key, user_agent_email, locations, log_fn):
    """
    Fetch federal government jobs from USAJobs.
    Requires free API key from developer.usajobs.gov (email + org, instant).
    Returns (jobs, api_calls).
    """
    if not api_key or not user_agent_email:
        log_fn("USAJobs: skipped (no API key configured)")
        return [], 0

    all_jobs = []
    seen_ids = set()
    api_calls = 0

    headers = {
        "Authorization-Key": api_key,
        "User-Agent": user_agent_email,
        "Host": "data.usajobs.gov",
    }

    log_fn(f"USAJobs: searching {len(USAJOBS_KEYWORDS)} keywords...")

    # Search by keyword, no location filter — USAJobs is nationwide + remote
    for keyword in USAJOBS_KEYWORDS:
        try:
            resp = _safe_get(
                USAJOBS_BASE,
                params={
                    "Keyword": keyword,
                    "ResultsPerPage": 25,
                    "SortField": "OpenDate",
                    "SortDirection": "Desc",
                    # GradeLevel 5-9 = GS-5 to GS-9 (entry/junior level)
                    "GradeLevel": "5;6;7;8;9",
                },
                headers=headers,
                timeout=20,
                source="USAJobs"
            )
            api_calls += 1
            data = resp.json()
            items = data.get("SearchResult", {}).get("SearchResultItems", [])

            new_count = 0
            for item in items:
                match = item.get("MatchedObjectDescriptor", {})
                job_id = "usa_" + str(match.get("PositionID", ""))
                if job_id in seen_ids:
                    continue
                title = (match.get("PositionTitle") or "").strip()
                if not title:
                    continue
                seen_ids.add(job_id)

                org = match.get("OrganizationName", "")
                dept = match.get("DepartmentName", "")
                company = org or dept

                locs = match.get("PositionLocation", [])
                if isinstance(locs, list) and locs:
                    loc0 = locs[0]
                    location_str = loc0.get("LocationName", "")
                    lat = loc0.get("Latitude")
                    lng = loc0.get("Longitude")
                else:
                    location_str = "United States"
                    lat = lng = None

                # Remote?
                remote_indicator = match.get("PositionRemuneration", [{}])
                telecommute = match.get("UserArea", {}).get("Details", {}).get("Telework", "")
                work_type = "Remote" if "remote" in str(telecommute).lower() else "Onsite"

                # Salary
                rem = match.get("PositionRemuneration", [])
                sal_min = sal_max = None
                sal_display = ""
                if rem:
                    r0 = rem[0]
                    sal_min = _to_int(r0.get("MinimumRange"))
                    sal_max = _to_int(r0.get("MaximumRange"))
                    interval = r0.get("RateIntervalCode", "")
                    if sal_min and sal_max:
                        sal_display = f"${sal_min:,}–${sal_max:,}/{interval.lower() or 'yr'}"

                apply_url = match.get("PositionURI", "")
                posted = match.get("PublicationStartDate", "")

                all_jobs.append({
                    "job_id": job_id,
                    "title": title,
                    "company": company,
                    "location": location_str,
                    "lat": lat,
                    "lng": lng,
                    "work_type": work_type,
                    "salary_min": sal_min,
                    "salary_max": sal_max,
                    "salary_display": sal_display,
                    "description": match.get("UserArea", {}).get("Details", {}).get("JobSummary", "")[:2500],
                    "apply_url": apply_url,
                    "company_url": apply_url,
                    "source": "USAJobs",
                    "date_posted": posted,
                })
                new_count += 1

            log_fn(f"  USAJobs [{keyword}]: {new_count} listings")
            time.sleep(1.0)  # USAJobs asks for polite usage

        except RateLimitError as e:
            log_fn(f"  USAJobs rate limited — skipping remaining keywords")
            break
        except Exception as e:
            log_fn(f"  USAJobs error ({keyword}): {e}")

    log_fn(f"USAJobs: {len(all_jobs)} jobs, {api_calls} calls")
    return all_jobs, api_calls


# ═══════════════════════════════════════════════════════════════════════════════
# SOURCE 5: JSEARCH  (200/month — company-specific only)
# ═══════════════════════════════════════════════════════════════════════════════

COMPANY_JSEARCH = [
    {"name": "Rolls-Royce",          "query": "Rolls-Royce software engineer Indiana"},
    {"name": "Caterpillar",          "query": "Caterpillar software engineer entry level Indiana"},
    {"name": "Allison Transmission", "query": "Allison Transmission software engineer Indianapolis"},
    {"name": "Cummins",              "query": "Cummins software engineer entry level Indiana"},
    {"name": "KSM",                  "query": "KSM Katz Sapper Miller technology analyst Indianapolis"},
    {"name": "Eli Lilly",            "query": "Eli Lilly software engineer data engineer Indianapolis entry level"},
    {"name": "Purdue University",    "query": "Purdue University research programmer software engineer West Lafayette"},
    {"name": "OneAmerica",           "query": "OneAmerica software engineer Indianapolis"},
    {"name": "Ivy Tech",             "query": "Ivy Tech software technology Indianapolis"},
    {"name": "IUPUI",                "query": "IUPUI Indiana University software IT engineer Indianapolis"},
]

def scrape_jsearch_companies(jsearch_key, log_fn):
    """JSearch used ONLY for targeted Indiana company searches."""
    all_jobs = []
    seen_ids = set()
    api_calls = 0

    headers = {
        "X-RapidAPI-Key": jsearch_key,
        "X-RapidAPI-Host": "jsearch.p.rapidapi.com"
    }

    log_fn(f"JSearch: targeting {len(COMPANY_JSEARCH)} Indiana companies...")

    for company in COMPANY_JSEARCH:
        try:
            params = {
                "query": company["query"] + " in United States",
                "page": "1",
                "num_pages": "1",
                "date_posted": "month",
            }
            resp = _safe_get(
                "https://jsearch.p.rapidapi.com/search",
                params=params,
                headers=headers,
                timeout=20,
                source=f"JSearch/{company['name']}"
            )
            api_calls += 1
            data = resp.json()

            new_count = 0
            for job in data.get("data", []):
                job_id = job.get("job_id", "")
                if not job_id or job_id in seen_ids:
                    continue
                title = job.get("job_title", "")
                if not is_relevant_title(title):
                    continue
                seen_ids.add(job_id)

                sal_min = job.get("job_min_salary")
                sal_max = job.get("job_max_salary")
                sal_period = (job.get("job_salary_period") or "").upper()
                sal_display = ""
                if sal_min and sal_max:
                    sal_display = f"${sal_min:.0f}–${sal_max:.0f}/hr" if sal_period == "HOUR" \
                        else f"${int(sal_min):,}–${int(sal_max):,}/yr"
                elif sal_max:
                    sal_display = f"Up to ${int(sal_max):,}"

                city = job.get("job_city", "")
                state = job.get("job_state", "")
                location_str = ", ".join(filter(None, [city, state])) or job.get("job_country", "")
                apply_url = job.get("job_apply_link", "")

                all_jobs.append({
                    "job_id": job_id,
                    "title": title,
                    "company": job.get("employer_name", "") or company["name"],
                    "location": location_str,
                    "lat": job.get("job_latitude"),
                    "lng": job.get("job_longitude"),
                    "work_type": "Remote" if job.get("job_is_remote") else "Onsite",
                    "salary_min": _to_int(sal_min),
                    "salary_max": _to_int(sal_max),
                    "salary_display": sal_display,
                    "description": (job.get("job_description") or "")[:2500],
                    "apply_url": apply_url,
                    "company_url": job.get("employer_website", "") or apply_url,
                    "source": company["name"],
                    "date_posted": job.get("job_posted_at_datetime_utc", ""),
                })
                new_count += 1

            log_fn(f"  {company['name']}: {new_count} listings")
            time.sleep(0.5)

        except RateLimitError as e:
            log_fn(f"  JSearch rate limited — stopping company searches")
            break
        except Exception as e:
            log_fn(f"  JSearch error ({company['name']}): {e}")

    log_fn(f"JSearch: {len(all_jobs)} jobs, {api_calls} calls")
    return all_jobs, api_calls


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════════════

def scrape_jobs(usajobs_key, usajobs_email, jsearch_key, locations, log_fn, skip_jsearch=False):
    """
    Run all sources in sequence. Merge and deduplicate results.
    Returns (all_jobs, source_call_counts_dict).
    """
    all_jobs = []
    seen_ids = set()
    call_counts = {"muse": 0, "remotive": 0, "greenhouse": 0, "usajobs": 0, "jsearch": 0}

    def merge(jobs, source_key):
        added = 0
        for job in jobs:
            jid = job.get("job_id", "")
            if jid and jid not in seen_ids:
                seen_ids.add(jid)
                all_jobs.append(job)
                added += 1
        return added

    # 1. The Muse
    try:
        jobs, calls = scrape_muse(log_fn)
        call_counts["muse"] = calls
        merge(jobs, "muse")
    except Exception as e:
        log_fn(f"Muse source failed: {e}")

    # 2. Remotive
    try:
        jobs, calls = scrape_remotive(log_fn)
        call_counts["remotive"] = calls
        merge(jobs, "remotive")
    except Exception as e:
        log_fn(f"Remotive source failed: {e}")

    # 3. Greenhouse
    try:
        jobs, calls = scrape_greenhouse(log_fn)
        call_counts["greenhouse"] = calls
        merge(jobs, "greenhouse")
    except Exception as e:
        log_fn(f"Greenhouse source failed: {e}")

    # 4. USAJobs (optional)
    if usajobs_key:
        try:
            jobs, calls = scrape_usajobs(usajobs_key, usajobs_email, locations, log_fn)
            call_counts["usajobs"] = calls
            merge(jobs, "usajobs")
        except Exception as e:
            log_fn(f"USAJobs source failed: {e}")
    else:
        log_fn("USAJobs: skipped (no key — register free at developer.usajobs.gov)")

    # 5. JSearch — company-specific Indiana searches
    if jsearch_key and not skip_jsearch:
        try:
            jobs, calls = scrape_jsearch_companies(jsearch_key, log_fn)
            call_counts["jsearch"] = calls
            merge(jobs, "jsearch")
        except Exception as e:
            log_fn(f"JSearch source failed: {e}")
    elif skip_jsearch:
        log_fn("JSearch: skipped (low budget mode)")
    else:
        log_fn("JSearch: skipped (no key configured)")

    # Dedup by title+company (catches cross-source duplicates)
    before = len(all_jobs)
    all_jobs = dedup_by_title_company(all_jobs)
    removed = before - len(all_jobs)
    if removed:
        log_fn(f"Cross-source dedup removed {removed} duplicates")

    total_calls = sum(call_counts.values())
    log_fn(
        f"✓ Total: {len(all_jobs)} unique jobs | "
        f"Muse:{call_counts['muse']} Remotive:{call_counts['remotive']} "
        f"Greenhouse:{call_counts['greenhouse']} USAJobs:{call_counts['usajobs']} "
        f"JSearch:{call_counts['jsearch']}"
    )
    return all_jobs, call_counts


# ═══════════════════════════════════════════════════════════════════════════════
# JSON PARSING  (robust 4-strategy parser for AI responses)
# ═══════════════════════════════════════════════════════════════════════════════

def robust_parse_json_array(text: str, expected_count: int) -> list:
    text = text.strip()
    text_clean = re.sub(r'^```(?:json)?\s*', '', text)
    text_clean = re.sub(r'\s*```$', '', text_clean).strip()

    # Strategy 1: direct array parse
    bracket_match = re.search(r'\[[\s\S]*\]', text_clean)
    if bracket_match:
        try:
            result = json.loads(bracket_match.group())
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

    # Strategy 2: fix trailing commas + single quotes
    try:
        fixed = re.sub(r',\s*([}\]])', r'\1', text_clean).replace("'", '"')
        bracket_match2 = re.search(r'\[[\s\S]*\]', fixed)
        if bracket_match2:
            result = json.loads(bracket_match2.group())
            if isinstance(result, list):
                return result
    except Exception:
        pass

    # Strategy 3: extract individual objects and rebuild array
    objects = re.findall(r'\{[^{}]*\}', text_clean, re.DOTALL)
    if objects:
        parsed = []
        for obj_str in objects[:expected_count]:
            try:
                parsed.append(json.loads(obj_str))
            except Exception:
                try:
                    parsed.append(json.loads(re.sub(r',\s*}', '}', obj_str)))
                except Exception:
                    pass
        if parsed:
            return parsed

    raise ValueError(f"Could not parse JSON array. Raw: {text[:200]}")


# ═══════════════════════════════════════════════════════════════════════════════
# AI MATCHING
# ═══════════════════════════════════════════════════════════════════════════════

def match_jobs(jobs, api_key, resume_text, ai_context, api_url, model_name, log_fn):
    """Score jobs against resume. Returns (matched_jobs, ai_calls_used)."""
    matched = []
    ai_calls = 0
    batch_size = 5

    resume_short = resume_text[:2500]
    context_str = f"\nExtra context: {ai_context}" if ai_context else ""

    for i in range(0, len(jobs), batch_size):
        batch = jobs[i:i + batch_size]
        batch_num = i // batch_size + 1
        total_batches = (len(jobs) + batch_size - 1) // batch_size
        log_fn(f"AI matching batch {batch_num}/{total_batches} ({len(batch)} jobs)...")

        jobs_text = ""
        for j, job in enumerate(batch):
            jobs_text += (
                f"\nJob {j + 1}: {job['title']} @ {job['company']}\n"
                f"Location: {job['location']} | Type: {job['work_type']} | Salary: {job['salary_display'] or 'unlisted'}\n"
                f"Desc: {job['description'][:400]}\n---"
            )

        prompt = (
            f"You are a technical recruiter evaluating job fit.\n\n"
            f"CANDIDATE RESUME:\n{resume_short}{context_str}\n\n"
            f"JOBS TO SCORE:\n{jobs_text}\n\n"
            f"Scoring: 70-100=strong match, 40-69=worth applying, 0-39=poor fit.\n"
            f"Boost entry-level/new-grad/associate roles. "
            f"Correct work_type to Remote/Hybrid/Onsite based on description.\n\n"
            f"YOU MUST respond with ONLY a JSON array of exactly {len(batch)} objects:\n"
            f'[{{"score":85,"reasons":"Strong Python match. Entry-level.","work_type":"Remote"}},...]\n'
            f"No prose, no markdown, ONLY the JSON array."
        )

        success = False
        for attempt in range(3):
            try:
                resp = requests.post(
                    api_url,
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": model_name,
                        "messages": [
                            {"role": "system", "content": "You are a JSON-only API. Respond only with valid JSON arrays."},
                            {"role": "user", "content": prompt}
                        ],
                        "stream": False
                    },
                    timeout=120
                )
                ai_calls += 1
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"].strip()
                ratings = robust_parse_json_array(content, len(batch))

                for j, job in enumerate(batch):
                    if j < len(ratings):
                        r = ratings[j]
                        job["match_score"] = max(0, min(100, int(r.get("score", 50))))
                        job["match_reasons"] = str(r.get("reasons", "")).strip()
                        job["work_type"] = r.get("work_type", job["work_type"])
                    else:
                        job["match_score"] = -1
                        job["match_reasons"] = "Score unavailable (partial response)"
                    matched.append(job)
                success = True
                break

            except Exception as e:
                log_fn(f"  Attempt {attempt + 1}/3 failed: {e}")
                if attempt < 2:
                    time.sleep(3)

        if not success:
            log_fn(f"  Batch {batch_num} failed all retries — marking unscored")
            for job in batch:
                job["match_score"] = -1
                job["match_reasons"] = "AI matching failed — use Rescore to retry"
                matched.append(job)

        time.sleep(1)

    log_fn(f"AI matching complete: {len(matched)} jobs, {ai_calls} AI calls")
    return matched, ai_calls


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _to_int(val):
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return None

def _parse_salary_range(sal_str: str):
    """Try to extract min/max salary from a string like '$40,000 - $60,000'."""
    if not sal_str:
        return None, None
    nums = re.findall(r'[\d,]+', sal_str.replace(',', ''))
    nums = [int(n) for n in nums if n.isdigit() and int(n) > 1000]
    if len(nums) >= 2:
        return min(nums), max(nums)
    elif len(nums) == 1:
        return nums[0], nums[0]
    return None, None
