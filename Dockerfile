FROM node:24-slim AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1 PLAYWRIGHT_BROWSERS_PATH=/opt/browsers
WORKDIR /app
COPY pyproject.toml requirements-dev.lock ./
COPY backend/ backend/
COPY examples/ examples/
COPY alembic.ini ./
RUN pip install --no-cache-dir -r requirements-dev.lock && python -m playwright install --with-deps chromium
COPY --from=frontend /build/dist/ frontend/dist/
RUN useradd --create-home --uid 10001 omnitest && mkdir -p data && chown -R omnitest:omnitest /app /opt/browsers
USER omnitest
EXPOSE 8000
CMD ["omnitest", "serve", "--host", "0.0.0.0", "--port", "8000"]
