# Built from the repository root (see docker-compose.yml and render.yaml) so the demo repo can be baked in.
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The synthetic demo repository (with its git history), connectable as /demo/acme-shop.
COPY demo/create_demo_repo.py /demo/create_demo_repo.py
RUN python /demo/create_demo_repo.py /demo/acme-shop

COPY backend/app ./app

EXPOSE 8000

# Hosts like Render pass the port to listen on in $PORT.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
