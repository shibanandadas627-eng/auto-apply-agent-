"""Applies to jobs that list a recruiter email: sends your cover letter and CV from your own mailbox."""
import base64
import io
import re
import smtplib
import ssl
from email.message import EmailMessage
from xml.sax.saxutils import escape

EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
BAD = ("noreply", "no-reply", "donotreply", "do-not-reply", "example.", "sentry", "wixpress", "privacy@",
       "abuse@", "support@", "press@", "sales@", ".png", ".jpg", ".gif", ".svg", ".webp")
HIRING = ("hr", "job", "jobs", "career", "careers", "recruit", "recruiter", "recruitment", "hiring", "talent",
          "apply", "resume", "cv", "people", "internship", "internships", "join", "work")
CONTEXT = ("apply", "send", "mail", "email", "e-mail", "resume", "résumé", "cv", "contact", "forward", "share")


def find_email(text):
    """A recruiter email only when the text clearly says to apply there; otherwise '' (we never guess)."""
    text = text or ""
    for m in EMAIL.finditer(text):
        e = m.group(0).strip(".").lower()
        if any(b in e for b in BAD):
            continue
        local = re.split(r"[._\-+]", e.split("@")[0])
        before = text[max(0, m.start() - 90):m.start()].lower()
        if any(w in local for w in HIRING) or any(c in before for c in CONTEXT):
            return e
    return ""


def cv_pdf(cv_text, name=""):
    """A plain PDF of the CV text, used when you have not supplied your original PDF."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=48, rightMargin=48, topMargin=48, bottomMargin=48,
                            title=f"{name} CV".strip())
    style = getSampleStyleSheet()["BodyText"]
    story = []
    for line in cv_text.splitlines():
        line = line.strip().encode("latin-1", "replace").decode("latin-1")
        story.append(Paragraph(escape(line), style) if line else Spacer(1, 6))
    doc.build(story)
    return buf.getvalue()


def build(job, name, letter, sender, to, cv_bytes, cv_link=""):
    msg = EmailMessage()
    msg["Subject"] = f"Application for {job['title']} - {name}"
    msg["From"] = f"{name} <{sender}>" if name else sender
    msg["To"] = to
    msg["Reply-To"] = sender
    body = letter.strip()
    if cv_link:
        body += f"\n\nCV: {cv_link}"
    msg.set_content(body)
    msg.add_attachment(cv_bytes, maintype="application", subtype="pdf",
                       filename=(re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_") or "CV") + "_CV.pdf")
    return msg


def decode_cv(b64):
    try:
        return base64.b64decode(b64, validate=False) if b64 else b""
    except Exception:
        return b""


def send(msg, user, password, host="smtp.gmail.com"):
    with smtplib.SMTP_SSL(host, 465, context=ssl.create_default_context()) as s:
        s.login(user, password)
        s.send_message(msg)
