.PHONY: setup test check build up down logs scale monitoring migrate
PYTHON ?= python3
REPLICAS ?= 2
setup:
	$(PYTHON) scripts/setup_env.py
test:
	$(PYTHON) -m pytest -q
check:
	$(PYTHON) -m compileall -q app alembic scripts tests
	$(PYTHON) scripts/validate_deploy.py
	docker compose config --quiet
build:
	docker compose build
up:
	docker compose up -d --build --scale api=$(REPLICAS)
scale:
	docker compose up -d --scale api=$(REPLICAS)
migrate:
	docker compose run --rm migrate
monitoring:
	@test -s .secrets/metrics/token || (printf '%s\n' 'Run scripts/refresh_metrics_token.py first'; exit 1)
	docker compose --profile monitoring up -d prometheus
logs:
	docker compose logs -f --tail=100
down:
	docker compose --profile monitoring down
