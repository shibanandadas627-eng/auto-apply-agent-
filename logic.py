"""Pure helpers (no Streamlit, no disk) so they can be tested on their own."""
import json
import re

from utils import DEFAULT_PROFILE

STATUSES = ["new", "low", "shortlisted", "ready", "applied", "interview", "rejected", "skipped"]
SAFE_NAME = re.compile(r"^[a-z0-9_-]{1,60}$")

BLANK = {"id": "", "title": "", "company": "", "location": "", "url": "", "source": "",
         "description": "", "posted": "", "apply_kind": "manual", "score": None, "reason": "",
         "status": "new", "cover_letter": "", "highlights": "", "notes": ""}


def valid_url(u):
    return bool(re.match(r"^https?://[^\s]+$", u or ""))


def new_job(j):
    """A listing from jobs.py -> a tracked job."""
    return {**BLANK, **{k: j[k] for k in BLANK if k in j and k not in ("score", "status")}}


def friendly(e):
    m = str(e)
    low = m.lower()
    if any(x in low for x in ("api key not valid", "invalid api key", "invalid_api_key",
                              "permission_denied", "error 401", "error 403")):
        return ("The AI provider did not accept that key. Create a new key, paste it again, "
                "and make sure there are no spaces before or after it.")
    if "429" in low or "quota" in low or "rate-limited" in low:
        return ("The free limit was reached. Wait a minute and try again. If it keeps happening, "
                "the daily free limit is probably used up, so try again tomorrow.")
    if "timed out" in low or "network error" in low:
        return "The connection was slow or blocked. Try again, or switch to another network."
    return m


def export_state(profile, cv_text, jobs_dict):
    """Progress file for the visitor to keep. Never includes the API key."""
    return json.dumps({"version": 1, "profile": profile, "cv_text": cv_text,
                       "jobs": list(jobs_dict.values())}, indent=1)


def import_state(raw):
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("not a progress file")
    prof = dict(DEFAULT_PROFILE)
    for k, default in DEFAULT_PROFILE.items():
        v = (data.get("profile") or {}).get(k, default)
        if isinstance(default, bool):
            prof[k] = bool(v)
        elif isinstance(default, int):
            prof[k] = int(v) if isinstance(v, (int, float)) else default
        elif isinstance(default, list):
            prof[k] = [str(x) for x in v][:10] if isinstance(v, list) else default
        else:
            prof[k] = str(v)[:500]
    out = {}
    for j in (data.get("jobs") or [])[:2000]:
        if not isinstance(j, dict) or not j.get("id"):
            continue
        row = {}
        for k, default in BLANK.items():
            v = j.get(k, default)
            if k == "score":
                row[k] = int(v) if isinstance(v, (int, float)) else None
            else:
                row[k] = str(v)[:8000] if v is not None else ""
        if row["status"] not in STATUSES:
            row["status"] = "new"
        out[row["id"]] = row
    return prof, str(data.get("cv_text") or "")[:30000], out
