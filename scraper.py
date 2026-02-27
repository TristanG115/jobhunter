import requests
import json
import time
import re
from datetime import datetime

# ─── ROLE QUERIES (Adzuna - general searches) ─────────────────────────────────

ROLE_QUERIES = [
    "Software Engineer Entry Level",
    "Associate Software Engineer",
    "Junior Software Engineer",
    "Software Engineer I",
    "Backend Engineer Entry Level",
    "Full Stack Engineer Entry Level",
    "Machine Learning Engineer Entry Level",
    "AI Engineer Entry Level",
    "Data Engineer Entry Level",
    "Python Developer Entry Level",
    "Cloud Engineer Entry Level",
    "DevOps Engineer Entry Level",
    "Systems Analyst Entry Level",
    "Applications Analyst",
    "Data Scientist Entry Level",
    "New Grad Software Engineer",
    "Early Career Software Engineer",
    "Junior Data Engineer",
    "Associate Cloud Engineer",
    "Research Software Engineer",
    "Embedded Software Engineer Entry Level",
    "Implementation Consultant Technology",
    "Solutions Engineer Entry Level",
    "Infrastructure Engineer Entry Level",
    "Controls Software Engineer Entry Level",
    "AI Software Engineer New Grad",
    "Applied AI Engineer Entry Level",
    "Software Developer I",
]

REMOTE_ROLES = [
    "Remote Software Engineer Entry Level",
    "Remote AI Engineer New Grad",
    "Remote Python Developer Entry Level",
    "Remote Machine Learning Engineer Junior",
    "Remote Backend Engineer Entry Level",
    "Remote Data Engineer Entry Level",
]

# ─── COMPANY SEARCHES (JSearch only - targeted) ───────────────────────────────

COMPANY_JSEARCH = [
    {"name": "Rolls-Royce",          "query": "Rolls-Royce software engineer Indiana"},
    {"name": "Caterpillar",          "query": "Caterpillar software engineer entry level Indiana"},
    {"name": "Allison Transmission", "query": "Allison Transmission software engineer Indianapolis"},
    {"name": "Cummins",              "query": "Cummins software engineer entry level Indiana"},
    {"name": "KSM",                  "query": "KSM Katz Sapper Miller technology analyst Indianapolis"},
    {"name": "Eli Lilly",            "query": "Eli Lilly software engineer data engineer Indianapolis entry level"},
    {"name": "Salesforce",           "query": "Salesforce software engineer entry level Indianapolis"},
    {"name": "Purdue University",    "query": "Purdue University research programmer software engineer West Lafayette"},
    {"name": "Raytheon",             "query": "Raytheon software engineer entry level Indiana"},
    {"name": "Carrier",              "query": "Carrier software engineer entry level Indianapolis"},
    {"name": "Angi",                 "query": "Angi software engineer Indianapolis"},
    {"name": "Corteva",              "query": "Corteva software engineer data scientist Indianapolis"},
    {"name": "OneAmerica",           "query": "OneAmerica software engineer Indianapolis"},
    {"name": "Genesys",              "query": "Genesys software engineer Indianapolis"},
    {"name": "Infosys",              "query": "Infosys software engineer Indianapolis entry level"},
]

# ─── TITLE PRE-FILTER ─────────────────────────────────────────────────────────

EXCLUDE_TITLE_KEYWORDS = [
    "senior", "sr.", "sr ", "staff ", "principal", "director", "vp ", "vice president",
    "manager", "head of", "lead ", " lead", "architect", "cto", "cso",
    "surgeon", "physician", "nurse", "dental", "attorney", "lawyer",
    "account executive", "truck driver", "cdl", "warehouse", "hvac",
    "plumber", "electrician", "carpenter", "welder",
]

def is_relevant_title(title: str) -> bool:
    t = title.lower()
    return not any(kw in t for kw in EXCLUDE_TITLE_KEYWORDS)

def dedup_by_title_company(jobs: list) -> list:
    seen = set()
    result = []
    for job in jobs:
        key = re.sub(r'[^a-z0-9]', '', (job.get("title","") + job.get("company","")).lower())
        if key not in seen:
            seen.add(key)
            result.append(job)
    return result

# ─── ADZUNA SCRAPER ───────────────────────────────────────────────────────────

ADZUNA_BASE = "https://api.adzuna.com/v1/api/jobs/us/search/1"

# Adzuna location aliases - map our city names to what Adzuna understands
ADZUNA_LOCATION_MAP = {
    "Indianapolis": "Indianapolis, IN",
    "West Lafayette": "West Lafayette, IN",
    "Plainfield": "Indianapolis, IN",  # Adzuna doesn't have Plainfield, use Indy
}

