# USM v3 — convenience commands
# Run from repo root

COMPOSE = docker compose -f docker/docker-compose.yml

# ── Deploy target (dev server over SSH) ───────────────────────────────────────
# Override on the command line, e.g.:
#   make deploy DEPLOY_HOST=usm-dev DEPLOY_USER=svc_usm DEPLOY_PATH=/opt/usm
DEPLOY_USER   ?= deploy
DEPLOY_HOST   ?= usm-dev
DEPLOY_PATH   ?= /opt/usm
DEPLOY_BRANCH ?= main
SSH           = ssh $(DEPLOY_USER)@$(DEPLOY_HOST)

.PHONY: up down build logs restart status shell-backend shell-frontend deploy deploy-check


## Start v2 stack (build if needed)
up:
	$(COMPOSE) up -d --build

## Start without rebuilding
start:
	$(COMPOSE) up -d

## Stop and remove containers
down:
	$(COMPOSE) down

## Rebuild images without cache
build:
	$(COMPOSE) build --no-cache

## Follow logs (all services)
logs:
	$(COMPOSE) logs -f

## Follow backend logs only
logs-backend:
	$(COMPOSE) logs -f usm-backend

## Follow frontend logs only
logs-frontend:
	$(COMPOSE) logs -f usm-frontend

## Show container status
status:
	$(COMPOSE) ps

## Restart all
restart:
	$(COMPOSE) restart

## Open shell in backend container
shell-backend:
	docker exec -it usm-backend bash

## Open shell in frontend container
shell-frontend:
	docker exec -it usm-frontend sh

## Tail backend app log
tail-log:
	tail -f logs/usm_backend.log

## Deploy to the dev server over SSH: pull latest, rebuild, restart, health-check
## Usage: make deploy   (override DEPLOY_USER/DEPLOY_HOST/DEPLOY_PATH/DEPLOY_BRANCH as needed)
deploy:
	@echo "==> Deploying $(DEPLOY_BRANCH) to $(DEPLOY_USER)@$(DEPLOY_HOST):$(DEPLOY_PATH)"
	$(SSH) 'set -e; \
		cd $(DEPLOY_PATH); \
		echo "--> git fetch + reset to origin/$(DEPLOY_BRANCH)"; \
		git fetch --all --prune; \
		git checkout $(DEPLOY_BRANCH); \
		git reset --hard origin/$(DEPLOY_BRANCH); \
		echo "--> rebuilding + restarting containers"; \
		$(COMPOSE) up -d --build; \
		echo "--> pruning old images"; \
		docker image prune -f; \
		$(COMPOSE) ps'
	@echo "==> Deploy complete. Running health check..."
	@$(MAKE) --no-print-directory deploy-check

## Verify the deployed backend is healthy
deploy-check:
	@echo "==> Checking backend /health on $(DEPLOY_HOST)"
	$(SSH) 'curl -fsS http://localhost:8000/api/v1/health || (echo "HEALTH CHECK FAILED" && exit 1)'
	@echo "==> Health OK"

