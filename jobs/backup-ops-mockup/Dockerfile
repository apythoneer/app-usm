# Backup Operations showcase — read-only web app (static page + /api/jobs over
# stormart). Python stdlib HTTP server + psycopg2; no web framework, kept small
# and separate from the SIP app. Mirrors cvlt-backup-summary's hardened image.
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py index.html ./

RUN useradd --uid 1000 --create-home appuser
USER 1000

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080
EXPOSE 8080

CMD ["python", "-u", "app.py"]
