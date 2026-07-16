/* ============================================================================
   USM ↔ CMS bridge views  (SQL Server, database StorMart, schema USM)

   PURPOSE
     Flatten the CMS/CMDB asset model into a handful of obvious, read-only views
     so the Text-to-SQL chat can answer app/server/database questions without
     being taught 7 join paths and 3 type gotchas on every prompt.

   WHY VIEWS AND NOT RAW TABLES IN THE PROMPT
     The CMS core + bridges are 303 columns (CMSHost/CMSMalApps/CMSPC are 50
     each). Describing them raw takes the LLM prompt from ~3.4k to ~15k chars AND
     still leaves the model to get the joins/filters/casts right every time.
     These views collapse that to ~8 flat objects the model cannot misuse.

   NOTHING IS COPIED. A view is a stored query — it holds no data, costs no
   storage, and DROP VIEW removes it with zero impact on CMS or USM data.
   See cms_views_drop.sql for a full teardown.

   CREATED IN THE **USM** SCHEMA ON PURPOSE
     dbo is shared with CVLT / HITACHI / HPE / UI. USM is ours; these views are
     additive there and cannot collide with anyone else's objects.

   ── VERIFIED FACTS THIS DDL RELIES ON (checked against live data 2026-07-15) ──

   1. ASSIGNMENT filters are PER TABLE, not universal:
        CMSHost      'In Use'      44,827 of 622,426   (93% retired/in-stock)
        CMSMDBs      'In Use'      11,386 of  58,588
        CMSMalApps   'Production'   2,634 of   7,973
        >>> CMSMalApps has NO 'In Use' value at all. Filtering apps on 'In Use'
            returns ZERO ROWS. Apps use 'Production'. This is the single most
            important correction in this file.

   2. Join on NATURAL KEYS (NAME / APPID / ASSETTAG), not LASTID. LASTID types
      are inconsistent across the model:
        CMSHost.LASTID              bigint
        CMSMal2Server.SERVER_LASTID nvarchar   <-- would need a cast
        CMSMDB2Server.SERVER_LASTID int
        CMSHosttoCluster.*_LASTID   int
        CMSBackups.SERVER_LASTID    bigint     <-- matches CMSHost, no cast
      Name-joins sidestep this entirely and match what dbo.CMSServerAppSANView
      already does.

   3. Real column names (Postgres-side aliases do NOT exist here):
        CMSHost.OPERATINGSYSTEM   (not "os")
        CMSHost.STATUS            (not "server_status")
        CMSMDBs.DATABASE_NAME     (not "name")
        CMSMDBs.DATABASE_TYPE     (not "dbtype")

   Columns are aliased to lowercase snake_case so the chat prompt (and humans)
   get one consistent convention regardless of CMS naming.
   ============================================================================ */

/* ---------------------------------------------------------------------------
   1. app -> server        "what servers does this app run on?"
   --------------------------------------------------------------------------- */
CREATE OR ALTER VIEW USM.vw_cms_app_to_server AS
SELECT
    a.APPID                AS app_id,
    a.ACRONYM              AS app_acronym,
    a.NAME                 AS app_name,
    a.SOX_CRITICAL         AS app_sox_critical,
    a.BIA_CRITICAL         AS app_bia_critical,
    a.CRITICALITY          AS app_criticality,
    h.NAME                 AS server_name,
    h.OPERATINGSYSTEM      AS server_os,
    h.OS_FAMILY            AS server_os_family,
    h.STATUS               AS server_status,
    h.MODEL                AS server_model,
    h.SVR_PHYSICAL_VIRTUAL AS server_physical_virtual,
    h.TCPIPADDRESS         AS server_ip
FROM dbo.CMSMalApps    AS a
JOIN dbo.CMSMal2Server AS m ON m.APPID = a.APPID          -- natural key
JOIN dbo.CMSHost       AS h ON h.NAME  = m.SERVER_NAME    -- natural key
WHERE a.ASSIGNMENT = 'Production'   -- NOT 'In Use' — see header note 1
  AND h.ASSIGNMENT = 'In Use';
GO

