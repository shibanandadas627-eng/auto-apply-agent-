"""Find jobs from free, public sources (no scraping of login-only sites)."""
import hashlib
import html
import re

import requests

from utils import csv

UA = {"User-Agent": "autoapply-agent/1.0 (personal job search tool)"}


def _get(url, headers=None, **kw):
    r = requests.get(url, headers={**UA, **(headers or {})}, timeout=30, **kw)
    r.raise_for_status()
    return r.json()


def clean(t):
    t = html.unescape(t or "")
    t = re.sub(r"<(br|/p|/li|/h\d)\s*/?>", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"[ \t]+", " ", t)
    return re.sub(r"\n\s*\n+", "\n\n", t).strip()


def kind_from_url(url):
    u = (url or "").lower()
    if "greenhouse.io" in u or "gh_jid=" in u:
        return "greenhouse"
    if "lever.co" in u:
        return "lever"
    return "manual"


def make_job(title, company, location, url, source, description, posted=""):
    return {
        "id": hashlib.sha1((url or title + company).encode()).hexdigest()[:12],
        "title": (title or "").strip(), "company": (company or "").strip(),
        "location": (location or "").strip(), "url": url, "source": source,
        "description": clean(description)[:6000], "posted": str(posted or "")[:10],
        "apply_kind": kind_from_url(url),
    }


def from_text(title, company, url, description):
    """Add a job by hand (paste any link + description)."""
    return make_job(title or "Untitled role", company or "Unknown", "", url or "", "manual", description)


# ---------- sources ----------
def fetch_remotive():
    d = _get("https://remotive.com/api/remote-jobs")
    return [make_job(j["title"], j["company_name"], j.get("candidate_required_location", "Remote"),
                     j["url"], "remotive", j.get("description", ""), j.get("publication_date", ""))
            for j in d.get("jobs", [])]


def fetch_arbeitnow():
    d = _get("https://www.arbeitnow.com/api/job-board-api")
    return [make_job(j["title"], j["company_name"],
                     ("Remote, " if j.get("remote") else "") + j.get("location", ""),
                     j["url"], "arbeitnow", j.get("description", ""), j.get("created_at", ""))
            for j in d.get("data", [])]


def fetch_remoteok():
    d = _get("https://remoteok.com/api")
    return [make_job(j.get("position"), j.get("company"), j.get("location") or "Remote",
                     j.get("url"), "remoteok", j.get("description", ""), j.get("date", ""))
            for j in d if isinstance(j, dict) and j.get("position")]


def fetch_greenhouse(board):
    d = _get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true")
    return [make_job(j["title"], board, (j.get("location") or {}).get("name", ""),
                     j["absolute_url"], "greenhouse", j.get("content", ""), j.get("updated_at", ""))
            for j in d.get("jobs", [])]


def fetch_lever(company):
    d = _get(f"https://api.lever.co/v0/postings/{company}?mode=json")
    return [make_job(j["text"], company, (j.get("categories") or {}).get("location", ""),
                     j["hostedUrl"], "lever", j.get("descriptionPlain", ""), "")
            for j in d]


# ---------- search-based sources (need an extra free key, or the visitor's Gemini key) ----------
# These reach LinkedIn, Indeed, Naukri, Internshala and others the legal way: through job-search APIs and
# Google Search, never by logging in to or scraping those sites.
ALL_SOURCES = ["remotive", "arbeitnow", "remoteok", "adzuna", "jsearch", "google_web"]


def search_plan(p, max_queries=3):
    """(role, place) pairs to search for, built from the profile."""
    roles = csv(p.get("roles"))[:max_queries]
    places = csv(p.get("locations")) or csv(p.get("location"))
    place = places[0] if places else ""
    out = []
    for r in roles:
        if p.get("job_type") == "internship" and "intern" not in r:
            r += " internship"
        out.append((r, place))
    return out


def fetch_adzuna(app_id, app_key, country, what, where=""):
    country = country if re.fullmatch(r"[a-z]{2}", country or "") else "in"
    d = _get(f"https://api.adzuna.com/v1/api/jobs/{country}/search/1",
             params={"app_id": app_id, "app_key": app_key, "results_per_page": 40, "what": what,
                     "where": where, "sort_by": "date", "max_days_old": 14})
    return [make_job(j.get("title"), (j.get("company") or {}).get("display_name", ""),
                     (j.get("location") or {}).get("display_name", ""), j.get("redirect_url"),
                     "adzuna", j.get("description", ""), j.get("created", ""))
            for j in d.get("results", [])]


def fetch_jsearch(rapid_key, what, where=""):
    """JSearch (RapidAPI) returns Google for Jobs results: LinkedIn, Indeed, Glassdoor and more."""
    q = f"{what} in {where}" if where else what
    d = _get("https://jsearch.p.rapidapi.com/search",
             headers={"X-RapidAPI-Key": rapid_key, "X-RapidAPI-Host": "jsearch.p.rapidapi.com"},
             params={"query": q, "page": 1, "num_pages": 1, "date_posted": "month"})
    out = []
    for j in d.get("data", []):
        loc = ", ".join(x for x in (j.get("job_city"), j.get("job_state"), j.get("job_country")) if x)
        if j.get("job_is_remote"):
            loc = ("Remote, " + loc).strip(", ")
        out.append(make_job(j.get("job_title"), j.get("employer_name"), loc, j.get("job_apply_link"),
                            "jsearch/" + str(j.get("job_publisher") or "web").lower(),
                            j.get("job_description", ""), j.get("job_posted_at_datetime_utc", "")))
    return out


