from datetime import datetime
import re

from automind.config import DATETIME_FORMAT


EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def require_text(value: str, field_name: str) -> str:
    text = (value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required.")
    return text


def parse_schedule_datetime(date_text: str, time_text: str) -> datetime:
    combined = f"{require_text(date_text, 'Date')} {require_text(time_text, 'Time')}"
    try:
        return datetime.strptime(combined, DATETIME_FORMAT)
    except ValueError as exc:
        raise ValueError("Schedule must use YYYY-MM-DD for date and HH:MM for time.") from exc


def validate_email_payload(sender_email: str, app_password: str, recipient: str, subject: str, message: str) -> dict:
    sender_email = require_text(sender_email, "Sender email")
    recipient = require_text(recipient, "Recipient email")
    if not EMAIL_PATTERN.match(sender_email):
        raise ValueError("Sender email must look like name@example.com.")
    if not EMAIL_PATTERN.match(recipient):
        raise ValueError("Recipient email must look like name@example.com.")

    return {
        "sender_email": sender_email,
        "app_password": require_text(app_password, "App password"),
        "recipient": recipient,
        "subject": require_text(subject, "Subject"),
        "message": require_text(message, "Message"),
    }


def validate_sender_profile(profile_name: str, sender_email: str, app_password: str) -> dict:
    profile_name = require_text(profile_name, "Profile name")
    sender_email = require_text(sender_email, "Sender email")
    if not EMAIL_PATTERN.match(sender_email):
        raise ValueError("Sender email must look like name@example.com.")
    return {
        "profile_name": profile_name,
        "sender_email": sender_email,
        "app_password": require_text(app_password, "App password"),
    }


def parse_recipient_list(value: str) -> list[str]:
    raw_items = [item.strip() for item in re.split(r"[,\n;]+", value or "") if item.strip()]
    if not raw_items:
        raise ValueError("At least one recipient email is required.")
    invalid = [item for item in raw_items if not EMAIL_PATTERN.match(item)]
    if invalid:
        raise ValueError(f"These recipient emails are invalid: {', '.join(invalid)}")
    return raw_items


def validate_recipient_group(group_name: str, recipients_text: str, notes: str) -> dict:
    return {
        "group_name": require_text(group_name, "Group name"),
        "recipients": parse_recipient_list(recipients_text),
        "notes": (notes or "").strip(),
    }


def validate_email_template(template_name: str, subject_template: str, body_template: str, tone: str, notes: str) -> dict:
    return {
        "template_name": require_text(template_name, "Template name"),
        "subject_template": require_text(subject_template, "Subject template"),
        "body_template": require_text(body_template, "Body template"),
        "tone": require_text(tone, "Tone"),
        "notes": (notes or "").strip(),
    }


def validate_reminder_payload(message: str) -> dict:
    return {"message": require_text(message, "Reminder message")}
