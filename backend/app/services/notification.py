"""
Notification service — Microsoft Teams webhooks, ServiceNow (future).

Teams payloads use the Adaptive Card format wrapped in an attachment, which is
compatible with BOTH legacy Incoming Webhook connectors (webhook.office.com) and
the newer Power Automate / Workflows webhooks (logic.azure.com). Power Automate
webhooks no longer accept the deprecated MessageCard ("@type": "MessageCard")
format, so Adaptive Cards are the safe choice going forward.
"""

import logging
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


# Severity → Adaptive Card style + hex accent (used in the rendered fact block)
_SEVERITY_STYLE = {
    "critical": ("attention", "FF0000"),
    "warning": ("warning", "FFA500"),
    "info": ("accent", "0078D7"),
}


def _adaptive_card(title: str, subtitle: str, facts: list, style: str) -> dict:
    """Build a Teams-compatible Adaptive Card attachment payload."""
    body = [
        {
            "type": "TextBlock",
            "text": title,
            "weight": "Bolder",
            "size": "Medium",
            "wrap": True,
        }
    ]
    if subtitle:
        body.append({
            "type": "TextBlock",
            "text": subtitle,
            "isSubtle": True,
            "spacing": "None",
            "wrap": True,
        })
    if facts:
        body.append({
            "type": "FactSet",
            "facts": [{"title": f["name"], "value": f["value"]} for f in facts],
        })

    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "msteams": {"width": "Full"},
                    "body": body,
                },
            }
        ],
    }


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
    webhook_url: Optional[str] = None,
) -> bool:
    """Send a storage alert notification to Microsoft Teams via webhook."""
    url = webhook_url or settings.teams_webhook_url
    if not url:
        logger.debug("Teams webhook not configured, skipping notification")
        return False

    style, _ = _SEVERITY_STYLE.get(severity.lower(), ("default", "808080"))

    payload = _adaptive_card(
        title=f"🔔 Storage Alert — {severity.upper()}",
        subtitle=f"Array: {array_name}  |  Vendor: {vendor}",
        facts=[
            {"name": "Event", "value": event},
            {"name": "Component", "value": component or "N/A"},
            {"name": "Severity", "value": severity.upper()},
        ],
        style=style,
    )

    ok = _post(url, payload)
    if ok:
        logger.info(f"Teams notification sent for {array_name}: {event}")
    return ok


def send_teams_message(title: str, message: str, severity: str = "info") -> bool:
    """Send a generic Teams message (used for test cards, digests, etc.)."""
    url = settings.teams_webhook_url
    if not url:
        return False
    style, _ = _SEVERITY_STYLE.get(severity.lower(), ("default", "808080"))
    payload = _adaptive_card(
        title=title,
        subtitle="",
        facts=[{"name": "Details", "value": message}] if message else [],
        style=style,
    )
    return _post(url, payload)
