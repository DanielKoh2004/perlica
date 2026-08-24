FROM python:3.13-slim

WORKDIR /app

# Set unbuffered Python output for real-time logging
ENV PYTHONUNBUFFERED=1
ENV DATABASE_PATH=/data/tracker.db

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY src/ ./src/
COPY run_local_test.py .

# Create volume mount point
RUN mkdir -p /data

CMD ["python", "-m", "src.bot"]
