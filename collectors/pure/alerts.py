#!/usr/bin/env python3
"""
Pure Storage Alert Message Collector
- Collects alerts from Pure arrays
- Sends Teams notifications for critical/warning alerts
- Creates ServiceNow tickets via Zabbix for critical alerts
- Tracks notifications to prevent duplicates
"""

import requests
import json
import os
import subprocess
from datetime import datetime
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from collectors.common.db import get_db_cursor, get_db_connection, init_database

# Configuration
KEEPASS_URL = os.environ.get('KEEPASS_URL', 'http://usodclpsandadm1.corp.intranet:2000/keepass')
TEAMS_WEBHOOK_URL = os.environ.get('TEAMS_WEBHOOK_URL', '')
API_VERSION = '1.19'

# Zabbix/ServiceNow Configuration
ZABBIX_BIN = os.environ.get('ZABBIX_BIN', '/usr/local/zabbix/bin')
ZABBIX_CONF = os.environ.get('ZABBIX_CONF', '/usr/local/zabbix/conf/zabbix_agentd.conf')
SNOW_CATEGORY = os.environ.get('SNOW_CATEGORY', 'Alert > Infrastructure')
SNOW_CI = os.environ.get('SNOW_CI', 'Pure Storage CBS')
SNOW_LOCATION = os.environ.get('SNOW_LOCATION', 'Denver')
SNOW_GROUP = os.environ.get('SNOW_GROUP', '')

# Schema prefix
SCHEMA = os.environ.get('DB_SCHEMA', 'USM')

# Severity levels
ALERT_SEVERITIES = ['critical', 'warning']
SNOW_SEVERITIES = os.environ.get('SNOW_SEVERITIES', 'critical').split(',')


def get_api_token(array_name):
    """Fetch API token from KeePass"""
    try:
        resp = requests.get(f"{KEEPASS_URL}/PureStorage_API_{array_name}", timeout=10)
        if resp.status_code == 200:
            return resp.json().get('Password')
    except Exception as e:
        print(f"  [ERROR] KeePass: {e}")
    return None


def get_session(array_name, api_token):
    """Create authenticated session"""
    session = requests.Session()
    session.verify = False
    session.headers.update({'Content-Type': 'application/json'})
    
    try:
        resp = session.post(f"https://{array_name}/api/{API_VERSION}/auth/session",
                           json={"api_token": api_token}, timeout=10)
        if resp.status_code == 200:
            return session
    except Exception as e:
        print(f"  [ERROR] Auth: {e}")
    return None


def collect_messages(array_name):
    """Collect alert messages from array"""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Checking: {array_name}")
    
    api_token = get_api_token(array_name)
    if not api_token:
        print(f"  [SKIP] No credentials")
        return []
    
    session = get_session(array_name, api_token)
    if not session:
        print(f"  [SKIP] Auth failed")
        return []
    
    messages = []
    try:
        resp = session.get(f"https://{array_name}/api/{API_VERSION}/message?open=true", timeout=10)
        if resp.status_code == 200:
            for msg in resp.json():
                messages.append({
                    'array_name': array_name,
                    'message_id': msg.get('id'),
                    'event': msg.get('event', ''),
                    'severity': (msg.get('current_severity') or msg.get('severity', '')).lower(),
                    'component_type': msg.get('component_type', ''),
                    'component_name': msg.get('component_name', ''),
                    'opened': msg.get('opened', ''),
                    'closed': msg.get('closed', ''),
                    'expected': msg.get('expected', ''),
                    'actual': msg.get('actual', ''),
                    'collected_at': datetime.now().isoformat()
                })
        print(f"  [OK] {len(messages)} open alerts")
    except Exception as e:
        print(f"  [ERROR] {e}")
    finally:
        try:
            session.delete(f"https://{array_name}/api/{API_VERSION}/auth/session", timeout=5)
        except:
            pass
    return messages


