# ollama_client.py
import json
import logging
import urllib.request
import urllib.error
import PyPDF2

from config import OLLAMA_BASE_URL as OLLAMA_URL, OLLAMA_MODEL, CV_PDF_PATH

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a professional email writer for job and internship applications.
Write a short, professional cold-outreach email from a university student
seeking a job, co-op, or internship opportunity. The type of opportunity
will be determined by the job posting provided. Do not assume any specific
major, field, or skill set. Write a genuinely generic professional email.

STRICT RULES:
1. Output ONLY valid JSON in this exact format: {"subject": "...", "body": "..."}
2. Subject line must be under 65 characters and reference the opportunity type.
3. Body must be exactly 3 paragraphs, 2-3 sentences each.
4. Total body word count: 120-170 words maximum.
5. Paragraph 1: Brief intro — university student seeking a job, co-op, or internship opportunity.
6. Paragraph 2: Mention general academic background, problem-solving, teamwork, and eagerness to learn. Be professional, not generic.
7. Paragraph 3: Call to action — mention attached CV, express interest in contributing to their team.
8. Do NOT mention any company name, team name, hiring manager name, or specific internal projects.
9. Do NOT assume or invent any specific field, skill set, or technical knowledge.
10. Do NOT use cliché openers like "I am writing to express my interest."
11. No bullet points. Tone: confident, professional, concise, ATS-friendly.
12. No placeholders like [Company Name] or [Your Name]. Write complete, ready-to-send sentences.
13. Output raw JSON only. No markdown fences. No explanation. No text before or after the JSON.
14. If no job posting is provided, write a genuinely generic email.
    Do NOT invent job requirements, role titles, or company needs.
    Do NOT write phrases like "aligning with your requirements" or "crucial for your role" unless a real job posting was provided."""

FALLBACK = {
    "subject": "Job / Internship Application - CV Attached",
    "body": (
        "I am a university student actively seeking a job or internship opportunity "
        "where I can apply my academic background in a practical environment and "
        "contribute meaningfully from day one.\n\n"
        "Through my studies and project work, I have developed strong problem-solving "
        "skills, the ability to work effectively in team settings, and a commitment "
        "to delivering quality results under real-world conditions.\n\n"
        "I have attached my CV for your review and would welcome the opportunity to "
        "discuss how I can add value to your team. "
        "Thank you for your time and consideration."
    )
}

# Filled at startup by bot.py post_init via load_cv_text()
CV_TEXT = ""

SYSTEM_PROMPT_JOB = """You are a professional email writer for job and internship applications.
Write a short, tailored, professional cold-outreach email from a university student
applying for the position described in the job posting provided.
Tailor everything — field, skills, tone, opportunity type — based only on what the posting says.
Do not assume any specific major or field beyond what is mentioned in the posting.

