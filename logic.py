"""Pure helpers (no Streamlit, no disk) so they can be tested on their own."""
import json
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from utils import DEFAULT_PROFILE

STATUSES = ["new", "low", "shortlisted", "ready", "applied", "interview", "rejected", "skipped"]
SAFE_NAME = re.compile(r"^[a-z0-9_-]{1,60}$")

BLANK = {"id": "", "title": "", "company": "", "location": "", "url": "", "source": "",
         "description": "", "posted": "", "apply_kind": "manual", "score": None, "reason": "",
         "status": "new", "cover_letter": "", "highlights": "", "notes": "",
         "found_at": "", "scored_at": "", "letter_at": "", "applied_at": "", "status_at": ""}


# ---------------------------------------------------------------- dates and daily report
def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def safe_tz(name):
    try:
        return ZoneInfo(str(name))
    except Exception:
        return timezone.utc


def local_date(iso, tz):
    if not iso:
        return None
    try:
        dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return dt.astimezone(tz).date()


def set_status(job, status):
    job["status"] = status
    job["status_at"] = now_iso()
    if status == "applied" and not job.get("applied_at"):
        job["applied_at"] = now_iso()


def build_report(jobs_dict, day, tz):
    """Summary of one day's activity. `day` is a datetime.date in the visitor's time zone."""
    rows = list(jobs_dict.values())

    def on(j, field):
        return local_date(j.get(field), tz) == day

    found = [j for j in rows if on(j, "found_at")]
    scored = [j for j in rows if on(j, "scored_at")]
    letters = [j for j in rows if on(j, "letter_at")]
    applied = [j for j in rows if on(j, "applied_at")]
    replies = [j for j in rows if j["status"] in ("interview", "rejected") and on(j, "status_at")]
    pending = sorted((j for j in rows if j["status"] in ("shortlisted", "ready")),
                     key=lambda j: -(j["score"] or 0))[:5]
    follow = []
    for j in rows:
        if j["status"] == "applied":
            d = local_date(j.get("applied_at"), tz)
            if d and (day - d).days >= 7:
                follow.append((j, (day - d).days))
    follow.sort(key=lambda t: -t[1])
    follow = follow[:10]

    def line(j, extra=""):
        sc = "" if j["score"] is None else f" (score {j['score']})"
        return f"- {j['title']} at {j['company']}{sc}{extra}" + (f" - {j['url']}" if j["url"] else "")

    md = [f"# AutoApply daily report - {day.isoformat()}", "",
          f"Jobs found: {len(found)}", f"Jobs scored: {len(scored)}",
          f"Cover letters written: {len(letters)}", f"Applications sent: {len(applied)}",
          f"Interviews / rejections: {len(replies)}", ""]
    for title, items in (("Applied today", [line(j) for j in applied]),
                         ("Replies today", [line(j, f" [{j['status']}]") for j in replies]),
                         ("Best matches still waiting for you", [line(j) for j in pending]),
                         ("Time to follow up (applied 7+ days ago)",
                          [line(j, f" - {n} days ago") for j, n in follow])):
        md += [f"## {title}"] + (items or ["- none"]) + [""]
    return {"found": found, "scored": scored, "letters": letters, "applied": applied,
            "replies": replies, "pending": pending, "follow": follow, "markdown": "\n".join(md)}


def valid_url(u):
    return bool(re.match(r"^https?://[^\s]+$", u or ""))


def new_job(j):
    """A listing from jobs.py -> a tracked job."""
    row = {**BLANK, **{k: j[k] for k in BLANK if k in j and k not in ("score", "status")}}
    row["found_at"] = now_iso()
    return row


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


def export_state(profile, cv_text, jobs_dict, compact=False):
    """Progress data. Never includes the API key.
    compact=True is the smaller version kept in the browser on this device."""
    rows = list(jobs_dict.values())
    if compact:
        rows = [j for j in rows if j.get("score") is not None or j.get("status") != "new"]
        rows.sort(key=lambda j: -(j.get("score") if j.get("score") is not None else -1))
        rows = [{**j, "description": (j.get("description") or "")[:2500]} for j in rows[:150]]
    out = ""
    while True:
        out = json.dumps({"version": 1, "profile": profile, "cv_text": cv_text, "jobs": rows},
                         indent=None if compact else 1)
        if not compact or len(out) <= 2_000_000 or not rows:
            return out
        rows = rows[: len(rows) // 2]


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
