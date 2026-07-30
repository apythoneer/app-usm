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

import json
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


# ---------------------------------------------------------------------------
# Datadog Event Management (replaces the legacy ServiceNow/Zabbix ticket path)
# ---------------------------------------------------------------------------
# Storage alerts are posted to Datadog's v2 event intake. Lumen's monitoring team
# ingests them and maps onto ServiceNow (title→short desc, message→description,
# host→CMDB enrichment). Deduplication + resolution both key off `aggregation_key`:
# the SAME key with status='error'/'warn' opens/updates the event, and with
# status='ok' resolves it — which is exactly how our absence-based closure clears
# an alert on the array. So a Datadog event's lifecycle mirrors USM.messages.

logger_dd = logging.getLogger("usm.notification.datadog")

# USM severity → Datadog status + numeric priority ('1' highest … '5' lowest)
_DD_STATUS = {"critical": "error", "warning": "warn", "info": "info"}
_DD_PRIORITY = {"critical": "2", "warning": "3", "info": "4"}


def datadog_severity_enabled(severity: str) -> bool:
    """True if this severity is configured to be pushed to Datadog."""
    if not settings.datadog_enabled:
        return False
    enabled = {
        s.strip().lower()
        for s in (settings.datadog_severities or "").split(",")
        if s.strip()
    }
    return (severity or "").lower() in enabled


def datadog_agg_key(vendor: str, array_name: str, message_id) -> str:
    """Deterministic Datadog aggregation key for one alert.

    Identical for the open (error/warn) and resolve (ok) posts so Datadog treats
    them as the same event. Datadog caps this at 100 chars — keep it short and
    truncate defensively.
    """
    src = settings.datadog_source or "usm"
    return f"source:{src}-{vendor}-{array_name}-{message_id}"[:100]


def _dd_ui_base() -> str:
    """Datadog UI base URL derived from the intake host (for event deep-links).
    e.g. event-management-intake.us5.datadoghq.com -> https://us5.datadoghq.com"""
    try:
        host = settings.datadog_events_url.split("/")[2]
        host = host.replace("event-management-intake.", "").replace("api.", "")
        return f"https://{host}"
    except Exception:
        return "https://app.datadoghq.com"


def _datadog_api_key() -> str:
    """Resolve the Datadog API key: explicit env override wins, else KeePass
    (Password field of the DATADOG_CRED_KEY entry). Returns '' if unavailable."""
    if settings.datadog_api_key:
        return settings.datadog_api_key
    try:
        from app.services.keepass import get_credentials
        creds = get_credentials(settings.datadog_cred_key)
        return creds.get("password") or ""
    except Exception as e:
        logger_dd.error(f"Datadog API key unavailable from KeePass '{settings.datadog_cred_key}': {e}")
        return ""


def post_datadog_event(
    title: str,
    message: str,
    host: str,
    agg_key: str,
    tags: list,
    status: str,
    priority: str,
) -> Optional[dict]:
    """POST a single custom event to Datadog's v2 event-management intake.

    status: 'error' | 'warn' | 'ok' (use 'ok' to resolve). Returns a dict
    {id, uid, url} on 2xx (fields may be '' if unparseable), None on failure.
    """
    if not settings.datadog_enabled:
        return False
    api_key = _datadog_api_key()
    if not api_key:
        logger_dd.warning("Datadog enabled but no API key resolved — skipping event")
        return False

    payload = {
        "data": {
            "type": "event",
            "attributes": {
                "category": "alert",
                "title": title[:500],
                "message": message,
                "host": host,
                "aggregation_key": agg_key[:100],
                "tags": tags,
                "attributes": {"status": status, "priority": priority},
            }
        }
    }
    try:
        resp = requests.post(
            settings.datadog_events_url,
            headers={"Content-Type": "application/json", "DD-API-KEY": api_key},
            data=json.dumps(payload),
            timeout=10,
        )
        resp.raise_for_status()
        # Capture the event's numeric id, uid, and deep-link so the alert can be
        # traced back. Datadog events are append-only (no PATCH), so this is the
        # only handle we get. Returns a dict on 2xx even if the body can't be
        # parsed (so callers still see success), None only on transport failure.
        try:
            j = resp.json() or {}
            data = j.get("data", {}) or {}
            evt = ((data.get("attributes", {}) or {}).get("attributes", {}) or {}).get("evt", {}) or {}
            uid = evt.get("uid") or ""
            url = ((j.get("links", {}) or {}).get("self")) or (f"{_dd_ui_base()}/event/event?uid={uid}" if uid else "")
            return {"id": str(evt.get("id") or ""), "uid": uid, "url": url}
        except Exception:
            return {"id": "", "uid": "", "url": ""}
    except Exception as e:
        logger_dd.error(f"Datadog POST failed ({status}) for {host}: {e}")
        return None


