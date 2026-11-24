import hashlib
import json
import re
import time

import pandas as pd
import streamlit as st

import cv as cvmod
import jobs
import llm
import logic
import matcher
import store
import tailor
from llm import LLMError
from logic import STATUSES, SAFE_NAME, friendly, valid_url
from utils import DEFAULT_PROFILE, csv

st.set_page_config(page_title="AutoApply Agent", page_icon="🧭", layout="wide")
ss = st.session_state

# Everything below lives only in this visitor's browser session. Nothing is saved on the server.
ss.setdefault("settings", {"provider": "gemini", "api_key": "", "model": "", "base_url": ""})
ss.setdefault("connected", False)
ss.setdefault("cv_text", "")
ss.setdefault("cv_sig", None)
ss.setdefault("profile", dict(DEFAULT_PROFILE))
ss.setdefault("profile_saved", False)
ss.setdefault("jobs", {})
ss.setdefault("delay", 3)
ss.setdefault("tz", "UTC")
ss.setdefault("funnel", None)
ss.setdefault("extra_keys", {"adzuna_id": "", "adzuna_key": "", "rapidapi_key": ""})


def flash(kind, msg):
    ss["_flash"] = (kind, msg)


# ---------------------------------------------------------------- actions
def connect(provider, key):
    key = key.strip()
    if not key:
        raise LLMError("Paste your API key first.")
    if provider == "gemini":
        cands, base = llm.gemini_candidates(key), ""
    else:
        cands, base = llm.groq_candidates(key), llm.GROQ_BASE
    if not cands:
        raise LLMError("No usable AI model was found for this key.")
    last = None
    for model in cands[:5]:
        s = {"provider": provider, "api_key": key, "model": model, "base_url": base}
        try:
            llm.ask(s, "Reply with the single word OK.", retries=1)
            return s
        except LLMError as e:
            last = e
            if "api key not valid" in str(e).lower():
                break
    raise last


@st.cache_data(ttl=3600, show_spinner=False)
def listings(kind, name=""):
    """Public job listings only (never personal data), cached for an hour for everyone."""
    if kind == "remotive":
        return jobs.fetch_remotive()
    if kind == "arbeitnow":
        return jobs.fetch_arbeitnow()
    if kind == "remoteok":
        return jobs.fetch_remoteok()
    if kind == "greenhouse":
        return jobs.fetch_greenhouse(name)
    return jobs.fetch_lever(name)


def run_search(progress, note):
    p = ss.profile
    tasks = [(s, "") for s in p["sources"] if s in ("remotive", "arbeitnow", "remoteok")]
    tasks += [("greenhouse", b) for b in csv(p["greenhouse"]) if SAFE_NAME.match(b)]
    tasks += [("lever", b) for b in csv(p["lever"]) if SAFE_NAME.match(b)]
    pool, errors = [], []
    for kind, name in tasks:
        note.write(f"Reading {kind} {name}...")
        try:
            pool += listings(kind, name)
        except Exception as e:  # a source can be down; keep going
            errors.append(f"{kind} {name}: {str(e)[:120]}")
    note.write("Searching Google, LinkedIn, Naukri, Internshala and other sites through search...")
    extra, extra_err = jobs.fetch_extra(p, ss.settings, ss.extra_keys)
    pool += extra
    errors += extra_err
    ss.funnel = jobs.funnel([j for j in pool if j["url"]], p)
    added, seen = 0, set()
    for j in pool:
        if j["id"] in seen or not j["url"] or j["id"] in ss.jobs:
            continue
        seen.add(j["id"])
        if jobs.wanted(j, p):
            ss.jobs[j["id"]] = logic.new_job(j)
            added += 1
    todo = [j for j in ss.jobs.values() if j["score"] is None][: int(p["max_per_run"])]
    note.write(f"Found {added} new matching jobs. Scoring {len(todo)} of them against your CV...")
    for n, j in enumerate(todo, 1):
        try:
            s = matcher.score(j, p, ss.cv_text, ss.settings)
        except LLMError as e:
            errors.append(friendly(e))
            break
        j["score"], j["reason"], j["scored_at"] = s["score"], s["reason"], logic.now_iso()
        j["status"] = "shortlisted" if s["score"] >= int(p["min_score"]) else "low"
        progress.progress(n / max(len(todo), 1), text=f"Scored {n} of {len(todo)}")
        time.sleep(ss.delay)
    return added, len(todo), errors