def scrape_adzuna(app_id, app_key, locations, log_fn):
    """Fetch jobs from Adzuna API. Returns (jobs, api_calls)."""
    all_jobs = []
    seen_ids = set()
    api_calls = 0

    # Build location list for Adzuna
    adzuna_locations = []
    for loc in locations:
        mapped = ADZUNA_LOCATION_MAP.get(loc["city"], f"{loc['city']}, {loc['state']}")
        if mapped not in adzuna_locations:
            adzuna_locations.append(mapped)

    log_fn(f"Adzuna: searching {len(ROLE_QUERIES)} roles × {len(adzuna_locations)} locations + {len(REMOTE_ROLES)} remote")

    # Local role searches
    for loc_str in adzuna_locations:
        for role in ROLE_QUERIES[:10]:  # Top 10 roles per location to stay efficient
            try:
                params = {
                    "app_id": app_id,
                    "app_key": app_key,
                    "results_per_page": 20,
                    "what": role,
                    "where": loc_str,
                    "distance": 30,
                    "sort_by": "date",
                    "content-type": "application/json",
                }
                resp = requests.get(ADZUNA_BASE, params=params, timeout=20)
                api_calls += 1
                resp.raise_for_status()
                data = resp.json()

                new_count = 0
                for job in data.get("results", []):
                    job_id = "az_" + str(job.get("id", ""))
                    if not job_id or job_id in seen_ids:
                        continue
                    title = job.get("title", "")
                    if not is_relevant_title(title):
                        continue
                    seen_ids.add(job_id)

                    # Parse salary
                    sal_min = job.get("salary_min")
                    sal_max = job.get("salary_max")
                    sal_display = ""
                    if sal_min and sal_max:
                        sal_display = f"${int(sal_min):,}–${int(sal_max):,}/yr"
                    elif sal_max:
                        sal_display = f"Up to ${int(sal_max):,}"
                    elif sal_min:
                        sal_display = f"${int(sal_min):,}+"

                    # Location
                    loc_data = job.get("location", {})
                    display_loc = loc_data.get("display_name", loc_str)

                    # Coords
                    lat = job.get("latitude")
                    lng = job.get("longitude")

                    # Work type - Adzuna doesn't always specify, detect from title/desc
                    desc = (job.get("description") or "").lower()
                    title_lower = title.lower()
                    if "remote" in title_lower or "remote" in desc[:200]:
                        work_type = "Remote"
                    elif "hybrid" in title_lower or "hybrid" in desc[:200]:
                        work_type = "Hybrid"
                    else:
                        work_type = "Onsite"

                    apply_url = job.get("redirect_url", "")
                    company = job.get("company", {}).get("display_name", "")

                    all_jobs.append({
                        "job_id": job_id,
                        "title": title,
                        "company": company,
                        "location": display_loc,
                        "lat": lat,
                        "lng": lng,
                        "work_type": work_type,
                        "salary_min": int(sal_min) if sal_min else None,
                        "salary_max": int(sal_max) if sal_max else None,
                        "salary_display": sal_display,
                        "description": (job.get("description") or "")[:2500],
                        "apply_url": apply_url,
                        "company_url": apply_url,
                        "source": "Adzuna",
                        "date_posted": job.get("created", ""),
                    })
                    new_count += 1

                log_fn(f"  Adzuna [{loc_str}] {role[:40]}: {new_count} listings")
                time.sleep(0.3)

            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 429:
                    log_fn("  Adzuna rate limited — waiting 10s...")
                    time.sleep(10)
                    api_calls -= 1
                else:
                    log_fn(f"  Adzuna HTTP {e.response.status_code}: {e}")
            except Exception as e:
                log_fn(f"  Adzuna error: {e}")
                continue

    # Remote searches
    for role in REMOTE_ROLES:
        try:
            params = {
                "app_id": app_id,
                "app_key": app_key,
                "results_per_page": 15,
                "what": role,
                "where": "United States",
                "sort_by": "date",
                "content-type": "application/json",
            }
            resp = requests.get(ADZUNA_BASE, params=params, timeout=20)
            api_calls += 1
            resp.raise_for_status()
            data = resp.json()

            new_count = 0
            for job in data.get("results", []):
                job_id = "az_" + str(job.get("id", ""))
                if not job_id or job_id in seen_ids:
                    continue
                title = job.get("title", "")
                if not is_relevant_title(title):
                    continue
                seen_ids.add(job_id)

                sal_min = job.get("salary_min")
                sal_max = job.get("salary_max")
                sal_display = ""
                if sal_min and sal_max:
                    sal_display = f"${int(sal_min):,}–${int(sal_max):,}/yr"
                elif sal_max:
                    sal_display = f"Up to ${int(sal_max):,}"
                elif sal_min:
                    sal_display = f"${int(sal_min):,}+"

                loc_data = job.get("location", {})
                display_loc = loc_data.get("display_name", "Remote")

                all_jobs.append({
                    "job_id": job_id,
                    "title": title,
                    "company": job.get("company", {}).get("display_name", ""),
                    "location": display_loc,
                    "lat": job.get("latitude"),
                    "lng": job.get("longitude"),
                    "work_type": "Remote",
                    "salary_min": int(sal_min) if sal_min else None,
                    "salary_max": int(sal_max) if sal_max else None,
                    "salary_display": sal_display,
                    "description": (job.get("description") or "")[:2500],
                    "apply_url": job.get("redirect_url", ""),
                    "company_url": job.get("redirect_url", ""),
                    "source": "Adzuna",
                    "date_posted": job.get("created", ""),
                })
                new_count += 1

            log_fn(f"  Adzuna [Remote] {role[:40]}: {new_count} listings")
            time.sleep(0.3)

        except Exception as e:
            log_fn(f"  Adzuna remote error ({role}): {e}")
            continue

    log_fn(f"Adzuna complete: {len(all_jobs)} unique jobs, {api_calls} API calls")
    return all_jobs, api_calls


