# Standalone Commvault backup-summary -> Postgres -> Datadog job.
# Deliberately NOT built on the SIP backend image (no SQL Server / ODBC / FastAPI
# here) — this service stays separate from the app.
FROM python:3.11-slim

# No apt build deps needed: psycopg2-binary is a prebuilt wheel.
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY dataDogIncidentManagement.py run_job.py healthserver.py ./

# Run as non-root (matches the hardened prod securityContext). The secrets file is
# mounted read-only at /home/cvadmin/.var/secrets.json by both workloads.
RUN useradd --uid 1000 --create-home cvadmin
USER 1000

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

# Default to the health server; the CronJob overrides command to run run_job.py.
CMD ["python", "-u", "healthserver.py"]
