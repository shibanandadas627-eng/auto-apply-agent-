"""Talks to a free AI provider (Gemini or Groq) with the visitor's own key.
Uses plain HTTP, so there is no heavy SDK."""
import json
import re
import time

import requests

GROQ_BASE = "https://api.groq.com/openai/v1"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"


class LLMError(Exception):
    pass


def _post(url, retries=4, **kw):
    for attempt in range(retries):
        try:
            r = requests.post(url, timeout=90, **kw)
        except requests.RequestException as e:
            raise LLMError(f"Network error: {e}")
        if r.status_code in (429, 500, 503) and attempt < retries - 1:
            time.sleep(3 * 2 ** attempt)
            continue
        if r.status_code == 429:
            raise LLMError("AI provider error 429: free limit reached or rate-limited.")
        if not r.ok:
            raise LLMError(f"AI provider error {r.status_code}: {r.text[:300]}")
        return r.json()
    raise LLMError("AI provider error 429: rate-limited.")


def _get(url, **kw):
    try:
        r = requests.get(url, timeout=30, **kw)
    except requests.RequestException as e:
        raise LLMError(f"Network error: {e}")
    if not r.ok:
        raise LLMError(f"AI provider error {r.status_code}: {r.text[:300]}")
    return r.json()


def gemini_candidates(key):
    """Working Gemini text models for this key, best free-tier choice first
    (Flash-Lite has the most generous free limits), newest version first."""
    data = _get(f"{GEMINI_BASE}/models", headers={"x-goog-api-key": key}, params={"pageSize": 200})
    found = []
    for m in data.get("models", []):
        if "generateContent" not in m.get("supportedGenerationMethods", ["generateContent"]):
            continue
        name = m.get("name", "").split("/")[-1]
        mm = re.fullmatch(r"gemini-(\d+(?:\.\d+)*)-flash(-lite)?", name)
        if mm:
            found.append((bool(mm.group(2)), tuple(int(x) for x in mm.group(1).split(".")), name))
    found.sort(key=lambda t: t[1], reverse=True)   # newest first
    found.sort(key=lambda t: not t[0])             # then Flash-Lite first (stable sort)
    return [t[2] for t in found]


def groq_candidates(key):
    data = _get(f"{GROQ_BASE}/models", headers={"Authorization": f"Bearer {key}"})
    ids = [m.get("id", "") for m in data.get("data", [])]
    preferred = [i for i in ("llama-3.3-70b-versatile", "llama-3.1-8b-instant") if i in ids]
    others = [i for i in ids if "llama" in i and i not in preferred
              and not any(x in i for x in ("guard", "whisper", "tts", "tool-use", "vision"))]
    return preferred + others


def ask(settings, prompt, system=None, json_mode=False, retries=4):
    provider = settings.get("provider", "gemini")
    model = settings.get("model", "")
    key = settings.get("api_key", "")
    if not model or not key:
        raise LLMError("Connect your AI key first (Setup tab).")

    if provider == "gemini":
        url = f"{GEMINI_BASE}/models/{model}:generateContent"
        body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if json_mode:
            body["generationConfig"] = {"responseMimeType": "application/json"}
        data = _post(url, retries=retries, headers={"x-goog-api-key": key}, json=body)
        try:
            return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
        except (KeyError, IndexError):
            raise LLMError("The AI returned an empty answer. Try again.")

    msgs = ([{"role": "system", "content": system}] if system else []) + [
        {"role": "user", "content": prompt}]
    body = {"model": model, "messages": msgs, "temperature": 0.4}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    data = _post(f"{GROQ_BASE}/chat/completions", retries=retries,
                 headers={"Authorization": f"Bearer {key}"}, json=body)
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        raise LLMError("The AI returned an empty answer. Try again.")


def parse_json(text):
    text = re.sub(r"```(?:json)?", "", text or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON found")
    return json.loads(text[start:end + 1])
