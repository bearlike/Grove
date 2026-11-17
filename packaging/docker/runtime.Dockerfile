# The Grove runtime image: what a REMOTE workspace runs. One long-running
# container holding `grove-native-worker` (paneless: a protocol-frame log, no
# tmux), git for the branch it pushes before every freeze or stop, and the
# agent CLI the worker drives. It dials the daemon; nothing dials in, so no
# port is exposed.
#
#   make runtime-image      # build the wheel, then this image (host arch)
#   make runtime-smoke      # build + prove it boots paneless
#
# The build context is a directory holding exactly one Grove wheel, never the
# checkout: the image installs what a user would install, and a context of one
# file keeps the build cache honest. `docker buildx build --platform
# linux/amd64,linux/arm64` builds both; Lambda MicroVMs run arm64 only.
FROM python:3.12-slim

# The agent CLI(s) the worker launches, installed with the npm Grove itself
# ships (nodejs-wheel), so the image needs no distro Node.
ARG AGENT_NPM_PACKAGES="@anthropic-ai/claude-code"

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ca-certificates git openssh-client \
    && rm -rf /var/lib/apt/lists/*

COPY *.whl /tmp/wheel/
RUN pip install --no-cache-dir /tmp/wheel/*.whl \
    && rm -rf /tmp/wheel \
    && npm install --global --prefix /usr/local ${AGENT_NPM_PACKAGES} \
    && npm cache clean --force

ENV LANG=C.UTF-8 LC_ALL=C.UTF-8

# Non-root, so the agent's config and the worktree are a user's, as on a host.
RUN useradd --create-home --shell /bin/bash grove
USER grove
WORKDIR /home/grove/work

ENTRYPOINT ["grove-native-worker"]
CMD ["--help"]
