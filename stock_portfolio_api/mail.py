from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage

SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", "25"))
MAIL_FROM_ADDRESS = os.getenv("MAIL_FROM_ADDRESS", "no-reply@localhost")


def send_email(to_address: str, subject: str, body: str) -> None:
    if not SMTP_HOST:
        raise RuntimeError("SMTP_HOST environment variable must be set")

    message = EmailMessage()
    message["From"] = MAIL_FROM_ADDRESS
    message["To"] = to_address
    message["Subject"] = subject
    message.set_content(body)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as smtp:
        smtp.send_message(message)
