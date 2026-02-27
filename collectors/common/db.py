#!/usr/bin/env python3
"""
Database Configuration for Unified Storage Monitoring
SQL Server with USM schema support
"""

import os
import requests
import pyodbc
from contextlib import contextmanager

# Configuration from environment
KEEPASS_URL = os.environ.get('KEEPASS_URL', 'http://usodclpsandadm1.corp.intranet:2000/keepass')
SQL_CRED_KEY = os.environ.get('SQL_CRED_KEY', 'SQLServerDB')
SQL_SERVER = os.environ.get('SQL_SERVER', 'usidcvsql0252.ctl.intranet')
SQL_DATABASE = os.environ.get('SQL_DATABASE', 'StorMart')
SCHEMA = os.environ.get('DB_SCHEMA', 'USM')

# Cache credentials
_cached_creds = None


def get_sql_credentials():
    """Fetch SQL credentials from KeePass"""
    global _cached_creds
    if _cached_creds:
        return _cached_creds
    
    try:
        resp = requests.get(f"{KEEPASS_URL}/{SQL_CRED_KEY}", timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            _cached_creds = {
                'username': data.get('UserName', data.get('Username', '')),
                'password': data.get('Password', '')
            }
            return _cached_creds
    except Exception as e:
        print(f"[ERROR] KeePass: {e}")
    return None


def get_db_connection():
    """Get database connection"""
    creds = get_sql_credentials()
    if not creds:
        raise Exception("Could not get SQL credentials")
    
    drivers = ['ODBC Driver 18 for SQL Server', 'ODBC Driver 17 for SQL Server']
    available = pyodbc.drivers()
    driver = next((d for d in drivers if d in available), 'ODBC Driver 17 for SQL Server')
    
    conn_str = f"DRIVER={{{driver}}};SERVER={SQL_SERVER};DATABASE={SQL_DATABASE};UID={creds['username']};PWD={creds['password']};TrustServerCertificate=yes;"
    return pyodbc.connect(conn_str)


@contextmanager
def get_db_cursor():
    """Context manager for database cursor with auto-commit"""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        yield cursor
        conn.commit()
    except Exception as e:
        if conn:
            conn.rollback()
        raise e
    finally:
        if conn:
            conn.close()


def row_to_dict(cursor, row):
    """Convert row to dictionary"""
    if not row:
        return None
    cols = [c[0] for c in cursor.description]
    return dict(zip(cols, row))


def rows_to_dicts(cursor, rows):
    """Convert rows to list of dictionaries"""
    if not rows:
        return []
    cols = [c[0] for c in cursor.description]
    return [dict(zip(cols, row)) for row in rows]


def init_database():
    """Initialize database schema"""
    print(f"[DB] Initializing schema in {SCHEMA}...")
    
    with get_db_cursor() as cursor:
        # Create schema if not exists
        cursor.execute(f"""
            IF NOT EXISTS (SELECT * FROM sys.schemas WHERE name = '{SCHEMA}')
            EXEC('CREATE SCHEMA {SCHEMA}')
        """)
        
        # Messages table
        cursor.execute(f"""
            IF NOT EXISTS (SELECT * FROM sys.tables WHERE name='messages' AND schema_id = SCHEMA_ID('{SCHEMA}'))
            CREATE TABLE {SCHEMA}.messages (
                id INT IDENTITY(1,1) PRIMARY KEY,
                array_name NVARCHAR(255) NOT NULL,
                message_id INT NOT NULL,
                event NVARCHAR(500),
                severity NVARCHAR(50),
                component_type NVARCHAR(100),
                component_name NVARCHAR(255),
                opened NVARCHAR(50),
                closed NVARCHAR(50),
                expected NVARCHAR(500),
                actual NVARCHAR(500),
                collected_at NVARCHAR(50),
                alerted NVARCHAR(50),
                teams_notified DATETIME2,
                snow_ticket NVARCHAR(100),
                suppressed BIT DEFAULT 0,
                resolved BIT DEFAULT 0,
                CONSTRAINT UK_messages UNIQUE (array_name, message_id)
            )
        """)
        
        # Current metrics
        cursor.execute(f"""
            IF NOT EXISTS (SELECT * FROM sys.tables WHERE name='metrics_current' AND schema_id = SCHEMA_ID('{SCHEMA}'))
            CREATE TABLE {SCHEMA}.metrics_current (
                id INT IDENTITY(1,1) PRIMARY KEY,
                array_name NVARCHAR(255) NOT NULL UNIQUE,
                purity_version NVARCHAR(50),
                read_latency_us INT,
                write_latency_us INT,
                read_iops INT,
                write_iops INT,
                read_bandwidth BIGINT,
                write_bandwidth BIGINT,
                capacity_total BIGINT,
                capacity_used BIGINT,
                capacity_used_pct FLOAT,
                data_reduction FLOAT,
                total_reduction FLOAT,
                shared_space BIGINT,
                snapshot_space BIGINT,
                volume_space BIGINT,
                array_status NVARCHAR(50),
                controller_status NVARCHAR(50),
                network_status NVARCHAR(50),
                uptime_seconds BIGINT,
                uptime_str NVARCHAR(100),
                last_reboot NVARCHAR(50),
                reboot_count INT DEFAULT 0,
                collected_at NVARCHAR(50)
            )
        """)
        
        # Metrics history for time series
        cursor.execute(f"""
            IF NOT EXISTS (SELECT * FROM sys.tables WHERE name='metrics_history' AND schema_id = SCHEMA_ID('{SCHEMA}'))
            CREATE TABLE {SCHEMA}.metrics_history (
                id BIGINT IDENTITY(1,1) PRIMARY KEY,
                array_name NVARCHAR(255) NOT NULL,
                collected_at DATETIME2 NOT NULL DEFAULT GETDATE(),
                read_latency_us FLOAT,
                write_latency_us FLOAT,
                read_iops FLOAT,
                write_iops FLOAT,
                read_bandwidth BIGINT,
                write_bandwidth BIGINT,
                capacity_total BIGINT,
                capacity_used BIGINT,
                capacity_used_pct FLOAT,
                data_reduction FLOAT
            )
        """)
        
        # Daily stats
        cursor.execute(f"""
            IF NOT EXISTS (SELECT * FROM sys.tables WHERE name='daily_stats' AND schema_id = SCHEMA_ID('{SCHEMA}'))
            CREATE TABLE {SCHEMA}.daily_stats (
                id INT IDENTITY(1,1) PRIMARY KEY,
                stat_date DATE NOT NULL UNIQUE,
                total_arrays INT,
                total_volumes INT,
                total_hosts INT,
                total_capacity_tb FLOAT,
                total_used_tb FLOAT,
                avg_utilization_pct FLOAT,
                avg_data_reduction FLOAT,
                critical_alerts INT,
                warning_alerts INT,
                info_alerts INT,
                resolved_alerts INT,
                avg_read_latency_us FLOAT,
                avg_write_latency_us FLOAT,
                avg_total_iops FLOAT,
                collected_at DATETIME2 DEFAULT GETDATE()
            )
        """)
        
        # Volumes cache
        cursor.execute(f"""
            IF NOT EXISTS (SELECT * FROM sys.tables WHERE name='volumes_cache' AND schema_id = SCHEMA_ID('{SCHEMA}'))
            CREATE TABLE {SCHEMA}.volumes_cache (
                id INT IDENTITY(1,1) PRIMARY KEY,
                array_name NVARCHAR(255) NOT NULL,
                volume_name NVARCHAR(255) NOT NULL,
                size BIGINT DEFAULT 0,
                used BIGINT DEFAULT 0,
                data_reduction FLOAT DEFAULT 1.0,
                total_reduction FLOAT DEFAULT 1.0,
                thin_provisioning FLOAT DEFAULT 0,
                snapshots INT DEFAULT 0,
                created NVARCHAR(50),
                serial NVARCHAR(100),
                hosts NVARCHAR(MAX),
                host_groups NVARCHAR(MAX),
                protection_groups NVARCHAR(MAX),
                notes NVARCHAR(MAX),
                last_updated NVARCHAR(50),
                CONSTRAINT UK_volumes UNIQUE (array_name, volume_name)
            )
        """)
        
        # Hosts cache
        cursor.execute(f"""
            IF NOT EXISTS (SELECT * FROM sys.tables WHERE name='hosts_cache' AND schema_id = SCHEMA_ID('{SCHEMA}'))
            CREATE TABLE {SCHEMA}.hosts_cache (
                id INT IDENTITY(1,1) PRIMARY KEY,
                array_name NVARCHAR(255) NOT NULL,
                host_name NVARCHAR(255) NOT NULL,
                iqn NVARCHAR(500),
                wwn NVARCHAR(500),
                nqn NVARCHAR(500),
                host_group NVARCHAR(255),
                volumes NVARCHAR(MAX),
                last_updated NVARCHAR(50),
                CONSTRAINT UK_hosts UNIQUE (array_name, host_name)
            )
        """)
        
        # Host groups cache
        cursor.execute(f"""
            IF NOT EXISTS (SELECT * FROM sys.tables WHERE name='host_groups_cache' AND schema_id = SCHEMA_ID('{SCHEMA}'))
            CREATE TABLE {SCHEMA}.host_groups_cache (
                id INT IDENTITY(1,1) PRIMARY KEY,
                array_name NVARCHAR(255) NOT NULL,
                hgroup_name NVARCHAR(255) NOT NULL,
                hosts NVARCHAR(MAX),
                volumes NVARCHAR(MAX),
                last_updated NVARCHAR(50),
                CONSTRAINT UK_hgroups UNIQUE (array_name, hgroup_name)
            )
        """)
        
        # Protection groups cache
        cursor.execute(f"""
            IF NOT EXISTS (SELECT * FROM sys.tables WHERE name='protection_groups_cache' AND schema_id = SCHEMA_ID('{SCHEMA}'))
            CREATE TABLE {SCHEMA}.protection_groups_cache (
                id INT IDENTITY(1,1) PRIMARY KEY,
                array_name NVARCHAR(255) NOT NULL,
                pgroup_name NVARCHAR(255) NOT NULL,
                volumes NVARCHAR(MAX),
                hosts NVARCHAR(MAX),
                host_groups NVARCHAR(MAX),
                targets NVARCHAR(MAX),
                replication_enabled BIT DEFAULT 0,
                last_updated NVARCHAR(50),
                CONSTRAINT UK_pgroups UNIQUE (array_name, pgroup_name)
            )
        """)
        
        # Create indexes
        indexes = [
            f"IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name='IX_messages_array') CREATE INDEX IX_messages_array ON {SCHEMA}.messages(array_name)",
            f"IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name='IX_history_array') CREATE INDEX IX_history_array ON {SCHEMA}.metrics_history(array_name)",
            f"IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name='IX_history_time') CREATE INDEX IX_history_time ON {SCHEMA}.metrics_history(collected_at DESC)",
            f"IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name='IX_volumes_array') CREATE INDEX IX_volumes_array ON {SCHEMA}.volumes_cache(array_name)",
            f"IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name='IX_hosts_array') CREATE INDEX IX_hosts_array ON {SCHEMA}.hosts_cache(array_name)",
        ]
        for idx in indexes:
            try:
                cursor.execute(idx)
            except:
                pass
        
        print(f"[DB] Schema initialization complete!")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Database Config')
    parser.add_argument('--init', action='store_true', help='Initialize database')
    parser.add_argument('--test', action='store_true', help='Test connection')
    args = parser.parse_args()
    
    if args.test:
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            print("[OK] Database connection successful")
            conn.close()
        except Exception as e:
            print(f"[ERROR] {e}")
    elif args.init:
        init_database()
    else:
        parser.print_help()
