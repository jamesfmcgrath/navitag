FROM python:3.12-slim

LABEL org.opencontainers.image.title="navitag" \
      org.opencontainers.image.description="Self-hosted web editor for music tags" \
      org.opencontainers.image.source="https://github.com/jamesfmcgrath/navitag" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 NAVITAG_MUSIC_ROOT=/music PORT=8000
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY navitag/ navitag/
# Source may arrive owner-only (600); make it readable by any runtime uid.
RUN chmod -R a+rX /app

# Default uid/gid 1000. Override with `user:` in compose to match the owner of your music files.
USER 1000:1000
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"
CMD ["python", "-m", "navitag.app"]
