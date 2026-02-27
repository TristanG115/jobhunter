import requests
import json
import time

# Tristan's profile for AI matching
CANDIDATE_PROFILE = """
Name: Tristan Gooding
Degree: BS in Artificial Intelligence, Purdue University Indianapolis (May 2026, Senior)
Location: Martinsville, Indiana. Open to: Indianapolis area, West Lafayette area, or fully remote.

Technical Skills: Python, Java, C/C++, C#, JavaScript, R
Cloud: AWS (EC2), Azure, Google Cloud Platform, Firebase
Tools: REST APIs, SQL, Git, Agile/Scrum, SDLC, Database Design, Automation Scripting
AI/ML: AI/ML Technologies, model evaluation, data pipelines

Experience:
- Consultant Supervisor at SCIPS (Indiana University): Led team operations, automated data tracking/reporting, trained 8 new consultants/year
- Undergraduate Researcher at Purdue Data Mine: Worked with Rolls-Royce engineers on EMC engine data, built Python data pipelines, led 20+ Agile sprints
- Built a full-stack ML model evaluation system (AWS EC2, REST APIs, 85%+ test coverage)
- Built cross-platform mobile app (iOS/Android) with Firebase

Target roles: Software engineering, AI/ML engineering, backend development, data engineering, cloud/DevOps, research engineering, full-stack development. 
Open to entry-level and internship/co-op positions as well as new grad roles.
"""

SEARCH_QUERIES = [
    {"query": "software engineer entry level", "location": "Indianapolis, Indiana, USA"},
    {"query": "AI machine learning engineer", "location": "Indianapolis, Indiana, USA"},
    {"query": "backend developer python", "location": "Indianapolis, Indiana, USA"},
    {"query": "data engineer entry level", "location": "Indianapolis, Indiana, USA"},
    {"query": "software developer new grad", "location": "West Lafayette, Indiana, USA"},
    {"query": "software engineer entry level", "location": "West Lafayette, Indiana, USA"},
    {"query": "AI machine learning engineer remote", "location": "USA"},
    {"query": "python developer remote entry level", "location": "USA"},
    {"query": "backend engineer remote new grad", "location": "USA"},
    {"query": "cloud engineer AWS entry level", "location": "Indianapolis, Indiana, USA"},
]

def scrape_jobs(jsearch_key, log_fn):
    """Fetch jobs from JSearch API (RapidAPI)"""
    all_jobs = []
    seen_ids = set()

    headers = {
        "X-RapidAPI-Key": jsearch_key,
        "X-RapidAPI-Host": "jsearch.p.rapidapi.com"
    }

    for query_config in SEARCH_QUERIES:
        query = query_config["query"]
        location = query_config["location"]
        log_fn(f"Searching: '{query}' in {location}...")
        
        try:
            params = {
                "query": f"{query} in {location}",
                "page": "1",
                "num_pages": "2",
                "date_posted": "month",
                "employment_types": "FULLTIME,PARTTIME,CONTRACTOR,INTERN"
            }
            resp = requests.get(
                "https://jsearch.p.rapidapi.com/search",
                headers=headers,
                params=params,
                timeout=15
            )
            resp.raise_for_status()
            data = resp.json()

            for job in data.get("data", []):
                job_id = job.get("job_id", "")
                if job_id in seen_ids:
                    continue
                seen_ids.add(job_id)

                # Parse salary
                sal_min = job.get("job_min_salary")
                sal_max = job.get("job_max_salary")
                sal_period = job.get("job_salary_period", "")
                sal_display = ""
                if sal_min and sal_max:
                    if sal_period and sal_period.upper() == "HOUR":
                        sal_display = f"${sal_min:.0f}–${sal_max:.0f}/hr"
                    else:
                        sal_display = f"${sal_min:,.0f}–${sal_max:,.0f}/yr"
                elif sal_min:
                    sal_display = f"${sal_min:,.0f}+"

                # Work type
                is_remote = job.get("job_is_remote", False)
                job_type_raw = job.get("job_employment_type", "")
                if is_remote:
                    work_type = "Remote"
                else:
                    work_type = "Onsite"  # JSearch doesn't always have hybrid, we'll let AI refine

                # Location
                city = job.get("job_city", "")
                state = job.get("job_state", "")
                country = job.get("job_country", "")
                location_str = ", ".join(filter(None, [city, state]))
                if not location_str:
                    location_str = country

                # Coordinates from employer location
                lat = job.get("job_latitude")
                lng = job.get("job_longitude")

                apply_url = job.get("job_apply_link", "")
                company_url = job.get("employer_website", apply_url)

                all_jobs.append({
                    "job_id": job_id,
                    "title": job.get("job_title", ""),
                    "company": job.get("employer_name", ""),
                    "location": location_str,
                    "lat": lat,
                    "lng": lng,
                    "work_type": work_type,
                    "salary_min": int(sal_min) if sal_min else None,
                    "salary_max": int(sal_max) if sal_max else None,
                    "salary_display": sal_display,
                    "description": (job.get("job_description") or "")[:2000],
                    "apply_url": apply_url,
                    "company_url": company_url,
                    "source": "JSearch",
                    "date_posted": job.get("job_posted_at_datetime_utc", ""),
                    "raw_employment_type": job_type_raw
                })

            time.sleep(0.5)  # Be polite to API

        except Exception as e:
            log_fn(f"Error on query '{query}': {e}")
            continue

    log_fn(f"Total unique jobs fetched: {len(all_jobs)}")
    return all_jobs


