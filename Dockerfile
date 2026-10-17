# One image for api / worker / ui / toolbox (different commands per service).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt requirements-dev.txt ./
RUN pip install -r requirements.txt -r requirements-dev.txt

# api, worker and ui run as this user (not root). The toolbox of docker-compose.dev.yml sets `user: root`: it writes into the bind-mounted repo.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app
COPY --chown=app:app . .
USER app
