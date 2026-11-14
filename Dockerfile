FROM python:3.13-slim-bookworm@sha256:a5a7017efc5969e00ae2b91c8eb0631049a569652aa4ffc4501769049ae45c72

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_DB_PATH=/app/data/db.sqlite3

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home --uid 10001 hotel \
    && install -d -o hotel -g hotel /app/data

COPY . .
RUN DJANGO_DEBUG=1 python manage.py collectstatic --noinput
USER hotel
EXPOSE 8000
CMD ["sh", "-ec", "python manage.py migrate --noinput && exec python manage.py runserver 0.0.0.0:8000 --noreload"]
