FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY bot.py .

# Create volume for SQLite database
VOLUME ["/app/data"]
ENV DB_PATH=/app/data/forwarder.db

# Run bot
CMD ["python", "bot.py"]