STRICT RULES:
1. Output ONLY valid JSON in this exact format: {"subject": "...", "body": "..."}
2. Subject line must be under 65 characters and reference the role or field from the posting.
3. Body must be exactly 3 paragraphs, 2-3 sentences each.
4. Total body word count: 120-170 words maximum.
5. Paragraph 1: Reference the specific role or opportunity from the posting. Introduce yourself as a university student applying for it.
6. Paragraph 2: Connect the applicant's REAL skills from the CV text provided directly to the requirements in the posting. Be specific and precise — use only what appears in the CV. Do NOT invent skills, fields, or knowledge not present in the CV.
7. Paragraph 3: Mention attached CV, express interest in contributing, professional closing.
8. Do NOT invent skills not present in the CV text. Do NOT mention company names.
9. Do NOT assume any field or skills beyond what the job posting and CV text provide.
10. Do NOT use cliché openers like "I am writing to express my interest."
11. No bullet points. Tone: confident, professional, concise, ATS-friendly.
12. No placeholders like [Company Name] or [Your Name]. Write complete, ready-to-send sentences.
13. Output raw JSON only. No markdown fences. No explanation. No text before or after the JSON."""


def load_cv_text() -> str:
    """Reads the CV PDF and returns the full extracted text."""
    try:
        text = []
        with open(CV_PDF_PATH, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                extracted = page.extract_text()
                if extracted:
                    text.append(extracted)
        full_text = "\n".join(text).strip()
        if not full_text:
            logger.warning("CV was read but returned empty text. Is it a scanned PDF?")
            return ""
        logger.info(f"CV loaded successfully ({len(full_text)} characters)")
        return full_text
    except FileNotFoundError:
        logger.error(f"CV file not found: {CV_PDF_PATH}")
        return ""
    except Exception as e:
        logger.error(f"Failed to read CV: {e}")
        return ""


def _extract_domain(email: str) -> str:
    try:
        return email.split("@")[1].split(".")[0]
    except Exception:
        return "your organization"


def _parse_model_output(content: str) -> dict:
    """
    Extracts subject and body from model output, handling all known variants:
    - Single object:  {"subject": "...", "body": "..."}
    - Split objects:  {"subject": "..."}{"body": "..."}
    - Nested:         {"email": {"subject": ..., "body": ...}}
    - Capital keys:   {"Subject": ..., "Body": ...}
    - Alt key names:  subject_line, email_body, body_text, content, message
    """
    objects: list[dict] = []
    pos = 0
    decoder = json.JSONDecoder()
    while pos < len(content) and len(objects) < 5:
        idx = content.find("{", pos)
        if idx == -1:
            break
        try:
            obj, end = decoder.raw_decode(content, idx)
            if isinstance(obj, dict):
                objects.append(obj)
            pos = end
        except json.JSONDecodeError:
            pos = idx + 1

    if not objects:
        logger.warning(f"No JSON object in model output. Content: {content[:300]}")
        raise ValueError("No JSON object found in model output")

    merged: dict = {}
    for obj in objects:
        merged.update({k.lower(): v for k, v in obj.items()})

    def _find(d: dict):
        subj = (d.get("subject") or d.get("subject_line")
                or d.get("email_subject") or d.get("subjectline"))
        body = (d.get("body") or d.get("email_body")
                or d.get("body_text") or d.get("content")
                or d.get("message"))
        return subj, body

    subject, body = _find(merged)

    if not subject or not body:
        for v in merged.values():
            if isinstance(v, dict):
                s, b = _find({k.lower(): val for k, val in v.items()})
                subject = subject or s
                body = body or b

    if not subject or not body:
        logger.warning(
            f"Model JSON has unexpected keys {list(merged.keys())}. "
            f"Content (first 400 chars): {content[:400]}"
        )
        raise ValueError(f"Model JSON missing subject/body. Keys found: {list(merged.keys())}")

    return {"subject": str(subject).strip(), "body": str(body).strip()}


async def generate_email(recipient_email: str) -> dict:
    """Returns {"subject": str, "body": str}. Falls back to template on any failure."""
    domain = _extract_domain(recipient_email)
    user_prompt = (
        f"Write a cold-outreach job or internship application email. "
        f"The recipient organization's domain appears to be: {domain}. "
        f"The applicant is a university student seeking a job, co-op, or internship opportunity. "
        f"Do not assume any specific field or skill set. "
        f'Output only JSON: {{"subject": "...", "body": "..."}}'
    )

    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_prompt}
        ],
        "stream": False,
        "options": {
            "temperature": 0.3,
            "top_p": 0.9,
            "num_ctx": 4096
        }
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            raw     = json.loads(resp.read().decode("utf-8"))
            content = raw["message"]["content"].strip()
            return _parse_model_output(content)

    except Exception as e:
        logger.warning(f"Ollama generation failed ({e}), using fallback template")
        return FALLBACK


async def generate_email_from_posting(
    recipient_email: str,
    job_text: str,
    cv_text: str = None,
) -> dict:
    """
    Generates a tailored email using the job posting and CV context.
    cv_text defaults to the module-level CV_TEXT loaded at startup.
    Returns {"subject": str, "body": str}. Falls back to FALLBACK on any failure.
    """
    # Resolve at call time so the startup-loaded CV_TEXT is always used
    if cv_text is None:
        cv_text = CV_TEXT

    domain      = _extract_domain(recipient_email)
    job_snippet = job_text[:2000]
    cv_snippet  = cv_text[:2000]

    user_prompt = (
        f"Organization domain: {domain}\n\n"
        f"JOB POSTING:\n{job_snippet}\n\n"
        f"APPLICANT CV TEXT:\n{cv_snippet}\n\n"
        f"Write a tailored job or internship application email based entirely "
        f"on the posting and CV text above. "
        f'Output only JSON: {{"subject": "...", "body": "..."}}'
    )

    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT_JOB},
            {"role": "user",   "content": user_prompt}
        ],
        "stream": False,
        "options": {
            "temperature": 0.3,
            "top_p": 0.9,
            "num_ctx": 6144
        }
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            raw     = json.loads(resp.read().decode("utf-8"))
            content = raw["message"]["content"].strip()
            return _parse_model_output(content)

    except Exception as e:
        logger.warning(f"Ollama posting-generation failed ({e}), using fallback template")
        return FALLBACK
