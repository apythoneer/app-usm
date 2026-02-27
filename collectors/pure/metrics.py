#!/usr/bin/env python3
"""
Pure Storage Metrics Collector
- Collects performance and capacity metrics
- Stores current metrics and historical time series
- Calculates daily statistics
"""

import requests
import os
from datetime import datetime
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from collectors.common.db import get_db_cursor, get_db_connection, init_database

# Configuration
KEEPASS_URL = os.environ.get('KEEPASS_URL', 'http://usodclpsandadm1.corp.intranet:2000/keepass')
API_VERSION = '1.19'
SCHEMA = os.environ.get('DB_SCHEMA', 'USM')


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


def collect_metrics(array_name):
    """Collect metrics from array"""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Collecting: {array_name}")
    
    api_token = get_api_token(array_name)
    if not api_token:
        print(f"  [SKIP] No credentials")
        return None
    
    session = get_session(array_name, api_token)
    if not session:
        print(f"  [SKIP] Auth failed")
        return None
    
    metrics = {'array_name': array_name, 'collected_at': datetime.now().isoformat()}
    
    try:
        # Array info
        try:
            resp = session.get(f"https://{array_name}/api/{API_VERSION}/array", timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                arr = data[0] if isinstance(data, list) else data
                metrics['purity_version'] = arr.get('version', '')
        except:
            pass
        
        # Performance
        try:
            resp = session.get(f"https://{array_name}/api/{API_VERSION}/array?action=monitor", timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                perf = data[0] if isinstance(data, list) else data
                metrics['read_iops'] = perf.get('reads_per_sec', 0)
                metrics['write_iops'] = perf.get('writes_per_sec', 0)
                metrics['read_latency_us'] = perf.get('usec_per_read_op', 0)
                metrics['write_latency_us'] = perf.get('usec_per_write_op', 0)
                metrics['read_bandwidth'] = perf.get('input_per_sec', 0)
                metrics['write_bandwidth'] = perf.get('output_per_sec', 0)
        except:
            pass
        
        # Capacity
        try:
            resp = session.get(f"https://{array_name}/api/{API_VERSION}/array?space=true", timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                space = data[0] if isinstance(data, list) else data
                metrics['capacity_total'] = space.get('capacity', 0)
                metrics['capacity_used'] = space.get('total', 0)
                metrics['data_reduction'] = space.get('data_reduction', 1)
                metrics['total_reduction'] = space.get('total_reduction', 1)
                metrics['shared_space'] = space.get('shared_space', 0)
                metrics['snapshot_space'] = space.get('snapshots', 0)
                metrics['volume_space'] = space.get('volumes', 0)
                total = metrics.get('capacity_total', 0)
                used = metrics.get('capacity_used', 0)
                metrics['capacity_used_pct'] = round((used / total * 100), 2) if total > 0 else 0
        except:
            pass
        
        # Controller status
        try:
            resp = session.get(f"https://{array_name}/api/{API_VERSION}/array?controllers=true", timeout=10)
            if resp.status_code == 200:
                statuses = [c.get('status', 'unknown') for c in resp.json()]
                metrics['controller_status'] = 'healthy' if all(s == 'ready' for s in statuses) else 'degraded'
        except:
            pass
        
        print(f"  [OK] IOPS: R={metrics.get('read_iops', 0)} W={metrics.get('write_iops', 0)}")
        return metrics
        
    except Exception as e:
        print(f"  [ERROR] {e}")
        return None
    finally:
        try:
            session.delete(f"https://{array_name}/api/{API_VERSION}/auth/session", timeout=5)
        except:
            pass


def save_metrics(metrics):
    """Save metrics to current and history tables"""
    if not metrics:
        return
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        array_name = metrics['array_name']
        
        # Check existing
        cursor.execute(f"SELECT id FROM {SCHEMA}.metrics_current WHERE array_name = ?", (array_name,))
        existing = cursor.fetchone()
        
        if existing:
            cursor.execute(f"""
                UPDATE {SCHEMA}.metrics_current SET
                    purity_version = ?, read_iops = ?, write_iops = ?,
                    read_latency_us = ?, write_latency_us = ?,
                    read_bandwidth = ?, write_bandwidth = ?,
                    capacity_total = ?, capacity_used = ?, capacity_used_pct = ?,
                    data_reduction = ?, total_reduction = ?,
                    shared_space = ?, snapshot_space = ?, volume_space = ?,
                    controller_status = ?, collected_at = ?
                WHERE array_name = ?
            """, (
                metrics.get('purity_version', ''),
                metrics.get('read_iops', 0), metrics.get('write_iops', 0),
                metrics.get('read_latency_us', 0), metrics.get('write_latency_us', 0),
                metrics.get('read_bandwidth', 0), metrics.get('write_bandwidth', 0),
                metrics.get('capacity_total', 0), metrics.get('capacity_used', 0),
                metrics.get('capacity_used_pct', 0),
                metrics.get('data_reduction', 1), metrics.get('total_reduction', 1),
                metrics.get('shared_space', 0), metrics.get('snapshot_space', 0),
                metrics.get('volume_space', 0),
                metrics.get('controller_status', 'unknown'),
                metrics['collected_at'], array_name
            ))
        else:
            cursor.execute(f"""
                INSERT INTO {SCHEMA}.metrics_current (
                    array_name, purity_version, read_iops, write_iops,
                    read_latency_us, write_latency_us, read_bandwidth, write_bandwidth,
                    capacity_total, capacity_used, capacity_used_pct,
                    data_reduction, total_reduction, shared_space, snapshot_space, volume_space,
                    controller_status, collected_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                array_name, metrics.get('purity_version', ''),
                metrics.get('read_iops', 0), metrics.get('write_iops', 0),
                metrics.get('read_latency_us', 0), metrics.get('write_latency_us', 0),
                metrics.get('read_bandwidth', 0), metrics.get('write_bandwidth', 0),
                metrics.get('capacity_total', 0), metrics.get('capacity_used', 0),
                metrics.get('capacity_used_pct', 0),
                metrics.get('data_reduction', 1), metrics.get('total_reduction', 1),
                metrics.get('shared_space', 0), metrics.get('snapshot_space', 0),
                metrics.get('volume_space', 0),
                metrics.get('controller_status', 'unknown'),
                metrics['collected_at']
            ))
        
        # Save to history for time series
        cursor.execute(f"""
            INSERT INTO {SCHEMA}.metrics_history (
                array_name, collected_at,
                read_latency_us, write_latency_us, read_iops, write_iops,
                read_bandwidth, write_bandwidth,
                capacity_total, capacity_used, capacity_used_pct, data_reduction
            ) VALUES (?, GETDATE(), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            array_name,
            metrics.get('read_latency_us', 0), metrics.get('write_latency_us', 0),
            metrics.get('read_iops', 0), metrics.get('write_iops', 0),
            metrics.get('read_bandwidth', 0), metrics.get('write_bandwidth', 0),
            metrics.get('capacity_total', 0), metrics.get('capacity_used', 0),
            metrics.get('capacity_used_pct', 0), metrics.get('data_reduction', 1)
        ))
        
        conn.commit()


def calculate_daily_stats():
    """Calculate daily statistics"""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Calculating daily stats...")
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        stat_date = datetime.now().date()
        
        # Delete existing
        cursor.execute(f"DELETE FROM {SCHEMA}.daily_stats WHERE stat_date = ?", (stat_date,))
        
        # Get counts
        cursor.execute(f"SELECT COUNT(DISTINCT array_name) FROM {SCHEMA}.metrics_current")
        total_arrays = cursor.fetchone()[0] or 0
        
        cursor.execute(f"SELECT COUNT(*) FROM {SCHEMA}.volumes_cache")
        total_volumes = cursor.fetchone()[0] or 0
        
        cursor.execute(f"SELECT COUNT(*) FROM {SCHEMA}.hosts_cache")
        total_hosts = cursor.fetchone()[0] or 0
        
        # Capacity
        cursor.execute(f"""
            SELECT SUM(CAST(capacity_total AS FLOAT))/1099511627776,
                   SUM(CAST(capacity_used AS FLOAT))/1099511627776,
                   AVG(capacity_used_pct), AVG(data_reduction)
            FROM {SCHEMA}.metrics_current
        """)
        cap = cursor.fetchone()
        
        # Alerts
        cursor.execute(f"""
            SELECT SUM(CASE WHEN severity='critical' AND resolved=0 THEN 1 ELSE 0 END),
                   SUM(CASE WHEN severity='warning' AND resolved=0 THEN 1 ELSE 0 END),
                   SUM(CASE WHEN severity NOT IN ('critical','warning') AND resolved=0 THEN 1 ELSE 0 END),
                   SUM(CASE WHEN resolved=1 THEN 1 ELSE 0 END)
            FROM {SCHEMA}.messages
        """)
        alerts = cursor.fetchone()
        
        # Performance
        cursor.execute(f"""
            SELECT AVG(read_latency_us), AVG(write_latency_us), AVG(read_iops+write_iops)
            FROM {SCHEMA}.metrics_current
        """)
        perf = cursor.fetchone()
        
        # Insert
        cursor.execute(f"""
            INSERT INTO {SCHEMA}.daily_stats (
                stat_date, total_arrays, total_volumes, total_hosts,
                total_capacity_tb, total_used_tb, avg_utilization_pct, avg_data_reduction,
                critical_alerts, warning_alerts, info_alerts, resolved_alerts,
                avg_read_latency_us, avg_write_latency_us, avg_total_iops
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            stat_date, total_arrays, total_volumes, total_hosts,
            cap[0] or 0, cap[1] or 0, cap[2] or 0, cap[3] or 1,
            alerts[0] or 0, alerts[1] or 0, alerts[2] or 0, alerts[3] or 0,
            perf[0] or 0, perf[1] or 0, perf[2] or 0
        ))
        conn.commit()
        print(f"  [OK] Daily stats saved")


def cleanup_old_data(days=7):
    """Cleanup old time series data"""
    with get_db_cursor() as cursor:
        cursor.execute(f"""
            DELETE FROM {SCHEMA}.metrics_history 
            WHERE collected_at < DATEADD(DAY, -?, GETDATE())
        """, (days,))
        print(f"[CLEANUP] Removed data older than {days} days")


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
    
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Collecting metrics from {len(arrays)} arrays")
    
    success = 0
    for arr in arrays:
        try:
            metrics = collect_metrics(arr)
            if metrics:
                save_metrics(metrics)
                success += 1
        except Exception as e:
            print(f"  [ERROR] {arr}: {e}")
    
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Complete: {success}/{len(arrays)}")
    
    try:
        calculate_daily_stats()
    except Exception as e:
        print(f"[ERROR] Daily stats: {e}")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Pure Storage Metrics Collector')
    parser.add_argument('array', nargs='?', help='Single array')
    parser.add_argument('--all', action='store_true', help='All arrays')
    parser.add_argument('--init-db', action='store_true', help='Init database')
    parser.add_argument('--daily-stats', action='store_true', help='Calculate daily stats')
    parser.add_argument('--cleanup', type=int, help='Cleanup data older than N days')
    
    args = parser.parse_args()
    
    if args.init_db:
        init_database()
    elif args.daily_stats:
        calculate_daily_stats()
    elif args.cleanup:
        cleanup_old_data(args.cleanup)
    elif args.all:
        collect_all()
    elif args.array:
        init_database()
        m = collect_metrics(args.array)
        if m:
            save_metrics(m)
    else:
        parser.print_help()
