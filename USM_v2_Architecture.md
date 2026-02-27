# Unified Storage Monitoring v2.0 - Modern Architecture

## Project Structure

```
usm-v2/
├── backend/                    # FastAPI Backend
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py            # FastAPI app entry point
│   │   ├── config.py          # Settings management (Pydantic)
│   │   │
│   │   ├── api/               # API Routes (organized by feature)
│   │   │   ├── __init__.py
│   │   │   ├── deps.py        # Dependencies (auth, db session)
│   │   │   ├── v1/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── router.py  # Main API router
│   │   │   │   ├── auth.py    # Login/logout/refresh endpoints
│   │   │   │   ├── arrays.py  # Array metrics endpoints
│   │   │   │   ├── volumes.py # Volume endpoints
│   │   │   │   ├── hosts.py   # Host endpoints
│   │   │   │   ├── alerts.py  # Alert endpoints
│   │   │   │   ├── analytics.py # Time series endpoints
│   │   │   │   ├── settings.py  # Config endpoints
│   │   │   │   └── scheduler.py # Job management
│   │   │   └── websocket.py   # Real-time updates (optional)
│   │   │
│   │   ├── core/              # Core functionality
│   │   │   ├── __init__.py
│   │   │   ├── security.py    # JWT, password hashing
│   │   │   ├── config.py      # Pydantic settings
│   │   │   └── exceptions.py  # Custom exceptions
│   │   │
│   │   ├── db/                # Database
│   │   │   ├── __init__.py
│   │   │   ├── session.py     # Async SQLAlchemy session
│   │   │   ├── base.py        # Base model class
│   │   │   └── migrations/    # Alembic migrations
│   │   │
│   │   ├── models/            # SQLAlchemy Models
│   │   │   ├── __init__.py
│   │   │   ├── user.py        # User model
│   │   │   ├── array.py       # MetricsCurrent model
│   │   │   ├── volume.py      # VolumesCache model
│   │   │   ├── host.py        # HostsCache model
│   │   │   ├── alert.py       # Messages model
│   │   │   └── audit.py       # Audit log model
│   │   │
│   │   ├── schemas/           # Pydantic Schemas (Request/Response)
│   │   │   ├── __init__.py
│   │   │   ├── auth.py        # Login, Token schemas
│   │   │   ├── array.py       # Array schemas
│   │   │   ├── volume.py      # Volume schemas
│   │   │   ├── host.py        # Host schemas
│   │   │   ├── alert.py       # Alert schemas
│   │   │   └── common.py      # Pagination, filters
│   │   │
│   │   ├── services/          # Business Logic
│   │   │   ├── __init__.py
│   │   │   ├── auth.py        # Auth service
│   │   │   ├── array.py       # Array service
│   │   │   ├── volume.py      # Volume service
│   │   │   ├── host.py        # Host service
│   │   │   ├── alert.py       # Alert service
│   │   │   ├── notification.py # Teams/SNOW notifications
│   │   │   └── keepass.py     # KeePass integration
│   │   │
│   │   └── collectors/        # Data Collectors
│   │       ├── __init__.py
│   │       ├── base.py        # Base collector class
│   │       ├── pure_metrics.py
│   │       ├── pure_volumes.py
│   │       └── pure_alerts.py
│   │
│   ├── tests/                 # Backend tests
│   │   ├── __init__.py
│   │   ├── conftest.py
│   │   ├── test_auth.py
│   │   ├── test_arrays.py
│   │   └── ...
│   │
│   ├── alembic/               # Database migrations
│   │   ├── env.py
│   │   └── versions/
│   │
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── pyproject.toml         # Modern Python packaging
│   └── .env.example
│
├── frontend/                   # React Frontend
│   ├── src/
│   │   ├── main.tsx           # Entry point
│   │   ├── App.tsx            # Root component
│   │   ├── vite-env.d.ts
│   │   │
│   │   ├── api/               # API Client
│   │   │   ├── client.ts      # Axios instance with interceptors
│   │   │   ├── auth.ts        # Auth API calls
│   │   │   ├── arrays.ts      # Array API calls
│   │   │   ├── volumes.ts     # Volume API calls
│   │   │   ├── hosts.ts       # Host API calls
│   │   │   ├── alerts.ts      # Alert API calls
│   │   │   └── types.ts       # API response types
│   │   │
│   │   ├── components/        # Reusable Components
│   │   │   ├── ui/            # Base UI components
│   │   │   │   ├── Button.tsx
│   │   │   │   ├── Card.tsx
│   │   │   │   ├── Modal.tsx
│   │   │   │   ├── Table.tsx
│   │   │   │   ├── Badge.tsx
│   │   │   │   ├── Input.tsx
│   │   │   │   └── ...
│   │   │   │
│   │   │   ├── layout/        # Layout components
│   │   │   │   ├── Navbar.tsx
│   │   │   │   ├── Sidebar.tsx
│   │   │   │   ├── Footer.tsx
│   │   │   │   └── Layout.tsx
│   │   │   │
│   │   │   ├── charts/        # Chart components
│   │   │   │   ├── LatencyChart.tsx
│   │   │   │   ├── IOPSChart.tsx
│   │   │   │   ├── CapacityChart.tsx
│   │   │   │   └── AlertsChart.tsx
│   │   │   │
│   │   │   ├── arrays/        # Array-specific components
│   │   │   │   ├── ArrayCard.tsx
│   │   │   │   ├── ArrayTable.tsx
│   │   │   │   └── ArrayDetailModal.tsx
│   │   │   │
│   │   │   ├── volumes/       # Volume components
│   │   │   │   ├── VolumeTable.tsx
│   │   │   │   └── VolumeTopology.tsx
│   │   │   │
│   │   │   ├── hosts/         # Host components
│   │   │   │   ├── HostTable.tsx
│   │   │   │   └── HostTopology.tsx
│   │   │   │
│   │   │   ├── alerts/        # Alert components
│   │   │   │   ├── AlertTable.tsx
│   │   │   │   └── AlertBadge.tsx
│   │   │   │
│   │   │   └── common/        # Shared components
│   │   │       ├── DataTable.tsx    # Generic sortable/filterable table
│   │   │       ├── Pagination.tsx
│   │   │       ├── SearchBar.tsx
│   │   │       ├── StatusDot.tsx
│   │   │       ├── CapacityBar.tsx
│   │   │       └── TopologyView.tsx
│   │   │
│   │   ├── pages/             # Page Components (Routes)
│   │   │   ├── Login.tsx
│   │   │   ├── Dashboard.tsx
│   │   │   ├── Volumes.tsx
│   │   │   ├── Hosts.tsx
│   │   │   ├── Alerts.tsx
│   │   │   ├── Analytics.tsx
│   │   │   ├── Logs.tsx
│   │   │   ├── Settings.tsx
│   │   │   └── NotFound.tsx
│   │   │
│   │   ├── hooks/             # Custom React Hooks
│   │   │   ├── useAuth.ts     # Auth state hook
│   │   │   ├── useArrays.ts   # Arrays data hook
│   │   │   ├── useVolumes.ts  # Volumes data hook
│   │   │   ├── useAlerts.ts   # Alerts data hook
│   │   │   ├── useWebSocket.ts # Real-time updates
│   │   │   └── useLocalStorage.ts
│   │   │
│   │   ├── context/           # React Context
│   │   │   ├── AuthContext.tsx
│   │   │   └── ThemeContext.tsx
│   │   │
│   │   ├── stores/            # State Management (Zustand)
│   │   │   ├── authStore.ts
│   │   │   ├── filterStore.ts
│   │   │   └── settingsStore.ts
│   │   │
│   │   ├── utils/             # Utility functions
│   │   │   ├── formatters.ts  # formatBytes, formatLatency, etc.
│   │   │   ├── constants.ts   # App constants
│   │   │   └── helpers.ts     # Misc helpers
│   │   │
│   │   ├── types/             # TypeScript types
│   │   │   ├── array.ts
│   │   │   ├── volume.ts
│   │   │   ├── host.ts
│   │   │   ├── alert.ts
│   │   │   └── index.ts
│   │   │
│   │   └── styles/            # Global styles
│   │       └── globals.css    # Tailwind imports
│   │
│   ├── public/                # Static assets
│   │   └── favicon.ico
│   │
│   ├── index.html
│   ├── package.json
│   ├── tsconfig.json
│   ├── vite.config.ts
│   ├── tailwind.config.js
│   ├── postcss.config.js
│   ├── Dockerfile
│   └── .env.example
│
├── collectors/                 # Standalone Collectors (can be separate service)
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py            # Scheduler entry point
│   │   ├── config.py
│   │   ├── collectors/
│   │   │   ├── base.py
│   │   │   ├── pure_metrics.py
│   │   │   ├── pure_volumes.py
│   │   │   └── pure_alerts.py
│   │   └── services/
│   │       ├── keepass.py
│   │       ├── database.py
│   │       └── notification.py
│   ├── Dockerfile
│   └── requirements.txt
│
├── docker/
│   ├── docker-compose.yml     # Full stack
│   ├── docker-compose.dev.yml # Development overrides
│   ├── nginx/
│   │   └── nginx.conf         # Reverse proxy config
│   └── .env.example
│
├── docs/
│   ├── API.md                 # API documentation
│   ├── DEPLOYMENT.md          # Deployment guide
│   ├── DEVELOPMENT.md         # Dev setup guide
│   └── ARCHITECTURE.md        # Architecture overview
│
├── scripts/
│   ├── setup.sh               # Initial setup script
│   ├── migrate.sh             # Database migration script
│   └── backup.sh              # Backup script
│
├── sql/
│   └── migrations/            # Raw SQL migrations (if needed)
│
├── .github/
│   └── workflows/
│       ├── ci.yml             # CI pipeline
│       └── deploy.yml         # CD pipeline
│
├── .gitignore
├── README.md
└── Makefile                   # Common commands
```

