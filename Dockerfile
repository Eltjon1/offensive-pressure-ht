FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["sh","-c","gunicorn -w 1 --threads 4 --timeout 600 -b 0.0.0.0:${PORT:-10000} mobile_app:app"]