def prepare(j):
    out = tailor.prepare(j, ss.profile, ss.cv_text, ss.settings)
    j["cover_letter"], j["highlights"], j["letter_at"] = out["cover_letter"], out["highlights"], logic.now_iso()
    if j["status"] in ("new", "low", "shortlisted"):
        j["status"] = "ready"
    ss[f"cl_{j['id']}"] = out["cover_letter"]


# ---------------------------------------------------------------- remember on this device
# Saved in THIS browser's own storage (localStorage). It never goes to our server.
DEVICE_KEYS = ["aa_provider", "aa_key", "aa_model", "aa_state", "aa_adzuna_id", "aa_adzuna_key", "aa_rapid"]
ss.setdefault("remember", True)
ss.setdefault("device_loaded", False)
ss.setdefault("saved_hash", "")

with st.sidebar:
    stored = store.local_store("get", "init", keys=DEVICE_KEYS)
if stored is not None and stored.get("tz"):
    ss.tz = str(stored["tz"])
if stored is not None and not ss.device_loaded:
    ss.device_loaded = True
    items = stored.get("items") or {}
    if items.get("aa_state"):
        try:
            _prof, _cvt, _jb = logic.import_state(items["aa_state"])
            ss.profile, ss.cv_text, ss.jobs = _prof, _cvt, _jb
            ss.profile_saved = bool(_prof["name"].strip() and _prof["roles"].strip())
        except Exception:
            pass
    ss.extra_keys = {"adzuna_id": items.get("aa_adzuna_id") or "", "adzuna_key": items.get("aa_adzuna_key") or "",
                     "rapidapi_key": items.get("aa_rapid") or ""}
    if items.get("aa_key"):
        _prov = items.get("aa_provider") if items.get("aa_provider") in ("gemini", "groq") else "gemini"
        _model = items.get("aa_model") or ""
        _model = _model if re.fullmatch(r"[A-Za-z0-9._-]{1,80}", _model) else ""
        _s = {"provider": _prov, "api_key": items["aa_key"], "model": _model,
              "base_url": llm.GROQ_BASE if _prov == "groq" else ""}
        with st.spinner("Reconnecting your saved AI key..."):
            try:
                llm.ask(_s, "Reply with the single word OK.", retries=1)
                ss.settings, ss.connected = _s, True
            except LLMError:
                try:   # the model may have been retired: pick a fresh one
                    ss.settings, ss.connected = connect(_prov, items["aa_key"]), True
                except LLMError as e:
                    flash("warning", "Your saved key stopped working: " + friendly(e) +
                          " Paste a new key in Setup.")


def forget_device():
    ss.remember = False
    ss.connected = False
    ss.settings = {"provider": "gemini", "api_key": "", "model": "", "base_url": ""}
    ss.cv_text, ss.cv_sig, ss.jobs = "", None, {}
    ss.profile, ss.profile_saved = dict(DEFAULT_PROFILE), False
    ss.extra_keys = {"adzuna_id": "", "adzuna_key": "", "rapidapi_key": ""}
    for k in [k for k in ss if k.startswith("cl_")]:
        del ss[k]


# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.markdown("### Your progress")
    st.markdown(f"{'✅' if ss.connected else '⬜'} 1. Connect free AI")
    st.markdown(f"{'✅' if ss.cv_text else '⬜'} 2. Upload CV")
    st.markdown(f"{'✅' if ss.profile_saved else '⬜'} 3. Say what you want")
    st.markdown(f"{'✅' if ss.jobs else '⬜'} 4. Find jobs")
    st.divider()
    st.markdown("### Save your work")
    st.caption("Nothing is stored on our server. Download your progress file and load it next time.")
    st.download_button("⬇️ Download progress file", logic.export_state(ss.profile, ss.cv_text, ss.jobs),
                       file_name="autoapply-progress.json", mime="application/json")
    up = st.file_uploader("Load a progress file", type=["json"], key="restore_file")
    if up and st.button("Load it"):
        try:
            prof, cvt, jb = logic.import_state(up.getvalue().decode("utf-8"))
            ss.profile, ss.cv_text, ss.jobs, ss.profile_saved = prof, cvt, jb, True
            for k in [k for k in ss if k.startswith("cl_")]:
                del ss[k]
            flash("success", f"Loaded {len(jb)} jobs. Reconnect your AI key in Setup (keys are never saved).")
            st.rerun()
        except Exception:
            st.error("That doesn't look like a progress file from this app.")
    st.divider()
    st.markdown("### Privacy")
    st.button("🗑️ Forget my data on this device", on_click=forget_device)
    st.caption("If 'Remember on this device' is ticked in Setup, your key, CV and jobs are saved in this "
               "browser only (never on our server). Untick it or use the button above on a shared computer. "
               "To score jobs and write letters, your CV text and the job text are sent to the AI provider "
               "you choose, under that provider's terms (Google's free tier may use data to improve its "
               "products). Don't upload anything you aren't comfortable sharing with them.")

# ---------------------------------------------------------------- header
st.title("🧭 AutoApply Agent")
st.caption("Finds jobs and internships, scores them against your CV, and writes a tailored cover letter "
           "for each. Free. Bring your own free AI key.")
if "_flash" in ss:
    kind, msg = ss.pop("_flash")
    getattr(st, kind)(msg)

t_setup, t_find, t_review, t_track, t_report, t_ask, t_auto = st.tabs(
    ["1 · Setup", "2 · Find jobs", "3 · Review", "4 · Tracker", "5 · Today's report", "6 · Ask AI",
     "7 · Run daily by itself"])

