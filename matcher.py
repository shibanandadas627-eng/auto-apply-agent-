"""Ask the AI how well a job fits the CV."""
from llm import ask, parse_json

SYSTEM = ("You are a strict, honest career advisor. You judge fit only from the CV and profile "
          "given. You never invent facts.")


def score(job, profile, cv, settings):
    prompt = f"""Rate how well this candidate fits this job.

CANDIDATE CV:
{cv[:4500]}

CANDIDATE WANTS: roles = {profile['roles']}; job type = {profile['job_type']}

JOB: {job['title']} at {job['company']} ({job['location']})
{job['description'][:3500]}

Return JSON only: {{"score": <integer 0-100>, "reason": "<one or two sentences>", "missing": ["<skill>", ...]}}
Be strict: 85+ means an excellent fit, 60-84 a reasonable fit, below 60 a poor fit."""
    try:
        out = parse_json(ask(settings, prompt, system=SYSTEM, json_mode=True))
        s = max(0, min(100, int(out.get("score", 0))))
        reason = str(out.get("reason", ""))
        miss = out.get("missing") or []
        if miss:
            reason += " Missing: " + ", ".join(map(str, miss[:5]))
        return {"score": s, "reason": reason}
    except (ValueError, TypeError):
        return {"score": 0, "reason": "Could not read the AI reply for this job."}
