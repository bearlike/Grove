# Grove performance lab image: Grove + Claude Code + ssm-cli, and nothing else.
#
# Deliberately NOT the devcontainer image (.devcontainer/Dockerfile). That one
# bakes docker-in-docker, Playwright's Chromium and a Node toolchain, because
# its job is to DEVELOP Grove. This image's job is to MEASURE Grove, and every
# extra daemon in the cgroup is a term in the CPU number we are trying to
# attribute. What is reused is the pattern, not the payload: the same
# COPY-from-ghcr uv, the same non-root user owning the tool venv, the same
# null keyring backend (a container has no secret service and keyring's search
# for one hangs a headless ssm-cli).
#
# The user is created with the HOST's uid/gid at build time so the canonical
# bind mounts (a read-only worktree, a read-write state dir) resolve to the
# same identity inside and out. A uid mismatch here does not fail loudly — it
# surfaces later as a transcript Grove can stat but not read, which reads as a
# Grove bug rather than a mount one.
FROM ubuntu:24.04

ARG HOST_UID=1000
ARG HOST_GID=1000
ARG HOST_USER=grove
ARG HOST_HOME=/home/grove

# git + tmux are Grove's documented runtime prerequisites (it shells out to
# both). curl/ca-certificates serve the install paths and the gateway probe.
# procps supplies the /proc readers the resource sampler uses.
RUN apt-get update \
	&& DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
		ca-certificates curl git jq tmux procps \
	&& rm -rf /var/lib/apt/lists/*

ENV LANG=C.UTF-8 LC_ALL=C.UTF-8

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# Ubuntu 24.04 ships an `ubuntu` user already holding uid 1000, which is the
# uid we almost always want. Renaming it is cheaper and less surprising than
# deleting it, and leaves exactly one account at that uid either way.
RUN if getent passwd "${HOST_UID}" >/dev/null; then \
		existing="$(getent passwd "${HOST_UID}" | cut -d: -f1)"; \
		usermod -l "${HOST_USER}" -d "${HOST_HOME}" -m "$existing"; \
		groupmod -n "${HOST_USER}" "$(getent group "${HOST_GID}" | cut -d: -f1)" || true; \
	else \
		groupadd -g "${HOST_GID}" "${HOST_USER}"; \
		useradd -m -u "${HOST_UID}" -g "${HOST_GID}" -d "${HOST_HOME}" -s /bin/bash "${HOST_USER}"; \
	fi

# ssm-cli installs as root but RUNS as the lab user, so all three UV_* paths
# are load-bearing exactly as they are in .devcontainer/Dockerfile: a default
# install lands under /root and the non-root user gets exit 126/127 from a
# shim it can see but not execute.
ENV PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring
RUN UV_PYTHON_INSTALL_DIR=/opt/uv-python \
	UV_TOOL_DIR=/opt/uv-tools \
	UV_TOOL_BIN_DIR=/usr/local/bin \
	uv tool install git+https://github.com/bearlike/Simple-Secrets-Manager.git \
	&& for d in /opt/uv-python /opt/uv-tools; do [ -d "$d" ] && chmod -R a+rX "$d"; done

# Node is here ONLY to run Claude Code's own installer. The webapp is not built
# in this image (see the header), so no version pin is needed beyond what the
# agent CLI itself requires.
RUN apt-get update \
	&& DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends nodejs npm \
	&& rm -rf /var/lib/apt/lists/*

ARG CLAUDE_CODE_VERSION=latest
RUN npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
	&& npm cache clean --force

USER ${HOST_USER}
WORKDIR ${HOST_HOME}
ENV PATH="${HOST_HOME}/.local/bin:${PATH}"

# Pre-fetch the interpreter Grove's own install will use, so a lab run does not
# pay an interpreter download inside the window it is trying to measure.
RUN uv python install 3.13

CMD ["bash"]