# ─── JSEARCH SCRAPER (company-specific only) ──────────────────────────────────

def scrape_jsearch_companies(jsearch_key, log_fn):
    """JSearch used ONLY for targeted company searches."""
    all_jobs = []
    seen_ids = set()
    api_calls = 0

    headers = {
        "X-RapidAPI-Key": jsearch_key,
        "X-RapidAPI-Host": "jsearch.p.rapidapi.com"
    }

    log_fn(f"JSearch: targeting {len(COMPANY_JSEARCH)} specific companies...")

    for company in COMPANY_JSEARCH:
        try:
            params = {
                "query": company["query"] + " in United States",
                "page": "1",
                "num_pages": "1",
                "date_posted": "month",
            }
            resp = requests.get(
                "https://jsearch.p.rapidapi.com/search",
                headers=headers, params=params, timeout=20
            )
            api_calls += 1
            resp.raise_for_status()
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
                    sal_display = f"${sal_min:.0f}–${sal_max:.0f}/hr" if sal_period == "HOUR" else f"${int(sal_min):,}–${int(sal_max):,}/yr"
                elif sal_max:
                    sal_display = f"Up to ${int(sal_max):,}"
                elif sal_min:
                    sal_display = f"${int(sal_min):,}+"

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
                    "salary_min": int(sal_min) if sal_min else None,
                    "salary_max": int(sal_max) if sal_max else None,
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

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:
                log_fn(f"  JSearch rate limited — waiting 15s...")
                time.sleep(15)
                api_calls -= 1
            else:
                log_fn(f"  JSearch HTTP {e.response.status_code} for {company['name']}")
        except Exception as e:
            log_fn(f"  JSearch error ({company['name']}): {e}")

    log_fn(f"JSearch company search complete: {len(all_jobs)} jobs, {api_calls} API calls")
    return all_jobs, api_calls


def scrape_jobs(adzuna_app_id, adzuna_app_key, jsearch_key, locations, log_fn, skip_jsearch=False):
    """
    Main scrape entry point.
    Adzuna handles general roles (250/day free).
    JSearch handles company-specific (counts against 200/month).
    Returns (all_jobs, adzuna_calls, jsearch_calls).
    """
    all_jobs = []
    adzuna_calls = 0
    jsearch_calls = 0
    seen_ids = set()

    # Adzuna - general role searches
    if adzuna_app_id and adzuna_app_key:
        az_jobs, adzuna_calls = scrape_adzuna(adzuna_app_id, adzuna_app_key, locations, log_fn)
        for job in az_jobs:
            if job["job_id"] not in seen_ids:
                seen_ids.add(job["job_id"])
                all_jobs.append(job)
    else:
        log_fn("⚠ Adzuna keys not set — skipping general role search")

    # JSearch - company specific
    if jsearch_key and not skip_jsearch:
        js_jobs, jsearch_calls = scrape_jsearch_companies(jsearch_key, log_fn)
        for job in js_jobs:
            if job["job_id"] not in seen_ids:
                seen_ids.add(job["job_id"])
                all_jobs.append(job)
    elif skip_jsearch:
        log_fn("JSearch skipped (low budget mode)")
    else:
        log_fn("⚠ JSearch key not set — skipping company searches")

    # Dedup by title+company
    before = len(all_jobs)
    all_jobs = dedup_by_title_company(all_jobs)
    removed = before - len(all_jobs)
    if removed:
        log_fn(f"Deduped {removed} near-duplicate listings")

    log_fn(f"Total: {len(all_jobs)} unique jobs ({adzuna_calls} Adzuna + {jsearch_calls} JSearch calls)")
    return all_jobs, adzuna_calls, jsearch_calls