def save_messages(messages):
    """Save messages to SQL Server, return new alerts needing notification"""
    if not messages:
        return []
    
    new_alerts = []
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        for msg in messages:
            cursor.execute(f"""
                SELECT id, alerted, teams_notified, snow_ticket 
                FROM {SCHEMA}.messages WHERE array_name = ? AND message_id = ?
            """, (msg['array_name'], msg['message_id']))
            existing = cursor.fetchone()
            
            if existing:
                # Update existing
                cursor.execute(f"""
                    UPDATE {SCHEMA}.messages SET
                        event = ?, severity = ?, component_type = ?, component_name = ?,
                        opened = ?, closed = ?, expected = ?, actual = ?, collected_at = ?,
                        resolved = CASE WHEN ? IS NOT NULL AND ? != '' THEN 1 ELSE resolved END
                    WHERE array_name = ? AND message_id = ?
                """, (
                    msg['event'], msg['severity'], msg['component_type'], msg['component_name'],
                    msg['opened'], msg['closed'], msg['expected'], msg['actual'], msg['collected_at'],
                    msg['closed'], msg['closed'],
                    msg['array_name'], msg['message_id']
                ))
                
                # Check if needs notification
                alerted, teams_notified, snow_ticket = existing[1], existing[2], existing[3]
                if not alerted and not teams_notified and msg['severity'] in ALERT_SEVERITIES:
                    msg['_needs_teams'] = True
                    msg['_needs_snow'] = not snow_ticket and msg['severity'] in SNOW_SEVERITIES
                    new_alerts.append(msg)
            else:
                # Insert new
                cursor.execute(f"""
                    INSERT INTO {SCHEMA}.messages (
                        array_name, message_id, event, severity, component_type, component_name,
                        opened, closed, expected, actual, collected_at, suppressed, resolved
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0)
                """, (
                    msg['array_name'], msg['message_id'], msg['event'], msg['severity'],
                    msg['component_type'], msg['component_name'], msg['opened'], msg['closed'],
                    msg['expected'], msg['actual'], msg['collected_at']
                ))
                
                if msg['severity'] in ALERT_SEVERITIES:
                    msg['_needs_teams'] = True
                    msg['_needs_snow'] = msg['severity'] in SNOW_SEVERITIES
                    new_alerts.append(msg)
        
        conn.commit()
    return new_alerts


def send_teams_notification(alerts):
    """Send Teams notifications"""
    if not TEAMS_WEBHOOK_URL or not alerts:
        return 0
    
    sent = 0
    for alert in alerts:
        if not alert.get('_needs_teams', True):
            continue
            
        color = "FF0000" if alert['severity'] == 'critical' else "FFA500"
        payload = {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "themeColor": color,
            "summary": f"Pure Storage Alert: {alert['array_name']}",
            "sections": [{
                "activityTitle": f"🚨 {alert['severity'].upper()}: {alert['array_name']}",
                "facts": [
                    {"name": "Event", "value": (alert['event'] or '-')[:200]},
                    {"name": "Component", "value": f"{alert['component_type']}: {alert['component_name']}"},
                    {"name": "Opened", "value": alert['opened'] or '-'},
                    {"name": "Expected", "value": str(alert.get('expected', '-'))[:100]},
                    {"name": "Actual", "value": str(alert.get('actual', '-'))[:100]},
                    {"name": "Message ID", "value": str(alert['message_id'])}
                ],
                "markdown": True
            }],
            "potentialAction": [{
                "@type": "OpenUri",
                "name": "View Dashboard",
                "targets": [{"os": "default", "uri": os.environ.get('DASHBOARD_URL', 'http://usodclpsandadm1.corp.intranet:5050')}]
            }]
        }
        
        try:
            resp = requests.post(TEAMS_WEBHOOK_URL, json=payload, timeout=10)
            if resp.status_code == 200:
                print(f"  [TEAMS] Sent: {alert['array_name']} - {alert['message_id']}")
                mark_notified(alert['array_name'], alert['message_id'], 'teams')
                sent += 1
            else:
                print(f"  [TEAMS ERROR] HTTP {resp.status_code}")
        except Exception as e:
            print(f"  [TEAMS ERROR] {e}")
    return sent