# ---------------------------------------------------------------- SETUP
with t_setup:
    st.subheader("Step 1 · Connect a free AI key")
    prov = st.radio("AI provider", ["gemini", "groq"], horizontal=True,
                    format_func=lambda k: {"gemini": "Google Gemini (recommended)", "groq": "Groq"}[k])
    st.info({"gemini": "Get a free key: open aistudio.google.com/apikey, sign in, click Create API key, copy it.",
             "groq": "Get a free key: open console.groq.com/keys, sign in, click Create API Key, copy it."}[prov])
    key = st.text_input("Paste your API key here", type="password")
    st.checkbox("Remember on this device so I don't have to paste it again "
                "(don't tick on a shared computer)", key="remember")
    if ss.connected:
        st.success(f"Connected (model: {ss.settings['model']}).")
    if st.button("Connect", type="primary"):
        with st.spinner("Checking your key and picking a working model..."):
            try:
                ss.settings = connect(prov, key)
                ss.connected = True
                flash("success", f"Connected using {ss.settings['model']}.")
                st.rerun()
            except LLMError as e:
                ss.connected = False
                st.error(friendly(e))

    st.subheader("Step 2 · Upload your CV")
    f = st.file_uploader("PDF is best. DOCX or TXT also work. Max 5 MB.", type=["pdf", "docx", "txt"])
    if f is not None and ss.cv_sig != (f.name, f.size):
        try:
            text = cvmod.extract_text(f.getvalue(), f.name)
        except Exception:
            text = ""
        if len(text) < 200:
            st.error("Could not read enough text from this file. Try a text-based PDF or a DOCX.")
        else:
            ss.cv_text, ss.cv_sig = text[:30000], (f.name, f.size)
            flash("success", f"CV read ({len(text)} characters).")
            st.rerun()
    if ss.cv_text:
        with st.expander("What the agent reads from your CV"):
            st.text(ss.cv_text[:3000])

    st.subheader("Step 3 · What do you want?")
    p = ss.profile
    with st.form("profile_form"):
        a, b, c = st.columns(3)
        name = a.text_input("Your full name (signs your cover letters)", p["name"])
        city = b.text_input("Your city", p["location"])
        jt = c.selectbox("Looking for", ["any", "internship", "full-time"],
                         index=["any", "internship", "full-time"].index(p["job_type"]))
        roles = st.text_input("Roles you want, separated by commas", p["roles"],
                              placeholder="python developer, data analyst, ai intern")
        locs = st.text_input("Places you accept, separated by commas", p["locations"],
                             placeholder="bengaluru, bangalore, india")
        remote = st.checkbox("Remote jobs are fine", p["remote_ok"])
        avoid = st.text_input("Skip job titles containing", p["avoid"])
        must = st.text_input("Must mention one of these words (optional)", p["must_have"])
        d, e = st.columns(2)
        minscore = d.slider("Shortlist jobs scoring at least", 0, 100, int(p["min_score"]))
        perrun = e.slider("Jobs to score per search (free limits are small)", 5, 25, int(p["max_per_run"]))
        srcs = st.multiselect(
            "Job sources", jobs.ALL_SOURCES, [s for s in p["sources"] if s in jobs.ALL_SOURCES],
            help="google_web searches Google for LinkedIn, Naukri, Internshala and other listings (needs a "
                 "Gemini key). adzuna and jsearch need the optional free keys below.")
        country = st.text_input("Adzuna country code (in, gb, us, au, ca, de, ...)", p.get("adzuna_country", "in"))
        g, h = st.columns(2)
        gh = g.text_input("Greenhouse company boards (optional)", p["greenhouse"],
                          help="From boards.greenhouse.io/NAME use NAME")
        lv = h.text_input("Lever companies (optional)", p["lever"], help="From jobs.lever.co/NAME use NAME")
        if st.form_submit_button("Save"):
            ss.profile = {**ss.profile, "name": name, "location": city, "roles": roles, "must_have": must, "avoid": avoid,
                          "locations": locs, "remote_ok": remote, "job_type": jt, "min_score": minscore,
                          "max_per_run": perrun, "sources": srcs, "greenhouse": gh, "lever": lv,
                          "adzuna_country": country.strip().lower()[:2] or "in"}
            ss.profile_saved = bool(name.strip() and roles.strip())
            flash("success" if ss.profile_saved else "warning",
                  "Saved." if ss.profile_saved else "Saved, but please fill in your name and at least one role.")
            st.rerun()

    st.subheader("Optional · More job sources")
    st.caption("Both are free. They let the agent search LinkedIn, Indeed, Glassdoor and Naukri-style listings "
               "through proper APIs. Leave empty to skip.")
    k1, k2, k3 = st.columns(3)
    ek = ss.extra_keys
    ek["adzuna_id"] = k1.text_input("Adzuna app id (developer.adzuna.com)", ek["adzuna_id"]).strip()
    ek["adzuna_key"] = k2.text_input("Adzuna app key", ek["adzuna_key"], type="password").strip()
    ek["rapidapi_key"] = k3.text_input("RapidAPI key for JSearch (rapidapi.com)", ek["rapidapi_key"],
                                       type="password").strip()

