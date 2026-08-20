# Hosted demo image for Non-Pursuit.
#
# This builds the DEMO build, not the production utility: NON_PURSUIT_DEMO_MODE
# is on by default, which routes every write to a per-session temp database,
# disables the Playwright browser automation (there's no display in a
# container to launch Chrome into), and serves canned footprint matches
# instead of scanning real platforms from a shared server IP.
#
# To run the real local build, don't use this image -- run it on your own
# machine so data/tracker.db stays on hardware you control.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NON_PURSUIT_DEMO_MODE=true \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

WORKDIR /app

# curl is needed by the healthcheck below; nothing else is installed, which
# keeps the image close to the slim base.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first so a code change doesn't invalidate the pip layer.
# playwright is deliberately excluded from the image: the demo build never
# launches a browser, and pulling it in would add a large dependency that
# can only fail here.
COPY requirements.txt .
RUN grep -v '^playwright' requirements.txt > requirements-hosted.txt \
    && pip install --no-cache-dir -r requirements-hosted.txt

COPY . .

# Run as a non-root user. The app writes only to the system temp directory
# (per-session demo databases) and never to the source tree.
RUN useradd --create-home --shell /bin/bash appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8501

# Streamlit's own health endpoint -- returns ok once the server is actually
# serving, which is later than the process merely being up.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl --fail http://localhost:8501/_stcore/health || exit 1

CMD ["streamlit", "run", "app.py"]
