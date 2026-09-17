# USM v3 — Agile User Stories

## Epic: Unified Storage Monitoring Platform v3
**Goal**: Build a multi-vendor storage monitoring platform that provides a single pane of glass across all storage infrastructure (Pure, NetApp, HPE, Oracle, Hitachi, Dell, CommVault) with real-time capacity, performance, alerting, and AI-powered natural language queries.

---

## Sprint 1: Foundation & Bug Fixes (Completed)

### USM-101: Fix NetApp Array Status Reporting
**As a** storage administrator  
**I want** NetApp arrays to show correct health status  
**So that** I can trust the dashboard for operational decisions  
**Acceptance Criteria**:
- NetApp arrays no longer show "degraded" when healthy
- Health status defaults to "healthy" when ONTAP API omits the field
- Controller status displays correctly for all NetApp nodes

### USM-102: Reduce Alert Noise from NetApp EMS Events
**As a** storage administrator  
**I want** alerts to be auto-resolved after 7 days and filtered by severity  
**So that** I only see actionable alerts, not 170K+ stale events  
**Acceptance Criteria**:
- NetApp alerts filtered to last 24 hours only
- Auto-resolve after 7 days (configurable)
- Notice-severity events excluded
- Alert count reduced from 170K to manageable levels

### USM-103: Fix Slow NetApp Alert Collection
**As a** platform operator  
**I want** alert collection to complete in <5 seconds per array  
**So that** the scheduler doesn't block other vendor jobs  
**Acceptance Criteria**:
- NetApp alerts use time-filtered single GET (not paginated get_all)
- Collection time reduced from 60s to <5s per array

---

## Sprint 2: Inventory Integration & KeePass (Completed)

### USM-201: Auto-Discover Arrays from DimStorageFinance
**As a** storage administrator  
**I want** all 353 arrays across 11 vendors to be auto-discovered from the authoritative inventory source  
**So that** I don't manually maintain array lists and new arrays are monitored automatically  
**Acceptance Criteria**:
- Daily sync from `StorMart.dbo.DimStorageFinance` → `USM.managed_arrays`
- 353 arrays across 11 vendors imported with metadata (model, site, serial, FQDN, disposition)
- Decommissioned arrays automatically disabled
- Current arrays with working collectors automatically enabled

### USM-202: KeePass Credential Management
**As a** platform operator  
**I want** a containerized KeePass REST API with caching and browsing  
**So that** credentials are managed securely and API calls don't overwhelm the KeePass service  
**Acceptance Criteria**:
- KeePass Flask app containerized with gunicorn (4 workers)
- 60-second response caching with 24-hour TTL
- Midnight credential refresh job
- `/health` endpoint for Docker healthcheck
- Browsable KeePass entries in Settings UI

### USM-203: Editable Array Management UI
**As a** storage administrator  
**I want** to view, search, filter, and edit managed arrays in the Settings page  
**So that** I can manage array configuration (credentials, enable/disable) through the UI  
**Acceptance Criteria**:
- Arrays tab with search, vendor filter, model/site/status columns
- Edit modal with KeePass credential browser/picker
- Enable/disable toggle per array
- Inventory sync trigger button

---

## Sprint 3: Multi-Vendor Collectors (Completed)

### USM-301: HPE 3Par/Primera/Alletra Collector
**As a** storage administrator  
**I want** HPE storage arrays monitored for capacity, volumes, hosts, and alerts  
**So that** I have visibility into HPE infrastructure alongside Pure and NetApp  
**Acceptance Criteria**:
- WSAPI client with auto-port detection (8080 for 3Par, 443 for Primera/Alletra)
- Metrics: capacity (MiB→bytes), IOPS from port statistics, data reduction from CPGs
- Volumes: VLUNs with host mappings, host sets
- Alerts: WSAPI alerts with severity mapping
- **Result**: 22/23 HPE arrays collecting successfully

### USM-302: Oracle ZFS Storage Appliance Collector
**As a** storage administrator  
**I want** Oracle ZFS arrays monitored for capacity, volumes/shares, and alerts  
**So that** I have visibility into Oracle ZFS infrastructure  
**Acceptance Criteria**:
- REST API client on port 215 with Basic Auth
- Metrics: pool capacity from detail endpoint (`/pools/{name}`), compression, used%
- Volumes: LUNs + NFS filesystems per pool/project
- Alerts: Problems endpoint (`/problem/v1/problems`)
- Handle both flat and nested API response formats
- **Result**: 16/16 Oracle arrays, 60 volumes collected

