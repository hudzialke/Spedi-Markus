from __future__ import annotations

import re
from typing import Literal
from urllib.parse import quote

from markus_dispatcher.core.models import Client, ContactChannel

EmailComposeMode = Literal["gmail", "outlook", "mailto"]


def normalize_phone_for_wa(phone: str) -> str:
    """Zostawia cyfry; PL bez + → dokłada 48 gdy wygląda na krajowy."""
    digits = re.sub(r"\D", "", phone.strip())
    if not digits:
        return ""
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == 9:
        digits = "48" + digits
    return digits


def whatsapp_url(phone: str, message: str) -> str:
    digits = normalize_phone_for_wa(phone)
    text = quote(message)
    if digits:
        return f"https://wa.me/{digits}?text={text}"
    return f"https://wa.me/?text={text}"


def mailto_url(email: str, *, subject: str, body: str) -> str:
    return (
        f"mailto:{email.strip()}"
        f"?subject={quote(subject)}&body={quote(body)}"
    )


def gmail_compose_url(email: str, *, subject: str, body: str) -> str:
    """Compose w Gmailu (https) — otwiera się jako zakładka w przeglądarce."""
    return (
        "https://mail.google.com/mail/?view=cm&fs=1&tf=1"
        f"&to={quote(email.strip())}"
        f"&su={quote(subject)}"
        f"&body={quote(body)}"
    )


def outlook_compose_url(email: str, *, subject: str, body: str) -> str:
    return (
        "https://outlook.office.com/mail/deeplink/compose"
        f"?to={quote(email.strip())}"
        f"&subject={quote(subject)}"
        f"&body={quote(body)}"
    )


def email_compose_url(
    email: str,
    *,
    subject: str,
    body: str,
    mode: EmailComposeMode = "gmail",
) -> str:
    if mode == "outlook":
        return outlook_compose_url(email, subject=subject, body=body)
    if mode == "mailto":
        return mailto_url(email, subject=subject, body=body)
    return gmail_compose_url(email, subject=subject, body=body)


def default_subject(ref: str) -> str:
    ref = ref.strip()
    return f"Update transportu {ref}" if ref else "Update transportu"


def channels_for_client(client: Client | None) -> set[ContactChannel]:
    if client is None:
        return {"whatsapp", "email"}
    if client.preferred_channel == "both":
        return {"whatsapp", "email"}
    return {client.preferred_channel}
