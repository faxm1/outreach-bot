# ollama_client.py
import json
import logging
import urllib.request
import urllib.error

from config import OLLAMA_BASE_URL as OLLAMA_URL, OLLAMA_MODEL

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a professional email writer specializing in IT or cybersecurity job applications.
Write a short, professional cold-outreach email from a final-semester Cybersecurity student
seeking a IT or SOC (Security Operations Center) or Blue Team co-op/internship position.

STRICT RULES:
1. Output ONLY valid JSON in this exact format: {"subject": "...", "body": "..."}
2. Subject line must be under 65 characters and clearly reference SOC or cybersecurity.
3. Body must be exactly 3 paragraphs, 2-3 sentences each.
4. Total body word count: 120-170 words maximum.
5. Paragraph 1: Brief intro — final-semester Cybersecurity student seeking SOC/Blue Team co-op or internship.
6. Paragraph 2: Relevant skills — threat monitoring, log analysis, SIEM, incident response, triage. Be specific, not generic.
7. Paragraph 3: Call to action — mention attached CV, express interest in contributing to their team.
8. Do NOT mention any company name, team name, hiring manager name, or specific internal projects.
9. Do NOT use software engineering, software development, or general IT language.
10. Do NOT use cliché openers like "I am writing to express my interest."
11. No bullet points. Tone: confident, professional, concise, ATS-friendly.
12. No placeholders like [Company Name] or [Your Name]. Write complete, ready-to-send sentences.
13. Output raw JSON only. No markdown fences. No explanation. No text before or after the JSON.
14. If no job posting is provided, write a genuinely generic email.
    Do NOT invent job requirements, role titles, or company needs. 
    Do NOT write phrases like "aligning with your requirements" or "crucial for your role" unless a real job posting was provided."""

FALLBACK = {
    "subject": "Cybersecurity Co-op – SOC / Blue Team",
    "body": (
        "Hello Team,\n\n"
        "I’m a final-semester Information Technology student (Cybersecurity track) at Majmaah University, "
        "graduating in 2026, seeking a Cybersecurity Co-op opportunity in SOC or Blue Team operations.\n\n"
        "My foundation includes networking fundamentals (TCP/IP, OSI), basic network scanning with Nmap, and "
        "traffic analysis with Wireshark, with a clear focus on monitoring, alert triage, and incident "
        "documentation. I completed Tuwaiq Academy’s “Cybersecurity Fundamentals and Defensive Technologies” and "
        "relevant network security coursework from IBM and Google, and I’m actively strengthening my "
        "practical SOC-ready skills through hands-on learning.\n\n"
        "I bring strong ownership, discipline, and a growth mindset, and I aim to create measurable impact wherever "
        "I work. I’m comfortable learning quickly, adapting to team standards, and supporting security operations "
        "with accuracy, clear communication, and consistent execution.\n\n"
        "My CV is attached for your review, and I would welcome the opportunity to be considered for any suitable "
        "Co-op openings. Thank you for your time. I look forward to hearing from you.\n\n"
        "Faisal Mohammed Alhamad\n"
        "+966 580 509 593\n"
        "faisalmhofiicial@gmail.com"
    )
}


CV_TEXT = (
    "Final-semester Information Technology student (Cybersecurity track), Majmaah University, graduating 2026. "
    "Skills: TCP/IP, OSI model, Nmap (network scanning), Wireshark (traffic analysis), SIEM fundamentals, "
    "log analysis, alert triage, incident documentation, basic incident response. "
    "Certifications: Tuwaiq Academy 'Cybersecurity Fundamentals and Defensive Technologies'; "
    "IBM and Google network security coursework. "
    "Focus: SOC/Blue Team operations, threat monitoring, security event analysis."
)

SYSTEM_PROMPT_JOB = """You are a professional email writer specializing in cybersecurity job applications.
Write a short, tailored, professional cold-outreach email from a final-semester Cybersecurity student
applying for the SOC or Blue Team position described in the job posting provided.

STRICT RULES:
1. Output ONLY valid JSON in this exact format: {"subject": "...", "body": "..."}
2. Subject line must be under 65 characters and clearly reference SOC or cybersecurity.
3. Body must be exactly 3 paragraphs, 2-3 sentences each.
4. Total body word count: 120-170 words maximum.
5. Paragraph 1: Reference the specific role from the posting. Introduce yourself as a final-semester Cybersecurity student.
6. Paragraph 2: Connect skills from the CV directly to requirements mentioned in the posting. Be specific, not generic.
7. Paragraph 3: Mention attached CV, express interest, professional closing.
8. Do NOT invent skills not present in the CV summary. Do NOT mention company names.
9. Do NOT use software engineering, software development, or general IT language.
10. Do NOT use cliché openers like "I am writing to express my interest."
11. No bullet points. Tone: confident, professional, concise, ATS-friendly.
12. No placeholders like [Company Name] or [Your Name]. Write complete, ready-to-send sentences.
13. Output raw JSON only. No markdown fences. No explanation. No text before or after the JSON."""


def _extract_domain(email: str) -> str:
    try:
        return email.split("@")[1].split(".")[0]
    except Exception:
        return "your organization"


def _parse_model_output(content: str) -> dict:
    """
    Extracts subject and body from model output, handling all known variants:
    - Single object:  {"subject": "...", "body": "..."}
    - Split objects:  {"subject": "..."}{"body": "..."}   ← qwen2.5 does this
    - Nested:         {"email": {"subject": ..., "body": ...}}
    - Capital keys:   {"Subject": ..., "Body": ...}
    - Alt key names:  subject_line, email_body, body_text, content, message

    Strategy: collect ALL JSON objects in the content (up to 5), merge their
    keys case-insensitively, then search for subject and body.
    """
    # Collect all JSON objects found anywhere in the content
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

    # Merge all objects into a single flat dict (case-insensitive keys)
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

    # One level of nesting if still missing
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
        f"Write a cold-outreach SOC co-op/internship application email. "
        f"The recipient organization's domain appears to be: {domain}. "
        f"The applicant is a final-semester Cybersecurity student seeking a SOC or Blue Team role. "
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
    cv_text: str = CV_TEXT,
) -> dict:
    """
    Generates a tailored email using the job posting and CV context.
    Returns {"subject": str, "body": str}. Falls back to FALLBACK on any failure.
    """
    domain      = _extract_domain(recipient_email)
    job_snippet = job_text[:2000]
    cv_snippet  = cv_text[:1000]

    user_prompt = (
        f"Organization domain: {domain}\n\n"
        f"JOB POSTING:\n{job_snippet}\n\n"
        f"APPLICANT CV SUMMARY:\n{cv_snippet}\n\n"
        f"Write a tailored SOC/cybersecurity application email. "
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