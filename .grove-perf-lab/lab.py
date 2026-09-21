#!/usr/bin/env python3
"""Build, launch and measure the Grove performance lab container.

WHY A SEPARATE HARNESS RATHER THAN A devcontainer.json: the devcontainer is a
DEVELOPMENT environment, and its own README calls the privileged nested docker
daemon inherent to that role. A measurement needs the opposite property — the
cgroup should contain Grove and the agent and as little else as possible, or
the CPU number cannot be attributed to either. This harness therefore reuses
the devcontainer's *decisions* (uid identity, canonical paths, the SSM env
seam, the Claude config-dir mount) while dropping its payload.

Three isolation rules are deliberate and each is a limit on what the numbers
can be read to mean:

  * Repositories and transcripts mount READ-ONLY. The lab observes the host's
    real corpus; it must never write to it, and a measurement that needed to
    would be measuring something else.
  * Grove's config and state are the lab's OWN writable copies, so a lab
    daemon cannot act on production workspaces. Only workspace RECORDS are
    copied - never bearer tokens, hook credentials or share passcodes.
  * The network is a bridge, not the host's. The LiteLLM gateway is reachable
    at the docker gateway address because it binds 0.0.0.0, so host networking
    buys nothing here and would put the lab back on the host's loopback.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

LAB_DIR = Path(__file__).resolve().parent
IMAGE = "grove-perf-lab:local"
CONTAINER = "grove-perf-lab"
STATE_POINTER = Path("/tmp/grove-perf-lab-current")

# The SSM coordinates are READ FROM GROVE'S OWN CONFIG, never hard-coded here:
# `.grove/config.local.json` already declares `container.env_command` for the
# devcontainer, and `claude-code/default` is the project the host's telemetry
# env comes from. A literal project slug in this file would be a second place
# to update and a private name in a tracked tree.
CLAUDE_SSM_PROJECT = "claude-code"
CLAUDE_SSM_CONFIG = "default"


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, text=True, capture_output=True, **kw)


def host_identity() -> dict[str, str]:
    return {
        "uid": str(os.getuid()),
        "gid": str(os.getgid()),
        "user": Path.home().name,
        "home": str(Path.home()),
    }


def build(claude_version: str) -> None:
    ident = host_identity()
    cmd = [
        "docker", "build", "-t", IMAGE,
        "--build-arg", f"HOST_UID={ident['uid']}",
        "--build-arg", f"HOST_GID={ident['gid']}",
        "--build-arg", f"HOST_USER={ident['user']}",
        "--build-arg", f"HOST_HOME={ident['home']}",
        "--build-arg", f"CLAUDE_CODE_VERSION={claude_version}",
        str(LAB_DIR),
    ]
    print("==> docker build", flush=True)
    proc = subprocess.run(cmd)
    if proc.returncode:
        sys.exit(proc.returncode)


def gateway_address() -> str:
    """The docker bridge gateway, as seen from inside a container.

    Measured on this host: the LiteLLM gateway binds 0.0.0.0, so the bridge
    gateway address reaches it and `--network host` is unnecessary. Falling
    back to the documented `host.docker.internal` alias keeps this honest on a
    host whose bridge is named or numbered differently.
    """
    out = run(["docker", "network", "inspect", "bridge", "--format",
               "{{range .IPAM.Config}}{{.Gateway}}{{end}}"])
    gateway = out.stdout.strip()
    return gateway or "host.docker.internal"


def rewrite_loopback(url: str, gateway: str) -> str:
    for host in ("127.0.0.1", "localhost", "0.0.0.0", "[::1]"):
        if host in url:
            return url.replace(host, gateway)
    return url


def agent_environment(gateway: str) -> dict[str, str]:
    """Claude Code's provider env, with loopback rewritten for the bridge.

    The host points Claude Code at a LOOPBACK gateway address, which inside a
    container names the container itself - the failure is a connection refused
    that reads like a broken credential. Every ANTHROPIC_*/model variable is
    inherited as-is otherwise, because the model ids are the gateway's own
    vocabulary and inventing one here would measure a model nobody uses.
    """
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        if key.startswith("ANTHROPIC_") or key.startswith("CLAUDE_CODE_"):
            env[key] = rewrite_loopback(value, gateway) if "BASE_URL" in key else value
    # CLAUDE_CONFIG_DIR is supplied by the mount plan, not inherited: the host's
    # value names a host profile directory the container mounts elsewhere.
    env.pop("CLAUDE_CONFIG_DIR", None)
    return env


def ssm_environment(gateway: str, project: str, config: str) -> dict[str, str]:
    """Secrets fetched on the HOST and injected as env, never a mounted token.

    `ssm run` would need the CLI's credential file inside the container, which
    means mounting a token that unlocks every project this host can read. The
    values are fetched here instead and passed to `docker run` as env, so the
    container holds exactly the config it was scoped to and nothing that could
    fetch more. Loopback rewriting applies for the same reason as above.
    """
    out = run(["ssm-cli", "secrets", "download", "--format", "env",
               "--project", project, "--config", config])
    if out.returncode:
        print(f"-- ssm {project}/{config} unavailable: {out.stderr.strip()[:120]}")
        return {}
    env: dict[str, str] = {}
    for line in out.stdout.splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        env[key] = rewrite_loopback(value, gateway) if _looks_like_url(value) else value
    print(f"-- ssm {project}/{config}: {len(env)} keys")
    return env


def _looks_like_url(value: str) -> bool:
    return value.startswith("http://") or value.startswith("https://")


def prepare_state(mode: str) -> Path:
    """The lab's own writable Grove config/state, derived from the host's.

    Runs in-process against the installed Grove so the mount plan comes from
    the SAME resolver the daemon will use - a hand-written path list is how a
    profile directory gets missed and the lab silently observes less than the
    host does.
    """
    root = Path(tempfile.mkdtemp(prefix="grove-perf-lab-"))
    root.chmod(0o700)
    script = LAB_DIR / "prepare_state.py"
    # The INSTALLED Grove's interpreter, not whatever python3 runs this harness:
    # the mount plan must come from the same resolver the daemon uses, and a
    # bare python3 has no grove package at all.
    interpreter = Path.home() / ".local/share/uv/tools/grove/bin/python"
    proc = subprocess.run(
        [str(interpreter) if interpreter.exists() else sys.executable,
         str(script), str(root), mode],
        text=True,
    )
    if proc.returncode:
        sys.exit(proc.returncode)
    STATE_POINTER.write_text(str(root))
    return root


def launch(root: Path, *, seconds: int, with_agent: bool, mode: str) -> None:
    manifest = json.loads((root / "manifest.json").read_text())
    gateway = gateway_address()
    ident = host_identity()

    shutil.copyfile(LAB_DIR / "runner.py", root / "runner.py")

    run(["docker", "rm", "-f", CONTAINER])

    cmd = [
        "docker", "run", "--detach", "--name", CONTAINER,
        "--label", "grove.diagnostic=performance-lab",
        "--user", f"{ident['uid']}:{ident['gid']}",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--pids-limit", "512",
        # A bound the reader can see: --memory alone still grants twice that in
        # swap, exactly as .devcontainer/devcontainer.json documents.
        "--memory", "6g", "--memory-swap", "6g",
        "--cpus", "2",
        "--add-host", f"host.docker.internal:{gateway}",
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=256m,mode=1777",
        "--workdir", manifest["repo"],
        "--env", f"HOME={ident['home']}",
        "--env", "PYTHONUNBUFFERED=1",
        "--env", "PYTHONDONTWRITEBYTECODE=1",
        "--env", f"GROVE_LAB_SECONDS={seconds}",
        "--env", f"GROVE_LAB_MODE={mode}",
        "--env", f"GROVE_LAB_AGENT={'1' if with_agent else '0'}",
        "--env", "CLAUDE_CONFIG_DIR=/grove/agent-config/claude_code",
    ]

    for key, value in {**agent_environment(gateway),
                       **ssm_environment(gateway, CLAUDE_SSM_PROJECT, CLAUDE_SSM_CONFIG)}.items():
        cmd += ["--env", f"{key}={value}"]

    for path in manifest["read_only_mounts"]:
        cmd += ["--mount", f"type=bind,src={path},dst={path},readonly"]

    # The agent's own profile is mounted read-only at a NEUTRAL path, the same
    # split .devcontainer/.grove-override.json uses: Grove reads the host's
    # transcripts through the read-only corpus mounts above, while the agent
    # writes its new session into the lab's own scratch profile. Pointing the
    # agent at the host profile would have the experiment write into the corpus
    # it is measuring.
    for source, dest in [
        (root / "config", f"{ident['home']}/.config/grove"),
        (root / "state", f"{ident['home']}/.local/state/grove"),
        (root / "agent-config", "/grove/agent-config/claude_code"),
        (root / "output", "/diagnostics"),
    ]:
        cmd += ["--mount", f"type=bind,src={source},dst={dest}"]

    cmd += ["--mount", f"type=bind,src={root}/runner.py,dst=/runner.py,readonly"]
    # `uv tool install` builds its OWN venv, so the runner must execute under
    # that venv's interpreter - a bare python3 has grove's console scripts on
    # PATH and no grove package importable, which fails at the first import
    # rather than at install time where it would be obvious.
    cmd += [IMAGE, "bash", "-lc",
            "set -e; uv tool install --force --editable '.[all]' >/tmp/install.log 2>&1 "
            "|| { tail -20 /tmp/install.log; exit 1; }; "
            'exec "$(uv tool dir)/grove/bin/python" /runner.py']

    out = run(cmd, timeout=180)
    if out.returncode:
        print(out.stdout, out.stderr)
        sys.exit(out.returncode)
    print(f"==> {CONTAINER} started ({mode}, {seconds}s, gateway {gateway})")
    print(f"==> artifacts: {root}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=600)
    parser.add_argument("--mode", choices=("live", "frozen"), default="frozen",
                        help="frozen copies the corpus so appends are ours alone; "
                             "live observes the host's real, moving transcripts")
    parser.add_argument("--with-agent", action="store_true",
                        help="run a real Claude Code session against the gateway")
    parser.add_argument("--claude-version", default="latest")
    parser.add_argument("--skip-build", action="store_true")
    args = parser.parse_args()

    if not args.skip_build:
        build(args.claude_version)
    root = prepare_state(args.mode)
    launch(root, seconds=args.seconds, with_agent=args.with_agent, mode=args.mode)


if __name__ == "__main__":
    main()