# ---------------------------------------------------------------- FIND
with t_find:
    ready = ss.connected and bool(ss.cv_text) and bool(ss.profile["roles"].strip())
    if not ready:
        st.warning("Finish the Setup tab first: connect your key, upload your CV, and enter the roles you want.")
    if st.button("🔎 Search and score jobs", disabled=not ready, type="primary"):
        bar, note = st.progress(0.0, text="Starting..."), st.empty()
        added, scored, errs = run_search(bar, note)
        bar.empty()
        note.success(f"Done. {added} new jobs found, {scored} scored. Open the Review tab.")
        for er in errs:
            st.warning(er)
        fn = ss.get("funnel")
        if fn:
            src = ", ".join(f"{k}: {v}" for k, v in fn["by_source"].items()) or "none"
            st.info(f"**What the agent saw:** {fn['total']} listings fetched ({src}). "
                    f"{fn['passed']} matched all your filters.")
            if fn["total"] == 0:
                st.warning("No source returned anything. Check your internet or the warnings above, and that your "
                           "sources are selected in Setup (and Adzuna keys are filled).")
            elif fn["passed"] == 0 or added == 0:
                if fn["passed"] and not added:
                    st.write("The matching jobs were already in your list, so they are not new. Open the Review tab.")
                for name, n in sorted(fn["without"].items(), key=lambda x: -x[1]):
                    st.write(f"- Without the **{name}** filter you would get **{n}** jobs.")
                st.caption("Fix it in Setup: add more places, set Looking for to 'any', or use broader roles.")
    st.markdown("---")
    st.markdown("**Found a job somewhere else** (LinkedIn, Internshala, a company page)? Add it by hand:")
    with st.form("manual", clear_on_submit=True):
        m1, m2, m3 = st.columns(3)
        mt, mc, mu = m1.text_input("Job title"), m2.text_input("Company"), m3.text_input("Link to apply (https://...)")
        md = st.text_area("Paste the job description", height=150)
        if st.form_submit_button("Add job"):
            if len(md.strip()) < 50:
                st.error("Paste the full job description (at least a few lines).")
            else:
                j = jobs.from_text(mt, mc, mu if valid_url(mu) else "", md[:6000])
                j["id"] = j["id"] if j["url"] else "m" + hashlib.sha1(md.encode()).hexdigest()[:11]
                if j["id"] in ss.jobs:
                    st.info("That job is already in your list.")
                else:
                    ss.jobs[j["id"]] = logic.new_job(j)
                    st.success("Added. Use 'Search and score jobs' to score it.")

# ---------------------------------------------------------------- REVIEW
with t_review:
    show = st.multiselect("Show", STATUSES, default=["shortlisted", "ready"])
    rows = sorted((j for j in ss.jobs.values() if j["status"] in show),
                  key=lambda j: -1 if j["score"] is None else -j["score"])
    st.caption(f"{len(rows)} jobs")
    if ss.cv_text and ss.connected:
        top = [j for j in rows if j["status"] == "shortlisted"][:5]
        if top and st.button(f"✍️ Write cover letters for the top {len(top)} shortlisted jobs"):
            bar = st.progress(0.0)
            for n, j in enumerate(top, 1):
                try:
                    prepare(j)
                except LLMError as e:
                    st.warning(friendly(e))
                    break
                bar.progress(n / len(top))
                time.sleep(ss.delay)
            st.rerun()
    for j in rows:
        score = "unscored" if j["score"] is None else f"{j['score']}/100"
        with st.expander(f"{score} · {j['title']} · {j['company']} · {j['status']}",
                         expanded=(ss.get("open_job") == j["id"])):
            st.write(j["reason"] or "Not scored yet. Run a search to score it.")
            st.caption(f"{j['location']}  ·  source: {j['source']}")
            if valid_url(j["url"]):
                st.link_button("Open job page", j["url"])
            if st.button("✍️ Write my cover letter", key=f"prep{j['id']}", disabled=not (ss.cv_text and ss.connected)):
                try:
                    with st.spinner("Writing..."):
                        prepare(j)
                    ss["open_job"] = j["id"]
                    st.rerun()
                except LLMError as e:
                    st.error(friendly(e))
            if j["highlights"]:
                try:
                    h = json.loads(j["highlights"])
                    st.markdown("**Put these first on your CV:** " + " · ".join(map(str, h.get("bullets", []))))
                    st.markdown("**Keywords to use:** " + ", ".join(map(str, h.get("keywords", []))))
                except ValueError:
                    pass
            if j["cover_letter"]:
                k = f"cl_{j['id']}"
                if k not in ss:
                    ss[k] = j["cover_letter"]
                text = st.text_area("Cover letter (edit it freely)", key=k, height=260)
                j["cover_letter"] = text
                if "[FILL IN]" in text:
                    st.warning("Fill in the [FILL IN] parts before you send this.")
                if st.checkbox("Show copy-friendly view", key=f"cp{j['id']}"):
                    st.code(text, language=None, wrap_lines=True)
                st.download_button("⬇️ Download letter", text, file_name=f"cover_letter_{j['company'] or 'job'}.txt",
                                   key=f"dl{j['id']}")
            c1, c2 = st.columns(2)
            if c1.button("✅ I applied", key=f"ok{j['id']}"):
                logic.set_status(j, "applied")
                st.rerun()
            if c2.button("🚫 Skip", key=f"sk{j['id']}"):
                logic.set_status(j, "skipped")
                st.rerun()