def create_servicenow_ticket(alerts):
    """Create ServiceNow tickets via Zabbix"""
    zabbix_script = os.path.join(ZABBIX_BIN, 'zabbix_servicenow_ticket.sh')
    if not os.path.exists(zabbix_script):
        return 0
    
    sent = 0
    for alert in alerts:
        if not alert.get('_needs_snow', False):
            continue
        
        severity_map = {'critical': 1, 'warning': 2, 'info': 3}
        sev = severity_map.get(alert['severity'], 3)
        
        description = f"""Pure Storage Alert
Array: {alert['array_name']}
Severity: {alert['severity'].upper()}
Component: {alert['component_type']}: {alert['component_name']}
Event: {alert['event']}
Opened: {alert['opened']}
Expected: {alert.get('expected', 'N/A')}
Actual: {alert.get('actual', 'N/A')}
Message ID: {alert['message_id']}
Dashboard: {os.environ.get('DASHBOARD_URL', 'http://usodclpsandadm1.corp.intranet:5050')}"""
        
        cmd = [zabbix_script]
        if SNOW_GROUP:
            cmd.extend(['-group', SNOW_GROUP])
        
        ci = SNOW_CI if SNOW_CI else alert['array_name']
        cmd.extend([SNOW_CATEGORY, ci, SNOW_LOCATION, str(sev), description])
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                print(f"  [SNOW] Created ticket: {alert['array_name']} - {alert['message_id']}")
                mark_notified(alert['array_name'], alert['message_id'], 'snow')
                sent += 1
            else:
                print(f"  [SNOW ERROR] {result.stderr[:100]}")
        except Exception as e:
            print(f"  [SNOW ERROR] {e}")
    return sent


def mark_notified(array_name, message_id, notification_type):
    """Mark message as notified"""
    timestamp = datetime.now().isoformat()
    with get_db_cursor() as cursor:
        if notification_type == 'teams':
            cursor.execute(f"""
                UPDATE {SCHEMA}.messages 
                SET alerted = ?, teams_notified = GETDATE()
                WHERE array_name = ? AND message_id = ?
            """, (timestamp, array_name, message_id))
        elif notification_type == 'snow':
            cursor.execute(f"""
                UPDATE {SCHEMA}.messages 
                SET snow_ticket = ?
                WHERE array_name = ? AND message_id = ?
            """, (timestamp, array_name, message_id))


def get_arrays():
    """Load arrays from config"""
    paths = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'arrays.txt'),
        '/app/config/arrays.txt',
        os.environ.get('ARRAYS_FILE', '')
    ]
    for path in paths:
        if path and os.path.exists(path):
            with open(path) as f:
                return [l.strip() for l in f if l.strip() and not l.startswith('#')]
    return []


def collect_all():
    """Collect from all arrays"""
    init_database()
    arrays = get_arrays()
    if not arrays:
        print("No arrays found")
        return
    
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Collecting alerts from {len(arrays)} arrays")
    
    all_new = []
    for arr in arrays:
        try:
            msgs = collect_messages(arr)
            new = save_messages(msgs)
            all_new.extend(new)
        except Exception as e:
            print(f"  [ERROR] {arr}: {e}")
    
    teams_sent = snow_sent = 0
    if all_new:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {len(all_new)} new alerts to notify")
        if TEAMS_WEBHOOK_URL:
            teams_sent = send_teams_notification(all_new)
        snow_sent = create_servicenow_ticket(all_new)
    
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Complete - Teams: {teams_sent}, ServiceNow: {snow_sent}")


def test_teams():
    """Test Teams webhook"""
    if not TEAMS_WEBHOOK_URL:
        print("ERROR: TEAMS_WEBHOOK_URL not set")
        return False
    
    payload = {
        "@type": "MessageCard",
        "themeColor": "FFA500",
        "summary": "USM Test",
        "sections": [{"activityTitle": "🧪 TEST: USM Alert System", 
                      "facts": [{"name": "Status", "value": "Test notification"},
                               {"name": "Time", "value": datetime.now().isoformat()}]}]
    }
    try:
        resp = requests.post(TEAMS_WEBHOOK_URL, json=payload, timeout=10)
        print(f"{'SUCCESS' if resp.status_code == 200 else 'FAILED'}: HTTP {resp.status_code}")
        return resp.status_code == 200
    except Exception as e:
        print(f"FAILED: {e}")
        return False


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Pure Storage Alert Collector')
    parser.add_argument('array', nargs='?', help='Single array')
    parser.add_argument('--all', action='store_true', help='All arrays')
    parser.add_argument('--init-db', action='store_true', help='Init database')
    parser.add_argument('--test-teams', action='store_true', help='Test Teams')
    
    args = parser.parse_args()
    
    if args.test_teams:
        test_teams()
    elif args.init_db:
        init_database()
    elif args.all:
        collect_all()
    elif args.array:
        init_database()
        msgs = collect_messages(args.array)
        new = save_messages(msgs)
        if new:
            send_teams_notification(new)
            create_servicenow_ticket(new)
    else:
        parser.print_help()
