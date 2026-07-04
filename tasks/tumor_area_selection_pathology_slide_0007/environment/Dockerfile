FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    curl \
    wget \
    util-linux \
    jq \
    openslide-tools \
    libopenslide0 \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    imagecodecs \
    numpy \
    pillow \
    requests \
    tifffile \
    openslide-python

ENV PYTHONDONTWRITEBYTECODE=1
WORKDIR /workspace

# Only the agent-visible workspace is baked into the image. The download URL
# lives in the bind-mounted bootstrap.sh (bootstrap service only), never here.
COPY environment/workspace/ /workspace/

RUN mkdir -p /logs/verifier /logs/artifacts /data/slide/current

# No ENTRYPOINT/CMD: Harbor keeps main alive; the bootstrap service runs
# bootstrap.sh via an explicit command in docker-compose.yaml.
