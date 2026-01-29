# Unified Storage Monitoring

A modular, multi-vendor storage monitoring solution.

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌──────────────┐
│  pure-web   │◄───►│pure-scheduler│────►│pure-collector│
│             │     │              │     │              │
│ • Dashboard │     │ • Job Queue  │     │ • Pure       │
│ • REST API  │     │ • Intervals  │     │ • NetApp*    │
│ • Docker    │     │ • Run Now    │     │ • Dell EMC*  │
└─────────────┘     └──────────────┘     └──────────────┘
                           │
                           ▼
                    ┌──────────────┐
                    │  SQL Server  │
                    │  (StorMart)  │
                    └──────────────┘
                    
* Future support
```

## Directory Structure

```
unified_storage_monitoring/
├── docker/                      # Docker configuration
│   ├── docker-compose.yml       # Multi-service orchestration
│   ├── web.Dockerfile           # Web UI container
│   ├── scheduler.Dockerfile     # Scheduler container
│   ├── collector.Dockerfile     # Collector container
│   └── requirements/            # Python dependencies
│       ├── web.txt
│       ├── scheduler.txt
│       └── collector.txt
│
├── web/                         # Web UI application
│   └── app.py                   # Flask application
│
├── scheduler/                   # Scheduler service
│   └── service.py               # Job scheduler with API
│
├── collectors/                  # Data collectors
│   ├── common/                  # Shared utilities
│   │   ├── db.py                # Database helpers
│   │   └── base.py              # Base collector class
│   │
│   ├── pure/                    # Pure Storage collectors
│   │   ├── metrics.py           # Performance metrics
│   │   ├── volumes.py           # Volume/Host inventory
│   │   └── alerts.py            # Alerts and messages
│   │
│   ├── netapp/                  # NetApp collectors (future)
│   └── dell_emc/                # Dell EMC collectors (future)
│
├── config/                      # Configuration files
│   ├── arrays.txt               # Array list
│   └── jobs.json                # Scheduler job definitions
│
└── logs/                        # Log files (Docker volumes)
```

## Quick Start

### 1. Configure Arrays

Edit `config/arrays.txt`:
```
# One array FQDN per line
purecbs-aws-array1.example.com
purecbs-azure-array2.example.com
on-prem-array3.example.com
```

### 2. Start Services

```bash
cd docker
docker compose up -d
```

### 3. Access Dashboard

Open http://localhost:5000

## Services

### Web UI (port 5000)
- Dashboard with array overview
- Volume and host inventory
- Alerts management
- Settings for scheduler control
- Container stats monitoring

### Scheduler (port 5001)
- Background job management
- Configurable intervals
- REST API for control:
  - `GET /api/jobs` - List all jobs
  - `POST /api/jobs/{id}/run` - Run job now
  - `POST /api/jobs/{id}/enable` - Enable job
  - `POST /api/jobs/{id}/disable` - Disable job
  - `POST /api/jobs/{id}/interval` - Set interval

### Collector (on-demand)
```bash
# Run manually
docker compose --profile manual run pure-collector python /app/collectors/pure/metrics.py --all
```

## Adding New Vendors

1. Create collector directory: `collectors/newvendor/`
2. Inherit from `BaseCollector`:

```python
from common.base import BaseCollector

class NewVendorCollector(BaseCollector):
    VENDOR_NAME = "NewVendor"
    COLLECTOR_TYPE = "metrics"
    
    def authenticate(self) -> bool:
        # Implement authentication
        pass
    
    def collect(self) -> dict:
        # Implement data collection
        pass
    
    def save(self, data: dict) -> bool:
        # Implement database save
        pass
```

3. Add job to `config/jobs.json`

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| SQL_SERVER | usidcvsql0252.ctl.intranet | SQL Server hostname |
| SQL_DATABASE | StorMart | Database name |
| SQL_CRED_KEY | SQLServerDB | KeePass credential key |
| KEEPASS_URL | http://localhost:2000/keepass | KeePass API URL |
| SCHEDULER_URL | http://pure-scheduler:5001 | Scheduler service URL |

## Development

### Run collectors locally:
```bash
export PYTHONPATH=/path/to/unified_storage_monitoring
python collectors/pure/metrics.py --all
```

### Test database connection:
```bash
python collectors/common/db.py
```

## Troubleshooting

### Check container logs:
```bash
docker logs pure-web
docker logs pure-scheduler
```

### Restart services:
```bash
docker compose restart
```

### Rebuild after code changes:
```bash
docker compose build --no-cache
docker compose up -d
```
# app-usm
