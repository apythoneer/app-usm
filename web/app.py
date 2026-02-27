#!/usr/bin/env python3
"""
Unified Storage Monitoring Dashboard v8.3 - USM Enhanced
With Analytics, Time Series, Teams/ServiceNow Integration
Fixes: Volume/Host data, Analytics redesign, Split logs, Settings improvements
"""

from flask import Flask, render_template_string, jsonify, request
import pyodbc
import os
import json
import glob
import requests
import subprocess
from datetime import datetime

app = Flask(__name__)

# Configuration
SQL_SERVER = os.environ.get('SQL_SERVER', 'usidcvsql0252.ctl.intranet')
SQL_DATABASE = os.environ.get('SQL_DATABASE', 'StorMart')
SQL_CRED_KEY = os.environ.get('SQL_CRED_KEY', 'SQLServerDB')
KEEPASS_URL = os.environ.get('KEEPASS_URL', 'http://usodclpsandadm1.corp.intranet:2000/keepass')
LOG_DIRECTORY = os.environ.get('LOG_DIRECTORY', '/app/logs')
PURITY_VERSIONS_FILE = os.environ.get('PURITY_VERSIONS_FILE', '/app/versions/purity_versions.json')
SCHEDULER_URL = os.environ.get('SCHEDULER_URL', 'http://pure-scheduler:5001')
COLLECTORS_DIR = os.environ.get('COLLECTORS_DIR', '/app/collectors')
SCHEMA = os.environ.get('DB_SCHEMA', 'USM')

# Config file - use logs directory (already mounted) for persistence
def get_config_path():
    # Prioritize the logs directory since it's already a shared mount
    paths = [
        os.environ.get('CONFIG_FILE', ''),
        os.path.join(LOG_DIRECTORY, 'usm_config.json'),  # Same as logs - already mounted!
        '/app/config/settings.json',
        '/app/settings.json',
        os.path.join(os.path.dirname(__file__), 'settings.json'),
        '/tmp/usm_settings.json'
    ]
    for p in paths:
        if not p:
            continue
        try:
            d = os.path.dirname(p)
            if d and not os.path.exists(d):
                os.makedirs(d, exist_ok=True)
            # Test if we can write
            with open(p, 'a') as f:
                pass
            return p
        except:
            continue
    return '/tmp/usm_settings.json'

CONFIG_FILE = None  # Will be set on first use

# Default config
DEFAULT_CONFIG = {
    'teams_webhook_url': os.environ.get('TEAMS_WEBHOOK_URL', ''),
    'snow_enabled': False,
    'snow_instance': '',
    'snow_user': '',
    'notification_channels': {
        'critical': True,
        'warning': True,
        'info': False
    }
}

_sql_creds = None
_app_config = None

def load_config():
    global _app_config, CONFIG_FILE
    if _app_config:
        return _app_config
    
    if CONFIG_FILE is None:
        CONFIG_FILE = get_config_path()
        print(f"[INFO] Using config file: {CONFIG_FILE}")
    
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                _app_config = {**DEFAULT_CONFIG, **json.load(f)}
        else:
            _app_config = DEFAULT_CONFIG.copy()
    except Exception as e:
        print(f"[WARN] Could not load config: {e}")
        _app_config = DEFAULT_CONFIG.copy()
    return _app_config

def save_config(config):
    global _app_config, CONFIG_FILE
    
    if CONFIG_FILE is None:
        CONFIG_FILE = get_config_path()
    
    try:
        # Ensure directory exists
        config_dir = os.path.dirname(CONFIG_FILE)
        if config_dir and not os.path.exists(config_dir):
            os.makedirs(config_dir, exist_ok=True)
        
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
        _app_config = config
        print(f"[INFO] Config saved to {CONFIG_FILE}")
        return True
    except Exception as e:
        print(f"[ERROR] save_config to {CONFIG_FILE}: {e}")
        # Try fallback location
        try:
            fallback = '/tmp/usm_settings.json'
            with open(fallback, 'w') as f:
                json.dump(config, f, indent=2)
            _app_config = config
            CONFIG_FILE = fallback
            print(f"[INFO] Config saved to fallback: {fallback}")
            return True
        except Exception as e2:
            print(f"[ERROR] save_config fallback failed: {e2}")
            return False

def get_sql_credentials():
    global _sql_creds
    if _sql_creds:
        return _sql_creds
    try:
        url = f"{KEEPASS_URL}/{SQL_CRED_KEY}"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            _sql_creds = {
                'username': data.get('UserName', data.get('Username', '')),
                'password': data.get('Password', '')
            }
            return _sql_creds
    except Exception as e:
        print(f"[ERROR] KeePass: {e}")
    return None

def get_db():
    creds = get_sql_credentials()
    if not creds or not creds.get('username'):
        raise Exception("Could not get SQL credentials")

    drivers = ['ODBC Driver 18 for SQL Server', 'ODBC Driver 17 for SQL Server']
    available = pyodbc.drivers()
    driver = next((d for d in drivers if d in available), 'ODBC Driver 17 for SQL Server')

    conn_str = f"DRIVER={{{driver}}};SERVER={SQL_SERVER};DATABASE={SQL_DATABASE};UID={creds['username']};PWD={creds['password']};TrustServerCertificate=yes;"
    return pyodbc.connect(conn_str)

def rows_to_dicts(cursor, rows):
    if not rows: return []
    cols = [c[0] for c in cursor.description]
    return [dict(zip(cols, row)) for row in rows]

def get_table_name(table):
    """Returns schema-qualified table name, checking both USM and dbo"""
    return f"{SCHEMA}.{table}"

# ============== API ROUTES ==============