def fetch_google_web(settings, what, where=""):
    """Ask Gemini to search Google for open listings on the big job sites and give back the links."""
    if settings.get("provider") != "gemini" or not settings.get("api_key") or not settings.get("model"):
        raise RuntimeError("needs a connected Gemini key")
    from llm import GEMINI_BASE, LLMError, _post, gemini_candidates
    place = f" in {where}" if where else ""
    prompt = (f"Use Google Search to find currently open job listings for: {what}{place}. "
              "Look on linkedin.com/jobs, internshala.com, naukri.com, indeed.com, glassdoor.com, "
              "wellfound.com and company career pages. Return up to 10 lines, one per listing, in exactly "
              "this format: TITLE | COMPANY | LOCATION | URL | one-sentence summary. "
              "Only include listings and URLs that appeared in the search results. "
              "No other text, no numbering, no markdown.")
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "tools": [{"google_search": {}}]}
    # Free keys have very small Google Search allowances that differ per model, so try a few models.
    models = [settings["model"]]
    try:
        others = [m for m in gemini_candidates(settings["api_key"]) if m != settings["model"]]
        models += sorted(others, key=lambda m: "lite" in m)[:3]      # full Flash models first
    except Exception:
        pass
    data, last = None, None
    for m in models:
        try:
            data = _post(f"{GEMINI_BASE}/models/{m}:generateContent", retries=1,
                         headers={"x-goog-api-key": settings["api_key"]}, json=body)
            break
        except LLMError as e:
            last = e
    if data is None:
        raise last or RuntimeError("no Gemini model answered")
    try:
        text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError):
        return []
    out = []
    for line in text.splitlines():
        parts = [x.strip(" *`") for x in line.split("|")]
        if len(parts) < 5 or not re.match(r"^https?://\S+$", parts[3]):
            continue
        host = re.sub(r"^https?://(www\.)?", "", parts[3]).split("/")[0]
        out.append(make_job(parts[0], parts[1], parts[2], parts[3], "web/" + host, parts[4]))
    return out


def fetch_extra(p, settings=None, keys=None):
    """Search-based sources chosen in the profile. Returns (jobs, errors)."""
    keys, settings = keys or {}, settings or {}
    found, errors = [], []
    plan = search_plan(p)
    if not plan:
        return found, errors
    src = p.get("sources", [])
    if "adzuna" in src:
        if keys.get("adzuna_id") and keys.get("adzuna_key"):
            for what, where in plan:
                try:
                    found += fetch_adzuna(keys["adzuna_id"], keys["adzuna_key"],
                                          p.get("adzuna_country", "in"), what, where)
                except Exception as e:
                    errors.append(f"adzuna '{what}': {str(e)[:120]}")
        else:
            errors.append("adzuna: add your free Adzuna app id and key in Setup")
    if "jsearch" in src:
        if keys.get("rapidapi_key"):
            for what, where in plan[:2]:      # the free tier is small, so only two searches
                try:
                    found += fetch_jsearch(keys["rapidapi_key"], what, where)
                except Exception as e:
                    errors.append(f"jsearch '{what}': {str(e)[:120]}")
        else:
            errors.append("jsearch: add your free RapidAPI key in Setup")
    if "google_web" in src:
        for what, where in plan:
            try:
                found += fetch_google_web(settings, what, where)
            except Exception as e:
                if "429" in str(e):
                    errors.append("google_web: Gemini's free Google Search limit is used up for now. The other "
                                  "sources still ran. Try again in a few minutes or tomorrow.")
                    break                      # more queries would hit the same limit
                errors.append(f"google_web '{what}': {str(e)[:120]}")
    return found, errors


# ---------- filtering ----------
def _role_match(role, title, text):
    """All words of the role appear anywhere, or at least half of them appear in the title."""
    words = role.split()
    if not words:
        return False
    return all(w in text for w in words) or sum(w in title for w in words) * 2 >= len(words)


def wanted(job, p):
    text = (job["title"] + " " + job["description"][:1500]).lower()
    title = job["title"].lower()
    roles = csv(p["roles"])
    if roles and not any(_role_match(r, title, text) for r in roles):
        return False
    if any(a in title for a in csv(p["avoid"])):
        return False
    must = csv(p["must_have"])
    if must and not any(m in text for m in must):
        return False
    jt = p.get("job_type", "any")
    if jt == "internship" and "intern" not in text:
        return False
    if jt == "full-time" and "intern" in title:
        return False
    locs, loc = csv(p["locations"]), job["location"].lower()
    if locs or p.get("remote_ok"):
        ok = (p.get("remote_ok") and ("remote" in loc or "remote" in text[:300] or "anywhere" in loc)) \
            or any(l in loc for l in locs) or not loc
        if not ok:
            return False
    return True


def fetch_all(p, settings=None, keys=None):
    found, errors = [], []
    jobs_list = []
    fetchers = {"remotive": fetch_remotive, "arbeitnow": fetch_arbeitnow, "remoteok": fetch_remoteok}
    for name in p.get("sources", []):
        if name in fetchers:
            try:
                jobs_list += fetchers[name]()
            except Exception as e:
                errors.append(f"{name}: {e}")
    for b in csv(p.get("greenhouse")):
        try:
            jobs_list += fetch_greenhouse(b)
        except Exception as e:
            errors.append(f"greenhouse/{b}: {e}")
    for c in csv(p.get("lever")):
        try:
            jobs_list += fetch_lever(c)
        except Exception as e:
            errors.append(f"lever/{c}: {e}")
    extra, extra_err = fetch_extra(p, settings, keys)
    jobs_list += extra
    errors += extra_err
    seen = set()
    for j in jobs_list:
        if j["id"] in seen or not j["url"]:
            continue
        seen.add(j["id"])
        if wanted(j, p):
            found.append(j)
    return found, errors
