"""
Notification service — Microsoft Teams webhooks, ServiceNow (future).
"""

import logging
import requests
from typing import Optional

from app.core.config import get_settings

logger = logging.getLogger("usm.notification")
settings = get_settings()


def send_teams_alert(
    array_name: str,
    vendor: str,
    severity: str,
    event: str,
    component: Optional[str] = None,
    webhook_url: Optional[str] = None,
) -> bool:
    """Send alert notification to Microsoft Teams via webhook."""
    url = webhook_url or settings.teams_webhook_url
    if not url:
        logger.debug("Teams webhook not configured, skipping notification")
        return False

    severity_colors = {
        "critical": "FF0000",
        "warning": "FFA500",
        "info": "0078D7",
    }
    color = severity_colors.get(severity.lower(), "808080")

    payload = {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": color,
        "summary": f"[{severity.upper()}] {array_name}: {event}",
        "sections": [
            {
                "activityTitle": f"**Storage Alert — {severity.upper()}**",
                "activitySubtitle": f"Array: `{array_name}` | Vendor: `{vendor}`",
                "facts": [
                    {"name": "Event", "value": event},
                    {"name": "Component", "value": component or "N/A"},
                    {"name": "Severity", "value": severity.upper()},
                ],
            }
        ],
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        logger.info(f"Teams notification sent for {array_name}: {event}")
        return True
    except Exception as e:
        logger.error(f"Teams notification failed: {e}")
        return False


def send_teams_message(title: str, message: str, color: str = "0078D7") -> bool:
    """Send a generic Teams message."""
    url = settings.teams_webhook_url
    if not url:
        return False
    payload = {
        "@type": "MessageCard",
        "themeColor": color,
        "summary": title,
        "sections": [{"activityTitle": title, "activityText": message}],
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Teams message failed: {e}")
        return False
