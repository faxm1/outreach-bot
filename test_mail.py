# test_mail.py
from dotenv import load_dotenv; load_dotenv()
from config import CV_PDF_PATH, SENDER_NAME, SENDER_EMAIL
from mailer import send_email
send_email(
    recipient="anything@example.com",
    subject="Test Subject",
    body="Test body paragraph one.\n\nTest body paragraph two.",
    cv_path=CV_PDF_PATH,
    cv_filename="Test_CV_2025.pdf",
    sender_name=SENDER_NAME,
    sender_email=SENDER_EMAIL
)
print("✅ SMTP + attachment OK — check Mailtrap inbox")