# USM v3 — convenience commands
# Run from repo root

# --env-file is REQUIRED and must come before -f.
#
# Compose derives its project directory from the compose file's location, so
# `docker compose -f docker/docker-compose.yml` looks for docker/.env and
# silently ignores the .env at the repo root. Every ${VAR:-default} in the
# compose file therefore fell back to its default: SECRET_KEY has been running
# as the literal "CHANGE_ME_BEFORE_PRODUCTION" placeholder, and KEEPASS_PASSWORD
# resolved to the old hardcoded fallback rather than the .env value.
#
# It surfaced only when the KEEPASS_PASSWORD fallback was removed and compose
# started refusing to interpolate. Passing --env-file explicitly points it back
# at the root .env.
#
# (Teams is unaffected: the webhook is persisted in the app_settings table and
# loaded at startup by load_persisted_settings(), not read from the environment.)
COMPOSE = docker compose --env-file .env -f docker/docker-compose.yml

# ── Deploy target (dev server over SSH) ───────────────────────────────────────
# Override on the command line, e.g.:
#   make deploy DEPLOY_BRANCH=feature/capacity-overhaul
#
# These defaults were previously placeholders (deploy@usm-dev:/opt/usm) that
# matched nothing, so `make deploy` failed at the ssh and the team fell back to
# bundle+scp deploys — which is how the host silently diverged from the repo.
# They now point at the real dev host.
DEPLOY_USER   ?= ad64490
DEPLOY_HOST   ?= usodclpsandadm1.corp.intranet
DEPLOY_PATH   ?= /home/ad64490/unified_storage_monitoring
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
		echo "--> preflight: .env must define KEEPASS_PASSWORD"; \
		grep -q "^KEEPASS_PASSWORD=" .env || { \
			echo "ABORT: .env has no KEEPASS_PASSWORD."; \
			echo "The hardcoded compose fallback was removed, so compose would refuse"; \
			echo "to start and take the stack down with it. Set it, then re-run."; \
			exit 1; }; \
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
	$(SSH) 'curl -fsS http://localhost:8000/health >/dev/null || (echo "HEALTH CHECK FAILED" && exit 1)'
	@echo "==> Health OK"

