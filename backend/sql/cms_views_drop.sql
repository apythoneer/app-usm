/* ============================================================================
   Teardown for the USM <-> CMS bridge views (see cms_views.sql).

   SAFE BY CONSTRUCTION: these objects are VIEWS — stored queries holding no
   data. Dropping them removes only the query definitions. No CMS row, no USM
   row, and no storage is touched. Re-create at any time by running
   cms_views.sql.

   The only consequence of running this is that the Text-to-SQL chat loses its
   ability to answer app/server/database questions until the views are restored.
   ============================================================================ */

DROP VIEW IF EXISTS USM.vw_cms_app_to_server;
DROP VIEW IF EXISTS USM.vw_cms_app_to_database;
DROP VIEW IF EXISTS USM.vw_cms_database_to_server;
DROP VIEW IF EXISTS USM.vw_cms_app_to_database_to_server;
DROP VIEW IF EXISTS USM.vw_cms_server_to_cluster;
DROP VIEW IF EXISTS USM.vw_cms_vm_to_host;
DROP VIEW IF EXISTS USM.vw_cms_server_to_backups;
DROP VIEW IF EXISTS USM.vw_cms_array_to_app_db;
DROP VIEW IF EXISTS USM.vw_cms_switch_to_host_app_db;
GO
