# syntax=docker/dockerfile:1.7
FROM node:22-alpine AS frontend
WORKDIR /src
COPY v2/web-vue/package*.json ./
RUN npm ci --no-audit --no-fund
COPY v2/web-vue/ ./
RUN SPF_VUE_OUT=/src/dist npm run build
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 SPF_DOCKER_MODE=1 SPF_HOST=0.0.0.0
WORKDIR /app
COPY requirements.txt requirements-v2.txt requirements-ai.txt ./
RUN pip install --no-cache-dir -r requirements-v2.txt
COPY photo_pipeline.py photo_exporter.py photo_review.py photo_workbench.py delivery_state.py export_reviewed_photos.py xlsx_photo_core.py launch_docker.py ./
COPY v2/*.py /app/v2/
COPY --from=frontend /src/dist /app/v2/web
RUN test -f /app/v2/web/index.html && find /app/v2/web/assets -name '*.js' | grep -q .
ARG SOURCE_COMMIT=unknown
ARG APP_VERSION=2.4.0-rc.4
RUN test "$(python -c 'from v2.version import VERSION; print(VERSION)')" = "$APP_VERSION"
ENV SPF_SOURCE_COMMIT=$SOURCE_COMMIT
LABEL org.opencontainers.image.source="https://github.com/HankNovic/StudentPhotoFlow" org.opencontainers.image.revision=$SOURCE_COMMIT org.opencontainers.image.version=$APP_VERSION
VOLUME ["/data"]
EXPOSE 8769
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8769/healthz',timeout=3)"
ENTRYPOINT ["python","launch_docker.py"]

