# OYI sandbox image
#
# The agent's shell runs here, so this image needs both halves of its toolkit:
#   - Python 3.12, because deepagents uses Python for its file tools
#   - Node 20 + Pele, because Mermaid diagrams are rendered with pele.run
#
# Pele requires Node >=20. The Debian package (Node 18) is too old, so install
# the official Node binary instead. The image is built once and tagged:
#
#   contree build sandbox_image --tag oyi-sandbox:latest
#
# Then OYI_SANDBOX_IMAGE=oyi-sandbox:latest uses it for the agent backend and
# for the phone-use planner.

FROM python:3.12-slim

ARG NODE_VERSION=20.20.0

ENV NODE_VERSION=${NODE_VERSION} \
    NPM_CONFIG_UPDATE_NOTIFIER=false \
    NPM_CONFIG_FUND=false \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    NODE_PATH=/opt/oyi/node_modules

# curl and ca-certificates fetch the Node tarball; git is generally useful in
# an agent sandbox. Keep the apt lists out of the final image.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl git \
 && curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.gz" \
      -o /tmp/node.tar.gz \
 && tar -xzf /tmp/node.tar.gz -C /usr/local --strip-components=1 \
 && rm /tmp/node.tar.gz \
 && rm -rf /var/lib/apt/lists/*

# Pele is a library. Install it at a fixed path so a sandbox run never has to
# reach npm, and add a real command-line wrapper for the agent to call.
RUN mkdir -p /opt/oyi \
 && cd /opt/oyi \
 && npm init -y >/dev/null \
 && npm install pele@1.10.1 --no-audit --no-fund

COPY render_mermaid.mjs /opt/oyi/render_mermaid.mjs
RUN printf '%s\n' \
      '#!/bin/sh' \
      'exec node /opt/oyi/render_mermaid.mjs "$@"' \
      > /usr/local/bin/render-mermaid \
 && chmod 0755 /usr/local/bin/render-mermaid

# Fail the build if the wrapper or Pele is broken. This is the check that the
# old "node pele.js render ..." invocation silently passed.
RUN printf 'flowchart LR\n  A[Mermaid] --> B[SVG]\n' | render-mermaid > /tmp/smoke.svg \
 && grep -q '<svg' /tmp/smoke.svg \
 && node -e "if (Number(process.versions.node.split('.')[0]) < 20) process.exit(1)" \
 && python3 -c 'import sys; assert sys.version_info >= (3, 11)' \
 && rm /tmp/smoke.svg

WORKDIR /workspace
