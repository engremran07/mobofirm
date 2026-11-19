"""WhatsApp Cloud API support: the opt-in list, the 24 hour window and the webhook.

What the platform allows (Meta's public WhatsApp Business Platform documentation; the
links are shown in the channel setup drawer):

* **Cloud API (supported here).** A business may message a person only after that person
  opted in. Outside the 24 hour *customer-service window* (it opens each time the person
  writes to the business number) the first message must be an approved **template**; inside
  the window a free-form text is allowed. Marketing templates are additionally throttled
  per recipient by Meta, which can answer ``131049``/``131050``; ``131050`` means the person
  stopped marketing messages, so we treat it as an opt-out. Sending is rate limited per
  phone number and by the messaging tier of the account (unique recipients per 24 hours).
  Delivery status (``sent``/``delivered``/``read``/``failed``) and inbound messages arrive
  on a signed webhook: ``X-Hub-Signature-256`` is the HMAC-SHA256 of the raw body with the
  app secret, and the subscription handshake echoes ``hub.challenge`` when ``hub.verify_token``
  matches.
* **WhatsApp Channels (newsletters): not supported through any API.** Channels can only be
  posted to from the WhatsApp app by the channel admin, so the *assisted* platform prepares
  a copy-ready post and the channel link and leaves the posting to a person.

Opt-in is explicit: numbers enter the list because staff list them on the channel (staff
attest they hold consent); inbound START/STOP keywords and the platform's own signals only
change a number that is already known. An opt-out always wins over the staff list.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from django.utils import timezone

from apps.distribution.models import Channel, WhatsAppRecipient

SESSION_WINDOW = timedelta(hours=24)
MAX_RECIPIENTS = 50
MAX_WEBHOOK_BYTES = 1_000_000
# Meta error code: the person stopped marketing messages from this business.
OPT_OUT_ERROR_CODES = frozenset({"131050"})
OPT_OUT_WORDS = frozenset({"stop", "unsubscribe", "cancel", "end", "quit"})
OPT_IN_WORDS = frozenset({"start", "subscribe", "unstop"})
DELIVERY_STATES = frozenset({"sent", "delivered", "read", "failed"})
_NUMBER = re.compile(r"\+?\d{8,15}")


def normalise_numbers(raw: str) -> list[str]:
    """Valid international numbers out of a comma/space separated list (order kept)."""
    seen: dict[str, None] = {}
    for part in re.split(r"[,\s;]+", raw or ""):
        cleaned = re.sub(r"[^\d+]", "", part)
        if _NUMBER.fullmatch(cleaned):
            seen[cleaned if cleaned.startswith("+") else f"+{cleaned}"] = None
    return list(seen)


def audience(channel: Channel, declared: list[str]) -> list[WhatsAppRecipient]:
    """Record the staff-listed numbers (idempotent) and return who may be messaged.

    A number that opted out stays out even while it is still listed.
    """
    for phone in declared:
        WhatsAppRecipient.objects.get_or_create(
            channel=channel, phone=phone, defaults={"opt_in_source": "staff list"}
        )
    return list(
        WhatsAppRecipient.objects.filter(
            channel=channel, phone__in=declared, opted_out_at__isnull=True
        ).order_by("phone")
    )


def in_session(person: WhatsAppRecipient, now: datetime) -> bool:
    """True while the 24 hour customer-service window is open for this person."""
    return (
        person.last_inbound_at is not None
        and now - person.last_inbound_at < SESSION_WINDOW
    )


def opt_out(
    person: WhatsAppRecipient, reason: str, now: datetime | None = None
) -> bool:
    if person.opted_out_at is not None:
        return False
    person.opted_out_at = now or timezone.now()
    person.opt_out_reason = reason[:60]
    person.save(update_fields=["opted_out_at", "opt_out_reason"])
    return True


def opt_in(person: WhatsAppRecipient, source: str, now: datetime | None = None) -> bool:
    if person.opted_out_at is None:
        return False
    person.opted_out_at = None
    person.opt_out_reason = ""
    person.opted_in_at = now or timezone.now()
    person.opt_in_source = source[:60]
    person.save(
        update_fields=["opted_out_at", "opt_out_reason", "opted_in_at", "opt_in_source"]
    )
    return True


def record_status(
    person: WhatsAppRecipient, status: str, now: datetime | None = None
) -> None:
    person.last_status = status[:20]
    person.last_status_at = now or timezone.now()
    person.save(update_fields=["last_status", "last_status_at"])


def error_code(body: bytes) -> str:
    """The platform error code of a failed response ('' when there is none)."""
    try:
        data = json.loads(body or b"{}")
    except ValueError:
        return ""
    error = data.get("error") if isinstance(data, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    return str(code) if code else ""


# ---------------------------------------------------------------- webhook ----


def verify_signature(app_secret: str, body: bytes, header: str) -> bool:
    """``X-Hub-Signature-256`` check; an empty secret never verifies."""
    if not app_secret or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def _person(channel: Channel, digits: str) -> WhatsAppRecipient | None:
    if not digits:
        return None
    return WhatsAppRecipient.objects.filter(
        channel=channel, phone=f"+{digits.lstrip('+')}"
    ).first()


def _apply_status(channel: Channel, item: Mapping[str, Any], now: datetime) -> bool:
    person = _person(channel, str(item.get("recipient_id") or ""))
    state = str(item.get("status") or "")
    if person is None or state not in DELIVERY_STATES:
        return False
    record_status(person, state, now)
    errors = item.get("errors")
    codes = {
        str(err.get("code"))
        for err in (errors if isinstance(errors, list) else [])
        if isinstance(err, dict)
    }
    if codes & OPT_OUT_ERROR_CODES:
        opt_out(person, "platform: stopped marketing", now)
    return True


def _apply_message(channel: Channel, item: Mapping[str, Any], now: datetime) -> bool:
    person = _person(channel, str(item.get("from") or ""))
    if person is None:
        return False
    person.last_inbound_at = now
    person.save(update_fields=["last_inbound_at"])
    text = item.get("text")
    body = str(text.get("body") or "") if isinstance(text, dict) else ""
    word = body.strip().lower()
    if word in OPT_OUT_WORDS:
        opt_out(person, "replied STOP", now)
    elif word in OPT_IN_WORDS:
        opt_in(person, "replied START", now)
    return True


def handle_payload(
    channel: Channel, payload: Mapping[str, Any], now: datetime | None = None
) -> int:
    """Apply a webhook payload; returns how many items changed a known recipient.

    Idempotent: a replayed delivery re-applies the same state.
    """
    moment = now or timezone.now()
    phone_id = str((channel.options or {}).get("phone_number_id", ""))
    applied = 0
    entries = payload.get("entry")
    for entry in entries if isinstance(entries, list) else []:
        changes = entry.get("changes") if isinstance(entry, dict) else None
        for change in changes if isinstance(changes, list) else []:
            value = change.get("value") if isinstance(change, dict) else None
            if not isinstance(value, dict):
                continue
            meta = value.get("metadata")
            if (
                isinstance(meta, dict)
                and phone_id
                and str(meta.get("phone_number_id") or "") != phone_id
            ):
                continue
            for item in value.get("statuses") or []:
                applied += int(
                    isinstance(item, dict) and _apply_status(channel, item, moment)
                )
            for item in value.get("messages") or []:
                applied += int(
                    isinstance(item, dict) and _apply_message(channel, item, moment)
                )
    return applied
