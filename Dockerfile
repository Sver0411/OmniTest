FROM node:24-slim AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS package
WORKDIR /build
COPY pyproject.toml setup.py MANIFEST.in README.md LICENSE ./
COPY backend/ backend/
COPY --from=frontend /build/dist/ frontend/dist/
RUN pip install --no-cache-dir build==1.6.1 && OMNITEST_REQUIRE_UI=1 python -m build --wheel

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1 PLAYWRIGHT_BROWSERS_PATH=/opt/browsers
WORKDIR /app
COPY --from=package /build/dist/ /wheels/
COPY requirements-dev.lock /tmp/dependencies.lock
RUN python -c "from pathlib import Path; p=Path('/tmp/dependencies.lock'); Path('/tmp/constraints.txt').write_text('\n'.join(line for line in p.read_text().splitlines() if '==' in line))" \
    && pip install --no-cache-dir -c /tmp/constraints.txt '/wheels/omnitest-0.2.0-py3-none-any.whl[grpc]' \
    && python -m playwright install --with-deps chromium
COPY examples/ examples/
RUN useradd --create-home --uid 10001 omnitest && mkdir -p data && chown -R omnitest:omnitest /app /opt/browsers
USER omnitest
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/ready', timeout=4)"]
CMD ["omnitest", "serve", "--host", "0.0.0.0", "--port", "8000"]