/* ---------------------------------------------------------------------------
   2. app -> database      "what databases does this app own?"
   --------------------------------------------------------------------------- */
CREATE OR ALTER VIEW USM.vw_cms_app_to_database AS
SELECT
    a.APPID             AS app_id,
    a.ACRONYM           AS app_acronym,
    a.NAME              AS app_name,
    a.SOX_CRITICAL      AS app_sox_critical,
    d.ASSETTAG          AS database_assettag,
    d.DATABASE_NAME     AS database_name,
    d.DATABASE_TYPE     AS database_type,
    d.DATABASE_VERSION  AS database_version,
    d.INSTANCE_NAME     AS database_instance,
    d.STATUS            AS database_status
FROM dbo.CMSMalApps AS a
JOIN dbo.CMSDB2Mal  AS b ON b.OWNING_MAL_APPID = a.APPID
JOIN dbo.CMSMDBs    AS d ON d.ASSETTAG         = b.MDL_ASSETTAG
WHERE a.ASSIGNMENT = 'Production'
  AND d.ASSIGNMENT = 'In Use';
GO

/* ---------------------------------------------------------------------------
   3. database -> server   "what server hosts this database?"
   --------------------------------------------------------------------------- */
CREATE OR ALTER VIEW USM.vw_cms_database_to_server AS
SELECT
    d.ASSETTAG        AS database_assettag,
    d.DATABASE_NAME   AS database_name,
    d.DATABASE_TYPE   AS database_type,
    d.STATUS          AS database_status,
    h.NAME            AS server_name,
    h.OPERATINGSYSTEM AS server_os,
    h.STATUS          AS server_status,
    h.TCPIPADDRESS    AS server_ip
FROM dbo.CMSMDBs      AS d
JOIN dbo.CMSMDB2Server AS m ON m.MDL_ASSETTAG = d.ASSETTAG
JOIN dbo.CMSHost       AS h ON h.NAME         = m.SERVER_NAME
WHERE d.ASSIGNMENT = 'In Use'
  AND h.ASSIGNMENT = 'In Use';
GO

/* ---------------------------------------------------------------------------
   4. app -> database -> server    full chain
   --------------------------------------------------------------------------- */
CREATE OR ALTER VIEW USM.vw_cms_app_to_database_to_server AS
SELECT
    a.APPID           AS app_id,
    a.ACRONYM         AS app_acronym,
    a.NAME            AS app_name,
    a.SOX_CRITICAL    AS app_sox_critical,
    d.ASSETTAG        AS database_assettag,
    d.DATABASE_NAME   AS database_name,
    d.DATABASE_TYPE   AS database_type,
    h.NAME            AS server_name,
    h.OPERATINGSYSTEM AS server_os,
    h.STATUS          AS server_status
FROM dbo.CMSMalApps    AS a
JOIN dbo.CMSDB2Mal     AS b ON b.OWNING_MAL_APPID = a.APPID
JOIN dbo.CMSMDBs       AS d ON d.ASSETTAG         = b.MDL_ASSETTAG
JOIN dbo.CMSMDB2Server AS m ON m.MDL_ASSETTAG     = d.ASSETTAG
JOIN dbo.CMSHost       AS h ON h.NAME             = m.SERVER_NAME
WHERE a.ASSIGNMENT = 'Production'
  AND d.ASSIGNMENT = 'In Use'
  AND h.ASSIGNMENT = 'In Use';
GO

/* ---------------------------------------------------------------------------
   5. server -> cluster
   CMSHost.LASTID is bigint and CMSHosttoCluster.SERVER_LASTID is int; SQL Server
   widens int->bigint implicitly, so no explicit CAST is needed here.
   --------------------------------------------------------------------------- */
CREATE OR ALTER VIEW USM.vw_cms_server_to_cluster AS
SELECT
    h.NAME           AS server_name,
    h.STATUS         AS server_status,
    c.NAME           AS cluster_name,
    c.STATUS         AS cluster_status,
    c.MODEL_NAME     AS cluster_model,
    c.BRAND_NAME     AS cluster_brand
