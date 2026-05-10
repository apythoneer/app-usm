FROM python:3.11-slim

# System deps
RUN apt-get update && apt-get install -y \
    curl \
    gnupg \
    apt-transport-https \
    ca-certificates \
    unixodbc \
    unixodbc-dev \
    && rm -rf /var/lib/apt/lists/*

# Microsoft ODBC Driver 17 — Debian 12/bookworm method (matches v1)
RUN curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y msodbcsql17 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create dirs before copying code
RUN mkdir -p /app/logs /app/config

COPY . .

ENV PYTHONPATH=/app
ENV LOG_DIR=/app/logs
ENV ARRAYS_CONFIG_FILE=/app/config/arrays.txt

# Single uvicorn worker (APScheduler needs single process) but with increased
# connection backlog and timeout for handling concurrent API requests + collectors
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-keep-alive", "30", "--backlog", "100"]
