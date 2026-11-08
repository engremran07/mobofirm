# syntax=docker/dockerfile:1.7

# ---- Stage 1: build the Vite frontend (static/build) -----------------------
FROM node:22.12-bookworm-slim AS frontend
WORKDIR /build
COPY package.json package-lock.json vite.config.js ./
RUN npm ci
COPY frontend ./frontend
RUN npm run build

# ---- Stage 2: resolve and install Python dependencies from pyproject.toml --
FROM python:3.12-slim-bookworm AS python-deps
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
COPY pyproject.toml ./
# Install only the declared runtime dependencies (plus the "prod" extra); the
# application code itself is copied into the runtime image below.
RUN python - <<'PY' > requirements.txt
import tomllib

with open("pyproject.toml", "rb") as handle:
    project = tomllib.load(handle)["project"]
for requirement in project["dependencies"] + project["optional-dependencies"]["prod"]:
    print(requirement)
PY
RUN pip install -r requirements.txt

# ---- Stage 3: runtime -------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime
# Build metadata surfaced by app/version.py (/health/ready/, admin, footer); the
# release version itself always comes from pyproject.toml, never from build args.
ARG APP_GIT_SHA=""
ARG APP_BUILD_DATE=""
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=app.settings.production \
    PATH=/opt/venv/bin:$PATH \
    APP_GIT_SHA=$APP_GIT_SHA \
    APP_BUILD_DATE=$APP_BUILD_DATE
LABEL org.opencontainers.image.title="mobofirm" \
      org.opencontainers.image.revision=$APP_GIT_SHA \
      org.opencontainers.image.created=$APP_BUILD_DATE
RUN useradd --system --uid 10001 --create-home --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=python-deps /opt/venv /opt/venv
COPY --chown=app:app manage.py pyproject.toml ./
COPY --chown=app:app app ./app
COPY --chown=app:app apps ./apps
COPY --chown=app:app templates ./templates
COPY --chown=app:app static ./static
COPY --chown=app:app deploy ./deploy
COPY --from=frontend --chown=app:app /build/static/build ./static/build
# Windows checkouts may carry CRLF line endings, which break shell shebangs.
RUN sed -i 's/\r$//' deploy/entrypoint.sh \
    && chmod +x deploy/entrypoint.sh \
    && mkdir -p media storage staticfiles logs celerybeat \
    && chown -R app:app media storage staticfiles logs celerybeat
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD ["python", "deploy/healthcheck.py"]
ENTRYPOINT ["deploy/entrypoint.sh"]
CMD ["gunicorn", "-c", "deploy/gunicorn.conf.py", "app.wsgi:application"]
