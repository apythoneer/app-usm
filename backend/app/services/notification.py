"""
Notification service — Microsoft Teams webhooks, ServiceNow (future).

The Teams alert is delivered through a Power Automate / Workflows webhook whose
flow contains its OWN Adaptive Card. That flow card binds its fields to FLAT
keys read from the trigger body via expressions like
``@{triggerBody()?['array_name']}``. Therefore this service must POST a flat
JSON object whose keys match the flow exactly — NOT an Adaptive Card envelope.

Flow-expected keys (do not rename without updating the Power Automate flow):
    array_name, severity, event, message_id, component, opened
"""

import logging
from datetime import datetime, timezone

import requests
from typing import Optional

from app.core.config import get_settings

logger = logging.getLogger("usm.notification")
settings = get_settings()


def severity_enabled(severity: str) -> bool:
    """Return True if the given severity is configured to trigger a Teams alert."""
    enabled = {
        s.strip().lower()
        for s in (settings.teams_severities or "").split(",")
        if s.strip()
    }
    return severity.lower() in enabled


def _post(url: str, payload: dict) -> bool:
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Teams POST failed: {e}")
        return False


def send_teams_alert(
    array_name: str,
    vendor: str,
    severity: str,
    event: str,
    component: Optional[str] = None,
    message_id: Optional[str] = None,
    opened: Optional[str] = None,
    webhook_url: Optional[str] = None,
) -> bool:
    """Send a storage alert notification to Microsoft Teams via webhook.

    Posts a FLAT JSON body matching the Power Automate flow's trigger schema.
    The flow renders its own Adaptive Card from these keys.
    """
    url = webhook_url or settings.teams_webhook_url
    if not url:
        logger.debug("Teams webhook not configured, skipping notification")
        return False

    payload = {
        "array_name": array_name,
        "vendor": vendor,
        "severity": severity.upper(),
        "event": event,
        "component": component or "N/A",
        "message_id": message_id or "N/A",
        "opened": opened or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }

    ok = _post(url, payload)
    if ok:
        logger.info(f"Teams notification sent for {array_name}: {event}")
    return ok


def send_teams_message(title: str, message: str, severity: str = "info") -> bool:
    """Send a generic Teams message (used for test cards, digests, etc.).

    Mapped onto the same flat flow schema so the flow's card renders it.
    """
    url = settings.teams_webhook_url
    if not url:
        return False
    payload = {
        "array_name": title,
        "vendor": "USM",
        "severity": severity.upper(),
        "event": message,
        "component": "N/A",
        "message_id": "N/A",
        "opened": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }
    return _post(url, payload)