# ---------------------------------------------------------------- TRACKER
with t_track:
    if not ss.jobs:
        st.info("Nothing here yet.")
    else:
        counts = pd.Series([j["status"] for j in ss.jobs.values()]).value_counts()
        st.write("  ".join(f"**{k}**: {v}" for k, v in counts.items()))
        df = pd.DataFrame(list(ss.jobs.values()))[["id", "title", "company", "score", "status", "notes", "url"]]
        edited = st.data_editor(
            df, hide_index=True, disabled=["id", "title", "company", "score", "url"],
            column_config={"status": st.column_config.SelectboxColumn(options=STATUSES),
                           "url": st.column_config.LinkColumn()})
        if st.button("Save tracker changes"):
            for _, r in edited.iterrows():
                j = ss.jobs.get(r["id"])
                if j and r["status"] in STATUSES:
                    if r["status"] != j["status"]:
                        logic.set_status(j, r["status"])
                    j["notes"] = str(r["notes"] or "")
            st.success("Saved.")
        export = pd.DataFrame(list(ss.jobs.values())).drop(columns=["description", "cover_letter", "highlights"])
        st.download_button("⬇️ Export to CSV", export.to_csv(index=False), file_name="applications.csv")

# ---------------------------------------------------------------- REPORT
with t_report:
    tz = logic.safe_tz(ss.tz)
    today = logic.local_date(logic.now_iso(), tz)
    c0, c1 = st.columns(2)
    day = c0.date_input("Day", value=today, max_value=today)
    goal = int(c1.number_input("Your daily goal (applications)", 0, 50, int(ss.profile.get("daily_goal", 5))))
    ss.profile["daily_goal"] = goal
    if not ss.jobs:
        st.info("Nothing to report yet. Find some jobs first.")
    else:
        rep = logic.build_report(ss.jobs, day, tz)
        m = st.columns(5)
        m[0].metric("Jobs found", len(rep["found"]))
        m[1].metric("Jobs scored", len(rep["scored"]))
        m[2].metric("Letters written", len(rep["letters"]))
        m[3].metric("Applied", len(rep["applied"]))
        m[4].metric("Interviews / rejections", len(rep["replies"]))
        if goal > 0:
            st.progress(min(len(rep["applied"]) / goal, 1.0),
                        text=f"{len(rep['applied'])} of {goal} applications on {day.isoformat()}")

        def show(title, items):
            st.markdown(f"**{title}**")
            if not items:
                st.caption("None")
            for text, url in items:
                st.markdown(f"- [{text}]({url})" if logic.valid_url(url) else f"- {text}")

        def label(j, extra=""):
            sc = "" if j["score"] is None else f" · score {j['score']}"
            return f"{j['title']} at {j['company']}{sc}{extra}", j["url"]

        show("Applied", [label(j) for j in rep["applied"]])
        show("Interviews and rejections", [label(j, f" · {j['status']}") for j in rep["replies"]])
        show("Best matches still waiting for you", [label(j) for j in rep["pending"]])
        show("Time to follow up (applied 7+ days ago)", [label(j, f" · {n} days ago") for j, n in rep["follow"]])
        st.download_button("⬇️ Download this report", rep["markdown"], file_name=f"report-{day.isoformat()}.md")
        st.caption(f"Times shown in your time zone ({ss.tz}). Jobs saved before this feature existed have no "
                   "date, so they don't appear in daily counts. The report builds when you open this tab. "
                   "The website can't send it to you by itself.")

