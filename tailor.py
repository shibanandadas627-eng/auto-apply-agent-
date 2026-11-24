"""Write a tailored cover letter and CV highlights for one job."""
import json

from llm import ask, parse_json

RULES = ("Use ONLY facts that appear in the CV or profile. Never invent employers, degrees, skills, "
         "numbers or links. If something needed is missing, write [FILL IN]. "
         "Plain, honest, specific tone. No buzzwords, no flattery.")


def prepare(job, profile, cv, settings):
    prompt = f"""{RULES}

CANDIDATE PROFILE: name={profile['name']}, location={profile['location']}
CV:
{cv[:4500]}

JOB: {job['title']} at {job['company']}
{job['description'][:3500]}

Write JSON only with these keys:
"cover_letter": 150-220 words, starts "Dear Hiring Team,", ends with the candidate's name,
"resume_highlights": 4-6 short bullets from the CV worth putting first for THIS job,
"keywords": up to 8 keywords from the job post that honestly match the CV."""
    out = parse_json(ask(settings, prompt, json_mode=True))
    return {
        "cover_letter": str(out.get("cover_letter", "")).strip(),
        "highlights": json.dumps({"bullets": out.get("resume_highlights", []),
                                  "keywords": out.get("keywords", [])}),
    }


def answer(question, job, profile, cv, settings):
    ctx = f"JOB: {job['title']} at {job['company']}\n{job['description'][:2500]}\n" if job else ""
    prompt = f"""{RULES}

CV:
{cv[:4500]}

{ctx}
Answer this application question in at most 120 words:
{question}"""
    return ask(settings, prompt).strip()
