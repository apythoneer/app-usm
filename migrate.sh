#!/bin/bash
#
# Unified Storage Monitoring - Migration Script
# Reorganizes the project to new modular structure
#

set -e

echo "=========================================="
echo "  USM Migration to Modular Structure"
echo "=========================================="
echo ""

PROJECT_DIR=~/unified_storage_monitoring
cd "$PROJECT_DIR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

info() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Check we're in the right place
if [ ! -f "web_ui.py" ]; then
    error "web_ui.py not found. Run this from ~/unified_storage_monitoring"
    exit 1
fi

# Step 1: Create new directory structure
info "Creating new directory structure..."
mkdir -p collectors/common
mkdir -p collectors/pure
mkdir -p collectors/netapp
mkdir -p collectors/dell_emc
mkdir -p scheduler
mkdir -p web
mkdir -p config
mkdir -p docker/requirements
mkdir -p logs_new/web
mkdir -p logs_new/scheduler
mkdir -p logs_new/collectors

# Step 2: Copy arrays.txt to config
info "Moving configuration files..."
if [ -f "arrays.txt" ]; then
    cp arrays.txt config/arrays.txt
    info "  - Copied arrays.txt to config/"
fi

# Step 3: Create __init__.py files
info "Creating Python package files..."
touch collectors/__init__.py
touch collectors/common/__init__.py
touch collectors/pure/__init__.py
touch collectors/netapp/__init__.py
touch collectors/dell_emc/__init__.py
touch scheduler/__init__.py
touch web/__init__.py

# Step 4: Download new files from Claude (you'll copy these manually)
info "File structure created!"
echo ""
echo "=========================================="
echo "  Next Steps - Copy Files Manually"
echo "=========================================="
echo ""
echo "Copy the following files from Claude's output:"
echo ""
echo "  COLLECTORS:"
echo "    collectors/common/db.py"
echo "    collectors/common/base.py"
echo "    collectors/common/__init__.py"
echo "    collectors/pure/metrics.py"
echo "    collectors/pure/volumes.py"
echo "    collectors/pure/alerts.py"
echo "    collectors/pure/__init__.py"
echo ""
echo "  SCHEDULER:"
echo "    scheduler/service.py"
echo ""
echo "  CONFIG:"
echo "    config/jobs.json"
echo ""
echo "  DOCKER:"
echo "    docker/docker-compose.yml"
echo "    docker/web.Dockerfile"
echo "    docker/scheduler.Dockerfile"
echo "    docker/collector.Dockerfile"
echo "    docker/requirements/web.txt"
echo "    docker/requirements/scheduler.txt"
echo "    docker/requirements/collector.txt"
echo ""
echo "  WEB:"
echo "    web/app.py"
echo ""
echo "=========================================="
echo ""

# Verify structure
info "Current structure:"
find . -maxdepth 3 -type d | grep -v __pycache__ | grep -v _legacy | grep -v "\.git" | sort

echo ""
info "Migration prep complete!"
