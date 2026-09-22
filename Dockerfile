# syntax=docker/dockerfile:1.7
#
# One image, two entrypoints: the default CMD runs the FastAPI service, and
# docker-compose's `ui` service overrides it to run the Streamlit dashboard
# against that API.
#
# The image carries no Chrome, so /api/optout/trigger reports its browser as
# unavailable here -- opt-out form filling is a local-machine operation. Nor
# does it carry data/tracker.db or logs/ (see .dockerignore): those hold real
# personal information and an image is a distributable artifact.

FROM python:3.11-slim AS builder

ENV VIRTUAL_ENV=/opt/venv
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

WORKDIR /build

RUN python -m venv "${VIRTUAL_ENV}"
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt


FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}" \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_ENABLECORS=false \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

WORKDIR /app

RUN groupadd --system --gid 10001 b0t \
    && useradd --system --uid 10001 --gid b0t --create-home --shell /usr/sbin/nologin b0t

COPY --from=builder /opt/venv /opt/venv
COPY --chown=b0t:b0t . .
RUN mkdir -p /app/logs \
    && chown b0t:b0t /app/logs

USER b0t

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
