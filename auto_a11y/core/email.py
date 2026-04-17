"""
Email sending via SMTP for Auto A11y.
"""
from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from config import Config

logger = logging.getLogger(__name__)


def send_email(config: Config, to: str, subject: str, text_body: str, html_body: str) -> bool:
    """
    Send an email using SMTP settings from config.

    Args:
        config: App config object with SMTP_ attributes.
        to: Recipient email address.
        subject: Email subject line.
        text_body: Plain text version of the email.
        html_body: HTML version of the email.

    Returns:
        True if sent successfully, False otherwise.
    """
    if not config.SMTP_ENABLED:
        logger.warning('SMTP is not configured -- cannot send email to %s', to)
        return False

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = f'{config.SMTP_FROM_NAME} <{config.SMTP_FROM_EMAIL}>'
    msg['To'] = to

    msg.attach(MIMEText(text_body, 'plain'))
    msg.attach(MIMEText(html_body, 'html'))

    try:
        if config.SMTP_USE_TLS:
            server = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT)
            server.starttls()
        else:
            server = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT)

        if config.SMTP_USERNAME:
            server.login(config.SMTP_USERNAME, config.SMTP_PASSWORD)

        server.sendmail(config.SMTP_FROM_EMAIL, to, msg.as_string())
        server.quit()
        logger.info('Email sent to %s: %s', to, subject)
        return True
    except Exception:
        logger.error('Failed to send email to %s', to, exc_info=True)
        return False
