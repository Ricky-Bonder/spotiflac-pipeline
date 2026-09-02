# spotiflac-pipeline containerized runtime (experimental).
#
# Bundles everything the pipeline needs at runtime:
#   - Python 3.12 + the spotiflac package (>=3.8) in a venv
#   - Node 22 (spotiflac's JS extension runtime breaks on distro Node 18)
#   - Xvfb + Chromium (the deezer/tidal extensions solve a signed-session
#     challenge with a real browser)
#   - ffmpeg (verifier + taggers)
#
# The container runs the scheduler loop from docker/entrypoint.sh:
# watchdog every 15 min, playlist diff daily, full verify weekly — the same
# cadence the crontab example installs on bare metal.
#
# Build from the repo root:
#   docker build -f docker/Dockerfile -t spotiflac-pipeline .
# then see docker/docker-compose.example.yml for volumes + env.

FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    NODE_VERSION=22.23.2

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg curl ca-certificates xz-utils \
        xvfb chromium fonts-liberation \
        procps gosu \
    && rm -rf /var/lib/apt/lists/*

# Node 22 (official tarball — Debian bookworm ships 18, which breaks the
# extension sandbox: module loads get misclassified as writes and every
# extension dies with a startup timeout)
RUN curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" \
        | tar -xJ -C /opt \
    && ln -s "/opt/node-v${NODE_VERSION}-linux-x64/bin/node" /usr/local/bin/node

RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir \
        "spotiflac>=3.8,<4" "yt-dlp>=2024.0" "mutagen>=1.47"

COPY bin/   /app/bin/
COPY tools/ /app/tools/
COPY docker/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/bin/*.sh /app/bin/*.py /app/tools/*.py /app/entrypoint.sh

ENV SPOTIFLAC_VENV=/opt/venv \
    SPOTIFLAC_MUSIC_ROOT=/music \
    SPOTIFLAC_OUTPUT_DIR=/music/spotiflac \
    SPOTIFLAC_STATE_DIR=/state \
    SPOTIFLAC_CONFIG_FILE=/config/spotiflac.env \
    CHROME_PATH=/usr/bin/chromium

VOLUME ["/music", "/state", "/config"]

ENTRYPOINT ["/app/entrypoint.sh"]