# ─── JSON PARSING ─────────────────────────────────────────────────────────────

def robust_parse_json_array(text: str, expected_count: int) -> list:
    text = text.strip()
    text_clean = re.sub(r'^```(?:json)?\s*', '', text)
    text_clean = re.sub(r'\s*```$', '', text_clean).strip()

    # Strategy 1: direct parse of array
    bracket_match = re.search(r'\[[\s\S]*\]', text_clean)
    if bracket_match:
        try:
            result = json.loads(bracket_match.group())
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

    # Strategy 2: fix common issues
    try:
        fixed = re.sub(r',\s*([}\]])', r'\1', text_clean)
        fixed = fixed.replace("'", '"')
        bracket_match2 = re.search(r'\[[\s\S]*\]', fixed)
        if bracket_match2:
            result = json.loads(bracket_match2.group())
            if isinstance(result, list):
                return result
    except Exception:
        pass

    # Strategy 3: extract individual objects
    objects = re.findall(r'\{[^{}]*\}', text_clean, re.DOTALL)
    if objects:
        parsed = []
        for obj_str in objects[:expected_count]:
            try:
                obj = json.loads(obj_str)
                parsed.append(obj)
            except Exception:
                try:
                    fixed_obj = re.sub(r',\s*}', '}', obj_str)
                    obj = json.loads(fixed_obj)
                    parsed.append(obj)
                except Exception:
                    pass
        if parsed:
            return parsed

    raise ValueError(f"Could not parse JSON array. Raw: {text[:200]}")


# ─── AI MATCHING ──────────────────────────────────────────────────────────────

def match_jobs(jobs, api_key, resume_text, ai_context, api_url, model_name, log_fn):
    """Score jobs against resume using Purdue GenAI. Returns (matched_jobs, ai_calls)."""
    matched = []
    ai_calls = 0
    batch_size = 5

    resume_short = resume_text[:2500]
    context_str = f"\nExtra context: {ai_context}" if ai_context else ""

    for i in range(0, len(jobs), batch_size):
        batch = jobs[i:i+batch_size]
        batch_num = i // batch_size + 1
        total_batches = (len(jobs) + batch_size - 1) // batch_size
        log_fn(f"AI matching batch {batch_num}/{total_batches} ({len(batch)} jobs)...")

        jobs_text = ""
        for j, job in enumerate(batch):
            jobs_text += f"\nJob {j+1}: {job['title']} @ {job['company']}\nLocation: {job['location']} | Type: {job['work_type']} | Salary: {job['salary_display'] or 'unlisted'}\nDesc: {job['description'][:500]}\n---"

        prompt = f"""You are a technical recruiter evaluating job fit for a candidate.

CANDIDATE RESUME:
{resume_short}{context_str}

JOBS TO SCORE (evaluate all {len(batch)}):
{jobs_text}

Scoring guide:
- 70-100: Strong match, candidate clearly qualifies
- 40-69: Partial match, worth applying
- 0-39: Poor match, significant skill gaps
- Boost if role is entry-level/new-grad/associate/junior
- Correct work_type to "Remote", "Hybrid", or "Onsite" based on description

YOU MUST respond with ONLY a JSON array. No prose, no markdown.
Return exactly {len(batch)} objects.

[{{"score":85,"reasons":"Strong Python/AWS match. Entry-level role aligns with experience.","work_type":"Hybrid"}},...]"""

        success = False
        for attempt in range(3):
            try:
                resp = requests.post(
                    api_url,
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": model_name,
                        "messages": [
                            {"role": "system", "content": "You are a JSON-only API. Respond only with valid JSON arrays. Never include explanatory text outside the JSON."},
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
                log_fn(f"  Attempt {attempt+1}/3 failed: {e}")
                if attempt < 2:
                    time.sleep(3)

        if not success:
            log_fn(f"  Batch {batch_num} failed all retries — marking unscored")
            for job in batch:
                job["match_score"] = -1
                job["match_reasons"] = "AI matching failed — will retry on rescore"
                matched.append(job)

        time.sleep(1)

    log_fn(f"AI matching complete: {len(matched)} jobs scored, {ai_calls} AI calls")
    return matched, ai_calls