## Key Benefits of This Structure

### 1. **Separation of Concerns**
- Backend handles API only (no HTML rendering)
- Frontend is a standalone SPA
- Collectors run independently

### 2. **Type Safety**
- FastAPI + Pydantic = validated API requests/responses
- TypeScript = type-safe frontend
- Auto-generated API client possible

### 3. **Authentication Ready**
```python
# backend/app/core/security.py
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/v1/auth/login")

async def get_current_user(token: str = Depends(oauth2_scheme)):
    # Validate JWT, return user
    ...

# Usage in any endpoint:
@router.get("/arrays")
async def get_arrays(current_user: User = Depends(get_current_user)):
    ...
```

### 4. **Scalability**
- Frontend can be served from CDN
- Backend can be horizontally scaled
- Collectors can run on separate machines
- Easy to add Redis for caching

### 5. **Modern Developer Experience**
- Hot reload on both frontend and backend
- OpenAPI docs auto-generated at /docs
- Type hints everywhere
- Modern tooling (Vite, ESLint, Black, Ruff)

## Tech Stack Details

### Backend (FastAPI)
```
fastapi>=0.109.0
uvicorn[standard]>=0.27.0
sqlalchemy[asyncio]>=2.0.25
asyncpg                      # Async PostgreSQL (or aioodbc for SQL Server)
pydantic>=2.5.0
pydantic-settings>=2.1.0
python-jose[cryptography]    # JWT
passlib[bcrypt]              # Password hashing
python-multipart             # Form data
httpx                        # Async HTTP client
apscheduler                  # Job scheduling
alembic                      # Migrations
```