FROM dbo.CMSHost          AS h
JOIN dbo.CMSHosttoCluster AS x ON x.SERVER_LASTID  = h.LASTID
JOIN dbo.CMSCluster       AS c ON c.LASTID         = x.CLUSTER_LASTID
WHERE h.ASSIGNMENT = 'In Use';
GO

/* ---------------------------------------------------------------------------
   6. vm -> physical esx host   (both sides share CLUSTER_LASTID)
   --------------------------------------------------------------------------- */
CREATE OR ALTER VIEW USM.vw_cms_vm_to_host AS
SELECT
    v.VIRTUAL_SERVER_NAME AS vm_name,
    e.ESX_SERVER_NAME     AS esx_host_name,
    v.CLUSTER_NAME        AS cluster_name
FROM dbo.CMSESX2VM   AS v
JOIN dbo.CMSESX2Host AS e ON e.CLUSTER_LASTID = v.CLUSTER_LASTID;
GO

/* ---------------------------------------------------------------------------
   7. server -> backups
   CMSBackups.SERVER_LASTID is bigint == CMSHost.LASTID: no cast needed.
   --------------------------------------------------------------------------- */
CREATE OR ALTER VIEW USM.vw_cms_server_to_backups AS
SELECT
    h.NAME             AS server_name,
    h.STATUS           AS server_status,
    b.BACKUP_NODE      AS backup_node,
    b.BACKUP_NAME      AS backup_name,
    b.BACKUP_DATE      AS backup_date,
    b.BACKUP_EXEMPTION AS backup_exemption
FROM dbo.CMSHost    AS h
JOIN dbo.CMSBackups AS b ON b.SERVER_LASTID = h.LASTID
WHERE h.ASSIGNMENT = 'In Use';
GO

/* ---------------------------------------------------------------------------
   8. array -> host -> app -> database  (+ capacity)
   dbo.AllArrayHostAppDBData is ALREADY the resolved chain (21,273 rows), so this
   is a rename/projection rather than a join. This is the view that ties CMS to
   USM: `array_name` here matches USM.metrics_current.array_name.
   --------------------------------------------------------------------------- */
CREATE OR ALTER VIEW USM.vw_cms_array_to_app_db AS
SELECT
    d.Array           AS array_name,     -- joins USM.metrics_current.array_name
    d.Vendor          AS vendor,
    d.DC              AS datacenter,
    d.Hostgroup       AS host_group,
    d.Hostname        AS host_name,
    d.CMSHostName     AS cms_host_name,
    d.CMSTCPIPADDRESS AS host_ip,
    d.AllocatedGB     AS allocated_gb,
    d.UsedGB          AS used_gb,
    d.AppID           AS app_id,
    d.AppAcronym      AS app_acronym,
    d.AppName         AS app_name,
    d.SOX_CRITICAL    AS app_sox_critical,
    d.BIA_CRITICAL    AS app_bia_critical,
    d.DATABASE_NAME   AS database_name,
    d.DATABASE_TYPE   AS database_type,
    d.Database_status AS database_status
FROM dbo.AllArrayHostAppDBData AS d;
GO

/* ---------------------------------------------------------------------------
   9. san switch -> host -> array -> app/db
   dbo.SAN_Host_Switch already resolves host HBA WWPN -> switch port.
   --------------------------------------------------------------------------- */
CREATE OR ALTER VIEW USM.vw_cms_switch_to_host_app_db AS
SELECT
    s.SwitchName    AS switch_name,
    s.Fabric        AS fabric,
    s.Slot          AS slot,
    s.Port          AS port,
    s.HBA_WWPN      AS hba_wwpn,
    s.PortWWPN      AS port_wwpn,
    s.Host          AS host_name,
    d.Array         AS array_name,
    d.Vendor        AS vendor,
    d.AppAcronym    AS app_acronym,
    d.AppName       AS app_name,
    d.DATABASE_NAME AS database_name
FROM dbo.SAN_Host_Switch       AS s
LEFT JOIN dbo.AllArrayHostAppDBData AS d ON d.Hostname = s.Host;
GO
