FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash ca-certificates curl util-linux unzip \
    && rm -rf /var/lib/apt/lists/*

# Pre-install the standard ML stack so the agent can fit a count+GBM
# baseline (or a FM-embedding-based classifier) without runtime pip.
# ``redivis`` is needed by the bootstrap service to pull the EHRSHOT bundle
# on cache miss; the main service ignores it.
RUN pip install --no-cache-dir \
    numpy==2.1.3 \
    pandas==2.2.3 \
    scipy==1.14.1 \
    scikit-learn==1.5.2 \
    lightgbm==4.5.0 \
    xgboost==2.1.3 \
    pyarrow==18.1.0 \
    redivis==0.20.7

ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /workspace
# Nothing task-specific or source-revealing is baked into the shared image.
# The bootstrap entrypoint (bootstrap.sh) and the slicer (stage_data.py) are
# bind-mounted into the bootstrap service ONLY (see docker-compose.yaml); the
# scorer (evaluate.py) ships in tests/ (mounted only at verify time). So the
# agent's main container -- which shares this image -- cannot read any of them.
RUN mkdir -p /workspace/submission /workspace/data

# No ENTRYPOINT/CMD: Harbor's docker-compose-build.yaml overrides main's
# command to ``sleep infinity``. The bootstrap service uses an explicit
# ``command:`` in docker-compose.yaml to run /bootstrap.sh and exit cleanly.
