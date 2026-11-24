"""Runs once a day on GitHub Actions: finds jobs, scores them, writes letters, emails you the best ones.
All personal data comes from repository secrets. Nothing about your jobs is printed to the public log."""
import json
import os
import smtplib
import ssl
import sys
import time
from email.message import EmailMessage
from pathlib import Path

import jobs
import llm
import logic
import matcher
import tailor
from llm import LLMError
from utils import DEFAULT_PROFILE

SEEN = Path("seen.json")


def env(name, default=""):
    return (os.environ.get(name) or default).strip()


def connect(provider, key):
    cands = llm.gemini_candidates(key) if provider == "gemini" else llm.groq_candidates(key)
    base = "" if provider == "gemini" else llm.GROQ_BASE
    last = None
    for model in cands[:5]:
        s = {"provider": provider, "api_key": key, "model": model, "base_url": base}
        try:
            llm.ask(s, "Reply with the single word OK.", retries=1)
            return s
        except LLMError as e:
            last = e
    raise last or LLMError("No usable AI model was found for this key.")


def send_email(subject, body):
    user, pw, to = env("SMTP_USER"), env("SMTP_PASS"), env("EMAIL_TO") or env("SMTP_USER")
    if not (user and pw and to):
        sys.exit("SMTP_USER, SMTP_PASS and EMAIL_TO secrets are required (results are never printed in the "
                 "public log).")
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, user, to
    msg.set_content(body)
    with smtplib.SMTP_SSL(env("SMTP_HOST", "smtp.gmail.com"), 465, context=ssl.create_default_context()) as s:
        s.login(user, pw)
        s.send_message(msg)


def main():
    profile = {**DEFAULT_PROFILE, **json.loads(env("AA_PROFILE", "{}"))}
    cv = os.environ.get("AA_CV", "").strip()
    if not cv or not profile.get("roles"):
        sys.exit("AA_CV and AA_PROFILE secrets are required (roles must not be empty).")
    settings = connect(env("AI_PROVIDER", "gemini"), env("AI_KEY"))
    keys = {"adzuna_id": env("ADZUNA_ID"), "adzuna_key": env("ADZUNA_KEY"), "rapidapi_key": env("RAPIDAPI_KEY")}

    seen = set(json.loads(SEEN.read_text())) if SEEN.exists() else set()
    found, errors = jobs.fetch_all(profile, settings, keys)
    fresh = [j for j in found if j["id"] not in seen]
    todo = fresh[: int(profile["max_per_run"])]

    scored = []
    for j in todo:
        try:
            s = matcher.score(j, profile, cv, settings)
        except LLMError as e:
            errors.append(logic.friendly(e))
            break
        j["score"], j["reason"] = s["score"], s["reason"]
        seen.add(j["id"])                      # unscored jobs stay unseen and are tried again tomorrow
        scored.append(j)
        time.sleep(3)
    good = sorted((j for j in scored if j["score"] >= int(profile["min_score"])), key=lambda j: -j["score"])

    for j in good[: int(env("AA_LETTERS", "5"))]:
        try:
            j["cover_letter"] = tailor.prepare(j, profile, cv, settings)["cover_letter"]
        except (LLMError, ValueError) as e:
            errors.append(logic.friendly(e))
            break
        time.sleep(3)

    lines = [f"Searched {len(found)} matching listings, {len(fresh)} new, scored {len(scored)}, "
             f"{len(good)} at or above your minimum score of {profile['min_score']}.", ""]
    for j in good:
        lines += [f"{j['score']}/100  {j['title']} at {j['company']} ({j['location']})",
                  f"Apply: {j['url']}", f"Why: {j['reason']}", ""]
        if j.get("cover_letter"):
            lines += ["--- Draft cover letter (read and edit before sending) ---", j["cover_letter"], ""]
        lines += ["=" * 40, ""]
    if not good:
        lines.append("No strong new matches today.")
    if errors:
        lines += ["", "Notes:"] + [f"- {e}" for e in errors[:10]]
    send_email(f"AutoApply: {len(good)} new matches", "\n".join(lines))
    SEEN.write_text(json.dumps(sorted(seen)[-5000:]))
    print(f"Done: {len(found)} found, {len(scored)} scored, {len(good)} emailed, {len(errors)} notes.")


if __name__ == "__main__":
    main()
