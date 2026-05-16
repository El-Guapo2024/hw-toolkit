# hw-router-service — common ops
#
# Quick reference:
#   make up              start the stack (build if needed)
#   make down            stop and remove containers
#   make rebuild         force rebuild (no cache)
#   make logs            tail all service logs
#   make logs-api        tail only the api
#   make logs-worker     tail only the worker
#   make smoke           run the end-to-end smoke test
#   make health          curl /health
#   make ps              show container status
#   make shell           open a shell inside the api container
#   make clean           down + wipe redis + jobs volumes (destructive)

.PHONY: up down rebuild logs logs-api logs-worker smoke health ps shell clean

DEFAULT_URL ?= http://localhost:8002

up:
	docker compose up -d --build

down:
	docker compose down

rebuild:
	docker compose build --no-cache
	docker compose up -d --force-recreate

logs:
	docker compose logs -f --tail=50

logs-api:
	docker compose logs -f --tail=50 api

logs-worker:
	docker compose logs -f --tail=50 worker

smoke:
	python3 scripts/smoke_test.py --url $(DEFAULT_URL) --passes 3

health:
	curl -fsS $(DEFAULT_URL)/health | python3 -m json.tool

ps:
	docker compose ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}'

shell:
	docker compose exec api bash

# DESTRUCTIVE: removes the redis-data and jobs volumes too
clean:
	docker compose down -v
