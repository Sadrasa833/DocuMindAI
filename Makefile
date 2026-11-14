.PHONY: help setup up down logs restart migrate migration test test-docker lint format typecheck worker flower

help:
	@echo "DocuMind AI development commands"
	@echo "  make setup         Copy .env.example to .env (if missing)"
	@echo "  make up            Build and start the Docker Compose stack"
	@echo "  make down          Stop the stack"
	@echo "  make logs          Follow service logs"
	@echo "  make migrate       Apply migrations inside the backend container"
	@echo "  make migration     Create an Alembic revision (m='message')"
	@echo "  make test          Run backend tests locally"
	@echo "  make test-docker   Run backend tests inside the backend container"
	@echo "  make lint          Run Ruff checks for the backend"
	@echo "  make format        Format backend code with Ruff"
	@echo "  make typecheck     Type-check the frontend"
	@echo "  make worker        Follow the Celery worker logs"
	@echo "  make flower        Start the monitoring profile (Flower on :5555)"

setup:
	@test -f .env || cp .env.example .env

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f --tail=150

restart:
	docker compose restart

migrate:
	docker compose exec backend alembic upgrade head

migration:
	docker compose exec backend alembic revision --autogenerate -m "$(m)"

test:
	cd backend && pytest

test-docker:
	docker compose exec backend pytest

lint:
	cd backend && ruff check app tests alembic

format:
	cd backend && ruff format app tests alembic && ruff check --fix app tests alembic

typecheck:
	cd frontend && npm run typecheck

worker:
	docker compose logs -f celery-worker

flower:
	docker compose --profile monitoring up -d flower
