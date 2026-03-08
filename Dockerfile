FROM python:3.12-slim

WORKDIR /app

# Install git (needed for workspace scanning)
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV OPENQUEEN_HOME=/app

CMD ["python3", "listen.py"]
