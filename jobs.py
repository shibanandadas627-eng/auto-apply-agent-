"""Find jobs from free, public sources (no scraping of login-only sites)."""
import hashlib
import html
import re

import requests

from utils import csv

UA = {"User-Agent": "autoapply-agent/1.0 (personal job search tool)"}


def _get(url, **kw):
    r = requests.get(url, headers=UA, timeout=30, **kw)
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


def fetch_all(p):
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
    seen = set()
    for j in jobs_list:
        if j["id"] in seen or not j["url"]:
            continue
        seen.add(j["id"])
        if wanted(j, p):
            found.append(j)
    return found, errors
