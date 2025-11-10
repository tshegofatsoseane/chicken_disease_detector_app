# ==============================
# Dockerfile
# ==============================

FROM python:3.11-slim

# Install system dependencies for TLS & DNS resolution
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl gnupg dnsutils && \
    update-ca-certificates && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

ENV FLASK_ENV=production
ENV PYTHONUNBUFFERED=1

CMD ["python", "-u", "app.py"]
