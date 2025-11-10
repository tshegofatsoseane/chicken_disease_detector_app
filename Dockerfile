# ==============================
# Dockerfile for Chicken Disease Detector App
# ==============================

# Use official Python 3.11 slim image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy requirements first for caching
COPY requirements.txt .

# Upgrade pip and install dependencies
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy app files
COPY . .

# Expose port (matches Flask/SocketIO)
EXPOSE 8000

# Set environment variables (can override on Render)
ENV FLASK_ENV=production
ENV PYTHONUNBUFFERED=1

# Command to run the app with Eventlet
CMD ["python", "-u", "app.py"]
