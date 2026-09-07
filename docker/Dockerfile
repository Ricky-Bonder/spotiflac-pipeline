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

# Bake KNOWN-COMPATIBLE extensions (pinned by commit in the extension store).
# The registry serves builds for the newer mobile-app runtime; the PyPI
# module bridge lags behind, so out-of-the-box we pin versions whose
# requiredRuntimeFeatures the bridge actually implements. No registry is
# configured by default — the startup bootstrap would force-update the pin.
ARG EXT_REPO=https://raw.githubusercontent.com/zarzet/SpotiFLAC-Extension
ARG EXT_DEEZER_COMMIT=923d942
ARG EXT_TIDAL_COMMIT=2ed5d99
RUN apt-get update && apt-get install -y --no-install-recommends unzip \
    && mkdir -p /opt/default-extensions/deezer /opt/default-extensions/tidal-web \
    && curl -fsSL "$EXT_REPO/$EXT_DEEZER_COMMIT/extensions/deezer.sflx" -o /tmp/d.sflx \
    && curl -fsSL "$EXT_REPO/$EXT_TIDAL_COMMIT/extensions/tidal-web.sflx" -o /tmp/t.sflx \
    && unzip -o /tmp/d.sflx -d /opt/default-extensions/deezer \
    && unzip -o /tmp/t.sflx -d /opt/default-extensions/tidal-web \
    && rm /tmp/d.sflx /tmp/t.sflx \
    && apt-get purge -y unzip && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

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
