# ==============================
# Dockerfile for Chicken Disease Detector App
# ==============================

FROM python:3.11-slim

# ---- FIX SSL/TLS ----
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl gnupg && \
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

# ---- Run app ----
CMD ["python", "-u", "app.py"]
