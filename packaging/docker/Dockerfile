# Clean-install smoke image: a fresh Ubuntu with Grove's runtime prerequisites
# and uv, but NO Grove. Grove is installed at test time by smoke.sh (uv tool
# install from the mounted checkout), so a run proves the zero-touch install +
# first-run bootstrap path on a machine that has never seen Grove.
#
#   make install-smoke     # build this image + run smoke.sh against the checkout
#
# The image is deliberately minimal: git + tmux are Grove's documented
# prerequisites, jq/curl serve the smoke assertions, uv brings its own CPython.
FROM ubuntu:24.04

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ca-certificates curl git jq tmux \
    && rm -rf /var/lib/apt/lists/*

ENV LANG=C.UTF-8 LC_ALL=C.UTF-8

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# Grove is a user-scope tool; run as the stock non-root `ubuntu` user so the
# XDG paths under test are the real ~/.config + ~/.local/state, not /root.
USER ubuntu
WORKDIR /home/ubuntu
ENV PATH="/home/ubuntu/.local/bin:${PATH}"

# Pre-fetch a managed CPython so the smoke run itself stays fast.
RUN uv python install 3.12

CMD ["bash"]
