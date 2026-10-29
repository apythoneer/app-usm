#!/usr/bin/env python3

import json
import base64
import requests
import psycopg2
import urllib3
from psycopg2.extras import execute_batch, RealDictCursor
from concurrent.futures import ThreadPoolExecutor

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# =====================================================
# cool stuff
# =====================================================

def load_secrets():
    with open("/home/cvadmin/.var/secrets.json", "r") as f:
        return json.load(f)

SECRETS = load_secrets()


ACCESS_TOKEN = SECRETS["commvault"]["token"]

PG_HOST = SECRETS["postgres"]["host"]
PG_PORT = SECRETS["postgres"]["port"]
PG_DB = SECRETS["postgres"]["database"]
PG_USER = SECRETS["postgres"]["user"]
PG_PASSWORD = SECRETS["postgres"]["password"]
DD_API_KEY = SECRETS["datadog"]["api_key"]

# =====================================================
# CONFIGURATION
# =====================================================

COMMVAULT_URL = "https://uscomvlt1.corp.intranet"

REPORT_URL = (
    "https://uscomvlt1.corp.intranet/commandcenter/api/cr/reportsplusengine/"
    "datasets/2638c3d3-adc7-4b61-bb24-2ba509229bf5:"
    "a30bd278-c7d9-470f-9ae9-8b4922743330/data"
    )
REPORT_PARAMS = {
    "orderby": "[JobId] Desc",
    "parameter.JobStatus[]":
        "Completed,Failed,Completed with Errors,"
        "Completed with Warnings,Committed",
    "parameter.timeframe": "-PT24H P0D",
    "parameter.useCSTimeZone": "-1",
    "parameter.WindowStartTime": "00:00:00",
    "parameter.datasource[]": "2"
}

DD_URL = "https://api.us5.datadoghq.com/api/v1/events"

# =====================================================
# DATABASE
# =====================================================

def get_db_connection():

    return psycopg2.connect(
        host=PG_HOST,
        port=PG_PORT,
        database=PG_DB,
        user=PG_USER,
        password=PG_PASSWORD
    )

# =====================================================
# COMMVAULT LOGIN
# =====================================================

# REMOVED

# =====================================================
# GET REPORT
# =====================================================

def get_backup_report(token):

    response = requests.get(
        REPORT_URL,
        headers={
            "Accept": "application/json",
            "Authtoken": token
        },
        params=REPORT_PARAMS,
        verify=False
    )

    response.raise_for_status()

    return response.json()

# =====================================================
# TRANSFORM REPORT
# =====================================================

def transform_report(report_data):

    headers = [
        col["name"]
            .replace(" ", "")
            .replace("%", "Pct")
            .replace("#", "Num")
            .replace("(", "")
            .replace(")", "")
            .replace("/", "")
            .replace("-", "")
        for col in report_data["columns"]
    ]

    rows = [
        dict(zip(headers, record))
        for record in report_data["records"]
    ]

    for row in rows:

        row["CheckName"] = ":".join([
            str(row.get("ClientName", "")),
            str(row.get("Agent", "")),
            str(row.get("Instance", "")),
            str(row.get("BackupSet", "")),
            str(row.get("Subclient", ""))
        ])

    return rows

# =====================================================
# LOAD REPORT INTO POSTGRES
# =====================================================

