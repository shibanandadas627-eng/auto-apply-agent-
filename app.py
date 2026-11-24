import hashlib
import json
import time

import pandas as pd
import streamlit as st

import cv as cvmod
import jobs
import llm
import logic
import matcher
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
    tasks = [(s, "") for s in p["sources"]]
    tasks += [("greenhouse", b) for b in csv(p["greenhouse"]) if SAFE_NAME.match(b)]
    tasks += [("lever", b) for b in csv(p["lever"]) if SAFE_NAME.match(b)]
    pool, errors = [], []
    for kind, name in tasks:
        note.write(f"Reading {kind} {name}...")
        try:
            pool += listings(kind, name)
        except Exception as e:  # a source can be down; keep going
            errors.append(f"{kind} {name}: {str(e)[:120]}")
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
        j["score"], j["reason"] = s["score"], s["reason"]
        j["status"] = "shortlisted" if s["score"] >= int(p["min_score"]) else "low"
        progress.progress(n / max(len(todo), 1), text=f"Scored {n} of {len(todo)}")
        time.sleep(ss.delay)
    return added, len(todo), errors


def prepare(j):
    out = tailor.prepare(j, ss.profile, ss.cv_text, ss.settings)
    j["cover_letter"], j["highlights"] = out["cover_letter"], out["highlights"]
    if j["status"] in ("new", "low", "shortlisted"):
        j["status"] = "ready"
    ss[f"cl_{j['id']}"] = out["cover_letter"]


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
    st.caption("Your key, CV and jobs stay in this browser session and disappear when you close the tab. "
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

t_setup, t_find, t_review, t_track, t_ask = st.tabs(
    ["1 · Setup", "2 · Find jobs", "3 · Review", "4 · Tracker", "5 · Ask AI"])

# ---------------------------------------------------------------- SETUP
with t_setup:
    st.subheader("Step 1 · Connect a free AI key")
    prov = st.radio("AI provider", ["gemini", "groq"], horizontal=True,
                    format_func=lambda k: {"gemini": "Google Gemini (recommended)", "groq": "Groq"}[k])
    st.info({"gemini": "Get a free key: open aistudio.google.com/apikey, sign in, click Create API key, copy it.",
             "groq": "Get a free key: open console.groq.com/keys, sign in, click Create API Key, copy it."}[prov])
    key = st.text_input("Paste your API key here", type="password")
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
    with st.form("profile"):
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
        srcs = st.multiselect("Job sources", ["remotive", "arbeitnow", "remoteok"], p["sources"])
        g, h = st.columns(2)
        gh = g.text_input("Greenhouse company boards (optional)", p["greenhouse"],
                          help="From boards.greenhouse.io/NAME use NAME")
        lv = h.text_input("Lever companies (optional)", p["lever"], help="From jobs.lever.co/NAME use NAME")
        if st.form_submit_button("Save"):
            ss.profile = {"name": name, "location": city, "roles": roles, "must_have": must, "avoid": avoid,
                          "locations": locs, "remote_ok": remote, "job_type": jt, "min_score": minscore,
                          "max_per_run": perrun, "sources": srcs, "greenhouse": gh, "lever": lv}
            ss.profile_saved = bool(name.strip() and roles.strip())
            flash("success" if ss.profile_saved else "warning",
                  "Saved." if ss.profile_saved else "Saved, but please fill in your name and at least one role.")
            st.rerun()

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
        if added == 0 and scored == 0 and not errs:
            st.info("No new matches. Try broader roles or more places in Setup.")
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
                j["status"] = "applied"
                st.rerun()
            if c2.button("🚫 Skip", key=f"sk{j['id']}"):
                j["status"] = "skipped"
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
            df, hide_index=True, use_container_width=True, disabled=["id", "title", "company", "score", "url"],
            column_config={"status": st.column_config.SelectboxColumn(options=STATUSES),
                           "url": st.column_config.LinkColumn()})
        if st.button("Save tracker changes"):
            for _, r in edited.iterrows():
                if r["id"] in ss.jobs and r["status"] in STATUSES:
                    ss.jobs[r["id"]]["status"] = r["status"]
                    ss.jobs[r["id"]]["notes"] = str(r["notes"] or "")
            st.success("Saved.")
        export = pd.DataFrame(list(ss.jobs.values())).drop(columns=["description", "cover_letter", "highlights"])
        st.download_button("⬇️ Export to CSV", export.to_csv(index=False), file_name="applications.csv")

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