@app.route('/api/metrics')
def api_metrics():
    try:
        conn = get_db()
        cur = conn.cursor()
        
        # Try USM schema first, fallback to dbo
        try:
            cur.execute(f"""
                SELECT array_name, purity_version, array_status, controller_status, network_status,
                       read_latency_us, write_latency_us, read_iops, write_iops,
                       read_bandwidth, write_bandwidth,
                       capacity_total, capacity_used, capacity_used_pct,
                       data_reduction, total_reduction, shared_space, snapshot_space, volume_space,
                       uptime_seconds, uptime_str, last_reboot, reboot_count, collected_at
                FROM {SCHEMA}.metrics_current ORDER BY array_name
            """)
        except:
            cur.execute("""
                SELECT array_name, purity_version, array_status, controller_status, network_status,
                       read_latency_us, write_latency_us, read_iops, write_iops,
                       read_bandwidth, write_bandwidth,
                       capacity_total, capacity_used, capacity_used_pct,
                       data_reduction, total_reduction, shared_space, snapshot_space, volume_space,
                       uptime_seconds, uptime_str, last_reboot, reboot_count, collected_at
                FROM dbo.metrics_current ORDER BY array_name
            """)
        result = rows_to_dicts(cur, cur.fetchall())

        # Determine which schema has volume/host data by checking total counts
        vol_schema = 'dbo'
        host_schema = 'dbo'
        
        for schema in [SCHEMA, 'dbo']:
            try:
                cur.execute(f"SELECT COUNT(*) FROM {schema}.volumes_cache")
                count = cur.fetchone()[0]
                if count > 0:
                    vol_schema = schema
                    break
            except:
                pass
        
        for schema in [SCHEMA, 'dbo']:
            try:
                cur.execute(f"SELECT COUNT(*) FROM {schema}.hosts_cache")
                count = cur.fetchone()[0]
                if count > 0:
                    host_schema = schema
                    break
            except:
                pass
        
        # Now get counts per array using the correct schema
        for item in result:
            arr = item['array_name']
            
            # Volume count
            try:
                cur.execute(f"SELECT COUNT(*) FROM {vol_schema}.volumes_cache WHERE array_name = ?", (arr,))
                item['volume_count'] = cur.fetchone()[0]
            except:
                item['volume_count'] = 0
            
            # Host count
            try:
                cur.execute(f"SELECT COUNT(*) FROM {host_schema}.hosts_cache WHERE array_name = ?", (arr,))
                item['host_count'] = cur.fetchone()[0]
            except:
                item['host_count'] = 0
            
            # Alerts - check both schemas
            item['active_alerts'] = 0
            for schema in [SCHEMA, 'dbo']:
                try:
                    cur.execute(f"SELECT COUNT(*) FROM {schema}.messages WHERE array_name = ? AND resolved = 0 AND suppressed = 0", (arr,))
                    count = cur.fetchone()[0]
                    if count > 0:
                        item['active_alerts'] = count
                        break
                except:
                    pass

        cur.close()
        conn.close()
        return jsonify(result)
    except Exception as e:
        print(f"[ERROR] api_metrics: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/array/<array_name>')
def api_array_detail(array_name):
    try:
        conn = get_db()
        cur = conn.cursor()

        # Try USM then dbo
        try:
            cur.execute(f"SELECT * FROM {SCHEMA}.metrics_current WHERE array_name = ?", (array_name,))
        except:
            cur.execute("SELECT * FROM dbo.metrics_current WHERE array_name = ?", (array_name,))
        row = cur.fetchone()
        if not row:
            return jsonify({'error': 'Array not found'}), 404

        cols = [c[0] for c in cur.description]
        metrics = dict(zip(cols, row))

        # Volumes
        try:
            cur.execute(f"SELECT * FROM {SCHEMA}.volumes_cache WHERE array_name = ? ORDER BY volume_name", (array_name,))
            volumes = rows_to_dicts(cur, cur.fetchall())
        except:
            try:
                cur.execute("SELECT * FROM dbo.volumes_cache WHERE array_name = ? ORDER BY volume_name", (array_name,))
                volumes = rows_to_dicts(cur, cur.fetchall())
            except:
                volumes = []

        # Hosts
        try:
            cur.execute(f"SELECT * FROM {SCHEMA}.hosts_cache WHERE array_name = ? ORDER BY host_name", (array_name,))
            hosts = rows_to_dicts(cur, cur.fetchall())
        except:
            try:
                cur.execute("SELECT * FROM dbo.hosts_cache WHERE array_name = ? ORDER BY host_name", (array_name,))
                hosts = rows_to_dicts(cur, cur.fetchall())
            except:
                hosts = []

        # Alerts
        try:
            cur.execute(f"SELECT * FROM {SCHEMA}.messages WHERE array_name = ? ORDER BY opened DESC", (array_name,))
            alerts = rows_to_dicts(cur, cur.fetchall())
        except:
            try:
                cur.execute("SELECT * FROM dbo.messages WHERE array_name = ? ORDER BY opened DESC", (array_name,))
                alerts = rows_to_dicts(cur, cur.fetchall())
            except:
                alerts = []

        cur.close()
        conn.close()

        return jsonify({'metrics': metrics, 'volumes': volumes, 'hosts': hosts, 'alerts': alerts})
    except Exception as e:
        print(f"[ERROR] api_array_detail: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/all_volumes')
def api_all_volumes():
    try:
        conn = get_db()
        cur = conn.cursor()

        # Try USM first, then dbo
        volumes = []
        for schema in [SCHEMA, 'dbo']:
            if volumes:
                break
            try:
                cur.execute(f"""
                    SELECT array_name, volume_name, size, used, data_reduction, total_reduction,
                           thin_provisioning, snapshots, snap_count, created, serial, hosts, host_groups,
                           protection_groups, notes, last_updated
                    FROM {schema}.volumes_cache ORDER BY array_name, volume_name
                """)
                volumes = rows_to_dicts(cur, cur.fetchall())
            except:
                try:
                    cur.execute(f"""
                        SELECT array_name, volume_name, size, used, data_reduction, total_reduction,
                               thin_provisioning, snapshots, created, serial, hosts, host_groups,
                               protection_groups, notes, last_updated
                        FROM {schema}.volumes_cache ORDER BY array_name, volume_name
                    """)
                    volumes = rows_to_dicts(cur, cur.fetchall())
                except:
                    pass

        cur.close()
        conn.close()

        total_prov = sum(v.get('size') or 0 for v in volumes)
        total_used = sum(v.get('used') or 0 for v in volumes)
        total_snap = sum(v.get('snapshots') or 0 for v in volumes)
        drs = [v.get('data_reduction') or 1 for v in volumes if (v.get('data_reduction') or 0) >= 1]

        return jsonify({
            'volumes': volumes,
            'total_provisioned': total_prov,
            'total_used': total_used,
            'total_snapshots': total_snap,
            'avg_dr': sum(drs)/len(drs) if drs else 1,
            'count': len(volumes)
        })
    except Exception as e:
        print(f"[ERROR] api_all_volumes: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/all_hosts')
def api_all_hosts():
    try:
        conn = get_db()
        cur = conn.cursor()
        
        hosts = []
        for schema in [SCHEMA, 'dbo']:
            if hosts:
                break
            try:
                cur.execute(f"""
                    SELECT array_name, host_name, iqn, wwn, nqn, host_group, volumes, last_updated
                    FROM {schema}.hosts_cache ORDER BY array_name, host_name
                """)
                hosts = rows_to_dicts(cur, cur.fetchall())
            except:
                pass

        cur.close()
        conn.close()

        groups = set(h.get('host_group') for h in hosts if h.get('host_group'))
        iscsi = sum(1 for h in hosts if h.get('iqn'))
        fc = sum(1 for h in hosts if h.get('wwn'))
        nvme = sum(1 for h in hosts if h.get('nqn'))

        return jsonify({
            'hosts': hosts,
            'count': len(hosts),
            'total_groups': len(groups),
            'iscsi_count': iscsi,
            'fc_count': fc,
            'nvme_count': nvme
        })
    except Exception as e:
        print(f"[ERROR] api_all_hosts: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/alerts')
def api_alerts():
    try:
        conn = get_db()
        cur = conn.cursor()
        
        result = []
        for schema in [SCHEMA, 'dbo']:
            if result:
                break
            try:
                cur.execute(f"""
                    SELECT array_name, message_id, event, severity, component_type, component_name,
                           opened, closed, expected, actual, collected_at, alerted,
                           suppressed, resolved, snow_ticket, teams_notified
                    FROM {schema}.messages ORDER BY opened DESC
                """)
                result = rows_to_dicts(cur, cur.fetchall())
            except:
                try:
                    cur.execute(f"""
                        SELECT array_name, message_id, event, severity, component_type, component_name,
                               opened, closed, expected, actual, collected_at, 
                               CAST(NULL as NVARCHAR(50)) as alerted,
                               suppressed, resolved, 
                               CAST(NULL as NVARCHAR(100)) as snow_ticket,
                               CAST(NULL as DATETIME2) as teams_notified
                        FROM {schema}.messages ORDER BY opened DESC
                    """)
                    result = rows_to_dicts(cur, cur.fetchall())
                except:
                    pass
        
        cur.close()
        conn.close()
        return jsonify(result)
    except Exception as e:
        print(f"[ERROR] api_alerts: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/topology/volume/<path:array_name>/<path:volume_name>')
def api_topology_volume(array_name, volume_name):
    try:
        conn = get_db()
        cur = conn.cursor()

        vol = None
        vol_schema = None
        for schema in [SCHEMA, 'dbo']:
            if vol:
                break
            try:
                cur.execute(f"SELECT * FROM {schema}.volumes_cache WHERE array_name = ? AND volume_name = ?", (array_name, volume_name))
                rows = cur.fetchall()
                if rows:
                    cols = [c[0] for c in cur.description]
                    vol = dict(zip(cols, rows[0]))
                    vol_schema = schema
            except:
                pass

        if not vol:
            return jsonify({'error': 'Volume not found'}), 404

        # Get hosts - try multiple approaches
        hosts = []
        host_schema = None
        
        # First, check the 'hosts' field in the volume record
        vol_hosts_str = vol.get('hosts') or ''
        if vol_hosts_str:
            host_names = [h.strip() for h in vol_hosts_str.split(',') if h.strip()]
            for hname in host_names:
                for schema in [SCHEMA, 'dbo']:
                    try:
                        cur.execute(f"SELECT * FROM {schema}.hosts_cache WHERE array_name = ? AND host_name = ?", (array_name, hname))
                        h_rows = cur.fetchall()
                        if h_rows:
                            h_cols = [c[0] for c in cur.description]
                            hosts.append(dict(zip(h_cols, h_rows[0])))
                            host_schema = schema
                            break
                    except:
                        pass
        
        # Also search hosts_cache for hosts that have this volume in their volumes field
        for schema in [SCHEMA, 'dbo']:
            try:
                # Try exact match first, then pattern
                patterns = [
                    f'{volume_name}',  # exact
                    f'{volume_name},%',  # at start
                    f'%,{volume_name}',  # at end
                    f'%,{volume_name},%',  # in middle
                    f'%{volume_name}%'  # anywhere (fallback)
                ]
                for pattern in patterns:
                    cur.execute(f"""
                        SELECT * FROM {schema}.hosts_cache 
                        WHERE array_name = ? AND volumes LIKE ?
                    """, (array_name, pattern))
                    h_rows = cur.fetchall()
                    if h_rows:
                        h_cols = [c[0] for c in cur.description]
                        for row in h_rows:
                            host_dict = dict(zip(h_cols, row))
                            # Avoid duplicates
                            if not any(h['host_name'] == host_dict['host_name'] for h in hosts):
                                hosts.append(host_dict)
                        host_schema = schema
                        break
            except:
                pass

        # Get host groups if any
        host_groups = []
        vol_hg_str = vol.get('host_groups') or ''
        if vol_hg_str:
            hg_names = [hg.strip() for hg in vol_hg_str.split(',') if hg.strip()]
            for hgname in hg_names:
                for schema in [SCHEMA, 'dbo']:
                    try:
                        cur.execute(f"SELECT * FROM {schema}.host_groups_cache WHERE array_name = ? AND host_group_name = ?", (array_name, hgname))
                        hg_rows = cur.fetchall()
                        if hg_rows:
                            hg_cols = [c[0] for c in cur.description]
                            host_groups.append(dict(zip(hg_cols, hg_rows[0])))
                            break
                    except:
                        pass

        # Get protection groups if any
        protection_groups = []
        vol_pg_str = vol.get('protection_groups') or ''
        if vol_pg_str:
            pg_names = [pg.strip() for pg in vol_pg_str.split(',') if pg.strip()]
            for pgname in pg_names:
                for schema in [SCHEMA, 'dbo']:
                    try:
                        cur.execute(f"SELECT * FROM {schema}.protection_groups_cache WHERE array_name = ? AND pg_name = ?", (array_name, pgname))
                        pg_rows = cur.fetchall()
                        if pg_rows:
                            pg_cols = [c[0] for c in cur.description]
                            protection_groups.append(dict(zip(pg_cols, pg_rows[0])))
                            break
                    except:
                        pass

        cur.close()
        conn.close()

        return jsonify({
            'volume': vol, 
            'hosts': hosts, 
            'host_groups': host_groups,
            'protection_groups': protection_groups,
            'array_name': array_name
        })
    except Exception as e:
        print(f"[ERROR] api_topology_volume: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/topology/host/<path:array_name>/<path:host_name>')
def api_topology_host(array_name, host_name):
    try:
        conn = get_db()
        cur = conn.cursor()

        host = None
        host_schema = None
        for schema in [SCHEMA, 'dbo']:
            if host:
                break
            try:
                cur.execute(f"SELECT * FROM {schema}.hosts_cache WHERE array_name = ? AND host_name = ?", (array_name, host_name))
                rows = cur.fetchall()
                if rows:
                    cols = [c[0] for c in cur.description]
                    host = dict(zip(cols, rows[0]))
                    host_schema = schema
            except:
                pass

        if not host:
            return jsonify({'error': 'Host not found'}), 404

        # Get volumes from the host's volumes field
        volumes = []
        vols_str = host.get('volumes') or ''
        if vols_str:
            vol_names = [v.strip() for v in vols_str.split(',') if v.strip()]
            for vname in vol_names:
                for schema in [SCHEMA, 'dbo']:
                    try:
                        cur.execute(f"SELECT * FROM {schema}.volumes_cache WHERE array_name = ? AND volume_name = ?", (array_name, vname))
                        v_rows = cur.fetchall()
                        if v_rows:
                            v_cols = [c[0] for c in cur.description]
                            volumes.append(dict(zip(v_cols, v_rows[0])))
                            break
                    except:
                        pass
        
        # Also search volumes_cache for volumes that have this host in their hosts field
        for schema in [SCHEMA, 'dbo']:
            try:
                patterns = [
                    f'{host_name}',
                    f'{host_name},%',
                    f'%,{host_name}',
                    f'%,{host_name},%',
                    f'%{host_name}%'
                ]
                for pattern in patterns:
                    cur.execute(f"""
                        SELECT * FROM {schema}.volumes_cache 
                        WHERE array_name = ? AND hosts LIKE ?
                    """, (array_name, pattern))
                    v_rows = cur.fetchall()
                    if v_rows:
                        v_cols = [c[0] for c in cur.description]
                        for row in v_rows:
                            vol_dict = dict(zip(v_cols, row))
                            # Avoid duplicates
                            if not any(v['volume_name'] == vol_dict['volume_name'] for v in volumes):
                                volumes.append(vol_dict)
                        break
            except:
                pass

        # Get host group info if host belongs to one
        host_group = None
        hg_name = host.get('host_group') or ''
        if hg_name:
            for schema in [SCHEMA, 'dbo']:
                try:
                    cur.execute(f"SELECT * FROM {schema}.host_groups_cache WHERE array_name = ? AND host_group_name = ?", (array_name, hg_name))
                    hg_rows = cur.fetchall()
                    if hg_rows:
                        hg_cols = [c[0] for c in cur.description]
                        host_group = dict(zip(hg_cols, hg_rows[0]))
                        break
                except:
                    pass

        cur.close()
        conn.close()

        return jsonify({
            'host': host, 
            'volumes': volumes, 
            'host_group': host_group,
            'array_name': array_name
        })
    except Exception as e:
        print(f"[ERROR] api_topology_host: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/update_volume_notes', methods=['POST'])
def api_update_volume_notes():
    try:
        data = request.json or request.form
        array = data.get('array_name')
        volume = data.get('volume_name')
        notes = data.get('notes', '')

        if not array or not volume:
            return jsonify({'error': 'Missing array_name or volume_name'}), 400

        conn = get_db()
        cur = conn.cursor()
        
        for schema in [SCHEMA, 'dbo']:
            try:
                cur.execute(f"UPDATE {schema}.volumes_cache SET notes = ? WHERE array_name = ? AND volume_name = ?",
                           (notes, array, volume))
                conn.commit()
                break
            except:
                pass
        
        cur.close()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        print(f"[ERROR] api_update_volume_notes: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/update_alert', methods=['POST'])
def api_update_alert():
    try:
        data = request.json or request.form
        array = data.get('array')
        msg_id = data.get('message_id')
        field = data.get('field')
        value = data.get('value')

        if field not in ['resolved', 'suppressed', 'snow_ticket']:
            return jsonify({'error': 'Invalid field'}), 400

        conn = get_db()
        cur = conn.cursor()
        
        for schema in [SCHEMA, 'dbo']:
            try:
                if field == 'snow_ticket':
                    cur.execute(f"UPDATE {schema}.messages SET snow_ticket = ? WHERE array_name = ? AND message_id = ?", (value, array, msg_id))
                else:
                    cur.execute(f"UPDATE {schema}.messages SET {field} = ? WHERE array_name = ? AND message_id = ?", (1 if value else 0, array, msg_id))
                conn.commit()
                break
            except:
                pass
        
        cur.close()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        print(f"[ERROR] api_update_alert: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/log_files')
def api_log_files():
    try:
        files = glob.glob(os.path.join(LOG_DIRECTORY, '*.log'))
        return jsonify(sorted([os.path.basename(f) for f in files]))
    except:
        return jsonify([])

@app.route('/api/logs')
def api_logs():
    try:
        filename = request.args.get('file', '')
        lines_count = int(request.args.get('lines', 500))
        if not filename or '..' in filename:
            return jsonify({'lines': []})

        filepath = os.path.join(LOG_DIRECTORY, filename)
        if not os.path.exists(filepath):
            return jsonify({'lines': []})

        with open(filepath, 'r') as f:
            lines = f.readlines()[-lines_count:]
        return jsonify({'lines': [l.rstrip() for l in lines], 'file': filename})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/search')
def api_search():
    try:
        q = request.args.get('q', '').strip()
        if not q or len(q) < 2:
            return jsonify({'volumes': [], 'hosts': [], 'arrays': []})

        conn = get_db()
        cur = conn.cursor()
        pattern = f'%{q}%'

        volumes = []
        hosts = []
        arrays = []
        
        for schema in [SCHEMA, 'dbo']:
            if not volumes:
                try:
                    cur.execute(f"SELECT TOP 50 * FROM {schema}.volumes_cache WHERE volume_name LIKE ? OR array_name LIKE ? OR serial LIKE ?", (pattern, pattern, pattern))
                    volumes = rows_to_dicts(cur, cur.fetchall())
                except:
                    pass
            
            if not hosts:
                try:
                    cur.execute(f"SELECT TOP 50 * FROM {schema}.hosts_cache WHERE host_name LIKE ? OR array_name LIKE ? OR ISNULL(iqn,'') LIKE ? OR ISNULL(wwn,'') LIKE ?", (pattern, pattern, pattern, pattern))
                    hosts = rows_to_dicts(cur, cur.fetchall())
                except:
                    pass
            
            if not arrays:
                try:
                    cur.execute(f"SELECT TOP 20 * FROM {schema}.metrics_current WHERE array_name LIKE ?", (pattern,))
                    arrays = rows_to_dicts(cur, cur.fetchall())
                except:
                    pass

        cur.close()
        conn.close()
        return jsonify({'volumes': volumes, 'hosts': hosts, 'arrays': arrays})
    except Exception as e:
        print(f"[ERROR] api_search: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/purity_versions')
def api_purity_versions():
    try:
        if os.path.exists(PURITY_VERSIONS_FILE):
            with open(PURITY_VERSIONS_FILE, 'r') as f:
                return jsonify(json.load(f))
        return jsonify({
            "latest": "6.6.4",
            "recommended": ["6.6.3", "6.6.4", "6.5.6"],
            "current": ["6.6.0", "6.6.1", "6.6.2", "6.6.3", "6.6.4", "6.5.4", "6.5.5", "6.5.6"],
            "eol": ["5.3.0", "5.3.1", "6.0.0", "6.1.0", "6.2.0", "6.3.0", "6.4.0", "6.4.1"]
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/health')
def api_health():
    health = {'status': 'healthy', 'checks': {}}
    try:
        creds = get_sql_credentials()
        health['checks']['keepass'] = 'ok' if creds and creds.get('username') else 'error'
    except:
        health['checks']['keepass'] = 'error'
        health['status'] = 'degraded'

    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.close()
        conn.close()
        health['checks']['database'] = 'ok'
    except Exception as e:
        health['checks']['database'] = f'error: {str(e)}'
        health['status'] = 'degraded'

    return jsonify(health)

# ============== TIME SERIES API ENDPOINTS ==============

@app.route('/api/timeseries/latency')
def api_timeseries_latency():
    hours = request.args.get('hours', 24, type=int)
    arrays = request.args.getlist('arrays')  # Multiple arrays
    
    try:
        conn = get_db()
        cur = conn.cursor()
        
        # Get per-array data (not aggregated)
        if arrays and len(arrays) > 0 and arrays[0]:
            placeholders = ','.join(['?' for _ in arrays])
            cur.execute(f"""
                SELECT array_name, collected_at, read_latency_us, write_latency_us
                FROM {SCHEMA}.metrics_history
                WHERE array_name IN ({placeholders}) AND collected_at > DATEADD(HOUR, -?, GETDATE())
                ORDER BY array_name, collected_at
            """, (*arrays, hours))
        else:
            cur.execute(f"""
                SELECT array_name, collected_at, read_latency_us, write_latency_us
                FROM {SCHEMA}.metrics_history
                WHERE collected_at > DATEADD(HOUR, -?, GETDATE())
                ORDER BY array_name, collected_at
            """, (hours,))
        
        rows = cur.fetchall()
        cur.close()
        conn.close()
        
        # Organize by array
        data_by_array = {}
        all_times = set()
        for r in rows:
            arr = r[0]
            if arr not in data_by_array:
                data_by_array[arr] = {'read': {}, 'write': {}}
            time_str = r[1].strftime('%H:%M') if r[1] else ''
            all_times.add(time_str)
            data_by_array[arr]['read'][time_str] = float(r[2] or 0)
            data_by_array[arr]['write'][time_str] = float(r[3] or 0)
        
        labels = sorted(list(all_times))
        datasets = []
        colors = ['#FF6384', '#36A2EB', '#FFCE56', '#4BC0C0', '#9966FF', '#FF9F40', '#7CFC00', '#00CED1', '#FF69B4', '#8A2BE2', '#DC143C']
        
        i = 0
        for arr, data in data_by_array.items():
            color = colors[i % len(colors)]
            datasets.append({
                'label': f'{arr} Read',
                'data': [data['read'].get(t, 0) for t in labels],
                'borderColor': color,
                'borderDash': [],
                'fill': False
            })
            datasets.append({
                'label': f'{arr} Write',
                'data': [data['write'].get(t, 0) for t in labels],
                'borderColor': color,
                'borderDash': [5, 5],
                'fill': False
            })
            i += 1
        
        return jsonify({'labels': labels, 'datasets': datasets})
    except Exception as e:
        print(f"[ERROR] api_timeseries_latency: {e}")
        return jsonify({'labels': [], 'datasets': []})


@app.route('/api/timeseries/iops')
def api_timeseries_iops():
    hours = request.args.get('hours', 24, type=int)
    arrays = request.args.getlist('arrays')
    
    try:
        conn = get_db()
        cur = conn.cursor()
        
        if arrays and len(arrays) > 0 and arrays[0]:
            placeholders = ','.join(['?' for _ in arrays])
            cur.execute(f"""
                SELECT array_name, collected_at, read_iops, write_iops
                FROM {SCHEMA}.metrics_history
                WHERE array_name IN ({placeholders}) AND collected_at > DATEADD(HOUR, -?, GETDATE())
                ORDER BY array_name, collected_at
            """, (*arrays, hours))
        else:
            cur.execute(f"""
                SELECT array_name, collected_at, read_iops, write_iops
                FROM {SCHEMA}.metrics_history
                WHERE collected_at > DATEADD(HOUR, -?, GETDATE())
                ORDER BY array_name, collected_at
            """, (hours,))
        
        rows = cur.fetchall()
        cur.close()
        conn.close()
        
        data_by_array = {}
        all_times = set()
        for r in rows:
            arr = r[0]
            if arr not in data_by_array:
                data_by_array[arr] = {'read': {}, 'write': {}}
            time_str = r[1].strftime('%H:%M') if r[1] else ''
            all_times.add(time_str)
            data_by_array[arr]['read'][time_str] = float(r[2] or 0)
            data_by_array[arr]['write'][time_str] = float(r[3] or 0)
        
        labels = sorted(list(all_times))
        datasets = []
        colors = ['#FF6384', '#36A2EB', '#FFCE56', '#4BC0C0', '#9966FF', '#FF9F40', '#7CFC00', '#00CED1', '#FF69B4', '#8A2BE2', '#DC143C']
        
        i = 0
        for arr, data in data_by_array.items():
            color = colors[i % len(colors)]
            datasets.append({
                'label': f'{arr} Read',
                'data': [data['read'].get(t, 0) for t in labels],
                'borderColor': color,
                'borderDash': [],
                'fill': False
            })
            datasets.append({
                'label': f'{arr} Write',
                'data': [data['write'].get(t, 0) for t in labels],
                'borderColor': color,
                'borderDash': [5, 5],
                'fill': False
            })
            i += 1
        
        return jsonify({'labels': labels, 'datasets': datasets})
    except Exception as e:
        print(f"[ERROR] api_timeseries_iops: {e}")
        return jsonify({'labels': [], 'datasets': []})


@app.route('/api/timeseries/alerts')
def api_timeseries_alerts():
    days = request.args.get('days', 30, type=int)
    
    try:
        conn = get_db()
        cur = conn.cursor()
        
        for schema in [SCHEMA, 'dbo']:
            try:
                cur.execute(f"""
                    SELECT CAST(opened AS DATE) as dt,
                           SUM(CASE WHEN severity='critical' THEN 1 ELSE 0 END),
                           SUM(CASE WHEN severity='warning' THEN 1 ELSE 0 END),
                           SUM(CASE WHEN severity NOT IN ('critical','warning') THEN 1 ELSE 0 END)
                    FROM {schema}.messages
                    WHERE opened IS NOT NULL AND opened != '' 
                          AND TRY_CAST(opened AS DATE) > DATEADD(DAY, -?, GETDATE())
                    GROUP BY CAST(opened AS DATE)
                    ORDER BY dt
                """, (days,))
                rows = cur.fetchall()
                break
            except:
                rows = []
        
        cur.close()
        conn.close()
        
        return jsonify({
            'labels': [r[0].strftime('%m/%d') if r[0] else '' for r in rows],
            'critical': [int(r[1] or 0) for r in rows],
            'warning': [int(r[2] or 0) for r in rows],
            'info': [int(r[3] or 0) for r in rows]
        })
    except Exception as e:
        print(f"[ERROR] api_timeseries_alerts: {e}")
        return jsonify({'labels': [], 'critical': [], 'warning': [], 'info': []})


@app.route('/api/daily_stats')
def api_daily_stats():
    days = request.args.get('days', 14, type=int)
    
    try:
        conn = get_db()
        cur = conn.cursor()
        
        for schema in [SCHEMA, 'dbo']:
            try:
                cur.execute(f"SELECT TOP (?) * FROM {schema}.daily_stats ORDER BY stat_date DESC", (days,))
                rows = cur.fetchall()
                result = rows_to_dicts(cur, rows)
                break
            except:
                result = []
        
        cur.close()
        conn.close()
        
        for d in result:
            if d.get('stat_date'):
                d['stat_date'] = d['stat_date'].strftime('%Y-%m-%d') if hasattr(d['stat_date'], 'strftime') else str(d['stat_date'])
        
        return jsonify(result)
    except Exception as e:
        print(f"[ERROR] api_daily_stats: {e}")
        return jsonify([])

# ============== CONFIG API ==============

@app.route('/api/config')
def api_get_config():
    config = load_config()
    config['_config_file'] = CONFIG_FILE  # Include path for debugging
    return jsonify(config)

@app.route('/api/config', methods=['POST'])
def api_save_config():
    try:
        data = request.json
        if save_config(data):
            return jsonify({'success': True, 'config_file': CONFIG_FILE})
        return jsonify({'error': f'Failed to save config to {CONFIG_FILE}'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/config/teams_webhook', methods=['POST'])
def api_update_teams_webhook():
    try:
        data = request.json
        config = load_config()
        config['teams_webhook_url'] = data.get('url', '')
        if save_config(config):
            return jsonify({'success': True, 'url': config['teams_webhook_url'], 'saved_to': CONFIG_FILE})
        return jsonify({'error': f'Failed to write to {CONFIG_FILE}. Check container permissions.'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/test_teams_webhook', methods=['POST'])
def api_test_teams_webhook():
    try:
        data = request.json
        url = data.get('url', '')
        if not url:
            return jsonify({'error': 'No URL provided'}), 400
        
        # Send test message
        payload = {
            "type": "message",
            "attachments": [{
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [
                        {"type": "TextBlock", "size": "Medium", "weight": "Bolder", "text": "🧪 USM Test Notification"},
                        {"type": "TextBlock", "text": "This is a test notification from Unified Storage Monitoring.", "wrap": True}
                    ]
                }
            }]
        }
        
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code in [200, 202]:
            return jsonify({'success': True, 'message': 'Test notification sent!'})
        return jsonify({'error': f'Failed: HTTP {resp.status_code}'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ============== SCHEDULER API ==============

def proxy_scheduler(endpoint, method='GET', data=None):
    try:
        url = f"{SCHEDULER_URL}{endpoint}"
        if method == 'GET':
            resp = requests.get(url, timeout=10)
        else:
            resp = requests.post(url, json=data, timeout=10)
        return resp.json(), resp.status_code
    except requests.exceptions.ConnectionError:
        return {'error': 'Scheduler service unavailable', 'scheduler_url': SCHEDULER_URL}, 503
    except Exception as e:
        return {'error': str(e)}, 500

@app.route('/api/scheduler/status')
def api_scheduler_status():
    result, status = proxy_scheduler('/api/jobs')
    return jsonify(result), status

@app.route('/api/scheduler/run/<job_id>', methods=['POST'])
def api_scheduler_run(job_id):
    result, status = proxy_scheduler(f'/api/jobs/{job_id}/run', method='POST')
    return jsonify(result), status

@app.route('/api/scheduler/toggle/<job_id>', methods=['POST'])
def api_scheduler_toggle(job_id):
    data = request.get_json() or {}
    enabled = data.get('enabled', True)
    if enabled:
        result, status = proxy_scheduler(f'/api/jobs/{job_id}/enable', method='POST')
    else:
        result, status = proxy_scheduler(f'/api/jobs/{job_id}/disable', method='POST')
    return jsonify(result), status

@app.route('/api/scheduler/interval/<job_id>', methods=['POST'])
def api_scheduler_interval(job_id):
    data = request.get_json() or {}
    result, status = proxy_scheduler(f'/api/jobs/{job_id}/interval', method='POST', data=data)
    return jsonify(result), status

# ============== CONTAINER STATS API ==============

@app.route('/api/containers')
def api_containers():
    import socket
    import json as json_module

    def docker_api_get(path):
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(10)
            sock.connect('/var/run/docker.sock')
            request_str = f"GET {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
            sock.send(request_str.encode())
            response = b''
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk
            sock.close()
            response_str = response.decode('utf-8', errors='ignore')
            if '\r\n\r\n' in response_str:
                headers, body = response_str.split('\r\n\r\n', 1)
                if 'Transfer-Encoding: chunked' in headers:
                    decoded = ''
                    while body:
                        if '\r\n' not in body:
                            break
                        size_str, body = body.split('\r\n', 1)
                        try:
                            size = int(size_str, 16)
                        except ValueError:
                            break
                        if size == 0:
                            break
                        decoded += body[:size]
                        body = body[size+2:]
                    body = decoded
                return json_module.loads(body) if body.strip() else []
            return []
        except Exception as e:
            return {'error': str(e)}

    try:
        containers_data = docker_api_get('/containers/json?all=false')
        if isinstance(containers_data, dict) and 'error' in containers_data:
            return jsonify({'containers': [], 'error': containers_data['error']})
        containers = []
        for c in containers_data:
            name = c.get('Names', [''])[0].lstrip('/')
            status = c.get('Status', '')
            state = c.get('State', 'unknown')
            containers.append({
                'name': name,
                'status': state,
                'cpu': '-',
                'mem_usage': '-',
                'mem_pct': '-',
                'net_io': '-',
                'block_io': '-',
                'pids': '-',
                'uptime': status,
                'image': c.get('Image', '')[:30]
            })
        return jsonify({'containers': containers})
    except Exception as e:
        return jsonify({'containers': [], 'error': str(e)})

@app.route('/api/container/<name>/<action>', methods=['POST'])
def api_container_action(name, action):
    import socket as sock_module
    if action not in ['restart', 'stop', 'start']:
        return jsonify({'error': 'Invalid action'}), 400
    try:
        sock = sock_module.socket(sock_module.AF_UNIX, sock_module.SOCK_STREAM)
        sock.settimeout(60)
        sock.connect('/var/run/docker.sock')
        path = f"/containers/{name}/{action}"
        request_str = f"POST {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
        sock.send(request_str.encode())
        response = b''
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk
        sock.close()
        response_str = response.decode('utf-8', errors='ignore')
        if 'HTTP/1.1 204' in response_str or 'HTTP/1.1 200' in response_str:
            return jsonify({'status': 'ok', 'action': action, 'container': name})
        elif 'HTTP/1.1 304' in response_str:
            return jsonify({'status': 'ok', 'action': action, 'container': name, 'note': 'Already in desired state'})
        else:
            return jsonify({'error': 'Action failed'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/db_stats')
def api_db_stats():
    try:
        conn = get_db()
        cur = conn.cursor()
        stats = {}
        
        # Check each table in both schemas and report which has data
        for table in ['metrics_current', 'volumes_cache', 'hosts_cache', 'messages', 'host_groups_cache', 'protection_groups_cache']:
            stats[table] = 0
            stats[f'{table}_schema'] = 'none'
            for schema in [SCHEMA, 'dbo']:
                try:
                    cur.execute(f"SELECT COUNT(*) FROM {schema}.{table}")
                    count = cur.fetchone()[0]
                    if count > 0:
                        stats[table] = count
                        stats[f'{table}_schema'] = schema
                        break
                    elif stats[table] == 0:
                        stats[table] = count  # Keep 0 if both empty
                        stats[f'{table}_schema'] = schema
                except:
                    pass
        
        for schema in [SCHEMA, 'dbo']:
            try:
                cur.execute(f"SELECT MAX(collected_at) FROM {schema}.metrics_current")
                row = cur.fetchone()
                if row and row[0]:
                    stats['last_metrics_collection'] = str(row[0])
                    break
            except:
                stats['last_metrics_collection'] = 'N/A'
        
        for schema in [SCHEMA, 'dbo']:
            try:
                cur.execute(f"SELECT MAX(last_updated) FROM {schema}.volumes_cache")
                row = cur.fetchone()
                if row and row[0]:
                    stats['last_volumes_collection'] = str(row[0])
                    break
            except:
                stats['last_volumes_collection'] = 'N/A'
        
        cur.close()
        conn.close()
        return jsonify(stats)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/system_info')
def api_system_info():
    info = {
        'app_version': 'v8.3-USM',
        'python_version': '',
        'hostname': '',
        'uptime': '',
        'collectors_dir': COLLECTORS_DIR,
        'log_dir': LOG_DIRECTORY,
        'schema': SCHEMA,
        'config_file': CONFIG_FILE or get_config_path()
    }
    try:
        import platform
        info['python_version'] = platform.python_version()
        info['hostname'] = platform.node()
    except:
        pass
    try:
        result = subprocess.run(['uptime', '-p'], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            info['uptime'] = result.stdout.strip()
    except:
        pass
    return jsonify(info)

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

HTML_TEMPLATE = r'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Unified Storage Monitoring</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        :root { --bg-dark: #0d1117; --bg-card: #161b22; --bg-card-alt: #1c2128; --bg-hover: #21262d; --border: #30363d; --text: #e6edf3; --text-bright: #ffffff; --text-muted: #8b949e; --pure-orange: #fe5000; --aws-orange: #ff9900; --azure-blue: #0078d4; --green: #3fb950; --yellow: #d29922; --red: #f85149; --blue: #58a6ff; }
        * { box-sizing: border-box; }
        body { background: var(--bg-dark); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; margin: 0; font-size: 14px; }
        .navbar { background: linear-gradient(135deg, #1a1f2e 0%, #0d1117 100%); border-bottom: 1px solid var(--border); padding: 12px 20px; }
        .navbar-brand { color: var(--text-bright) !important; font-weight: 600; font-size: 18px; }
        .nav-tabs { background: var(--bg-card); border-bottom: 1px solid var(--border); padding: 0 20px; display: flex; }
        .nav-tabs .nav-item { flex: 0 0 auto; }
        .nav-tabs .nav-link { color: var(--text-muted); border: none; padding: 14px 20px; white-space: nowrap; }
        .nav-tabs .nav-link:hover { color: var(--text-bright); background: var(--bg-hover); }
        .nav-tabs .nav-link.active { color: var(--pure-orange); border-bottom: 3px solid var(--pure-orange); background: transparent; }
        .tab-content { padding: 20px; }
        .card { background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px; margin-bottom: 16px; }
        .card-header { background: var(--bg-card-alt); border-bottom: 1px solid var(--border); padding: 12px 16px; font-weight: 600; color: var(--text-bright); }
        .card-body { padding: 16px; }
        .summary-card { background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px; padding: 20px; text-align: center; cursor: pointer; transition: all 0.2s; height: 100%; }
        .summary-card:hover { transform: translateY(-2px); border-color: var(--blue); }
        .summary-card .label { font-size: 11px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 1px; margin-bottom: 8px; }
        .summary-card .value { font-size: 28px; font-weight: 700; color: var(--text-bright); }
        .summary-card .subtext { font-size: 12px; color: var(--text); margin-top: 6px; }
        .io-card .value { font-size: 24px; font-weight: 700; color: var(--text-bright); }
        .io-card small { color: var(--text-muted); }
        .vendor-section { margin-bottom: 20px; }
        .vendor-header { background: linear-gradient(135deg, #1a1f2e 0%, #161b22 100%); border: 1px solid var(--border); border-radius: 8px; padding: 16px 20px; margin-bottom: 12px; display: flex; align-items: center; justify-content: space-between; }
        .vendor-title { font-size: 18px; font-weight: 600; color: var(--text-bright); }
        .vendor-stats { display: flex; gap: 24px; font-size: 13px; color: var(--text); }
        .cloud-section { margin-bottom: 12px; margin-left: 20px; }
        .cloud-header { background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px 8px 0 0; padding: 12px 16px; display: flex; justify-content: space-between; align-items: center; cursor: pointer; }
        .cloud-header:hover { background: var(--bg-hover); }
        .cloud-header .title { font-weight: 600; color: var(--text-bright); display: flex; align-items: center; gap: 10px; }
        .cloud-header .stats { font-size: 12px; color: var(--text); display: flex; gap: 20px; }
        .cloud-content { border: 1px solid var(--border); border-top: none; border-radius: 0 0 8px 8px; overflow-x: auto; }
        .cloud-content.collapsed { display: none; }
        .array-table { width: 100%; border-collapse: collapse; table-layout: fixed; }
        .array-table th { background: var(--bg-card-alt); padding: 10px 12px; text-align: left; font-size: 11px; color: var(--text-muted); text-transform: uppercase; border-bottom: 1px solid var(--border); white-space: nowrap; overflow: hidden; }
        .array-table td { padding: 10px 12px; border-bottom: 1px solid var(--border); color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .array-table tr:hover { background: var(--bg-hover); cursor: pointer; }
        .array-link { color: var(--blue); font-weight: 600; }
        .status-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }
        .status-dot.online { background: var(--green); }
        .status-dot.offline { background: var(--red); }
        .capacity-bar { height: 6px; background: var(--border); border-radius: 3px; width: 80px; display: inline-block; margin-right: 8px; vertical-align: middle; }
        .capacity-fill { height: 100%; border-radius: 3px; }
        .capacity-fill.green { background: var(--green); }
        .capacity-fill.yellow { background: var(--yellow); }
        .capacity-fill.red { background: var(--red); }
        .version-badge { padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }
        .version-badge.latest { background: rgba(63,185,80,0.2); color: var(--green); }
        .version-badge.current { background: rgba(88,166,255,0.2); color: var(--blue); }
        .version-badge.update { background: rgba(210,153,34,0.2); color: var(--yellow); }
        .version-badge.eol { background: rgba(248,81,73,0.2); color: var(--red); }
        .cloud-badge { padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }
        .cloud-badge.aws { background: rgba(255,153,0,0.15); color: var(--aws-orange); }
        .cloud-badge.azure { background: rgba(0,120,212,0.15); color: var(--azure-blue); }
        .inventory-card { display: flex; flex-direction: column; height: calc(100vh - 280px); min-height: 400px; }
        .inventory-card .card-body { flex: 1; overflow: hidden; display: flex; flex-direction: column; padding: 0; }
        .inventory-table-wrap { flex: 1; overflow-y: auto; }
        .data-table { width: 100%; border-collapse: collapse; font-size: 12px; }
        .data-table th { background: var(--bg-card-alt); padding: 8px 10px; text-align: left; font-size: 10px; color: var(--text-muted); text-transform: uppercase; border-bottom: 1px solid var(--border); position: sticky; top: 0; z-index: 1; }
        .data-table th.sortable { cursor: pointer; user-select: none; }
        .data-table th.sortable:hover { background: var(--bg-hover); color: var(--text-bright); }
        .data-table th.sortable::after { content: ' ↕'; opacity: 0.3; font-size: 10px; }
        .data-table th.sortable.asc::after { content: ' ↑'; opacity: 1; }
        .data-table th.sortable.desc::after { content: ' ↓'; opacity: 1; }
        .data-table td { padding: 6px 10px; border-bottom: 1px solid var(--border); color: var(--text); white-space: nowrap; }
        .data-table tr:hover { background: var(--bg-hover); cursor: pointer; }
        .data-table .truncate { max-width: 180px; overflow: hidden; text-overflow: ellipsis; }
        .pagination-bar { background: var(--bg-card-alt); border-top: 1px solid var(--border); padding: 10px 16px; display: flex; justify-content: space-between; align-items: center; font-size: 12px; }
        .pagination-bar .info { color: var(--text-muted); }
        .pagination-bar .pages { display: flex; gap: 4px; }
        .page-btn { background: var(--bg-card); border: 1px solid var(--border); color: var(--text); padding: 4px 10px; border-radius: 4px; cursor: pointer; font-size: 12px; }
        .page-btn:hover:not(:disabled) { background: var(--bg-hover); }
        .page-btn.active { background: var(--blue); border-color: var(--blue); color: #fff; }
        .page-btn:disabled { opacity: 0.4; cursor: not-allowed; }
        .inv-summary { background: var(--bg-card); border: 1px solid var(--border); border-radius: 6px; padding: 12px; text-align: center; }
        .inv-summary .label { font-size: 10px; color: var(--text-muted); text-transform: uppercase; }
        .inv-summary .value { font-size: 22px; font-weight: 700; color: var(--text-bright); }
        .inv-summary.alert-critical { border-color: #dc3545; background: rgba(220, 53, 69, 0.1); }
        .inv-summary.alert-critical .value { color: #dc3545; }
        .inv-summary.alert-warning { border-color: #ffc107; background: rgba(255, 193, 7, 0.1); }
        .inv-summary.alert-warning .value { color: #ffc107; }
        .inv-summary.alert-info { border-color: #0dcaf0; background: rgba(13, 202, 240, 0.1); }
        .inv-summary.alert-info .value { color: #0dcaf0; }
        .modal-content { background: var(--bg-card); border: 1px solid var(--border); }
        .modal-header { background: var(--bg-card-alt); border-bottom: 1px solid var(--border); padding: 14px 20px; }
        .modal-header .modal-title { color: var(--text-bright); font-size: 16px; }
        .modal-header .btn-close { filter: invert(1); }
        .modal-body { background: var(--bg-dark); padding: 20px; }
        .topology-container { padding: 24px; }
        .topology-flow { display: flex; align-items: center; justify-content: center; gap: 0; padding: 30px 20px; flex-wrap: nowrap; }
        .topology-node { background: linear-gradient(180deg, #1e2a3a 0%, #162029 100%); border: 2px solid; border-radius: 12px; padding: 24px 28px; text-align: center; min-width: 220px; }
        .topology-node.array { border-color: #00d4aa; }
        .topology-node.host { border-color: #00ff88; }
        .topology-node.volume { border-color: #ff6b6b; }
        .topology-node .node-icon { font-size: 32px; margin-bottom: 10px; }
        .topology-node .node-name { font-weight: 600; color: var(--text-bright); font-size: 14px; word-break: break-word; margin-bottom: 6px; }
        .topology-node .node-detail { font-size: 12px; color: var(--text-muted); }
        .topology-connector { display: flex; align-items: center; padding: 0 6px; }
        .topology-connector .connector-line { width: 50px; height: 4px; background: linear-gradient(90deg, var(--pure-orange), var(--yellow)); border-radius: 2px; }
        .topology-connector .connector-arrow { width: 0; height: 0; border-top: 10px solid transparent; border-bottom: 10px solid transparent; border-left: 14px solid var(--yellow); }
        .topology-volumes-stack, .topology-hosts-stack { display: flex; flex-direction: column; gap: 10px; max-height: 280px; overflow-y: auto; }
        .topology-volume-item { background: linear-gradient(180deg, #2a1a2a 0%, #1f0d1f 100%); border: 2px solid #8b5cf6; border-radius: 10px; padding: 14px 18px; min-width: 200px; display: flex; align-items: center; gap: 12px; }
        .topology-host-item { background: linear-gradient(180deg, #1a2a1a 0%, #0d1f0d 100%); border: 2px solid #00ff88; border-radius: 10px; padding: 14px 18px; min-width: 200px; display: flex; align-items: center; gap: 12px; }
        .metric-row { display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid var(--border); }
        .metric-row:last-child { border-bottom: none; }
        .metric-row .label { color: var(--text-muted); font-size: 12px; }
        .metric-row .value { font-weight: 600; color: var(--text-bright); font-size: 12px; }
        .notes-cell { position: relative; cursor: pointer; }
        .notes-cell:hover { background: var(--bg-hover); }
        .notes-cell .edit-icon { opacity: 0; margin-left: 4px; font-size: 10px; }
        .notes-cell:hover .edit-icon { opacity: 0.6; }
        #pureStorageContent.collapsed { display: none; }
        .form-control, .form-select { background: var(--bg-card); border: 1px solid var(--border); color: var(--text); }
        .form-control:focus { background: var(--bg-card); border-color: var(--blue); color: var(--text); box-shadow: none; }
        .btn-outline-secondary { border-color: var(--border); color: var(--text); }
        .btn-outline-secondary:hover { background: var(--bg-hover); color: var(--text-bright); }
        .btn-outline-secondary.active { background: var(--blue); border-color: var(--blue); color: #fff; }
        .btn-primary { background: var(--blue); border-color: var(--blue); color: #fff; }
        .log-viewer { background: #000; border-radius: 6px; padding: 16px; height: 100%; overflow-y: auto; font-family: monospace; font-size: 12px; line-height: 1.7; color: #e0e0e0; }
        .btn-xs { padding: 2px 6px; font-size: 10px; border-radius: 4px; }
        .log-line.error { color: #ff6b6b; }
        .log-line.warning { color: #ffd93d; }
        .log-line.info { color: #6bceff; }
        .text-success { color: var(--green) !important; }
        .text-warning { color: var(--yellow) !important; }
        .text-danger { color: var(--red) !important; }
        .text-muted { color: var(--text-muted) !important; }
        .pulse { animation: pulse 2s infinite; }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
        ::-webkit-scrollbar { width: 8px; height: 8px; }
        ::-webkit-scrollbar-track { background: var(--bg-dark); }
        ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 4px; }
        /* Split log view */
        .split-logs { display: flex; gap: 10px; height: calc(100vh - 200px); }
        .split-logs .log-panel { flex: 1; display: flex; flex-direction: column; }
        .split-logs .log-panel .log-header { background: var(--bg-card-alt); border: 1px solid var(--border); border-bottom: none; border-radius: 8px 8px 0 0; padding: 10px 16px; display: flex; justify-content: space-between; align-items: center; }
        .split-logs .log-panel .log-viewer { flex: 1; border-radius: 0 0 8px 8px; border: 1px solid var(--border); border-top: none; }
        /* Analytics sidebar */
        .analytics-layout { display: flex; gap: 20px; }
        .analytics-sidebar { width: 280px; flex-shrink: 0; }
        .analytics-main { flex: 1; min-width: 0; }
        .filter-panel { background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px; padding: 16px; margin-bottom: 16px; }
        .filter-panel h6 { color: var(--text-bright); margin-bottom: 12px; font-size: 13px; }
        .filter-panel .form-check { margin-bottom: 6px; }
        .filter-panel .form-check-input { background-color: var(--bg-card); border-color: var(--border); }
        .filter-panel .form-check-input:checked { background-color: var(--blue); border-color: var(--blue); }
        .filter-panel .form-check-label { color: var(--text); font-size: 12px; }
        .array-color-dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; }
        /* Settings improvements */
        .settings-section { background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px; margin-bottom: 16px; }
        .settings-section .section-header { background: var(--bg-card-alt); padding: 12px 16px; border-bottom: 1px solid var(--border); font-weight: 600; color: var(--text-bright); display: flex; justify-content: space-between; align-items: center; }
        .settings-section .section-body { padding: 16px; }
        .job-card { background: var(--bg-card-alt); border: 1px solid var(--border); border-radius: 8px; padding: 16px; height: 100%; }
        .job-card .job-header { display: flex; justify-content: space-between; align-items: start; margin-bottom: 12px; }
        .job-card .job-title { font-weight: 600; color: var(--text-bright); }
        .job-card .job-desc { font-size: 12px; color: var(--text-muted); margin-bottom: 12px; }
        .job-card .job-meta { font-size: 11px; color: var(--text-muted); margin-bottom: 12px; }
        .job-card .job-actions { display: flex; gap: 8px; flex-wrap: wrap; }
        .interval-input { width: 70px; }
    </style>
</head>
<body>
    <nav class="navbar d-flex justify-content-between">
        <span class="navbar-brand">Unified Storage Monitoring</span>
        <div class="d-flex align-items-center gap-3">
            <input type="text" id="globalSearch" class="form-control form-control-sm" style="width:280px;" placeholder="Search arrays, volumes, hosts..." onkeyup="if(event.key==='Enter')doSearch()">
            <small style="color:var(--text);"><span class="pulse text-success">●</span> Auto-refresh 60s | <span id="lastUpdate">-</span></small>
        </div>
    </nav>

    <ul class="nav nav-tabs">
        <li class="nav-item"><a class="nav-link active" data-bs-toggle="tab" href="#tabDashboard">Dashboard</a></li>
        <li class="nav-item"><a class="nav-link" data-bs-toggle="tab" href="#tabVolumes" onclick="loadVolumes()">Volumes</a></li>
        <li class="nav-item"><a class="nav-link" data-bs-toggle="tab" href="#tabHosts" onclick="loadHosts()">Hosts</a></li>
        <li class="nav-item"><a class="nav-link" data-bs-toggle="tab" href="#tabAlerts">Alerts <span class="badge bg-danger" id="alertBadge" style="display:none;"></span></a></li>
        <li class="nav-item"><a class="nav-link" data-bs-toggle="tab" href="#tabAnalytics" onclick="loadAnalytics()">Analytics</a></li>
        <li class="nav-item"><a class="nav-link" data-bs-toggle="tab" href="#tabLogs" onclick="loadLogsSplit()">Logs</a></li>
        <li class="nav-item"><a class="nav-link" data-bs-toggle="tab" href="#tabSettings" onclick="loadSettings()">⚙️ Settings</a></li>
    </ul>

    <div class="tab-content">
        <!-- Dashboard Tab -->
        <div class="tab-pane fade show active" id="tabDashboard">
            <div class="row g-3 mb-4">
                <div class="col"><div class="summary-card" onclick="goToTab('tabVolumes')"><div class="label">Total Arrays</div><div class="value" id="sumArrays">-</div><div class="subtext"><span id="sumOnline">-</span> online</div></div></div>
                <div class="col"><div class="summary-card" onclick="goToTab('tabVolumes')"><div class="label">Total Volumes</div><div class="value" id="sumVolumes">-</div></div></div>
                <div class="col"><div class="summary-card" onclick="goToTab('tabHosts')"><div class="label">Total Hosts</div><div class="value" id="sumHosts">-</div></div></div>
                <div class="col"><div class="summary-card"><div class="label">Total Capacity</div><div class="value" id="sumCapTotal">-</div><div class="subtext">Used: <span id="sumCapUsed">-</span> (<span id="sumCapPct">-</span>)</div></div></div>
                <div class="col"><div class="summary-card"><div class="label">Fleet Reduction</div><div class="value" id="sumDR">-</div></div></div>
                <div class="col"><div class="summary-card" onclick="goToTab('tabAlerts')" id="alertCard"><div class="label">Active Alerts</div><div class="value" id="sumAlerts">-</div></div></div>
            </div>
            <div class="row g-3 mb-4">
                <div class="col-md-6">
                    <div class="card">
                        <div class="card-header d-flex justify-content-between"><span>Purity Version Status</span><span>Latest: <span class="text-success" id="latestVersion">-</span></span></div>
                        <div class="card-body">
                            <div class="row text-center">
                                <div class="col"><div class="h4 text-success mb-1" id="vCountLatest">0</div><small class="text-muted">Latest</small></div>
                                <div class="col"><div class="h4 text-info mb-1" id="vCountCurrent">0</div><small class="text-muted">Current</small></div>
                                <div class="col"><div class="h4 text-warning mb-1" id="vCountUpdate">0</div><small class="text-muted">Update Avail</small></div>
                                <div class="col"><div class="h4 text-danger mb-1" id="vCountEOL">0</div><small class="text-muted">EOL</small></div>
                            </div>
                        </div>
                    </div>
                </div>
                <div class="col-md-6">
                    <div class="card io-card">
                        <div class="card-header">Fleet I/O Summary</div>
                        <div class="card-body">
                            <div class="row text-center">
                                <div class="col"><div class="value" id="sumTotalIOPS">-</div><small class="text-muted">Total IOPS</small></div>
                                <div class="col"><div class="value" id="sumReadIOPS">-</div><small class="text-muted">Read IOPS</small></div>
                                <div class="col"><div class="value" id="sumWriteIOPS">-</div><small class="text-muted">Write IOPS</small></div>
                                <div class="col"><div class="value" id="sumAvgLatency">-</div><small class="text-muted">Avg Latency</small></div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
            <div id="arrayGroups"></div>
        </div>

        <!-- Volumes Tab -->
        <div class="tab-pane fade" id="tabVolumes">
            <div class="row g-2 mb-3">
                <div class="col"><div class="inv-summary"><div class="label">Volumes</div><div class="value" id="vTotal">-</div></div></div>
                <div class="col"><div class="inv-summary"><div class="label">Provisioned</div><div class="value" id="vProv">-</div></div></div>
                <div class="col"><div class="inv-summary"><div class="label">Used</div><div class="value" id="vUsed">-</div></div></div>
                <div class="col"><div class="inv-summary"><div class="label">Snapshots</div><div class="value" id="vSnap">-</div></div></div>
                <div class="col"><div class="inv-summary"><div class="label">Avg DR</div><div class="value" id="vDR">-</div></div></div>
            </div>
            <div class="card inventory-card">
                <div class="card-header d-flex justify-content-between align-items-center">
                    <span>All Volumes</span>
                    <div class="d-flex gap-2">
                        <input type="text" id="volFilter" class="form-control form-control-sm" style="width:150px;" placeholder="Filter..." onkeyup="filterVolumes()">
                        <select id="volArrayFilter" class="form-select form-select-sm" style="width:200px;" onchange="filterVolumes()"><option value="">All Arrays</option></select>
                        <button class="btn btn-sm btn-outline-secondary" onclick="exportCSV('volumes')">📥 Export</button>
                    </div>
                </div>
                <div class="card-body">
                    <div class="inventory-table-wrap">
                        <table class="data-table" id="volTable">
                            <thead><tr>
                                <th class="sortable" data-sort="volume_name">Volume</th>
                                <th class="sortable" data-sort="array_name">Array</th>
                                <th class="sortable" data-sort="size">Size</th>
                                <th class="sortable" data-sort="used">Used</th>
                                <th class="sortable" data-sort="pct">%</th>
                                <th class="sortable" data-sort="data_reduction">DR</th>
                                <th class="sortable" data-sort="snap_count">Snaps</th>
                                <th>Hosts</th>
                                <th class="sortable" data-sort="created">Created</th>
                                <th>Notes</th>
                            </tr></thead>
                            <tbody id="volBody"></tbody>
                        </table>
                    </div>
                    <div class="pagination-bar" id="volPagination"></div>
                </div>
            </div>
        </div>

        <!-- Hosts Tab -->
        <div class="tab-pane fade" id="tabHosts">
            <div class="row g-2 mb-3">
                <div class="col"><div class="inv-summary"><div class="label">Hosts</div><div class="value" id="hTotal">-</div></div></div>
                <div class="col"><div class="inv-summary"><div class="label">Host Groups</div><div class="value" id="hGroups">-</div></div></div>
                <div class="col"><div class="inv-summary"><div class="label">iSCSI</div><div class="value" id="hIscsi">-</div></div></div>
                <div class="col"><div class="inv-summary"><div class="label">Fibre Channel</div><div class="value" id="hFC">-</div></div></div>
                <div class="col"><div class="inv-summary"><div class="label">NVMe</div><div class="value" id="hNvme">-</div></div></div>
            </div>
            <div class="card inventory-card">
                <div class="card-header d-flex justify-content-between align-items-center">
                    <span>All Hosts</span>
                    <div class="d-flex gap-2">
                        <input type="text" id="hostFilter" class="form-control form-control-sm" style="width:150px;" placeholder="Filter..." onkeyup="filterHosts()">
                        <select id="hostArrayFilter" class="form-select form-select-sm" style="width:200px;" onchange="filterHosts()"><option value="">All Arrays</option></select>
                        <button class="btn btn-sm btn-outline-secondary" onclick="exportCSV('hosts')">📥 Export</button>
                    </div>
                </div>
                <div class="card-body">
                    <div class="inventory-table-wrap">
                        <table class="data-table" id="hostTable">
                            <thead><tr>
                                <th class="sortable" data-sort="host_name">Host</th>
                                <th class="sortable" data-sort="array_name">Array</th>
                                <th class="sortable" data-sort="host_group">Host Group</th>
                                <th>IQN</th>
                                <th>WWN</th>
                                <th class="sortable" data-sort="vol_count">Vols</th>
                            </tr></thead>
                            <tbody id="hostBody"></tbody>
                        </table>
                    </div>
                    <div class="pagination-bar" id="hostPagination"></div>
                </div>
            </div>
        </div>

        <!-- Alerts Tab -->
        <div class="tab-pane fade" id="tabAlerts">
            <div class="row g-2 mb-3">
                <div class="col"><div class="inv-summary"><div class="label">Active</div><div class="value" id="aTotal">-</div></div></div>
                <div class="col"><div class="inv-summary alert-critical"><div class="label">Critical</div><div class="value" id="aCritical">-</div></div></div>
                <div class="col"><div class="inv-summary alert-warning"><div class="label">Warning</div><div class="value" id="aWarning">-</div></div></div>
                <div class="col"><div class="inv-summary alert-info"><div class="label">Info</div><div class="value" id="aInfo">-</div></div></div>
                <div class="col"><div class="inv-summary"><div class="label">Resolved</div><div class="value" id="aResolved">-</div></div></div>
            </div>
            <div class="card inventory-card">
                <div class="card-header d-flex justify-content-between align-items-center">
                    <span>System Alerts</span>
                    <div class="d-flex gap-2">
                        <input type="text" id="alertFilter" class="form-control form-control-sm" style="width:180px;" placeholder="Search..." onkeyup="renderAlerts()">
                        <select id="alertArrayFilter" class="form-select form-select-sm" style="width:180px;" onchange="renderAlerts()"><option value="">All Arrays</option></select>
                        <select id="alertSeverityFilter" class="form-select form-select-sm" style="width:120px;" onchange="renderAlerts()">
                            <option value="">All Severity</option>
                            <option value="critical">Critical</option>
                            <option value="warning">Warning</option>
                            <option value="info">Info</option>
                        </select>
                        <div class="btn-group btn-group-sm">
                            <button class="btn btn-outline-secondary active" id="btnAlertActive" onclick="filterAlerts('active')">Active</button>
                            <button class="btn btn-outline-secondary" id="btnAlertAll" onclick="filterAlerts('all')">All</button>
                        </div>
                    </div>
                </div>
                <div class="card-body">
                    <div class="inventory-table-wrap">
                        <table class="data-table" id="alertTable">
                            <thead><tr>
                                <th class="sortable" data-sort="array_name">Array</th>
                                <th class="sortable" data-sort="severity">Severity</th>
                                <th class="sortable" data-sort="component_type">Component</th>
                                <th class="sortable" data-sort="component_name">Name</th>
                                <th class="sortable" data-sort="event">Event</th>
                                <th class="sortable" data-sort="opened">Opened</th>
                                <th>Teams</th>
                                <th>SNOW</th>
                                <th>Actions</th>
                            </tr></thead>
                            <tbody id="alertBody"></tbody>
                        </table>
                    </div>
                    <div class="pagination-bar" id="alertPagination"></div>
                </div>
            </div>
        </div>

        <!-- Analytics Tab -->
        <div class="tab-pane fade" id="tabAnalytics">
            <div class="analytics-layout">
                <div class="analytics-sidebar">
                    <div class="filter-panel">
                        <h6>📅 Time Range</h6>
                        <select id="analyticsTimeRange" class="form-select form-select-sm" onchange="refreshAnalyticsCharts()">
                            <option value="6">Last 6 Hours</option>
                            <option value="12">Last 12 Hours</option>
                            <option value="24" selected>Last 24 Hours</option>
                            <option value="48">Last 48 Hours</option>
                            <option value="168">Last 7 Days</option>
                        </select>
                    </div>
                    <div class="filter-panel">
                        <h6>🗄️ Select Arrays</h6>
                        <div class="mb-2">
                            <button class="btn btn-xs btn-outline-secondary me-1" onclick="selectAllArrays()">All</button>
                            <button class="btn btn-xs btn-outline-secondary" onclick="selectNoArrays()">None</button>
                        </div>
                        <div id="arrayCheckboxes" style="max-height:300px;overflow-y:auto;"></div>
                    </div>
                    <div class="filter-panel">
                        <h6>📊 Daily Statistics</h6>
                        <div id="dailyStatsBody" style="font-size:11px;max-height:200px;overflow-y:auto;"></div>
                    </div>
                </div>
                <div class="analytics-main">
                    <div class="row g-3 mb-3">
                        <div class="col-12">
                            <div class="card">
                                <div class="card-header">📈 Read Latency by Array</div>
                                <div class="card-body" style="height:200px;"><canvas id="readLatencyChart"></canvas></div>
                            </div>
                        </div>
                    </div>
                    <div class="row g-3 mb-3">
                        <div class="col-12">
                            <div class="card">
                                <div class="card-header">📈 Write Latency by Array</div>
                                <div class="card-body" style="height:200px;"><canvas id="writeLatencyChart"></canvas></div>
                            </div>
                        </div>
                    </div>
                    <div class="row g-3 mb-3">
                        <div class="col-12">
                            <div class="card">
                                <div class="card-header">📊 Read IOPS by Array</div>
                                <div class="card-body" style="height:200px;"><canvas id="readIOPSChart"></canvas></div>
                            </div>
                        </div>
                    </div>
                    <div class="row g-3 mb-3">
                        <div class="col-12">
                            <div class="card">
                                <div class="card-header">📊 Write IOPS by Array</div>
                                <div class="card-body" style="height:200px;"><canvas id="writeIOPSChart"></canvas></div>
                            </div>
                        </div>
                    </div>
                    <div class="row g-3">
                        <div class="col-md-6">
                            <div class="card">
                                <div class="card-header">🗄️ Capacity by Array</div>
                                <div class="card-body" style="height:250px;"><canvas id="capacityChart"></canvas></div>
                            </div>
                        </div>
                        <div class="col-md-6">
                            <div class="card">
                                <div class="card-header">⚠️ Alerts by Day (30 Days)</div>
                                <div class="card-body" style="height:250px;"><canvas id="alertsChart"></canvas></div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Logs Tab (Split View) -->
        <div class="tab-pane fade" id="tabLogs">
            <div class="split-logs">
                <div class="log-panel">
                    <div class="log-header">
                        <select id="logSelect1" class="form-select form-select-sm" style="width:200px;" onchange="loadLogPanel(1)"></select>
                        <button class="btn btn-sm btn-outline-secondary" onclick="loadLogPanel(1)">🔄</button>
                    </div>
                    <div id="logViewer1" class="log-viewer"></div>
                </div>
                <div class="log-panel">
                    <div class="log-header">
                        <select id="logSelect2" class="form-select form-select-sm" style="width:200px;" onchange="loadLogPanel(2)"></select>
                        <button class="btn btn-sm btn-outline-secondary" onclick="loadLogPanel(2)">🔄</button>
                    </div>
                    <div id="logViewer2" class="log-viewer"></div>
                </div>
            </div>
        </div>

        <!-- Settings Tab -->
        <div class="tab-pane fade" id="tabSettings">
            <!-- Scheduler Section -->
            <div class="settings-section">
                <div class="section-header">
                    <span>🕐 Scheduler Jobs</span>
                    <button class="btn btn-sm btn-outline-secondary" onclick="loadSchedulerStatus()">🔄 Refresh</button>
                </div>
                <div class="section-body">
                    <div class="row g-3" id="schedulerJobs"></div>
                </div>
            </div>

            <!-- Teams/SNOW Integration Section -->
            <div class="settings-section">
                <div class="section-header">
                    <span>🔔 Notifications</span>
                </div>
                <div class="section-body">
                    <div class="row g-3">
                        <div class="col-md-6">
                            <label class="form-label text-muted small">Microsoft Teams Webhook URL</label>
                            <div class="input-group">
                                <input type="text" id="teamsWebhookUrl" class="form-control form-control-sm" placeholder="https://...workflow.office.com/...">
                                <button class="btn btn-sm btn-outline-secondary" onclick="saveTeamsWebhook()">💾 Save</button>
                                <button class="btn btn-sm btn-outline-info" onclick="testTeamsWebhook()">🧪 Test</button>
                            </div>
                            <small class="text-muted">Power Automate webhook URL for Teams notifications</small>
                        </div>
                        <div class="col-md-6">
                            <label class="form-label text-muted small">Notification Settings</label>
                            <div class="form-check">
                                <input class="form-check-input" type="checkbox" id="notifyCritical" checked>
                                <label class="form-check-label small" for="notifyCritical">Notify on Critical alerts</label>
                            </div>
                            <div class="form-check">
                                <input class="form-check-input" type="checkbox" id="notifyWarning" checked>
                                <label class="form-check-label small" for="notifyWarning">Notify on Warning alerts</label>
                            </div>
                            <div class="form-check">
                                <input class="form-check-input" type="checkbox" id="notifyInfo">
                                <label class="form-check-label small" for="notifyInfo">Notify on Info alerts</label>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Containers Section -->
            <div class="settings-section">
                <div class="section-header">
                    <span>🐳 Containers</span>
                    <button class="btn btn-sm btn-outline-secondary" onclick="loadContainerStats()">🔄 Refresh</button>
                </div>
                <div class="section-body p-0">
                    <table class="data-table"><thead><tr><th>Container</th><th>Status</th><th>CPU</th><th>Memory</th><th>Uptime</th><th>Actions</th></tr></thead><tbody id="containerBody"></tbody></table>
                </div>
            </div>

            <!-- DB Stats & System Info -->
            <div class="row g-3">
                <div class="col-md-6">
                    <div class="settings-section">
                        <div class="section-header">📊 Database Stats</div>
                        <div class="section-body" id="dbStats">Loading...</div>
                    </div>
                </div>
                <div class="col-md-6">
                    <div class="settings-section">
                        <div class="section-header">ℹ️ System Info</div>
                        <div class="section-body" id="systemInfo">Loading...</div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- Modals -->
    <div class="modal fade" id="arrayModal" tabindex="-1"><div class="modal-dialog modal-lg"><div class="modal-content">
        <div class="modal-header"><h5 class="modal-title" id="arrayModalTitle">Array Details</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div>
        <div class="modal-body" id="arrayModalBody"></div>
    </div></div></div>

    <div class="modal fade" id="topologyModal" tabindex="-1"><div class="modal-dialog modal-xl"><div class="modal-content">
        <div class="modal-header"><h5 class="modal-title" id="topologyModalTitle">Topology</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div>
        <div class="modal-body" id="topologyModalBody" style="min-height:400px;"></div>
    </div></div></div>

    <div class="modal fade" id="notesModal" tabindex="-1"><div class="modal-dialog"><div class="modal-content">
        <div class="modal-header"><h5 class="modal-title">Edit Notes</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div>
        <div class="modal-body">
            <input type="hidden" id="notesArrayName"><input type="hidden" id="notesVolumeName">
            <div class="mb-3"><label class="form-label text-muted">Volume: <span id="notesVolumeLabel"></span></label><textarea id="notesText" class="form-control" rows="4"></textarea></div>
        </div>
        <div class="modal-footer" style="background:var(--bg-card-alt);"><button class="btn btn-sm btn-outline-secondary" data-bs-dismiss="modal">Cancel</button><button class="btn btn-sm btn-primary" onclick="saveNotes()">Save</button></div>
    </div></div></div>

    <div class="modal fade" id="searchModal" tabindex="-1"><div class="modal-dialog modal-lg"><div class="modal-content">
        <div class="modal-header"><h5 class="modal-title">Search Results</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div>
        <div class="modal-body" id="searchResults"></div>
    </div></div></div>

    <script src="https://code.jquery.com/jquery-3.7.1.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
    <script>
    let purityVersions=null, alertMode='active';
    let allArrays=[], allVolumes=[], allHosts=[], allAlerts=[];
    let volPage=1, hostPage=1, pageSize=50;
    let volSortField='array_name', volSortDir='asc';
    let hostSortField='array_name', hostSortDir='asc';
    let alertSortCol='opened', alertSortAsc=false, alertPage=1, alertPageSize=50;
    let readLatencyChart=null, writeLatencyChart=null, readIOPSChart=null, writeIOPSChart=null, capacityChart=null, alertsChart=null;
    const COLORS = ['#FF6384','#36A2EB','#FFCE56','#4BC0C0','#9966FF','#FF9F40','#7CFC00','#00CED1','#FF69B4','#8A2BE2','#DC143C','#00FA9A','#FFD700','#1E90FF','#FF4500'];

    $(document).ready(function() {
        loadVersions(); loadDashboard(); loadAlerts(); loadLogFiles();
        setInterval(loadDashboard, 60000);
        $(document).on('click','#volTable th.sortable',function(){sortVolumes($(this).data('sort'));});
        $(document).on('click','#hostTable th.sortable',function(){sortHosts($(this).data('sort'));});
        $(document).on('click','#alertTable th.sortable',function(){sortAlerts($(this).data('sort'));});
    });

    function goToTab(tabId){$('a[href="#'+tabId+'"]').tab('show');if(tabId==='tabVolumes')loadVolumes();if(tabId==='tabHosts')loadHosts();if(tabId==='tabAnalytics')loadAnalytics();}
    function formatBytes(b){if(!b)return'0 B';const u=['B','KB','MB','GB','TB','PB'];let i=0;while(b>=1024&&i<u.length-1){b/=1024;i++;}return b.toFixed(1)+' '+u[i];}
    function formatLatency(us){if(!us)return'-';return us<1000?us.toFixed(0)+' μs':(us/1000).toFixed(2)+' ms';}
    function formatIOPS(n){if(!n)return'0';return n>=1000?(n/1000).toFixed(1)+'K':n.toString();}
    function formatDR(val){if(!val||val<1)return'1.0:1';if(val>100)return'>100:1';return val.toFixed(1)+':1';}
    function getCloud(name){name=(name||'').toLowerCase();if(name.includes('-aws-')||name.startsWith('purecbs-aws'))return{id:'aws',name:'AWS'};if(name.includes('-gp-')||name.includes('-azure-'))return{id:'azure',name:'Azure'};return null;}
    function loadVersions(){$.getJSON('/api/purity_versions',d=>{purityVersions=d;$('#latestVersion').text(d.latest||'-');});}
    function getVersionClass(v){if(!purityVersions||!v)return'current';if(v===purityVersions.latest||(purityVersions.recommended&&purityVersions.recommended.includes(v)))return'latest';if(purityVersions.eol&&purityVersions.eol.includes(v))return'eol';if(purityVersions.current&&purityVersions.current.includes(v))return'current';return'update';}

    function loadDashboard(){$.getJSON('/api/metrics',function(data){if(data.error)return;allArrays=data;let online=0,vols=0,hosts=0,alerts=0,capTotal=0,capUsed=0,drSum=0,drCount=0;let readIOPS=0,writeIOPS=0,latSum=0,latCount=0;let versions={latest:0,current:0,update:0,eol:0};let grouped={aws:[],azure:[]};data.forEach(m=>{if(m.array_status==='online')online++;vols+=m.volume_count||0;hosts+=m.host_count||0;alerts+=m.active_alerts||0;capTotal+=m.capacity_total||0;capUsed+=m.capacity_used||0;let dr=m.data_reduction||0;if(dr>=1&&dr<=100){drSum+=dr;drCount++;}readIOPS+=m.read_iops||0;writeIOPS+=m.write_iops||0;if(m.read_latency_us){latSum+=m.read_latency_us;latCount++;}let c=getCloud(m.array_name);if(c)grouped[c.id].push(m);versions[getVersionClass(m.purity_version)]++;});$('#sumArrays').text(data.length);$('#sumOnline').text(online);$('#sumVolumes').text(vols.toLocaleString());$('#sumHosts').text(hosts.toLocaleString());$('#sumCapTotal').text(formatBytes(capTotal));$('#sumCapUsed').text(formatBytes(capUsed));$('#sumCapPct').text((capTotal>0?(capUsed/capTotal*100).toFixed(1):0)+'%');$('#sumDR').text(formatDR(drCount>0?drSum/drCount:1));$('#sumAlerts').text(alerts);$('#alertBadge').text(alerts).toggle(alerts>0);if(alerts>0)$('#alertCard').css('border-left','4px solid var(--red)');$('#sumTotalIOPS').text(formatIOPS(readIOPS+writeIOPS));$('#sumReadIOPS').text(formatIOPS(readIOPS));$('#sumWriteIOPS').text(formatIOPS(writeIOPS));$('#sumAvgLatency').text(latCount>0?formatLatency(latSum/latCount):'-');$('#vCountLatest').text(versions.latest);$('#vCountCurrent').text(versions.current);$('#vCountUpdate').text(versions.update);$('#vCountEOL').text(versions.eol);renderArrayGroups(grouped);$('#lastUpdate').text(new Date().toLocaleTimeString());});}

    function renderArrayGroups(grouped){let allList=[...grouped.aws,...grouped.azure];if(allList.length===0){$('#arrayGroups').html('<div class="text-center text-muted py-4">No arrays</div>');return;}let totalCap=allList.reduce((s,a)=>s+(a.capacity_total||0),0);let usedCap=allList.reduce((s,a)=>s+(a.capacity_used||0),0);let drs=allList.map(a=>a.data_reduction||0).filter(d=>d>=1&&d<=100);let avgDR=drs.length>0?drs.reduce((a,b)=>a+b,0)/drs.length:1;let html=`<div class="vendor-section"><div class="vendor-header" onclick="$('#pureStorageContent').toggleClass('collapsed');$(this).find('.toggle-icon').text($('#pureStorageContent').hasClass('collapsed')?'▶':'▼')" style="cursor:pointer;"><div class="vendor-title">Pure Storage</div><div class="vendor-stats"><span>📦 ${allList.length} Arrays</span><span>💾 ${formatBytes(usedCap)} / ${formatBytes(totalCap)}</span><span>📊 ${formatDR(avgDR)}</span><span class="toggle-icon" style="margin-left:10px;">▼</span></div></div><div id="pureStorageContent">`;if(grouped.aws.length)html+=renderCloudSection('aws','AWS CBS',grouped.aws);if(grouped.azure.length)html+=renderCloudSection('azure','Azure CBS',grouped.azure);html+='</div></div>';$('#arrayGroups').html(html);}

    function renderCloudSection(id,name,arrays){let totalCap=arrays.reduce((s,a)=>s+(a.capacity_total||0),0);let usedCap=arrays.reduce((s,a)=>s+(a.capacity_used||0),0);let drs=arrays.map(a=>a.data_reduction||0).filter(d=>d>=1&&d<=100);let avgDR=drs.length>0?drs.reduce((a,b)=>a+b,0)/drs.length:1;let html=`<div class="cloud-section"><div class="cloud-header" onclick="$('#cloud-${id}').toggleClass('collapsed');$(this).find('.toggle').text($('#cloud-${id}').hasClass('collapsed')?'▶':'▼')"><div class="title"><span class="cloud-badge ${id}">${id.toUpperCase()}</span> ${name} <small class="text-muted">(${arrays.length})</small></div><div class="stats"><span>Cap: ${formatBytes(usedCap)} / ${formatBytes(totalCap)}</span><span>DR: ${formatDR(avgDR)}</span><span class="toggle">▼</span></div></div><div class="cloud-content" id="cloud-${id}"><table class="array-table"><thead><tr><th style="width:200px;">Array</th><th style="width:70px;">Status</th><th style="width:80px;">Read Lat</th><th style="width:80px;">Write Lat</th><th style="width:80px;">IOPS</th><th style="width:160px;">Capacity</th><th style="width:80px;">DR</th><th style="width:70px;">Uptime</th><th style="width:80px;">Version</th></tr></thead><tbody>`;arrays.forEach(m=>{let capPct=m.capacity_total>0?(m.capacity_used/m.capacity_total*100):0;let capColor=capPct>=90?'red':capPct>=75?'yellow':'green';let vc=getVersionClass(m.purity_version);let totalIOPS=(m.read_iops||0)+(m.write_iops||0);let uptime=m.uptime_str||(m.uptime_seconds?Math.floor(m.uptime_seconds/86400)+'d':'-');let dr=(m.data_reduction&&m.data_reduction>=1&&m.data_reduction<=100)?m.data_reduction.toFixed(1):'-';html+=`<tr ondblclick="showArrayDetail('${m.array_name}')"><td><span class="array-link">${m.array_name}</span></td><td><span class="status-dot ${m.array_status==='online'?'online':'offline'}"></span>${m.array_status||'-'}</td><td>${formatLatency(m.read_latency_us)}</td><td>${formatLatency(m.write_latency_us)}</td><td><strong>${formatIOPS(totalIOPS)}</strong></td><td><div class="capacity-bar"><div class="capacity-fill ${capColor}" style="width:${Math.max(capPct,2)}%"></div></div>${formatBytes(m.capacity_used)} (${capPct.toFixed(0)}%)</td><td>${dr}:1</td><td>${uptime}</td><td><span class="version-badge ${vc}">${m.purity_version||'-'}</span></td></tr>`;});html+='</tbody></table></div></div>';return html;}

    function loadVolumes(){$.getJSON('/api/all_volumes',function(data){if(data.error)return;allVolumes=data.volumes||[];$('#vTotal').text(data.count||0);$('#vProv').text(formatBytes(data.total_provisioned));$('#vUsed').text(formatBytes(data.total_used));$('#vSnap').text(formatBytes(data.total_snapshots));$('#vDR').text(formatDR(data.avg_dr));let arrays=[...new Set(allVolumes.map(v=>v.array_name))].sort();$('#volArrayFilter').html('<option value="">All Arrays</option>'+arrays.map(a=>`<option>${a}</option>`).join(''));volPage=1;filterVolumes();});}
    function filterVolumes(){let text=$('#volFilter').val().toLowerCase();let array=$('#volArrayFilter').val();let filtered=allVolumes.filter(v=>{if(array&&v.array_name!==array)return false;if(text&&!(v.volume_name||'').toLowerCase().includes(text)&&!(v.array_name||'').toLowerCase().includes(text))return false;return true;});filtered.sort((a,b)=>{let aVal,bVal;switch(volSortField){case'volume_name':aVal=a.volume_name||'';bVal=b.volume_name||'';break;case'array_name':aVal=a.array_name||'';bVal=b.array_name||'';break;case'size':aVal=a.size||0;bVal=b.size||0;break;case'used':aVal=a.used||0;bVal=b.used||0;break;case'pct':aVal=a.size>0?(a.used||0)/a.size:0;bVal=b.size>0?(b.used||0)/b.size:0;break;case'data_reduction':aVal=a.data_reduction||0;bVal=b.data_reduction||0;break;default:aVal=a.array_name||'';bVal=b.array_name||'';}if(typeof aVal==='string')return volSortDir==='asc'?aVal.localeCompare(bVal):bVal.localeCompare(aVal);return volSortDir==='asc'?aVal-bVal:bVal-aVal;});renderVolumes(filtered);updateSortIndicators('volTable',volSortField,volSortDir);}
    function sortVolumes(field){if(volSortField===field)volSortDir=volSortDir==='asc'?'desc':'asc';else{volSortField=field;volSortDir='asc';}volPage=1;filterVolumes();}
    function renderVolumes(vols){let total=vols.length;let pages=Math.ceil(total/pageSize);if(volPage>pages)volPage=Math.max(1,pages);let start=(volPage-1)*pageSize;let pageVols=vols.slice(start,start+pageSize);let html=pageVols.map(v=>{let pct=v.size>0?(v.used||0)/v.size*100:0;let pctClass=pct>=90?'text-danger':pct>=75?'text-warning':'text-success';let hostCount=(v.hosts||'').split(',').filter(h=>h.trim()).length;let notes=v.notes||'';let notesDisplay=notes.length>30?notes.substring(0,30)+'...':(notes||'-');let snapCount=v.snap_count||0;return`<tr ondblclick="showVolumeTopology('${v.array_name}','${v.volume_name}')"><td><strong style="color:var(--blue)">${v.volume_name||''}</strong></td><td class="truncate">${v.array_name||''}</td><td>${formatBytes(v.size)}</td><td>${formatBytes(v.used)}</td><td class="${pctClass}">${pct.toFixed(1)}%</td><td>${formatDR(v.data_reduction)}</td><td>${snapCount}</td><td>${hostCount}</td><td>${v.created||'-'}</td><td class="notes-cell" onclick="event.stopPropagation();editNotes('${v.array_name}','${v.volume_name}','${(notes||'').replace(/'/g,"\\'")}')"><span title="${(notes||'').replace(/"/g,'&quot;')}">${notesDisplay}</span><span class="edit-icon">✏️</span></td></tr>`;}).join('');$('#volBody').html(html||'<tr><td colspan="10" class="text-center text-muted py-4">No volumes</td></tr>');$('#volPagination').html(renderPagination(total,volPage,pages,'volPage','filterVolumes'));}

    function loadHosts(){$.getJSON('/api/all_hosts',function(data){if(data.error)return;allHosts=data.hosts||[];$('#hTotal').text(data.count||0);$('#hGroups').text(data.total_groups||0);$('#hIscsi').text(data.iscsi_count||0);$('#hFC').text(data.fc_count||0);$('#hNvme').text(data.nvme_count||0);let arrays=[...new Set(allHosts.map(h=>h.array_name))].sort();$('#hostArrayFilter').html('<option value="">All Arrays</option>'+arrays.map(a=>`<option>${a}</option>`).join(''));hostPage=1;filterHosts();});}
    function filterHosts(){let text=$('#hostFilter').val().toLowerCase();let array=$('#hostArrayFilter').val();let filtered=allHosts.filter(h=>{if(array&&h.array_name!==array)return false;if(text&&!((h.host_name||'')+(h.iqn||'')+(h.wwn||'')).toLowerCase().includes(text))return false;return true;});filtered.sort((a,b)=>{let aVal,bVal;switch(hostSortField){case'host_name':aVal=a.host_name||'';bVal=b.host_name||'';break;case'array_name':aVal=a.array_name||'';bVal=b.array_name||'';break;case'host_group':aVal=a.host_group||'';bVal=b.host_group||'';break;case'vol_count':aVal=(a.volumes||'').split(',').filter(v=>v.trim()).length;bVal=(b.volumes||'').split(',').filter(v=>v.trim()).length;break;default:aVal=a.array_name||'';bVal=b.array_name||'';}if(typeof aVal==='string')return hostSortDir==='asc'?aVal.localeCompare(bVal):bVal.localeCompare(aVal);return hostSortDir==='asc'?aVal-bVal:bVal-aVal;});renderHosts(filtered);updateSortIndicators('hostTable',hostSortField,hostSortDir);}
    function sortHosts(field){if(hostSortField===field)hostSortDir=hostSortDir==='asc'?'desc':'asc';else{hostSortField=field;hostSortDir='asc';}hostPage=1;filterHosts();}
    function updateSortIndicators(tableId,field,dir){$(`#${tableId} th.sortable`).removeClass('asc desc');$(`#${tableId} th.sortable[data-sort="${field}"]`).addClass(dir);}
    function renderHosts(hosts){let total=hosts.length;let pages=Math.ceil(total/pageSize);if(hostPage>pages)hostPage=Math.max(1,pages);let start=(hostPage-1)*pageSize;let pageHosts=hosts.slice(start,start+pageSize);let html=pageHosts.map(h=>{let volCount=(h.volumes||'').split(',').filter(v=>v.trim()).length;let iqn=h.iqn||'-';if(iqn.length>50)iqn=iqn.substring(0,50)+'...';return`<tr ondblclick="showHostTopology('${h.array_name}','${h.host_name}')"><td><strong style="color:var(--blue)">${h.host_name||''}</strong></td><td class="truncate">${h.array_name||''}</td><td>${h.host_group||'-'}</td><td class="truncate" title="${h.iqn||''}">${iqn}</td><td class="truncate">${h.wwn||'-'}</td><td>${volCount}</td></tr>`;}).join('');$('#hostBody').html(html||'<tr><td colspan="6" class="text-center text-muted py-4">No hosts</td></tr>');$('#hostPagination').html(renderPagination(total,hostPage,pages,'hostPage','filterHosts'));}
    function renderPagination(total,current,pages,pageVar,filterFunc){let html=`<div class="info">Showing ${Math.min((current-1)*pageSize+1,total)}-${Math.min(current*pageSize,total)} of ${total}</div><div class="pages">`;html+=`<button class="page-btn" onclick="${pageVar}=1;${filterFunc}()" ${current===1?'disabled':''}>«</button>`;html+=`<button class="page-btn" onclick="${pageVar}--;${filterFunc}()" ${current===1?'disabled':''}>‹</button>`;let start=Math.max(1,current-2);let end=Math.min(pages,start+4);if(end-start<4)start=Math.max(1,end-4);for(let i=start;i<=end;i++){html+=`<button class="page-btn ${i===current?'active':''}" onclick="${pageVar}=${i};${filterFunc}()">${i}</button>`;}html+=`<button class="page-btn" onclick="${pageVar}++;${filterFunc}()" ${current>=pages?'disabled':''}>›</button>`;html+=`<button class="page-btn" onclick="${pageVar}=${pages};${filterFunc}()" ${current>=pages?'disabled':''}>»</button>`;return html+'</div>';}

    function loadAlerts(){$.getJSON('/api/alerts',function(data){if(data.error)return;allAlerts=data;let active=data.filter(a=>!a.resolved&&!a.suppressed);let critical=active.filter(a=>a.severity==='critical').length;let warning=active.filter(a=>a.severity==='warning').length;let info=active.filter(a=>!['critical','warning'].includes(a.severity)).length;let resolved=data.filter(a=>a.resolved).length;$('#aTotal').text(active.length);$('#aCritical').text(critical);$('#aWarning').text(warning);$('#aInfo').text(info);$('#aResolved').text(resolved);let arrays=[...new Set(data.map(a=>a.array_name))].sort();$('#alertArrayFilter').html('<option value="">All Arrays</option>'+arrays.map(a=>`<option value="${a}">${a}</option>`).join(''));renderAlerts();});}
    function sortAlerts(col){if(alertSortCol===col)alertSortAsc=!alertSortAsc;else{alertSortCol=col;alertSortAsc=true;}renderAlerts();}
    function filterAlerts(mode){alertMode=mode;alertPage=1;$('#btnAlertActive,#btnAlertAll').removeClass('active');$(mode==='active'?'#btnAlertActive':'#btnAlertAll').addClass('active');renderAlerts();}
    function renderAlerts(){let filtered=alertMode==='active'?allAlerts.filter(a=>!a.resolved&&!a.suppressed):allAlerts;let search=($('#alertFilter').val()||'').toLowerCase();if(search){filtered=filtered.filter(a=>(a.array_name||'').toLowerCase().includes(search)||(a.event||'').toLowerCase().includes(search)||(a.component_name||'').toLowerCase().includes(search));}let arrayFilter=$('#alertArrayFilter').val();if(arrayFilter)filtered=filtered.filter(a=>a.array_name===arrayFilter);let sevFilter=$('#alertSeverityFilter').val();if(sevFilter)filtered=filtered.filter(a=>a.severity===sevFilter);filtered.sort((a,b)=>{let va=a[alertSortCol]||'';let vb=b[alertSortCol]||'';if(alertSortCol==='severity'){let order={critical:0,warning:1,info:2};va=order[va]!==undefined?order[va]:3;vb=order[vb]!==undefined?order[vb]:3;}if(va<vb)return alertSortAsc?-1:1;if(va>vb)return alertSortAsc?1:-1;return 0;});$('#alertTable th').removeClass('asc desc');$(`#alertTable th[data-sort="${alertSortCol}"]`).addClass(alertSortAsc?'asc':'desc');let totalPages=Math.ceil(filtered.length/alertPageSize)||1;if(alertPage>totalPages)alertPage=totalPages;let start=(alertPage-1)*alertPageSize;let pageData=filtered.slice(start,start+alertPageSize);let html=pageData.map(a=>{let sevClass=a.severity==='critical'?'danger':a.severity==='warning'?'warning':'info';let openedDate=a.opened?new Date(a.opened).toLocaleString():'-';let teamsNotified=a.teams_notified?'<span class="badge bg-success">✓</span>':'<span class="badge bg-secondary">-</span>';let snowTicket=a.snow_ticket?`<span class="badge bg-info" title="${a.snow_ticket}">✓</span>`:'<span class="badge bg-secondary">-</span>';return`<tr class="${a.resolved?'text-muted':''}"><td><span class="text-primary">${a.array_name||'-'}</span></td><td><span class="badge bg-${sevClass}">${a.severity||'-'}</span></td><td>${a.component_type||'-'}</td><td>${a.component_name||'-'}</td><td class="truncate" style="max-width:300px;" title="${(a.event||'').replace(/"/g,'&quot;')}">${(a.event||'').substring(0,80)}${(a.event||'').length>80?'...':''}</td><td class="small">${openedDate}</td><td>${teamsNotified}</td><td>${snowTicket}</td><td><div class="d-flex gap-1"><input type="checkbox" class="form-check-input" ${a.resolved?'checked':''} onchange="updateAlert('${a.array_name}','${a.message_id}','resolved',this.checked)" title="Resolved"><input type="checkbox" class="form-check-input" ${a.suppressed?'checked':''} onchange="updateAlert('${a.array_name}','${a.message_id}','suppressed',this.checked)" title="Suppress"></div></td></tr>`;}).join('');$('#alertBody').html(html||'<tr><td colspan="9" class="text-center text-muted py-4">No alerts</td></tr>');let pagHtml=`<span class="text-muted small">Showing ${start+1}-${Math.min(start+alertPageSize,filtered.length)} of ${filtered.length}</span>`;if(totalPages>1){pagHtml+=`<div class="btn-group btn-group-sm ms-3"><button class="btn btn-outline-secondary" onclick="alertPage=1;renderAlerts()" ${alertPage===1?'disabled':''}>«</button><button class="btn btn-outline-secondary" onclick="alertPage--;renderAlerts()" ${alertPage===1?'disabled':''}>‹</button><span class="btn btn-outline-secondary disabled">Page ${alertPage}/${totalPages}</span><button class="btn btn-outline-secondary" onclick="alertPage++;renderAlerts()" ${alertPage===totalPages?'disabled':''}>›</button><button class="btn btn-outline-secondary" onclick="alertPage=${totalPages};renderAlerts()" ${alertPage===totalPages?'disabled':''}>»</button></div>`;}$('#alertPagination').html(pagHtml);}
    function updateAlert(array,msgId,field,value){$.ajax({url:'/api/update_alert',method:'POST',contentType:'application/json',data:JSON.stringify({array,message_id:msgId,field,value}),success:loadAlerts});}

    // Analytics
    function loadAnalytics(){populateArrayCheckboxes();refreshAnalyticsCharts();loadCapacityChart();loadAlertsChart();loadDailyStats();}
    function populateArrayCheckboxes(){$.getJSON('/api/metrics',function(data){let html=data.map((d,i)=>{let color=COLORS[i%COLORS.length];return`<div class="form-check"><input class="form-check-input array-cb" type="checkbox" value="${d.array_name}" id="arr_${i}" checked onchange="refreshAnalyticsCharts()"><label class="form-check-label" for="arr_${i}"><span class="array-color-dot" style="background:${color}"></span>${d.array_name.replace('purecbs-','')}</label></div>`;}).join('');$('#arrayCheckboxes').html(html);});}
    function selectAllArrays(){$('.array-cb').prop('checked',true);refreshAnalyticsCharts();}
    function selectNoArrays(){$('.array-cb').prop('checked',false);refreshAnalyticsCharts();}
    function getSelectedArrays(){return $('.array-cb:checked').map(function(){return $(this).val();}).get();}
    function refreshAnalyticsCharts(){let hours=$('#analyticsTimeRange').val();let arrays=getSelectedArrays();loadLatencyCharts(hours,arrays);loadIOPSCharts(hours,arrays);}
    function loadLatencyCharts(hours,arrays){$.getJSON('/api/timeseries/latency?hours='+hours+'&'+arrays.map(a=>'arrays='+encodeURIComponent(a)).join('&'),function(data){if(readLatencyChart)readLatencyChart.destroy();if(writeLatencyChart)writeLatencyChart.destroy();let readDatasets=data.datasets.filter(d=>d.label.includes('Read'));let writeDatasets=data.datasets.filter(d=>d.label.includes('Write'));const chartOpts={responsive:true,maintainAspectRatio:false,plugins:{legend:{position:'right',labels:{color:'#ccc',boxWidth:12,font:{size:10}}}},scales:{x:{ticks:{color:'#999',maxTicksLimit:12},grid:{color:'#333'}},y:{ticks:{color:'#999'},grid:{color:'#333'},beginAtZero:true}}};readLatencyChart=new Chart(document.getElementById('readLatencyChart'),{type:'line',data:{labels:data.labels,datasets:readDatasets},options:chartOpts});writeLatencyChart=new Chart(document.getElementById('writeLatencyChart'),{type:'line',data:{labels:data.labels,datasets:writeDatasets},options:chartOpts});});}
    function loadIOPSCharts(hours,arrays){$.getJSON('/api/timeseries/iops?hours='+hours+'&'+arrays.map(a=>'arrays='+encodeURIComponent(a)).join('&'),function(data){if(readIOPSChart)readIOPSChart.destroy();if(writeIOPSChart)writeIOPSChart.destroy();let readDatasets=data.datasets.filter(d=>d.label.includes('Read'));let writeDatasets=data.datasets.filter(d=>d.label.includes('Write'));const chartOpts={responsive:true,maintainAspectRatio:false,plugins:{legend:{position:'right',labels:{color:'#ccc',boxWidth:12,font:{size:10}}}},scales:{x:{ticks:{color:'#999',maxTicksLimit:12},grid:{color:'#333'}},y:{ticks:{color:'#999'},grid:{color:'#333'},beginAtZero:true}}};readIOPSChart=new Chart(document.getElementById('readIOPSChart'),{type:'line',data:{labels:data.labels,datasets:readDatasets},options:chartOpts});writeIOPSChart=new Chart(document.getElementById('writeIOPSChart'),{type:'line',data:{labels:data.labels,datasets:writeDatasets},options:chartOpts});});}
    function loadCapacityChart(){$.getJSON('/api/metrics',function(data){if(capacityChart)capacityChart.destroy();data.sort((a,b)=>(b.capacity_used_pct||0)-(a.capacity_used_pct||0));capacityChart=new Chart(document.getElementById('capacityChart'),{type:'bar',data:{labels:data.map(d=>d.array_name.replace('purecbs-','')),datasets:[{label:'%',data:data.map(d=>d.capacity_used_pct||0),backgroundColor:data.map((d,i)=>COLORS[i%COLORS.length]),borderWidth:0}]},options:{responsive:true,maintainAspectRatio:false,indexAxis:'y',plugins:{legend:{display:false}},scales:{x:{max:100,ticks:{color:'#999'},grid:{color:'#333'}},y:{ticks:{color:'#999',font:{size:10}},grid:{display:false}}}}});});}
    function loadAlertsChart(){$.getJSON('/api/timeseries/alerts?days=30',function(data){if(alertsChart)alertsChart.destroy();alertsChart=new Chart(document.getElementById('alertsChart'),{type:'bar',data:{labels:data.labels||[],datasets:[{label:'Critical',data:data.critical||[],backgroundColor:'#FF6384',stack:'a'},{label:'Warning',data:data.warning||[],backgroundColor:'#FFCE56',stack:'a'},{label:'Info',data:data.info||[],backgroundColor:'#36A2EB',stack:'a'}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{position:'top',labels:{color:'#ccc'}}},scales:{x:{stacked:true,ticks:{color:'#999',maxTicksLimit:15},grid:{color:'#333'}},y:{stacked:true,ticks:{color:'#999'},grid:{color:'#333'},beginAtZero:true}}}});});}
    function loadDailyStats(){$.getJSON('/api/daily_stats?days=7',function(data){let html=(data||[]).map(d=>`<div class="d-flex justify-content-between border-bottom py-1"><span>${d.stat_date||'-'}</span><span class="text-muted">${(d.avg_utilization_pct||0).toFixed(0)}% | ${(d.critical_alerts||0)}C/${(d.warning_alerts||0)}W</span></div>`).join('');$('#dailyStatsBody').html(html||'<div class="text-muted">No data</div>');});}

    // Split Logs
    function loadLogFiles(){$.getJSON('/api/log_files',files=>{let opts=files.map(f=>`<option>${f}</option>`).join('');$('#logSelect1').html(opts);$('#logSelect2').html(opts);if(files.length>=2)$('#logSelect2').val(files[1]);});}
    function loadLogsSplit(){loadLogPanel(1);loadLogPanel(2);}
    function loadLogPanel(n){let file=$('#logSelect'+n).val();if(!file)return;$.getJSON('/api/logs?file='+encodeURIComponent(file)+'&lines=300',data=>{let html=(data.lines||[]).map(line=>{let cls='';let l=line.toLowerCase();if(l.includes('error')||l.includes('failed'))cls='error';else if(l.includes('warn'))cls='warning';else if(l.includes('info'))cls='info';return`<div class="log-line ${cls}">${$('<div>').text(line).html()}</div>`;}).join('');$('#logViewer'+n).html(html||'<div class="text-muted p-3">No logs</div>');$('#logViewer'+n).scrollTop($('#logViewer'+n)[0].scrollHeight);});}

    // Settings
    function loadSettings(){loadSchedulerStatus();loadContainerStats();loadDbStats();loadSystemInfo();loadConfig();}
    function loadConfig(){$.getJSON('/api/config',function(data){$('#teamsWebhookUrl').val(data.teams_webhook_url||'');$('#notifyCritical').prop('checked',data.notification_channels?.critical!==false);$('#notifyWarning').prop('checked',data.notification_channels?.warning!==false);$('#notifyInfo').prop('checked',data.notification_channels?.info===true);});}
    function saveTeamsWebhook(){let url=$('#teamsWebhookUrl').val();$.ajax({url:'/api/config/teams_webhook',method:'POST',contentType:'application/json',data:JSON.stringify({url:url}),success:function(r){alert('Teams webhook URL saved to: '+(r.saved_to||'config file'));},error:function(e){alert('Error saving: '+(e.responseJSON?.error||'Unknown error'));}});}
    function testTeamsWebhook(){let url=$('#teamsWebhookUrl').val();if(!url){alert('Please enter a webhook URL first');return;}$.ajax({url:'/api/test_teams_webhook',method:'POST',contentType:'application/json',data:JSON.stringify({url:url}),success:function(r){alert(r.message||'Test notification sent!');},error:function(e){alert('Error: '+(e.responseJSON?.error||'Failed to send test'));}});}
    function loadSchedulerStatus(){$.getJSON('/api/scheduler/status',function(data){let html='';(data.jobs||[]).forEach(job=>{let statusBadge=job.running?'<span class="badge bg-info">Running</span>':job.enabled?'<span class="badge bg-success">Enabled</span>':'<span class="badge bg-secondary">Disabled</span>';let lastRun=job.last_run?new Date(job.last_run).toLocaleString():'Never';let intervalMins=Math.round((job.interval||300)/60);html+=`<div class="col-md-4"><div class="job-card"><div class="job-header"><span class="job-title">${job.name}</span>${statusBadge}</div><div class="job-desc">${job.description||''}</div><div class="job-meta">Last: ${lastRun}</div><div class="job-actions"><div class="input-group input-group-sm" style="width:140px;"><input type="number" class="form-control interval-input" id="interval_${job.id}" value="${intervalMins}" min="1" max="1440"><span class="input-group-text">min</span><button class="btn btn-outline-secondary" onclick="updateInterval('${job.id}')">💾</button></div><button class="btn btn-sm btn-primary" onclick="runJob('${job.id}')" ${job.running?'disabled':''}>▶️ Run</button><button class="btn btn-sm ${job.enabled?'btn-warning':'btn-success'}" onclick="toggleJob('${job.id}',${!job.enabled})">${job.enabled?'⏸️':'▶️'}</button></div></div></div>`;});$('#schedulerJobs').html(html||'<div class="col-12 text-center text-muted py-4">No jobs</div>');});}
    function runJob(jobId){$.post('/api/scheduler/run/'+jobId,function(){setTimeout(loadSchedulerStatus,1000);});}
    function toggleJob(jobId,enabled){$.ajax({url:'/api/scheduler/toggle/'+jobId,method:'POST',contentType:'application/json',data:JSON.stringify({enabled:enabled}),success:loadSchedulerStatus});}
    function updateInterval(jobId){let mins=$('#interval_'+jobId).val();let secs=parseInt(mins)*60;$.ajax({url:'/api/scheduler/interval/'+jobId,method:'POST',contentType:'application/json',data:JSON.stringify({interval:secs}),success:function(){alert('Interval updated!');loadSchedulerStatus();},error:function(e){alert('Error: '+(e.responseJSON?.error||'Failed'));}});}
    function loadContainerStats(){$.getJSON('/api/containers',function(data){let html=(data.containers||[]).map(c=>`<tr><td><strong style="color:var(--blue)">${c.name}</strong></td><td><span class="badge bg-success">Running</span></td><td>${c.cpu}</td><td>${c.mem_usage}</td><td class="small">${c.uptime}</td><td><button class="btn btn-xs btn-outline-warning" onclick="containerAction('${c.name}','restart')">🔄</button></td></tr>`).join('');$('#containerBody').html(html||'<tr><td colspan="6" class="text-center text-muted py-3">No containers</td></tr>');});}
    function containerAction(name,action){if(!confirm(`${action} ${name}?`))return;$.post(`/api/container/${name}/${action}`,function(){setTimeout(loadContainerStats,2000);});}
    function loadDbStats(){$.getJSON('/api/db_stats',function(data){if(data.error){$('#dbStats').html(`<div class="text-danger">${data.error}</div>`);return;}let html=`<table class="table table-sm table-dark mb-0" style="font-size:12px;"><tr><td>Arrays</td><td class="text-end"><strong>${data.metrics_current||0}</strong> <small class="text-muted">(${data.metrics_current_schema||'?'})</small></td></tr><tr><td>Volumes</td><td class="text-end"><strong>${data.volumes_cache||0}</strong> <small class="text-muted">(${data.volumes_cache_schema||'?'})</small></td></tr><tr><td>Hosts</td><td class="text-end"><strong>${data.hosts_cache||0}</strong> <small class="text-muted">(${data.hosts_cache_schema||'?'})</small></td></tr><tr><td>Alerts</td><td class="text-end"><strong>${data.messages||0}</strong> <small class="text-muted">(${data.messages_schema||'?'})</small></td></tr><tr><td colspan="2" class="pt-2 border-top"><small class="text-muted">Last: ${data.last_metrics_collection||'N/A'}</small></td></tr></table>`;$('#dbStats').html(html);});}
    function loadSystemInfo(){$.getJSON('/api/system_info',function(data){let html=`<table class="table table-sm table-dark mb-0" style="font-size:12px;"><tr><td>Version</td><td class="text-end"><strong>${data.app_version||'-'}</strong></td></tr><tr><td>Schema</td><td class="text-end"><strong>${data.schema||'-'}</strong></td></tr><tr><td>Config</td><td class="text-end text-truncate" style="max-width:150px;" title="${data.config_file||'-'}"><small>${(data.config_file||'-').split('/').pop()}</small></td></tr><tr><td>Python</td><td class="text-end"><strong>${data.python_version||'-'}</strong></td></tr><tr><td>Hostname</td><td class="text-end"><strong>${data.hostname||'-'}</strong></td></tr><tr><td>Uptime</td><td class="text-end"><strong>${data.uptime||'-'}</strong></td></tr></table>`;$('#systemInfo').html(html);});}

    // Other functions
    function showArrayDetail(name){$('#arrayModalTitle').text('Array: '+name);$('#arrayModalBody').html('<div class="text-center py-4 text-muted">Loading...</div>');new bootstrap.Modal(document.getElementById('arrayModal')).show();$.getJSON('/api/array/'+encodeURIComponent(name),function(data){if(data.error){$('#arrayModalBody').html('<div class="text-danger p-3">'+data.error+'</div>');return;}let m=data.metrics;let html=`<div class="row g-3"><div class="col-md-4"><div class="card"><div class="card-header">Performance</div><div class="card-body"><div class="metric-row"><span class="label">Read Latency</span><span class="value">${formatLatency(m.read_latency_us)}</span></div><div class="metric-row"><span class="label">Write Latency</span><span class="value">${formatLatency(m.write_latency_us)}</span></div><div class="metric-row"><span class="label">Read IOPS</span><span class="value">${formatIOPS(m.read_iops)}</span></div><div class="metric-row"><span class="label">Write IOPS</span><span class="value">${formatIOPS(m.write_iops)}</span></div></div></div></div><div class="col-md-4"><div class="card"><div class="card-header">Capacity</div><div class="card-body"><div class="metric-row"><span class="label">Total</span><span class="value">${formatBytes(m.capacity_total)}</span></div><div class="metric-row"><span class="label">Used</span><span class="value">${formatBytes(m.capacity_used)} (${(m.capacity_used_pct||0).toFixed(1)}%)</span></div><div class="metric-row"><span class="label">Data Reduction</span><span class="value">${formatDR(m.data_reduction)}</span></div></div></div></div><div class="col-md-4"><div class="card"><div class="card-header">System</div><div class="card-body"><div class="metric-row"><span class="label">Status</span><span class="value"><span class="status-dot ${m.array_status==='online'?'online':'offline'}"></span>${m.array_status||'-'}</span></div><div class="metric-row"><span class="label">Purity</span><span class="value">${m.purity_version||'-'}</span></div><div class="metric-row"><span class="label">Reboots</span><span class="value">${m.reboot_count||0}</span></div></div></div></div></div><div class="row g-3 mt-2"><div class="col-md-4"><div class="inv-summary"><div class="label">Volumes</div><div class="value">${data.volumes.length}</div></div></div><div class="col-md-4"><div class="inv-summary"><div class="label">Hosts</div><div class="value">${data.hosts.length}</div></div></div><div class="col-md-4"><div class="inv-summary"><div class="label">Active Alerts</div><div class="value">${data.alerts.filter(a=>!a.resolved&&!a.suppressed).length}</div></div></div></div>`;$('#arrayModalBody').html(html);});}
    function showVolumeTopology(array,volume){$('#topologyModalTitle').text('Volume Topology');$('#topologyModalBody').html('<div class="text-center py-4 text-muted">Loading...</div>');new bootstrap.Modal(document.getElementById('topologyModal')).show();$.getJSON(`/api/topology/volume/${encodeURIComponent(array)}/${encodeURIComponent(volume)}`,function(data){if(data.error){$('#topologyModalBody').html('<div class="text-danger p-3">'+data.error+'</div>');return;}
        let vol = data.volume;
        let hosts = data.hosts || [];
        let hostGroups = data.host_groups || [];
        let protectionGroups = data.protection_groups || [];
        
        // Build export text
        let exportText = `Array: ${array}\n`;
        exportText += `Volume: ${vol.volume_name}\n`;
        exportText += `Size: ${formatBytes(vol.size)}\n`;
        exportText += `Used: ${formatBytes(vol.used)}\n`;
        exportText += `Data Reduction: ${formatDR(vol.data_reduction)}\n`;
        exportText += `Serial: ${vol.serial || 'N/A'}\n`;
        exportText += `Created: ${vol.created || 'N/A'}\n`;
        if(hosts.length > 0) exportText += `Hosts: ${hosts.map(h=>h.host_name).join(', ')}\n`;
        if(hostGroups.length > 0) exportText += `Host Groups: ${hostGroups.map(hg=>hg.host_group_name).join(', ')}\n`;
        if(protectionGroups.length > 0) exportText += `Protection Groups: ${protectionGroups.map(pg=>pg.pg_name).join(', ')}\n`;
        if(vol.notes) exportText += `Notes: ${vol.notes}\n`;
        
        // Store for export
        window._topologyExportData = exportText;
        
        let html = `<div class="topology-container">
            <div class="d-flex justify-content-end mb-3 gap-2">
                <button class="btn btn-sm btn-outline-secondary" onclick="copyTopologyToClipboard()">📋 Copy to Clipboard</button>
                <button class="btn btn-sm btn-outline-secondary" onclick="downloadTopologyText('volume_${vol.volume_name}')">📥 Download</button>
            </div>
            <div class="topology-flow">
                <div class="topology-node array">
                    <div class="node-icon">⚡</div>
                    <div class="node-name">${array}</div>
                </div>
                <div class="topology-connector"><div class="connector-line"></div><div class="connector-arrow"></div></div>
                <div class="topology-node volume">
                    <div class="node-icon">💾</div>
                    <div class="node-name">${vol.volume_name}</div>
                    <div class="node-detail">${formatBytes(vol.size)}</div>
                    <div class="node-detail small">DR: ${formatDR(vol.data_reduction)}</div>
                </div>
                <div class="topology-connector"><div class="connector-line"></div><div class="connector-arrow"></div></div>
                <div class="topology-hosts-stack">`;
        
        if(hosts.length > 0) {
            hosts.forEach(h => {
                html += `<div class="topology-host-item">
                    <div>🖥️</div>
                    <div>
                        <div>${h.host_name}</div>
                        <div class="small text-muted">${h.host_group || ''}</div>
                    </div>
                </div>`;
            });
        } else {
            html += `<div class="topology-host-item" style="border-color:var(--border);background:#2a2a2a;">
                <div>❓</div>
                <div>No hosts connected</div>
            </div>`;
        }
        
        html += '</div></div>';
        
        // Metadata section
        html += `<div class="row mt-4 g-3">
            <div class="col-md-6">
                <div class="card">
                    <div class="card-header">Volume Details</div>
                    <div class="card-body" style="font-size:12px;">
                        <div class="metric-row"><span class="label">Serial</span><span class="value">${vol.serial || 'N/A'}</span></div>
                        <div class="metric-row"><span class="label">Created</span><span class="value">${vol.created || 'N/A'}</span></div>
                        <div class="metric-row"><span class="label">Used</span><span class="value">${formatBytes(vol.used)} (${vol.size > 0 ? ((vol.used||0)/vol.size*100).toFixed(1) : 0}%)</span></div>
                        <div class="metric-row"><span class="label">Snapshots</span><span class="value">${vol.snap_count || 0}</span></div>
                        <div class="metric-row"><span class="label">Notes</span><span class="value">${vol.notes || '-'}</span></div>
                    </div>
                </div>
            </div>
            <div class="col-md-6">
                <div class="card">
                    <div class="card-header">Connections</div>
                    <div class="card-body" style="font-size:12px;">
                        <div class="metric-row"><span class="label">Hosts</span><span class="value">${hosts.length > 0 ? hosts.map(h=>h.host_name).join(', ') : 'None'}</span></div>
                        <div class="metric-row"><span class="label">Host Groups</span><span class="value">${hostGroups.length > 0 ? hostGroups.map(hg=>hg.host_group_name).join(', ') : (vol.host_groups || 'None')}</span></div>
                        <div class="metric-row"><span class="label">Protection Groups</span><span class="value">${protectionGroups.length > 0 ? protectionGroups.map(pg=>pg.pg_name).join(', ') : (vol.protection_groups || 'None')}</span></div>
                    </div>
                </div>
            </div>
        </div>`;
        
        html += '</div>';
        $('#topologyModalBody').html(html);
    });}
    function showHostTopology(array,host){$('#topologyModalTitle').text('Host Topology');$('#topologyModalBody').html('<div class="text-center py-4 text-muted">Loading...</div>');new bootstrap.Modal(document.getElementById('topologyModal')).show();$.getJSON(`/api/topology/host/${encodeURIComponent(array)}/${encodeURIComponent(host)}`,function(data){if(data.error){$('#topologyModalBody').html('<div class="text-danger p-3">'+data.error+'</div>');return;}
        let hostData = data.host;
        let volumes = data.volumes || [];
        let hostGroup = data.host_group;
        
        // Build export text
        let exportText = `Array: ${array}\n`;
        exportText += `Host: ${hostData.host_name}\n`;
        if(hostData.host_group) exportText += `Host Group: ${hostData.host_group}\n`;
        if(hostData.iqn) exportText += `IQN: ${hostData.iqn}\n`;
        if(hostData.wwn) exportText += `WWN: ${hostData.wwn}\n`;
        if(hostData.nqn) exportText += `NQN: ${hostData.nqn}\n`;
        if(volumes.length > 0) exportText += `Volumes: ${volumes.map(v=>v.volume_name).join(', ')}\n`;
        exportText += `Total Volume Size: ${formatBytes(volumes.reduce((sum,v)=>sum+(v.size||0),0))}\n`;
        
        // Store for export
        window._topologyExportData = exportText;
        
        let html = `<div class="topology-container">
            <div class="d-flex justify-content-end mb-3 gap-2">
                <button class="btn btn-sm btn-outline-secondary" onclick="copyTopologyToClipboard()">📋 Copy to Clipboard</button>
                <button class="btn btn-sm btn-outline-secondary" onclick="downloadTopologyText('host_${hostData.host_name}')">📥 Download</button>
            </div>
            <div class="topology-flow">
                <div class="topology-node array">
                    <div class="node-icon">⚡</div>
                    <div class="node-name">${array}</div>
                </div>
                <div class="topology-connector"><div class="connector-line"></div><div class="connector-arrow"></div></div>
                <div class="topology-node host">
                    <div class="node-icon">🖥️</div>
                    <div class="node-name">${hostData.host_name}</div>
                    ${hostData.host_group ? `<div class="node-detail small">Group: ${hostData.host_group}</div>` : ''}
                </div>
                <div class="topology-connector"><div class="connector-line"></div><div class="connector-arrow"></div></div>
                <div class="topology-volumes-stack">`;
        
        if(volumes.length > 0) {
            volumes.forEach(v => {
                html += `<div class="topology-volume-item">
                    <div>💾</div>
                    <div>
                        <div>${v.volume_name}</div>
                        <div class="small text-muted">${formatBytes(v.size)}</div>
                    </div>
                </div>`;
            });
        } else {
            html += `<div class="topology-volume-item" style="border-color:var(--border);background:#2a2a2a;">
                <div>❓</div>
                <div>No volumes connected</div>
            </div>`;
        }
        
        html += '</div></div>';
        
        // Metadata section
        html += `<div class="row mt-4 g-3">
            <div class="col-md-6">
                <div class="card">
                    <div class="card-header">Host Details</div>
                    <div class="card-body" style="font-size:12px;">
                        <div class="metric-row"><span class="label">Host Group</span><span class="value">${hostData.host_group || 'None'}</span></div>
                        <div class="metric-row"><span class="label">IQN</span><span class="value text-truncate" style="max-width:200px;" title="${hostData.iqn || ''}">${hostData.iqn || 'N/A'}</span></div>
                        <div class="metric-row"><span class="label">WWN</span><span class="value">${hostData.wwn || 'N/A'}</span></div>
                        <div class="metric-row"><span class="label">NQN</span><span class="value">${hostData.nqn || 'N/A'}</span></div>
                    </div>
                </div>
            </div>
            <div class="col-md-6">
                <div class="card">
                    <div class="card-header">Volume Summary</div>
                    <div class="card-body" style="font-size:12px;">
                        <div class="metric-row"><span class="label">Volume Count</span><span class="value">${volumes.length}</span></div>
                        <div class="metric-row"><span class="label">Total Size</span><span class="value">${formatBytes(volumes.reduce((s,v)=>s+(v.size||0),0))}</span></div>
                        <div class="metric-row"><span class="label">Total Used</span><span class="value">${formatBytes(volumes.reduce((s,v)=>s+(v.used||0),0))}</span></div>
                        <div class="metric-row"><span class="label">Volumes</span><span class="value text-truncate" style="max-width:200px;" title="${volumes.map(v=>v.volume_name).join(', ')}">${volumes.length > 0 ? volumes.map(v=>v.volume_name).join(', ') : 'None'}</span></div>
                    </div>
                </div>
            </div>
        </div>`;
        
        html += '</div>';
        $('#topologyModalBody').html(html);
    });}
    function editNotes(array,volume,currentNotes){$('#notesArrayName').val(array);$('#notesVolumeName').val(volume);$('#notesVolumeLabel').text(volume);$('#notesText').val(currentNotes||'');new bootstrap.Modal(document.getElementById('notesModal')).show();}
    function saveNotes(){let array=$('#notesArrayName').val();let volume=$('#notesVolumeName').val();let notes=$('#notesText').val();$.ajax({url:'/api/update_volume_notes',method:'POST',contentType:'application/json',data:JSON.stringify({array_name:array,volume_name:volume,notes:notes}),success:function(resp){if(resp.success){bootstrap.Modal.getInstance(document.getElementById('notesModal')).hide();let vol=allVolumes.find(v=>v.array_name===array&&v.volume_name===volume);if(vol)vol.notes=notes;filterVolumes();}}});}
    function doSearch(){let q=$('#globalSearch').val().trim();if(!q||q.length<2)return;$('#searchResults').html('<div class="text-center py-4 text-muted">Searching...</div>');new bootstrap.Modal(document.getElementById('searchModal')).show();$.getJSON('/api/search?q='+encodeURIComponent(q),data=>{let html='';if(data.arrays?.length){html+=`<h6 class="text-muted mb-2">Arrays (${data.arrays.length})</h6><div class="list-group mb-3">`;data.arrays.forEach(a=>html+=`<div class="list-group-item" style="background:var(--bg-card);border-color:var(--border);color:var(--text);">${a.array_name}</div>`);html+='</div>';}if(data.volumes?.length){html+=`<h6 class="text-muted mb-2">Volumes (${data.volumes.length})</h6><div class="list-group mb-3">`;data.volumes.forEach(v=>html+=`<div class="list-group-item" style="background:var(--bg-card);border-color:var(--border);color:var(--text);cursor:pointer;" ondblclick="showVolumeTopology('${v.array_name}','${v.volume_name}')">${v.volume_name} <small class="text-muted">on ${v.array_name}</small></div>`);html+='</div>';}if(data.hosts?.length){html+=`<h6 class="text-muted mb-2">Hosts (${data.hosts.length})</h6><div class="list-group">`;data.hosts.forEach(h=>html+=`<div class="list-group-item" style="background:var(--bg-card);border-color:var(--border);color:var(--text);cursor:pointer;" ondblclick="showHostTopology('${h.array_name}','${h.host_name}')">${h.host_name} <small class="text-muted">on ${h.array_name}</small></div>`);html+='</div>';}$('#searchResults').html(html||'<div class="text-center text-muted py-4">No results</div>');});}
    function exportCSV(type){let csv='',filename='';if(type==='volumes'){csv='Volume,Array,Size,Used,DR,Hosts,Created,Notes\n';allVolumes.forEach(v=>csv+=`"${v.volume_name}","${v.array_name}",${v.size||0},${v.used||0},${v.data_reduction||1},"${v.hosts||''}","${v.created||''}","${(v.notes||'').replace(/"/g,'""')}"\n`);filename='volumes.csv';}else if(type==='alerts'){csv='Array,Severity,Component,Event,Opened,Resolved,Teams,SNOW\n';allAlerts.forEach(a=>csv+=`"${a.array_name||''}","${a.severity||''}","${a.component_type||''}","${(a.event||'').replace(/"/g,'""')}","${a.opened||''}",${a.resolved?'Yes':'No'},${a.teams_notified?'Yes':'No'},"${a.snow_ticket||''}"\n`);filename='alerts.csv';}else{csv='Host,Array,Host Group,IQN,WWN,Volumes\n';allHosts.forEach(h=>csv+=`"${h.host_name}","${h.array_name}","${h.host_group||''}","${h.iqn||''}","${h.wwn||''}","${h.volumes||''}"\n`);filename='hosts.csv';}let a=document.createElement('a');a.href=URL.createObjectURL(new Blob([csv],{type:'text/csv'}));a.download=filename;a.click();}
    
    function copyTopologyToClipboard(){
        if(window._topologyExportData){
            navigator.clipboard.writeText(window._topologyExportData).then(function(){
                alert('Topology data copied to clipboard!');
            }).catch(function(err){
                // Fallback for older browsers
                let textarea = document.createElement('textarea');
                textarea.value = window._topologyExportData;
                document.body.appendChild(textarea);
                textarea.select();
                document.execCommand('copy');
                document.body.removeChild(textarea);
                alert('Topology data copied to clipboard!');
            });
        }
    }
    
    function downloadTopologyText(filename){
        if(window._topologyExportData){
            let blob = new Blob([window._topologyExportData], {type: 'text/plain'});
            let a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = (filename || 'topology') + '.txt';
            a.click();
        }
    }
    </script>
</body>
</html>
'''

if __name__ == '__main__':
    port = int(os.environ.get('WEB_PORT', 5050))
    app.run(host='0.0.0.0', port=port, debug=True)