def load_backup_summary(rows):

    conn = get_db_connection()
    cur = conn.cursor()

    insert_sql = """
    INSERT INTO stormart.cvlt_backupjobsummary (
        job_id,
        parent_job_id,
        app_id,
        data_source,
        client,
        client_id,
        client_name,
        client_description,
        owner_name,
        company,
        client_group,
        workload,
        agent,
        instance,
        backupset,
        subclient,
        subclient_content,
        operation_type,
        backup_type,
        media_agent,
        backup_window,
        start_time,
        end_time,
        duration_mins,
        job_phase,
        size_of_application_gb,
        media_size_gb,
        estimated_media_size_gb,
        network_size_gb,
        throughput_gb_per_hour,
        dedup_savings_pct,
        job_status,
        failure_reason,
        retain_data_until,
        encryption_status,
        sync_source_files_and_backup_index,
        storage_policy,
        plan_name,
        schedule_policy,
        schedule_name,
        system_state,
        tags,
        protected_objects,
        failed_objects,
        failed_folders,
        vm_backup_size_gb,
        vm_size_gb,
        vm_guest_size_gb,
        proxy,
        vm_guest_tools,
        vm_transport_mode,
        vm_cbt_status,
        vm_operating_system,
        vm_guid,
        vm_host,
        vm_datastore,
        virtualization_client,
        virtual_server,
        kubernetes_namespace,
        kubernetes_type,
        check_name
    )
    VALUES (
        %(JobId)s,
        %(ParentJobId)s,
        %(AppId)s,
        %(DataSource)s,
        %(Client)s,
        %(ClientId)s,
        %(ClientName)s,
        %(ClientDescription)s,
        %(OwnerName)s,
        %(Company)s,
        %(ClientGroup)s,
        %(Workload)s,
        %(Agent)s,
        %(Instance)s,
        %(BackupSet)s,
        %(Subclient)s,
        %(SubclientContent)s,
        %(OperationType)s,
        %(BackupType)s,
        %(MediaAgent)s,
        %(BackupWindow)s,
        %(StartTime)s,
        %(EndTime)s,
        %(Durationmins)s,
        %(JobPhase)s,
        %(SizeofApplication)s,
        %(MediaSize)s,
        %(EstimatedMediaSize)s,
        %(NetworkSize)s,
        %(ThroughputGBHour)s,
        %(DedupSavingsPct)s,
        %(JobStatus)s,
        %(FailureReason)s,
        %(RetainDataUntil)s,
        %(EncryptionStatus)s,
        %(SyncSourceFilesAndBackupIndex)s,
        %(StoragePolicy)s,
        %(PlanName)s,
        %(SchedulePolicy)s,
        %(ScheduleName)s,
        %(SystemState)s,
        %(Tags)s,
        %(ProtectedObjects)s,
        %(FailedObjects)s,
        %(FailedFolders)s,
        %(VMBackupSize)s,
        %(VMSize)s,
        %(VMGuestSize)s,
        %(Proxy)s,
        %(VMGuestTools)s,
        %(VMTransportMode)s,
        %(VMCBTStatus)s,
        %(VMOperatingSystem)s,
        %(VMGUID)s,
        %(VMHost)s,
        %(VMDatastore)s,
        %(VirtualizationClient)s,
        %(VirtualServer)s,
        %(KubernetesNamespace)s,
        %(KubernetesType)s,
        %(CheckName)s
    )
    ON CONFLICT (data_source, job_id)
    DO NOTHING
    """

    execute_batch(
        cur,
        insert_sql,
        rows,
        page_size=1000
    )

    conn.commit()

    cur.close()
    conn.close()

# =====================================================
# DATADOG FAILURE EVENTS
# =====================================================