### USM-303: Hitachi VSP F900 Collector
**As a** storage administrator  
**I want** Hitachi VSP F900 arrays monitored for capacity, LDEVs, hosts, and alerts  
**So that** I have visibility into Hitachi enterprise storage (3.1 PB total)  
**Acceptance Criteria**:
- Configuration Manager REST API client (port 443, Basic Auth + Session Auth)
- Metrics: pool capacity in MB, used%, data reduction, firmware version
- Volumes: LDEVs (512-byte blocks) with host-group port mappings
- Unique LDEV naming (`LDEV:00123 (label)`) to prevent duplicates
- 300s timeout for large arrays with 3000+ LDEVs
- **Result**: 5/5 arrays, 505+ LDEVs, 22 host groups, 3.1 PB capacity

---

## Sprint 4: AI Chat & Observability (Completed)

### USM-401: Storage AI — Natural Language Queries
**As a** storage administrator  
**I want** to ask questions about storage infrastructure in plain English  
**So that** I can get quick answers without writing SQL or navigating dashboards  
**Acceptance Criteria**:
- Local Ollama LLM (qwen2.5:3b) — no data leaves the host
- Text-to-SQL pipeline: question → SQL generation → execution → formatted answer
- SQL safety validation (SELECT-only, USM schema, blocked patterns)
- Self-correction retry when generated SQL fails
- Full-page chat UI with session persistence
- Examples: "how many arrays?", "which array has highest IOPS?", "show capacity by vendor"
- **Result**: Working chat at `/chat` route, accessible via "Storage AI" sidebar link

### USM-402: Array Uptime & Reboot Tracking
**As a** storage administrator  
**I want** to see uptime and last reboot time for each array  
**So that** I can identify recently rebooted arrays and track availability  
**Acceptance Criteria**:
- Pure: uptime from reboot alerts in messages table
- NetApp: uptime from ONTAP node uptime field
- Oracle: boot time from system version API
- Displayed in array detail modal

---

## Sprint 5: Dell EMC & v3 Release (Completed)

### USM-501: Dell EMC Collector (Unity/PowerStore)
**As a** storage administrator  
**I want** Dell EMC arrays monitored (11 current arrays)  
**So that** all production storage is visible in a single dashboard  
**Acceptance Criteria**:
- Dell EMC Unity/PowerStore REST API client
- Metrics, volumes, hosts, alerts collectors
- KeePass credential mapping
- **Result**: Dell collector implemented with capacity, volumes, hosts, alerts

### USM-505: v3 Release — Cleanup & Documentation
**As a** platform operator  
**I want** v1 legacy code removed, version bumped to 3.0.0, and comprehensive docs  
**So that** the codebase is clean, well-documented, and production-ready  
**Acceptance Criteria**:
- Remove v1 directories (collectors/, scheduler/, web/, docker/requirements/)
- Remove old Dockerfiles and compose files
- Rename docker-compose.v2.yml → docker-compose.yml
- Version bump to 3.0.0 in config.py
- Comprehensive README.md with architecture, vendors, API reference, how-to-add-vendor
- Disable 6 unreachable arrays (HPE DNS failure, StorageGRID auth, CVO unreachable)
- **Result**: Clean repo, full docs, 84 arrays actively monitored across 7 vendors

### USM-502: Backend Performance Optimization
**As a** platform operator  
**I want** the backend to remain responsive during heavy collection cycles  
**So that** the dashboard and API don't timeout under load  
**Acceptance Criteria**:
- Add SQL indexes on `volumes_cache`, `hosts_cache`, `messages` tables
- Connection pooling for SQL Server (replace per-request connections)
- Stagger collector schedules to avoid concurrent DB pressure
- Fleet-stats query optimization (cached aggregates)

### USM-503: CI/CD Pipeline
**As a** developer  
**I want** automated deployments from GitHub to the host server  
**So that** code changes don't require manual SCP + Docker rebuild  
**Acceptance Criteria**:
- GitHub credentials on host server
- `git pull` based deployment or webhook-triggered rebuild
- Automated frontend + backend rebuild on push to main

### USM-504: CommVault/Veeam Backup Monitoring
**As a** storage administrator  
**I want** backup infrastructure (CommVault 164 arrays, Veeam 3) monitored  
**So that** backup capacity and job status is visible alongside storage  
**Acceptance Criteria**:
- CommVault REST API integration
- Backup capacity, job status, client counts
- Veeam backup server monitoring

---

## Current Platform Metrics (v3.0.0)

| Metric | Value |
|--------|-------|
| **Total Arrays Discovered** | 353 (from DimStorageFinance) |
| **Arrays Actively Monitored** | ~84 |
| **Vendors with Collectors** | 7 (Pure, NetApp, HPE, Oracle, Hitachi, Dell, StorageGRID) |
| **Total Volumes Tracked** | 22,500+ |
| **Total Hosts Cataloged** | 2,740+ |
| **Total Storage Capacity** | ~10+ PB |
| **Scheduler Jobs** | 21 (metrics/volumes/alerts per vendor + maintenance) |
| **Collection Cycle** | Metrics: 60s, Alerts: 5min, Volumes: 15min |
| **AI Chat** | Local Ollama (qwen2.5:3b) Text-to-SQL |