def match_jobs(jobs, openai_key, log_fn):
    """Use OpenAI GPT-4 to score each job against Tristan's profile"""
    from openai import OpenAI
    client = OpenAI(api_key=openai_key)

    matched = []
    batch_size = 5  # Process in batches to save tokens

    for i in range(0, len(jobs), batch_size):
        batch = jobs[i:i+batch_size]
        log_fn(f"AI matching jobs {i+1}–{min(i+batch_size, len(jobs))} of {len(jobs)}...")

        # Build batch prompt
        jobs_text = ""
        for j, job in enumerate(batch):
            jobs_text += f"""
Job {j+1}:
Title: {job['title']}
Company: {job['company']}
Location: {job['location']}
Work Type: {job['work_type']}
Employment: {job.get('raw_employment_type', '')}
Salary: {job['salary_display'] or 'Not listed'}
Description: {job['description'][:800]}
---"""

        prompt = f"""You are evaluating job listings for this candidate:

{CANDIDATE_PROFILE}

Rate each of the following {len(batch)} jobs on a scale of 0-100 for match quality.
Consider: skill alignment, seniority fit (entry-level/new grad), location fit, role relevance.
Also determine if work type should be corrected to "Remote", "Hybrid", or "Onsite" based on the description.

{jobs_text}

Respond ONLY with a JSON array with {len(batch)} objects, each with:
- "score": integer 0-100
- "reasons": string (2-3 sentence explanation)
- "work_type": "Remote" | "Hybrid" | "Onsite"

Example: [{{"score": 85, "reasons": "Strong Python match...", "work_type": "Hybrid"}}]"""

        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",  # Cost-efficient, fast
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=1500
            )
            text = response.choices[0].message.content.strip()
            # Strip markdown code blocks if present
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            ratings = json.loads(text)

            for j, job in enumerate(batch):
                if j < len(ratings):
                    rating = ratings[j]
                    job["match_score"] = rating.get("score", 50)
                    job["match_reasons"] = rating.get("reasons", "")
                    job["work_type"] = rating.get("work_type", job["work_type"])
                else:
                    job["match_score"] = 50
                    job["match_reasons"] = "Score unavailable"
                matched.append(job)

        except Exception as e:
            log_fn(f"AI matching error on batch {i}: {e}")
            for job in batch:
                job["match_score"] = 50
                job["match_reasons"] = "AI matching failed for this job"
                matched.append(job)

        time.sleep(1)  # Rate limiting

    return matched
