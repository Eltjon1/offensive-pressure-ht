FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install --with-deps chromium

COPY . .

ENV PORT=10000
EXPOSE 10000

CMD ["sh", "-c", "gunicorn -w 1 --threads 4 --timeout 600 -b 0.0.0.0:${PORT} mobile_app:app"]
