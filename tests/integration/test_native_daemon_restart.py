"""A native owner survives an abrupt daemon restart without replaying its task."""

from __future__ import annotations

import contextlib
import json
import os
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from grove.core.auth import SessionStore

pytestmark = [
    pytest.mark.integration,
    pytest.mark.requires_tmux,
    pytest.mark.skipif(
        not all(shutil.which(command) for command in ("git", "tmux")),
        reason="git and tmux must be installed",
    ),
]


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _eventually(
    path: Path, *, field: str, expected: object, timeout: float = 10
) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            data = json.loads(path.read_text())
            if data.get(field) == expected:
                return data
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {path}")


def _daemon(port: int, env: dict[str, str]) -> subprocess.Popen[str]:
    daemon = subprocess.Popen(
        [sys.executable, "-m", "grove.cli", "daemon", "serve", "--port", str(port)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            with httpx.Client() as client:
                if client.get(f"http://127.0.0.1:{port}/healthz", timeout=0.2).is_success:
                    return daemon
        except httpx.HTTPError:
            pass
        time.sleep(0.05)
    daemon.kill()
    daemon.wait(timeout=5)
    raise AssertionError("scratch daemon did not become healthy")


@pytest.fixture
def native_sandbox(tmp_path: Path, tmp_repo: Path) -> Iterator[tuple[dict[str, str], Path]]:
    state, config, home = (tmp_path / name for name in ("state", "config", "home"))
    state.mkdir()
    config.mkdir()
    home.mkdir()
    provider_log = tmp_path / "provider.json"
    fake = (
        "import json,os,sys\n"
        "log=os.environ['FAKE_PROVIDER_LOG']\n"
        "data={'pid':os.getpid(),'session':'6c44c19e-8f97-47fb-86e1-e82b6bc00cd7','initial':0,'followups':[]}\n"
        "pending=None\n"
        "def save():\n"
        " with open(log+'.tmp','w') as out: json.dump(data,out)\n"
        " os.replace(log+'.tmp',log)\n"
        "save()\n"
        "print(json.dumps({'type':'system','subtype':'init','session_id':data['session']}),flush=True)\n"
        "for raw in sys.stdin:\n"
        " frame=json.loads(raw)\n"
        " if frame.get('type') == 'control_request':\n"
        "  if frame['request']['subtype'] == 'interrupt':\n"
        "   data['interrupted']=True\n"
        "   save()\n"
        "   response={'subtype':'success','request_id':frame['request_id'],'response':{}}\n"
        "   print(json.dumps({'type':'control_response','response':response}),flush=True)\n"
        "   if pending: print(json.dumps(pending),flush=True)\n"
        "   pending=None\n"
        "  continue\n"
        " if frame.get('type') != 'user': continue\n"
        " text=frame['message']['content']\n"
        " if 'Initialize this Grove-owned native session' in text: continue\n"
        " if 'Grove mailbox-enabled agent' in text: data['initial']+=1\n"
        " else: data['followups'].append(text)\n"
        " save()\n"
        " reply={'type':'user','uuid':frame.get('uuid'),'message':frame['message']}\n"
        " if text == 'oversized':\n"
        "  large={'type':'assistant','message':{'content':[{'type':'text','text':'x'*70000}]}}\n"
        "  print(json.dumps(large),flush=True)\n"
        " if text == 'hold replay':\n"
        "  pending=reply\n"
        "  continue\n"
        " print(json.dumps(reply),flush=True)\n"
    )
    config_dir = config / "grove"
    config_dir.mkdir()
    config_dir.joinpath("config.json").write_text(
        json.dumps(
            {
                "auth": {"enabled": True},
                "hooks": {"enabled": False},
                "telemetry": {"enabled": False},
                "container": {"enabled": False},
                "tmux": {"session_prefix": f"native-restart-{tmp_path.name}-"},
                "projects": [str(tmp_repo)],
                "agents": [
                    {
                        "name": "claude",
                        "command": " ".join(
                            [
                                str(Path(sys.executable)),
                                "-u",
                                "-c",
                                shlex.quote(fake),
                            ]
                        ),
                        "kind": "claude_code",
                        "native": True,
                        "env": {
                            "FAKE_PROVIDER_LOG": str(provider_log),
                            "HOME": str(home),
                            "XDG_STATE_HOME": str(state),
                            "XDG_CONFIG_HOME": str(config),
                            "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
                        },
                    }
                ],
            }
        )
    )
    env = {
        "PATH": os.environ["PATH"],
        "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
        "HOME": str(home),
        "XDG_STATE_HOME": str(state),
        "XDG_CONFIG_HOME": str(config),
        "CLAUDE_CONFIG_DIR": str(home / "claude"),
    }
    yield env, provider_log
    sessions = subprocess.run(
        ["tmux", "list-sessions", "-F", "#{session_name}"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.splitlines()
    prefix = f"native-restart-{tmp_path.name}-"
    for name in sessions:
        if name.startswith(prefix):
            subprocess.run(["tmux", "kill-session", "-t", name], capture_output=True, check=False)


@pytest.mark.parametrize("first_message", ["oversized", "hold replay"])
def test_native_interrupt_reaches_provider_before_replay_timeout(
    native_sandbox: tuple[dict[str, str], Path], tmp_repo: Path, first_message: str
) -> None:
    env, provider_log = native_sandbox
    port = _port()
    env["GROVE_MAILBOX_URL"] = f"http://127.0.0.1:{port}"
    auth = SessionStore(path=Path(env["XDG_CONFIG_HOME"]) / "grove" / "auth.json")
    challenge = auth.pair_init(label="interrupt regression")
    auth.pair_approve(challenge.challenge_id)
    _, token = auth.pair_poll(challenge.challenge_id)
    daemon = _daemon(port, env)
    provider_pid: int | None = None
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        ) as client:
            response = client.post(
                "/workspaces",
                json={
                    "agent_name": "claude",
                    "title": "interrupt native",
                    "repo_root": str(tmp_repo),
                    "branch_plan": {"kind": "auto"},
                    "initial_prompt": "initial prompt",
                },
            )
            response.raise_for_status()
            workspace = response.json()
            before = _eventually(provider_log, field="initial", expected=1)
            assert isinstance(before["pid"], int)
            provider_pid = before["pid"]
            route = f"/workspaces/{workspace['id']}"
            assert client.post(route + "/message", json={"text": first_message}).status_code == 204
            _eventually(provider_log, field="followups", expected=[first_message])

            # This bound is below the provider's 15-second replay timeout:
            # interruption must not wait behind the input it needs to cancel.
            assert client.post(route + "/interrupt").status_code == 204
            _eventually(provider_log, field="interrupted", expected=True, timeout=5)
            assert (
                client.post(route + "/message", json={"text": "after interrupt"}).status_code == 204
            )
            after = _eventually(
                provider_log,
                field="followups",
                expected=[first_message, "after interrupt"],
                timeout=5,
            )
            assert after["pid"] == before["pid"]
            assert after["session"] == before["session"]
            assert after["initial"] == 1
    finally:
        daemon.kill()
        daemon.wait(timeout=5)
        if provider_pid is not None:
            with contextlib.suppress(ProcessLookupError):
                os.kill(provider_pid, signal.SIGTERM)


def test_native_full_input_queue_reserves_interrupt_and_preserves_fifo(
    native_sandbox: tuple[dict[str, str], Path], tmp_repo: Path
) -> None:
    env, provider_log = native_sandbox
    port = _port()
    env["GROVE_MAILBOX_URL"] = f"http://127.0.0.1:{port}"
    auth = SessionStore(path=Path(env["XDG_CONFIG_HOME"]) / "grove" / "auth.json")
    challenge = auth.pair_init(label="input credit regression")
    auth.pair_approve(challenge.challenge_id)
    _, token = auth.pair_poll(challenge.challenge_id)
    daemon = _daemon(port, env)
    provider_pid: int | None = None
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        ) as client:
            response = client.post(
                "/workspaces",
                json={
                    "agent_name": "claude",
                    "title": "bounded native inputs",
                    "repo_root": str(tmp_repo),
                    "branch_plan": {"kind": "auto"},
                    "initial_prompt": "initial prompt",
                },
            )
            response.raise_for_status()
            route = f"/workspaces/{response.json()['id']}"
            before = _eventually(provider_log, field="initial", expected=1)
            assert isinstance(before["pid"], int)
            provider_pid = before["pid"]
            admitted = ["hold replay", *(f"queued {index}" for index in range(15))]
            for text in admitted:
                assert client.post(route + "/message", json={"text": text}).status_code == 204
            _eventually(provider_log, field="followups", expected=["hold replay"])
            refused = client.post(route + "/message", json={"text": "not admitted"})
            assert refused.status_code == 409
            assert "backpressure" in refused.text

            assert client.post(route + "/interrupt").status_code == 204
            _eventually(provider_log, field="interrupted", expected=True, timeout=5)
            after = _eventually(provider_log, field="followups", expected=admitted, timeout=5)
            assert after["pid"] == before["pid"]
            assert after["initial"] == 1
    finally:
        daemon.kill()
        daemon.wait(timeout=5)
        if provider_pid is not None:
            with contextlib.suppress(ProcessLookupError):
                os.kill(provider_pid, signal.SIGTERM)


def test_native_worker_keeps_its_provider_across_a_killed_daemon(
    native_sandbox: tuple[dict[str, str], Path], tmp_repo: Path
) -> None:
    env, provider_log = native_sandbox
    port = _port()
    env["GROVE_MAILBOX_URL"] = f"http://127.0.0.1:{port}"
    auth = SessionStore(path=Path(env["XDG_CONFIG_HOME"]) / "grove" / "auth.json")
    challenge = auth.pair_init(label="restart regression")
    auth.pair_approve(challenge.challenge_id)
    _, token = auth.pair_poll(challenge.challenge_id)
    headers = {"Authorization": f"Bearer {token}"}
    first = _daemon(port, env)
    second: subprocess.Popen[str] | None = None
    provider_pid: int | None = None
    try:
        created = httpx.post(
            f"http://127.0.0.1:{port}/workspaces",
            headers=headers,
            json={
                "agent_name": "claude",
                "title": "durable native",
                "repo_root": str(tmp_repo),
                "branch_plan": {"kind": "auto"},
                "initial_prompt": "initial prompt",
            },
            timeout=10,
        ).json()
        assert created["native"] is True
        before = _eventually(provider_log, field="initial", expected=1)
        assert isinstance(before["pid"], int)
        provider_pid = before["pid"]

        first.kill()
        first.wait(timeout=5)
        second = _daemon(port, env)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            response = httpx.post(
                f"http://127.0.0.1:{port}/workspaces/{created['id']}/message",
                headers=headers,
                json={"text": "followup"},
                timeout=1,
            )
            if response.status_code == 204:
                break
            time.sleep(0.1)
        else:
            raise AssertionError("worker did not reconnect to fresh daemon")
        after = _eventually(provider_log, field="followups", expected=["followup"])
        assert after == {**before, "followups": ["followup"]}
    finally:
        for process in (first, second):
            if process is not None and process.poll() is None:
                process.send_signal(signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        if provider_pid is not None:
            with contextlib.suppress(ProcessLookupError):
                os.kill(provider_pid, signal.SIGTERM)
