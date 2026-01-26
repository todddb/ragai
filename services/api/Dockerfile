FROM python:3.11-slim

WORKDIR /app

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

COPY requirements.txt /app/requirements.txt
RUN mkdir -p /ms-playwright \
 && pip install --no-cache-dir -r /app/requirements.txt \
 && python -m playwright install-deps chromium \
 && python -m playwright install chromium \
 && rm -rf /var/lib/apt/lists/*

COPY app /app/app

# Note: tools directory is mounted via docker-compose volume: ./tools:/app/tools:ro

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
