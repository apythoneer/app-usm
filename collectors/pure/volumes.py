#!/usr/bin/env python3
"""
Pure Storage Volumes Collector
Collects volume, host, host group, and protection group inventory
"""

import os
import sys
import json
import requests
from datetime import datetime
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Add parent to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from collectors.common.db import get_db_connection, init_database
from collectors.common.base import BaseCollector, CollectorRunner

# Configuration
KEEPASS_URL = os.environ.get('KEEPASS_URL', 'http://usodclpsandadm1.corp.intranet:2000/keepass')
API_VERSION = '1.19'


class PureVolumesCollector(BaseCollector):
    """Collector for Pure Storage volume and host inventory"""
    
    VENDOR_NAME = "Pure"
    COLLECTOR_TYPE = "volumes"
    
    def __init__(self, array_name: str):
        super().__init__(array_name)
        self.session = None
        self.api_token = None
    
    def _get_api_token(self) -> str:
        """Fetch API token from KeePass"""
        try:
            url = f"{KEEPASS_URL}/PureStorage_API_{self.array_name}"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, str):
                    return data
                elif isinstance(data, dict):
                    return data.get('Password', data.get('password', ''))
        except Exception as e:
            self.log_error(f"KeePass error: {e}")
        return None
    
    def authenticate(self) -> bool:
        """Authenticate with Pure Storage array"""
        self.api_token = self._get_api_token()
        if not self.api_token:
            self.log_error("No API token found")
            return False
        
        self.session = requests.Session()
        self.session.verify = False
        self.session.headers.update({'Content-Type': 'application/json'})
        
        try:
            url = f"https://{self.array_name}/api/{API_VERSION}/auth/session"
            resp = self.session.post(url, json={"api_token": self.api_token}, timeout=10)
            if resp.status_code == 200:
                self.log_info("Authentication successful")
                return True
            else:
                self.log_error(f"Auth failed: HTTP {resp.status_code}")
                return False
        except Exception as e:
            self.log_error(f"Auth error: {e}")
            return False
    
    def _get_snapshot_counts(self) -> dict:
        """Get count of snapshots per volume"""
        snap_counts = {}
        try:
            resp = self.session.get(f"https://{self.array_name}/api/{API_VERSION}/volume?snap=true", timeout=30)
            if resp.status_code == 200:
                for snap in resp.json():
                    snap_name = snap.get('name', '')
                    source = snap.get('source', '')
                    if not source and '.' in snap_name:
                        source = snap_name.rsplit('.', 1)[0]
                    if source:
                        snap_counts[source] = snap_counts.get(source, 0) + 1
        except Exception as e:
            self.log_warning(f"Snapshot counts error: {e}")
        return snap_counts
    
    def collect(self) -> dict:
        """Collect volumes, hosts, host groups, and protection groups"""
        data = {
            'volumes': {},
            'hosts': {},
            'host_groups': {},
            'protection_groups': {}
        }
        now = datetime.now().isoformat()
        
        # Get snapshot counts first
        snap_counts = self._get_snapshot_counts()
        
        # ===== VOLUMES =====
        try:
            resp = self.session.get(f"https://{self.array_name}/api/{API_VERSION}/volume", timeout=30)
            if resp.status_code == 200:
                for v in resp.json():
                    name = v.get('name', '')
                    if name and '::' not in name:  # Skip snapshots
                        data['volumes'][name] = {
                            'volume_name': name,
                            'created': v.get('created', ''),
                            'serial': v.get('serial', ''),
                            'size': 0,
                            'used': 0,
                            'data_reduction': 1.0,
                            'total_reduction': 1.0,
                            'snapshots': 0,
                            'snap_count': snap_counts.get(name, 0),
                            'hosts': [],
                            'host_groups': [],
                            'protection_groups': [],
                            'last_updated': now
                        }
        except Exception as e:
            self.log_warning(f"Volume basic error: {e}")
        
        # Get space metrics
        try:
            resp = self.session.get(f"https://{self.array_name}/api/{API_VERSION}/volume?space=true", timeout=30)
            if resp.status_code == 200:
                for v in resp.json():
                    name = v.get('name', '')
                    if name in data['volumes']:
                        data['volumes'][name]['size'] = int(v.get('size', 0) or 0)
                        data['volumes'][name]['used'] = int(v.get('total', 0) or 0)
                        data['volumes'][name]['data_reduction'] = float(v.get('data_reduction', 1) or 1)
                        data['volumes'][name]['total_reduction'] = float(v.get('total_reduction', 1) or 1)
                        data['volumes'][name]['snapshots'] = int(v.get('snapshots', 0) or 0)
        except Exception as e:
            self.log_warning(f"Volume space error: {e}")
        
        # Get host connections
        try:
            resp = self.session.get(f"https://{self.array_name}/api/{API_VERSION}/volume?connect=true", timeout=30)
            if resp.status_code == 200:
                for conn in resp.json():
                    vol_name = conn.get('vol', conn.get('name', ''))
                    host = conn.get('host', '')
                    hgroup = conn.get('hgroup', '')
                    if vol_name in data['volumes']:
                        if host and host not in data['volumes'][vol_name]['hosts']:
                            data['volumes'][vol_name]['hosts'].append(host)
                        if hgroup and hgroup not in data['volumes'][vol_name]['host_groups']:
                            data['volumes'][vol_name]['host_groups'].append(hgroup)
        except Exception as e:
            self.log_warning(f"Volume connections error: {e}")
        
        # Get protection group membership
        try:
            resp = self.session.get(f"https://{self.array_name}/api/{API_VERSION}/pgroup?members=true", timeout=30)
            if resp.status_code == 200:
                for pg in resp.json():
                    pg_name = pg.get('name', '')
                    pg_vols = pg.get('volumes') or []
                    for vol in pg_vols:
                        if vol in data['volumes']:
                            if pg_name not in data['volumes'][vol]['protection_groups']:
                                data['volumes'][vol]['protection_groups'].append(pg_name)
        except Exception as e:
            self.log_warning(f"Protection groups error: {e}")
        
        # ===== HOSTS =====
        try:
            resp = self.session.get(f"https://{self.array_name}/api/{API_VERSION}/host", timeout=30)
            if resp.status_code == 200:
                for h in resp.json():
                    name = h.get('name', '')
                    if name:
                        data['hosts'][name] = {
                            'host_name': name,
                            'iqn': ','.join(h.get('iqn', []) or []),
                            'wwn': ','.join(h.get('wwn', []) or []),
                            'nqn': ','.join(h.get('nqn', []) or []),
                            'host_group': h.get('hgroup', ''),
                            'volumes': [],
                            'last_updated': now
                        }
        except Exception as e:
            self.log_warning(f"Host basic error: {e}")
        
        # Get host volume connections
        try:
            resp = self.session.get(f"https://{self.array_name}/api/{API_VERSION}/host?connect=true", timeout=30)
            if resp.status_code == 200:
                for conn in resp.json():
                    host_name = conn.get('host', conn.get('name', ''))
                    vol = conn.get('vol', '')
                    if host_name in data['hosts'] and vol:
                        if vol not in data['hosts'][host_name]['volumes']:
                            data['hosts'][host_name]['volumes'].append(vol)
        except Exception as e:
            self.log_warning(f"Host connections error: {e}")
        
        # ===== HOST GROUPS =====
        try:
            resp = self.session.get(f"https://{self.array_name}/api/{API_VERSION}/hgroup", timeout=30)
            if resp.status_code == 200:
                for hg in resp.json():
                    name = hg.get('name', '')
                    if name:
                        data['host_groups'][name] = {
                            'hgroup_name': name,
                            'hosts': hg.get('hosts', []) or [],
                            'volumes': [],
                            'last_updated': now
                        }
        except Exception as e:
            self.log_warning(f"Host group basic error: {e}")
        
        # Get host group connections
        try:
            resp = self.session.get(f"https://{self.array_name}/api/{API_VERSION}/hgroup?connect=true", timeout=30)
            if resp.status_code == 200:
                for conn in resp.json():
                    hg_name = conn.get('hgroup', conn.get('name', ''))
                    vol = conn.get('vol', '')
                    if hg_name in data['host_groups'] and vol:
                        if vol not in data['host_groups'][hg_name]['volumes']:
                            data['host_groups'][hg_name]['volumes'].append(vol)
        except Exception as e:
            self.log_warning(f"Host group connections error: {e}")
        
        # ===== PROTECTION GROUPS =====
        try:
            resp = self.session.get(f"https://{self.array_name}/api/{API_VERSION}/pgroup", timeout=30)
            if resp.status_code == 200:
                for pg in resp.json():
                    name = pg.get('name', '')
                    if name:
                        data['protection_groups'][name] = {
                            'pgroup_name': name,
                            'volumes': pg.get('volumes', []) or [],
                            'hosts': pg.get('hosts', []) or [],
                            'host_groups': pg.get('hgroups', []) or [],
                            'targets': pg.get('targets', []) or [],
                            'replication_enabled': 1 if pg.get('targets') else 0,
                            'last_updated': now
                        }
        except Exception as e:
            self.log_warning(f"Protection groups error: {e}")
        
        # Update stats
        self.stats = {
            'volumes': len(data['volumes']),
            'hosts': len(data['hosts']),
            'host_groups': len(data['host_groups']),
            'protection_groups': len(data['protection_groups'])
        }
        
        return data
    
    def save(self, data: dict) -> bool:
        """Save all collected data to database"""
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            
            # Save volumes
            self._save_volumes(cursor, data.get('volumes', {}))
            
            # Save hosts
            self._save_hosts(cursor, data.get('hosts', {}))
            
            # Save host groups
            self._save_host_groups(cursor, data.get('host_groups', {}))
            
            # Save protection groups
            self._save_protection_groups(cursor, data.get('protection_groups', {}))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            self.log_info(f"Saved: {self.stats}")
            return True
            
        except Exception as e:
            self.log_error(f"Save error: {e}")
            return False
    
    def _save_volumes(self, cursor, volumes: dict):
        """Save volumes to database"""
        # Get existing
        cursor.execute("SELECT volume_name FROM volumes_cache WHERE array_name = ?", (self.array_name,))
        existing = set(row[0] for row in cursor.fetchall())
        current = set(volumes.keys())
        
        # Delete removed
        for vol_name in (existing - current):
            cursor.execute("DELETE FROM volumes_cache WHERE array_name = ? AND volume_name = ?",
                         (self.array_name, vol_name))
        
        # Upsert current
        for vol_name, vol in volumes.items():
            cursor.execute("SELECT id FROM volumes_cache WHERE array_name = ? AND volume_name = ?",
                         (self.array_name, vol_name))
            existing_row = cursor.fetchone()
            
            hosts_json = json.dumps(vol['hosts'])
            hgroups_json = json.dumps(vol['host_groups'])
            pgroups_json = json.dumps(vol['protection_groups'])
            
            if existing_row:
                cursor.execute("""
                    UPDATE volumes_cache SET
                        size = ?, used = ?, data_reduction = ?, total_reduction = ?,
                        snapshots = ?, snap_count = ?, created = ?, serial = ?,
                        hosts = ?, host_groups = ?, protection_groups = ?, last_updated = GETDATE()
                    WHERE array_name = ? AND volume_name = ?
                """, (
                    vol['size'], vol['used'], vol['data_reduction'], vol['total_reduction'],
                    vol.get('snapshots', 0), vol.get('snap_count', 0), vol['created'], vol['serial'],
                    hosts_json, hgroups_json, pgroups_json,
                    self.array_name, vol_name
                ))
            else:
                cursor.execute("""
                    INSERT INTO volumes_cache (
                        array_name, volume_name, size, used, data_reduction, total_reduction,
                        snapshots, snap_count, created, serial, hosts, host_groups, protection_groups
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    self.array_name, vol_name, vol['size'], vol['used'],
                    vol['data_reduction'], vol['total_reduction'], vol.get('snapshots', 0),
                    vol.get('snap_count', 0), vol['created'], vol['serial'],
                    hosts_json, hgroups_json, pgroups_json
                ))
    
    def _save_hosts(self, cursor, hosts: dict):
        """Save hosts to database"""
        cursor.execute("SELECT host_name FROM hosts_cache WHERE array_name = ?", (self.array_name,))
        existing = set(row[0] for row in cursor.fetchall())
        current = set(hosts.keys())
        
        for host_name in (existing - current):
            cursor.execute("DELETE FROM hosts_cache WHERE array_name = ? AND host_name = ?",
                         (self.array_name, host_name))
        
        for host_name, host in hosts.items():
            cursor.execute("SELECT id FROM hosts_cache WHERE array_name = ? AND host_name = ?",
                         (self.array_name, host_name))
            existing_row = cursor.fetchone()
            
            volumes_json = json.dumps(host['volumes'])
            
            if existing_row:
                cursor.execute("""
                    UPDATE hosts_cache SET
                        iqn = ?, wwn = ?, nqn = ?, host_group = ?, volumes = ?, last_updated = GETDATE()
                    WHERE array_name = ? AND host_name = ?
                """, (host['iqn'], host['wwn'], host['nqn'], host['host_group'],
                      volumes_json, self.array_name, host_name))
            else:
                cursor.execute("""
                    INSERT INTO hosts_cache (array_name, host_name, iqn, wwn, nqn, host_group, volumes)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (self.array_name, host_name, host['iqn'], host['wwn'], host['nqn'],
                      host['host_group'], volumes_json))
    
    def _save_host_groups(self, cursor, hgroups: dict):
        """Save host groups to database"""
        cursor.execute("SELECT hgroup_name FROM host_groups_cache WHERE array_name = ?", (self.array_name,))
        existing = set(row[0] for row in cursor.fetchall())
        current = set(hgroups.keys())
        
        for hg_name in (existing - current):
            cursor.execute("DELETE FROM host_groups_cache WHERE array_name = ? AND hgroup_name = ?",
                         (self.array_name, hg_name))
        
        for hg_name, hg in hgroups.items():
            cursor.execute("SELECT id FROM host_groups_cache WHERE array_name = ? AND hgroup_name = ?",
                         (self.array_name, hg_name))
            existing_row = cursor.fetchone()
            
            hosts_json = json.dumps(hg['hosts'])
            volumes_json = json.dumps(hg['volumes'])
            
            if existing_row:
                cursor.execute("""
                    UPDATE host_groups_cache SET hosts = ?, volumes = ?, last_updated = GETDATE()
                    WHERE array_name = ? AND hgroup_name = ?
                """, (hosts_json, volumes_json, self.array_name, hg_name))
            else:
                cursor.execute("""
                    INSERT INTO host_groups_cache (array_name, hgroup_name, hosts, volumes)
                    VALUES (?, ?, ?, ?)
                """, (self.array_name, hg_name, hosts_json, volumes_json))
    
    def _save_protection_groups(self, cursor, pgroups: dict):
        """Save protection groups to database"""
        cursor.execute("SELECT pgroup_name FROM protection_groups_cache WHERE array_name = ?", (self.array_name,))
        existing = set(row[0] for row in cursor.fetchall())
        current = set(pgroups.keys())
        
        for pg_name in (existing - current):
            cursor.execute("DELETE FROM protection_groups_cache WHERE array_name = ? AND pgroup_name = ?",
                         (self.array_name, pg_name))
        
        for pg_name, pg in pgroups.items():
            cursor.execute("SELECT id FROM protection_groups_cache WHERE array_name = ? AND pgroup_name = ?",
                         (self.array_name, pg_name))
            existing_row = cursor.fetchone()
            
            volumes_json = json.dumps(pg['volumes'])
            hosts_json = json.dumps(pg['hosts'])
            hgroups_json = json.dumps(pg['host_groups'])
            targets_json = json.dumps(pg['targets'])
            
            if existing_row:
                cursor.execute("""
                    UPDATE protection_groups_cache SET
                        volumes = ?, hosts = ?, host_groups = ?, targets = ?,
                        replication_enabled = ?, last_updated = GETDATE()
                    WHERE array_name = ? AND pgroup_name = ?
                """, (volumes_json, hosts_json, hgroups_json, targets_json,
                      pg['replication_enabled'], self.array_name, pg_name))
            else:
                cursor.execute("""
                    INSERT INTO protection_groups_cache (
                        array_name, pgroup_name, volumes, hosts, host_groups, targets, replication_enabled
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (self.array_name, pg_name, volumes_json, hosts_json, hgroups_json,
                      targets_json, pg['replication_enabled']))
    
    def disconnect(self):
        """Close session"""
        if self.session:
            try:
                self.session.delete(f"https://{self.array_name}/api/{API_VERSION}/auth/session", timeout=5)
            except:
                pass


def load_arrays(config_file: str = None) -> list:
    """Load array list from config file"""
    if not config_file:
        config_file = os.environ.get('ARRAYS_FILE', '/app/config/arrays.txt')
    
    paths = [
        config_file,
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'config', 'arrays.txt'),
        '/app/config/arrays.txt',
        'arrays.txt'
    ]
    
    for path in paths:
        if os.path.exists(path):
            with open(path) as f:
                return [line.strip() for line in f if line.strip() and not line.startswith('#')]
    
    return []


def main():
    """Main entry point"""
    import argparse
    parser = argparse.ArgumentParser(description='Pure Storage Volumes Collector')
    parser.add_argument('array', nargs='?', help='Single array to collect')
    parser.add_argument('--all', action='store_true', help='Collect from all arrays')
    parser.add_argument('--init-db', action='store_true', help='Initialize database')
    
    args = parser.parse_args()
    
    if args.init_db:
        init_database()
        return
    
    if args.all:
        arrays = load_arrays()
        if not arrays:
            print("No arrays found in config")
            return
        
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Collecting volumes from {len(arrays)} arrays")
        runner = CollectorRunner(PureVolumesCollector, arrays)
        runner.run_all()
        summary = runner.get_summary()
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Completed: {summary['successful']}/{summary['total']} successful")
        
    elif args.array:
        collector = PureVolumesCollector(args.array)
        result = collector.run()
        print(f"Result: {'Success' if result['success'] else 'Failed'}")
        print(f"Stats: {result.get('stats', {})}")
        if result['errors']:
            print(f"Errors: {result['errors']}")
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
