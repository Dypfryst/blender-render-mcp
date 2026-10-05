# Render-MCP for Blender på Unraid. Se SPEC.md.
FROM ubuntu:24.04

# Blender låses til nøyaktig versjon og kontrolleres mot sjekksummen fra download.blender.org.
ARG BLENDER_VERSION=5.2.2
ARG BLENDER_SHA256=84098912789dc450e95697c4184fb8a90acbe5111c2ba4aede3fecb57806a168

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=all \
    PORT=8765 \
    DATA_DIR=/data \
    CONFIG_DIR=/config \
    PUID=99 \
    PGID=100 \
    UMASK=000

# Systempakker: Python for serveren, ffmpeg for video, oiiotool for forhåndsvisning,
# og bibliotekene Blender trenger, inkludert EGL for EEVEE uten skjerm.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl xz-utils tzdata \
        python3 python3-venv \
        ffmpeg openimageio-tools \
        libegl1 libgl1 libglvnd0 libxi6 libxkbcommon0 libsm6 libice6 libxrender1 \
        libxxf86vm1 libxfixes3 libx11-6 libxext6 \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    fil="blender-${BLENDER_VERSION}-linux-x64.tar.xz"; \
    curl -fsSL -o "/tmp/${fil}" "https://download.blender.org/release/Blender${BLENDER_VERSION%.*}/${fil}"; \
    echo "${BLENDER_SHA256}  /tmp/${fil}" | sha256sum -c -; \
    mkdir -p /opt/blender; \
    tar -xJf "/tmp/${fil}" -C /opt/blender --strip-components=1; \
    rm "/tmp/${fil}"; \
    ln -s /opt/blender/blender /usr/local/bin/blender; \
    blender --factory-startup --version | grep -q "^Blender ${BLENDER_VERSION}"

# Serveren kjører på systemets Python i et eget miljø. Skriptene i Blender bruker Blenders egen Python.
COPY requirements.txt /tmp/requirements.txt
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

WORKDIR /app
COPY app/ /app/app/
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod 755 /entrypoint.sh

EXPOSE 8765
ENTRYPOINT ["/entrypoint.sh"]
