# SmartPOS - Railway production image
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /app

# Keep the runtime image small while allowing common Python wheels to install.
RUN apt-get update \
    && apt-get install -y --no-install-recommends bash ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./requirements.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

COPY . .

# collectstatic is a build-time operation. Use a build-only development secret;
# Railway runtime SECRET_KEY is supplied through the service environment.
RUN SECRET_KEY='build-only-secret-do-not-use-in-production-7f3a8c2d1e9b4a6c' \
    DEBUG=True \
    python manage.py collectstatic --noinput

RUN chmod +x /app/start.sh /app/worker.sh

EXPOSE 8000

CMD ["bash", "start.sh"]
