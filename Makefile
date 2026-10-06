# One entry point for the monorepo. Needs Docker for `up`, Node 22 for the console, Python 3.12 for the services.
.PHONY: up down db-init test test-console test-gateway lint
up:            ; docker compose up --build
down:          ; docker compose down
db-init:       ; docker compose run --rm performance python -m scripts.setup_database && docker compose run --rm performance python -m scripts.run_pipeline
test: test-console test-gateway
test-console:  ; cd apps/console && npm ci && npm run typecheck && npm test
test-gateway:  ; cd services/gateway && python -m pytest tests -q
lint:          ; cd services/gateway && python -m ruff check .
