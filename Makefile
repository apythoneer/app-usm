# USM v3 — convenience commands
# Run from repo root

COMPOSE = docker compose -f docker/docker-compose.yml

.PHONY: up down build logs restart status shell-backend shell-frontend

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
