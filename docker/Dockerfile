# Ringer worker container — Linux write-isolation for swarm runs.
# The macOS builds get Seatbelt via engines/opencode-sandboxed.sh; on Linux the
# container boundary is the sandbox: repo mounted read-only, writes confined to
# the mounted ~/.ringer volume, HOME is a tmpfs that dies with the container.
# Build:  docker build -t ringer-worker docker/
# Run:    docker/run.sh run <manifest.json> --identity <who>
FROM node:22-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 ca-certificates curl git \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g opencode-ai && opencode --version

# Auth is env-var only (OPENROUTER_API_KEY passed at run time). No credentials
# are baked into this image and none may be written to it.
ENTRYPOINT ["python3", "ringer.py"]
