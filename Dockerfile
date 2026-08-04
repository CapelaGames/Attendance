FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

# Single worker (SQLite lives in one file) + threads for concurrent check-ins.
# WAL mode (set in app.py) keeps simultaneous writes safe.
# --limit-request-line: the PeopleSoft sync popup carries its roster in the URL,
# which overflows gunicorn's 4094-byte default on a large class.
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "8", \
     "--limit-request-line", "8190", "app:app"]