### Frontend (React + Vite)
```json
{
  "dependencies": {
    "react": "^18.2.0",
    "react-dom": "^18.2.0",
    "react-router-dom": "^6.21.0",
    "@tanstack/react-query": "^5.17.0",
    "axios": "^1.6.0",
    "recharts": "^2.10.0",
    "zustand": "^4.4.0",
    "react-hot-toast": "^2.4.0",
    "lucide-react": "^0.300.0",
    "clsx": "^2.1.0",
    "date-fns": "^3.2.0"
  },
  "devDependencies": {
    "typescript": "^5.3.0",
    "vite": "^5.0.0",
    "tailwindcss": "^3.4.0",
    "@types/react": "^18.2.0",
    "eslint": "^8.56.0",
    "prettier": "^3.2.0"
  }
}
```

## Docker Compose (Production)

```yaml
version: '3.8'

services:
  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./docker/nginx/nginx.conf:/etc/nginx/nginx.conf
      - ./frontend/dist:/usr/share/nginx/html
    depends_on:
      - backend

  backend:
    build: ./backend
    environment:
      - DATABASE_URL=mssql+aioodbc://...
      - SECRET_KEY=${SECRET_KEY}
      - KEEPASS_URL=${KEEPASS_URL}
    volumes:
      - ./logs:/app/logs
    expose:
      - "8000"

  collectors:
    build: ./collectors
    environment:
      - DATABASE_URL=mssql+aioodbc://...
      - KEEPASS_URL=${KEEPASS_URL}
    volumes:
      - ./logs:/app/logs
    depends_on:
      - backend

  redis:  # Optional: for caching
    image: redis:alpine
    expose:
      - "6379"
```

## Migration Path

### Phase 1: Backend First (1-2 weeks)
1. Create FastAPI backend with same API endpoints
2. Add Pydantic schemas for all data
3. Test alongside existing Flask app
4. Switch when ready

### Phase 2: Frontend (2-3 weeks)
1. Set up React + Vite + Tailwind
2. Create reusable components
3. Implement pages one by one
4. Dashboard → Volumes → Hosts → Alerts → Analytics → Settings

### Phase 3: Authentication (1 week)
1. Add User model
2. Implement JWT auth
3. Add login page
4. Protect routes
5. Optional: Add AD/LDAP integration

### Phase 4: Polish (1 week)
1. Add tests
2. Set up CI/CD
3. Documentation
4. Performance optimization

## Ready to Start?

If you want to proceed, I can create:
1. **Backend skeleton** with FastAPI + auth + one endpoint working
2. **Frontend skeleton** with React + routing + one page working
3. **Docker setup** for local development

Let me know and I'll generate the starter code!