def _dd_component(alert: dict) -> str:
    ct = (alert.get("component_type") or "").strip()
    cn = (alert.get("component_name") or "").strip()
    return f"{ct}: {cn}".strip(": ").strip() or "N/A"


def _dd_tags(vendor: str, array_name: str, severity: str, alert: dict) -> list:
    tags = [
        f"source:{settings.datadog_source or 'usm'}",
        f"vendor:{vendor}",
        f"array:{array_name}",
        f"severity:{(severity or '').lower()}",
        f"env:{settings.datadog_env}",
    ]
    ct = (alert.get("component_type") or "").strip()
    if ct:
        tags.append(f"component:{ct}")
    return tags


def send_datadog_alert(alert: dict) -> Optional[dict]:
    """Open/update a Datadog event for a new critical/warning storage alert.

    `alert` is the collector's message dict (array_name, vendor, severity, event,
    component_type, component_name, message_id, opened). No-op unless the severity
    is enabled for Datadog. Returns {id, uid, url} on success, else None.
    """
    severity = (alert.get("severity") or "").lower()
    if not datadog_severity_enabled(severity):
        return None
    vendor = alert.get("vendor") or ""
    array_name = alert.get("array_name") or ""
    message_id = alert.get("message_id")
    event = (alert.get("event") or "").strip() or "Storage alert"
    component = _dd_component(alert)

    title = f"{array_name}: {event}"[:200]
    message = (
        f"**Storage alert — {vendor.upper()}**\n\n"
        f"- **Array:** {array_name}\n"
        f"- **Severity:** {severity.upper()}\n"
        f"- **Component:** {component}\n"
        f"- **Event:** {event}\n"
        f"- **Opened:** {alert.get('opened') or 'N/A'}\n"
        f"- **Alert ID:** {message_id}"
    )
    if alert.get("expected") or alert.get("actual"):
        message += f"\n- **Expected:** {alert.get('expected') or 'N/A'}\n- **Actual:** {alert.get('actual') or 'N/A'}"

    ok = post_datadog_event(
        title=title,
        message=message,
        host=array_name,
        agg_key=datadog_agg_key(vendor, array_name, message_id),
        tags=_dd_tags(vendor, array_name, severity, alert),
        status=_DD_STATUS.get(severity, "info"),
        priority=_DD_PRIORITY.get(severity, "4"),
    )
    if ok:
        logger_dd.info(f"Datadog event opened for {array_name}: {event[:60]}")
    return ok


def resolve_datadog_alert(vendor: str, array_name: str, message_id, event: str = "") -> bool:
    """Resolve (status='ok') a previously-opened Datadog event via the same
    aggregation key. Called when USM's absence-based closure clears the alert."""
    if not settings.datadog_enabled:
        return False
    title = f"{array_name}: {event or 'alert'} — RESOLVED"[:200]
    message = (
        f"Storage alert resolved on **{array_name}** ({vendor.upper()}).\n\n"
        f"- **Alert ID:** {message_id}\n- **Event:** {event or 'N/A'}"
    )
    ok = post_datadog_event(
        title=title,
        message=message,
        host=array_name,
        agg_key=datadog_agg_key(vendor, array_name, message_id),
        tags=[
            f"source:{settings.datadog_source or 'usm'}",
            f"vendor:{vendor}", f"array:{array_name}", f"env:{settings.datadog_env}",
        ],
        status="ok",
        priority="5",
    )
    if ok:
        logger_dd.info(f"Datadog event resolved for {array_name} (id {message_id})")
    return ok