# ---------------------------------------------------------------- ASK
with t_ask:
    st.write("Paste any application question (for example, \"Why do you want this internship?\") and get a "
             "draft answer based only on your CV. Always read it before using it.")
    options = {"(no specific job)": None, **{f"{j['title']} · {j['company']}": j["id"] for j in ss.jobs.values()}}
    pick = st.selectbox("For which job?", list(options))
    q = st.text_area("Question", height=100)
    if st.button("Draft answer", disabled=not (q.strip() and ss.cv_text and ss.connected)):
        try:
            job = ss.jobs.get(options[pick]) if options[pick] else None
            with st.spinner("Writing..."):
                ans = tailor.answer(q[:2000], job, ss.profile, ss.cv_text, ss.settings)
            st.text_area("Draft (check it before using)", ans, height=200)
        except LLMError as e:
            st.error(friendly(e))

# ---------------------------------------------------------------- AUTOMATION
with t_auto:
    st.write("This website only works while you have it open. To make the agent search **every day by itself**, "
             "it runs on GitHub (free) and emails you the best matches with cover letters already written. "
             "You still read each letter and press apply yourself.")
    st.markdown("""**One-time setup (about 10 minutes)**
1. Upload `auto_run.py`, `requirements-agent.txt` and the `.github` folder to your repository (they are in this update).
2. In your repository open **Settings → Secrets and variables → Actions → New repository secret** and add the secrets below.
3. Open the **Actions** tab, choose **Daily job search**, click **Run workflow** to test it. After that it runs every morning.""")
    st.markdown("**Secrets to add**")
    st.markdown("- `AI_PROVIDER`: `gemini` or `groq`\n- `AI_KEY`: your AI key\n"
                "- `AA_PROFILE`: the text in the first box below\n- `AA_CV`: the text in the second box below\n"
                "- `SMTP_USER`, `SMTP_PASS`: your Gmail address and a Gmail **app password** "
                "(Google Account → Security → App passwords)\n- `EMAIL_TO`: where to send the digest\n"
                "- Optional: `ADZUNA_ID`, `ADZUNA_KEY`, `RAPIDAPI_KEY`")
    st.caption("Your repository is public, so use secrets for all of this. Never put these values in a file.")
    st.markdown("**AA_PROFILE** (copy with the icon in the corner)")
    st.code(json.dumps(ss.profile), language="json", wrap_lines=True)
    st.markdown("**AA_CV**")
    st.code(ss.cv_text[:30000] or "(upload your CV in Setup first)", language=None, wrap_lines=True)
    st.warning("Don't share these boxes with anyone. They contain your details.")

# ---------------------------------------------------------------- save to this device (keep at the very end)
if ss.device_loaded:
    if ss.remember:
        _items = {}
        if ss.connected:
            _items.update(aa_provider=ss.settings["provider"], aa_key=ss.settings["api_key"],
                          aa_model=ss.settings["model"])
        _ek = ss.extra_keys
        if _ek["adzuna_id"] or _ek["adzuna_key"] or _ek["rapidapi_key"]:
            _items.update(aa_adzuna_id=_ek["adzuna_id"], aa_adzuna_key=_ek["adzuna_key"], aa_rapid=_ek["rapidapi_key"])
        if ss.cv_text or ss.jobs or ss.profile_saved:
            _items["aa_state"] = logic.export_state(ss.profile, ss.cv_text, ss.jobs, compact=True)
        if _items:
            _h = hashlib.sha1(json.dumps(_items, sort_keys=True).encode()).hexdigest()[:16]
            if _h != ss.saved_hash:
                _ack = store.local_store("set", _h, items=_items)
                if _ack and _ack.get("id") == _h and _ack.get("ok"):
                    ss.saved_hash = _h
    else:
        ss.saved_hash = ""
        store.local_store("clear", "forget", keys=DEVICE_KEYS)
