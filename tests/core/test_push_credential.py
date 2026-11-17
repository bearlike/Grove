"""A remote worker pushes its branch with the token it was handed, and only git sees it (#1068).

The push is the real consumer, so the decisive test runs a real ``git push``
over HTTP to a smart-HTTP server that answers 401 without Basic auth: it can
only pass if git actually asked the askpass for credentials and got the token.
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from grove.core.native_worker import GitPushCredential, NativeWorker, NativeWorkerConfig
from grove.core.runtimes import PUSH_TOKEN_ENV

_TOKEN = "push-token-1068"
_BACKEND = (
    Path(
        subprocess.run(
            ["git", "--exec-path"], capture_output=True, text=True, check=True
        ).stdout.strip()
    )
    / "git-http-backend"
)


def _git(*argv: str, cwd: Path) -> None:
    subprocess.run(["git", *argv], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def authed_remote(tmp_path: Path) -> Iterator[tuple[str, Path]]:
    """A bare repo served over smart HTTP that requires ``grove`` + the token to push."""
    if not _BACKEND.exists():
        pytest.skip("git-http-backend is not installed")
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    _git("config", "http.receivepack", "true", cwd=bare)
    expected = (
        "Basic " + base64.b64encode(f"{GitPushCredential.USERNAME}:{_TOKEN}".encode()).decode()
    )

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            pass

        def _serve(self) -> None:
            path, _, query = self.path.partition("?")
            pushing = "git-receive-pack" in self.path
            if pushing and self.headers.get("Authorization") != expected:
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="forge"')
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            env = {
                "GIT_PROJECT_ROOT": str(tmp_path),
                "GIT_HTTP_EXPORT_ALL": "1",
                "PATH_INFO": path,
                "QUERY_STRING": query,
                "REQUEST_METHOD": self.command,
                "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                "CONTENT_LENGTH": str(length),
                "REMOTE_USER": GitPushCredential.USERNAME if pushing else "",
            }
            out = subprocess.run(
                [str(_BACKEND)], input=body, env=env, capture_output=True, check=False
            ).stdout
            head, _, payload = out.partition(b"\r\n\r\n")
            status = 200
            headers = []
            for line in head.decode().splitlines():
                key, _, value = line.partition(":")
                if key.lower() == "status":
                    status = int(value.split()[0])
                else:
                    headers.append((key, value.strip()))
            self.send_response(status)
            for key, value in headers:
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = do_POST = _serve

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/remote.git", bare
    finally:
        server.shutdown()


@pytest.fixture
def clone(tmp_path: Path, authed_remote: tuple[str, Path]) -> Path:
    url, _ = authed_remote
    work = tmp_path / "work"
    work.mkdir()
    _git("init", "-q", "-b", "grove/x", cwd=work)
    _git(
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.test",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "c",
        cwd=work,
    )
    _git("remote", "add", "origin", url, cwd=work)
    return work


_PUSH = ["git", "push", "-q", "origin", "HEAD:refs/heads/grove/x"]


async def _push_through_the_read_channel(
    clone: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[int, str]:
    """Push exactly as the daemon's freeze does: a ``read`` frame served by the worker."""
    monkeypatch.chdir(clone)
    answered: list[dict[str, object]] = []

    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *exc: object) -> None:
            pass

        async def post(self, path: str, **kwargs: object) -> object:
            answered.append(kwargs["json"])  # type: ignore[arg-type]

            class Response:
                def raise_for_status(self) -> None:
                    pass

            return Response()

    worker = NativeWorker(
        NativeWorkerConfig(
            workspace_id="a" * 32,
            provider="claude_code",
            command=["claude"],
            repo_url="http://forge.example/acme/widgets.git",
            branch="grove/x",
        )
    )
    monkeypatch.setattr(worker, "_client", lambda **_: Client())
    await worker._serve_read("read_1", {"argv": _PUSH, "timeout": 30})
    [result] = answered
    return int(result["returncode"]), str(result["output"])  # type: ignore[call-overload]


@pytest.mark.asyncio
async def test_the_worker_pushes_with_the_token_it_was_handed(
    clone: Path, authed_remote: tuple[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(PUSH_TOKEN_ENV, _TOKEN)
    returncode, output = await _push_through_the_read_channel(clone, monkeypatch)
    assert returncode == 0, output
    _, bare = authed_remote
    pushed = subprocess.run(
        ["git", "rev-parse", "refs/heads/grove/x"],
        cwd=bare,
        capture_output=True,
        text=True,
        check=False,
    )
    assert pushed.returncode == 0
    assert _TOKEN not in output


@pytest.mark.asyncio
async def test_without_a_token_the_push_is_refused_and_says_so(
    clone: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The live failure, reproduced: the forge asks, nothing answers, git refuses."""
    monkeypatch.delenv(PUSH_TOKEN_ENV, raising=False)
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    returncode, output = await _push_through_the_read_channel(clone, monkeypatch)
    assert returncode != 0
    assert "Username" in output or "Authentication" in output or "401" in output


def test_only_git_gets_the_askpass(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PUSH_TOKEN_ENV, _TOKEN)
    assert GitPushCredential.env_for(["cat", "README.md"]) is None
    assert GitPushCredential.env_for([]) is None
    git = GitPushCredential.env_for(["/usr/bin/git", "push"])
    assert git is not None and "GIT_ASKPASS" in git
    askpass = Path(git["GIT_ASKPASS"])
    assert _TOKEN not in askpass.read_text()
    assert askpass.stat().st_mode & 0o777 == 0o700


def test_the_agent_never_inherits_the_push_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The token is the worker's, for its own git; the agent's env is built
    without it, so an agent cannot push anywhere with the workspace's token."""
    monkeypatch.setenv(PUSH_TOKEN_ENV, _TOKEN)
    monkeypatch.chdir(tmp_path)
    captured: dict[str, dict[str, str]] = {}

    class Owner:
        def __init__(
            self, command: object, cwd: object, *, env: dict[str, str], **_: object
        ) -> None:
            captured["env"] = env

    monkeypatch.setattr("grove.core.native_worker.ClaudeNativeOwner", Owner)
    worker = NativeWorker(
        NativeWorkerConfig(
            workspace_id="a" * 32,
            provider="claude_code",
            command=["claude"],
            repo_url="http://forge.example/acme/widgets.git",
            branch="grove/x",
        )
    )
    worker.transport()
    assert PUSH_TOKEN_ENV not in captured["env"]
    assert _TOKEN not in captured["env"].values()
    assert os.environ[PUSH_TOKEN_ENV] == _TOKEN
    shutil.rmtree(tmp_path / ".grove", ignore_errors=True)
