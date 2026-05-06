FROM python:3.11-slim

RUN pip install --no-cache-dir flask flask-restful pykeepass gunicorn

WORKDIR /app
COPY keepass_app.py .

EXPOSE 2000

# Use gunicorn with 4 workers for concurrency (the original Flask dev server was single-threaded)
CMD ["gunicorn", "--bind", "0.0.0.0:2000", "--workers", "4", "--timeout", "30", "--access-logfile", "-", "keepass_app:app"]