def send_failure_events():

    conn = get_db_connection()

    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("""
    select
    x.job_id,
    x.check_name,
    x.client_name,
    x.agent,
    x.instance,
    x.backupset,
    x.subclient,
    x.storage_policy,
    x.failure_reason,
    x.subclient_id,
    x.vm_guid,
    x.job_status,
    x.data_source
    from
    (
        select
            cb.job_id,
            cb.check_name,
            cb.client_name,
            cb.agent,
            cb.instance,
            cb.backupset,
            cb.subclient,
            cb.storage_policy,
            cb.failure_reason,
            cb.app_id as subclient_id,
            cb.vm_guid,
            cb.job_status,
            cb.data_source,

            ROW_NUMBER() OVER (
                PARTITION BY cb.check_name
                ORDER BY cb.job_id DESC
            ) as rn

        from cvlt_backupjobsummary cb

        where cb.instance not in ('Kubernetes')

        and not exists (
            select 1
            from stormart.cvlt_datadog_events d
            where d.job_id = cb.job_id
            and d.data_source = cb.data_source
        )

    ) x

    where x.rn = 1
    and x.job_status = 'Failed'
    """)

    rows = cur.fetchall()

    if not rows:
        print("No failure events found.")
        return

    headers = {
        "DD-API-KEY": DD_API_KEY,
        "Content-Type": "application/json"
    }

    def send_event(row):
        JOBID = row['job_id']
        CHECK = row['check_name']
        CLIENT = row['client_name']
        AGENT = row['agent']
        INSTANCE = row['instance']
        BACKUPSET = row['backupset']
        SUBCLIENT = row['subclient']
        STORAGEPOLICY = row['storage_policy']
        FAILUREREASON = row['failure_reason']
        SUBCLIENTID = row['subclient_id']
        VMGUID = row['vm_guid']
        JOBSTATUS = row['job_status']
        DATASOURCE = row['data_source']
        payload = {
        "title": f"Backup Failure on [{CHECK}]",
        "text": (
            f"JobId [{JOBID}] "
            f"has status [{JOBSTATUS}] "
            f"for [{CHECK}]"
        ),
        "tags": [
            "host:usodcwpcomvlt01",
            "source:commvault",
            f"datasource:{DATASOURCE}",
            f"check:{CHECK}",
            f"jobid:{JOBID}",
            f"client:{CLIENT}",
            f"agent:{AGENT}",
            f"instance:{INSTANCE}",
            f"backupset:{BACKUPSET}",
            f"subclient:{SUBCLIENT}",
            f"storagepolicy:{STORAGEPOLICY}",
            f"failureReason:{FAILUREREASON}",
            f"description:{FAILUREREASON}",
            f"subclientId:{SUBCLIENTID}",
            f"vmGuid:{VMGUID}",
            f"jobStatus:{JOBSTATUS}"
        ],
        "alert_type": "error"
        }

        response = requests.post(
            DD_URL,
            headers=headers,
            json=payload,
            timeout=30
        )

        response.raise_for_status()

        dd_id = str(
            response.json()
                    .get("event", {})
                    .get("id")
        )

        return (
            row["data_source"],
            row["check_name"],
            row["job_id"],
            dd_id
        )

    with ThreadPoolExecutor(max_workers=10) as pool:

        results = list(
            pool.map(send_event, rows)
        )

    for result in results:

        cur.execute("""
            INSERT INTO stormart.cvlt_datadog_events (
                data_source,
                check_name,
                job_id,
                dd_id
            )
            VALUES (%s,%s,%s,%s)
            ON CONFLICT (
                data_source,
                check_name
            )
            DO NOTHING
        """, result)

    conn.commit()

    cur.close()
    conn.close()

# =====================================================
# DATADOG RESOLUTION EVENTS
# =====================================================

def send_resolution_events():

    conn = get_db_connection()

    cur = conn.cursor(
        cursor_factory=RealDictCursor
    )

    cur.execute("""
        SELECT
            cb.check_name,
            cb.data_source,
            cb.job_id,
            cb.job_status,
            dde.dd_id

        FROM stormart.cvlt_backupjobsummary cb

        JOIN stormart.cvlt_datadog_events dde
            ON dde.data_source = cb.data_source
           AND dde.check_name = cb.check_name

        WHERE cb.job_status <> 'Failed'
          AND dde.resolved_time IS NULL
    """)

    rows = cur.fetchall()

    if not rows:

        print("No resolution events found.")
        return

    headers = {
        "DD-API-KEY": DD_API_KEY,
        "Content-Type": "application/json"
    }

    for row in rows:

        payload = {
            "title":
                f"Backup Resolved for "
                f"[{row['check_name']}]",

            "text":
                f"Latest backup is now "
                f"[{row['job_status']}]",

            "tags": [
                f"previousEvent:{row['dd_id']}"
            ],

            "alert_type": "info"
        }

        response = requests.post(
            DD_URL,
            headers=headers,
            json=payload,
            timeout=30
        )

        response.raise_for_status()

        resolved_dd_id = str(
            response.json()
                    .get("event", {})
                    .get("id")
        )

        cur.execute("""
            UPDATE stormart.cvlt_datadog_events
            SET
                resolved_time = CURRENT_TIMESTAMP,
                resolved_dd_id = %s
            WHERE data_source = %s
              AND check_name = %s
        """, (
            resolved_dd_id,
            row["data_source"],
            row["check_name"]
        ))

    conn.commit()

    cur.close()
    conn.close()

# =====================================================
# MAIN
# =====================================================

def main():

    print("Authenticating to Commvault...")

    token = ACCESS_TOKEN

    print("Getting report...")

    report = get_backup_report(token)

    print("Transforming report...")

    rows = transform_report(report)

    print(
        f"Loaded {len(rows)} rows "
        f"from Commvault."
    )

    print("Loading PostgreSQL...")

    load_backup_summary(rows)

    print("Creating Datadog failures...")

    send_failure_events()

    print("Creating Datadog resolutions...")

    send_resolution_events()

    print("Complete.")

# =====================================================
# ENTRY POINT
# =====================================================

if __name__ == "__main__":
    main()
