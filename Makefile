# Meridian — common commands (optional; requires GNU Make)

.PHONY: help install test backend-stop backend docker-up docker-down

help:
	@echo "Targets: install test backend-stop backend docker-up docker-down"
	@echo "  backend-stop  — free TCP 8000"
	@echo "  backend       — run uvicorn (foreground; loads .env via settings)"

install:
	pip install -r requirements.txt

test:
	pytest tests/ -m "not integration" -v

backend-stop:
	-fuser -k 8000/tcp 2>/dev/null || true

backend:
	uvicorn main:app --host 127.0.0.1 --port 8000 --reload

docker-up:
	docker compose up --build -d

docker-down:
	docker compose down
