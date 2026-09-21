#!/usr/bin/env python3
"""Run the Grove daemon in the lab and report cost PER SCENARIO.

The production measurement's defect was that one number covered startup, a
browser session, a webapp rebuild and idle at once - so its 50.7% average
described no particular thing. This runner separates the scenarios explicitly,
because "Grove costs X" is only meaningful once X names a workload.

Two corrections over the first pass, both of which had made a phase vacuous:

  * AUTH IS OFF. The first run sent unauthenticated requests and collected 60
    HTTPErrors in 0.124s, then reported 105% of a core - the cost of being
    refused, not of serving. A phase that never reached the handler cannot say
    anything about the handler.
  * COST IS PER OPERATION, NOT PER WINDOW. The first append phase charged its
    own `sleep(1)` pacing to Grove and reported 1367 ms/append, which is the
    wall clock of the pacing loop. Work is timed around the operation itself,
    and the idle pacing between operations is excluded.

Sampling records CODE LOCATIONS only - file, line and function - never locals,
arguments or transcript content. The corpus is the operator's real sessions, so
a profiler that captured values would be exporting their prompts.
"""

from __future__ import annotations

import collections
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

DIAGNOSTICS = Path("/diagnostics")
SECONDS = int(os.environ.get("GROVE_LAB_SECONDS", "600"))
MODE = os.environ.get("GROVE_LAB_MODE", "frozen")
DAEMON = "http://127.0.0.1:7421"


class Sampler(threading.Thread):
    """A stack sampler cheap enough not to be its own subject.

    At 20 Hz this costs well under a percent of one core, which matters because
    the thing being measured is also a small percentage - a sampler in the same
    order as its subject mostly reports itself.
    """

    def __init__(self) -> None:
        super().__init__(name="lab-sampler", daemon=True)
        self.counts: collections.Counter = collections.Counter()
        self.phase = "startup"
        self._stop = threading.Event()

    def run(self) -> None:
        mine = threading.get_ident()
        while not self._stop.wait(0.05):
            names = {t.ident: t.name for t in threading.enumerate()}
            for tid, frame in sys._current_frames().items():
                if tid == mine:
                    continue
                stack = []
                while frame is not None and len(stack) < 14:
                    code = frame.f_code
                    stack.append(f"{code.co_filename}:{frame.f_lineno}:{code.co_name}")
                    frame = frame.f_back
                self.counts[(self.phase, names.get(tid, str(tid)), tuple(stack))] += 1

    def stop(self) -> None:
        self._stop.set()

    def dump(self) -> None:
        rows = [
            {"phase": phase, "thread": thread, "samples": n, "stack": list(stack)}
            for (phase, thread, stack), n in self.counts.most_common(600)
        ]
        (DIAGNOSTICS / "stacks.json").write_text(json.dumps(rows, indent=2))


def cpu_seconds() -> float:
    """Process CPU across every thread, read the same way the host monitor read
    the production daemon, so the two numbers are directly comparable."""
    fields = Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()
    return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")


def rss_kib() -> int:
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1])
    return 0


class Phase:
    """One named window reported as its own cost.

    `charge` accumulates only the spans that are actual operations, so a
    scenario that paces itself reports the work rather than the pacing.
    """

    def __init__(self, name: str, sampler: Sampler, results: list) -> None:
        self.name, self.sampler, self.results = name, sampler, results
        self.charged_cpu = 0.0
        self.charged_wall = 0.0
        self.operations = 0
        self.extra: dict = {}

    def __enter__(self) -> "Phase":
        self.sampler.phase = self.name
        self.cpu, self.wall, self.rss = cpu_seconds(), time.monotonic(), rss_kib()
        return self

    def charge(self, count: int = 1):
        """Time one operation, excluding whatever idles around it."""
        phase = self

        class _Span:
            def __enter__(self):
                self.cpu, self.wall = cpu_seconds(), time.monotonic()
                return self

            def __exit__(self, *exc):
                phase.charged_cpu += cpu_seconds() - self.cpu
                phase.charged_wall += time.monotonic() - self.wall
                phase.operations += count

        return _Span()

    def __exit__(self, *exc) -> None:
        wall = time.monotonic() - self.wall
        cpu = cpu_seconds() - self.cpu
        row = {
            "phase": self.name,
            "wall_seconds": round(wall, 3),
            "cpu_seconds": round(cpu, 3),
            "one_core_percent": round(100 * cpu / wall, 3) if wall else None,
            "rss_start_kib": self.rss,
            "rss_end_kib": rss_kib(),
        }
        if self.operations:
            row["operations"] = self.operations
            # The honest per-operation number: CPU charged inside the spans
            # only, divided by the operations those spans covered.
            row["cpu_ms_per_operation"] = round(1000 * self.charged_cpu / self.operations, 3)
            row["wall_ms_per_operation"] = round(1000 * self.charged_wall / self.operations, 3)
        row.update(self.extra)
        self.results.append(row)
        print(json.dumps(row), flush=True)
        (DIAGNOSTICS / "phases.json").write_text(json.dumps(self.results, indent=2))


def get(path: str) -> tuple[int, int]:
    """Fetch one route, returning (status, bytes). A refusal is reported, never
    silently counted as a served request - that is what made the first pass
    vacuous."""
    try:
        with urllib.request.urlopen(f"{DAEMON}{path}", timeout=60) as response:
            return response.status, len(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, 0
    except Exception as exc:  # noqa: BLE001 - a probe reports, it does not raise
        print(f"-- {path}: {type(exc).__name__}", flush=True)
        return 0, 0


def manifest() -> dict:
    return json.loads((DIAGNOSTICS / "manifest.json").read_text())


def appendable_transcripts() -> list[Path]:
    """The lab's OWN transcript copies, largest first.

    Only ever the frozen copy: appending to the host's corpus would corrupt the
    operator's real sessions, which is why `live` mode measures no append phase
    rather than falling back to writing somewhere else.
    """
    roots = [Path(dest) for _, dest in manifest().get("frozen_corpus", [])]
    files = [p for root in roots for p in root.rglob("*.jsonl") if p.is_file()]
    return sorted(files, key=lambda p: p.stat().st_size, reverse=True)


def routes_for(workspaces: list[dict]) -> list[str]:
    """A request mix shaped like the dashboard's, not a synthetic one."""
    mix = ["/activity", "/workspaces", "/projects", "/sessions?limit=50", "/gallery"]
    for workspace in workspaces[:3]:
        wid = workspace.get("state", {}).get("id") or workspace.get("id")
        if wid:
            mix += [f"/workspaces/{wid}", f"/workspaces/{wid}/todo"]
    return mix


def main() -> None:
    sampler = Sampler()
    sampler.start()
    results: list[dict] = []

    from grove.core.config import GroveConfig
    from grove.core.store import JsonWorkspaceStore
    import grove.daemon.app as application
    import uvicorn

    raw = json.loads((Path.home() / ".config/grove/config.json").read_text())
    config = GroveConfig.model_validate(raw)
    application.load_config = lambda *a, **k: config

    with Phase("startup_build", sampler, results):
        app = application.build_app(cfg=config, store=JsonWorkspaceStore())
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=7421, log_level="warning")
        )
        threading.Thread(target=server.run, name="lab-daemon", daemon=True).start()
        for _ in range(900):
            if get("/healthz")[0] == 200:
                break
            time.sleep(0.1)

    # Bootstrap drains on background threads AFTER the port answers, which is
    # why the first pass reported a 0.78s startup and then a 60%-of-a-core
    # "idle": the readiness of the socket is not the readiness of the daemon.
    # Settle on observed CPU rather than a guessed sleep.
    with Phase("startup_settle", sampler, results) as phase:
        previous, quiet = cpu_seconds(), 0
        for _ in range(600):
            time.sleep(1.0)
            current = cpu_seconds()
            delta = current - previous
            previous = current
            quiet = quiet + 1 if delta < 0.02 else 0
            if quiet >= 5:
                break
        phase.extra["settled_after_seconds"] = round(time.monotonic() - phase.wall, 1)

    idle_seconds = max(60, SECONDS // 6)
    with Phase("idle", sampler, results):
        time.sleep(idle_seconds)

    status, _ = get("/activity")
    workspaces: list[dict] = []
    if status == 200:
        try:
            payload = json.loads(urllib.request.urlopen(f"{DAEMON}/workspaces", timeout=60).read())
            workspaces = payload if isinstance(payload, list) else []
        except Exception:  # noqa: BLE001
            workspaces = []
    print(f"-- auth check: /activity -> {status}, {len(workspaces)} workspaces", flush=True)

    mix = routes_for(workspaces)

    # Cold: every route's first read, when no memo is warm. This is what the
    # first dashboard load of the day actually pays.
    with Phase("serve_cold", sampler, results) as phase:
        codes: collections.Counter = collections.Counter()
        for route in mix:
            with phase.charge():
                code, size = get(route)
            codes[code] += 1
        phase.extra["status_codes"] = dict(codes)

    # Warm: the same mix repeated. The gap between cold and warm IS the memo's
    # value, and a warm cost that stays high names a memo that is not holding.
    with Phase("serve_warm", sampler, results) as phase:
        codes = collections.Counter()
        for _ in range(10):
            for route in mix:
                with phase.charge():
                    code, _size = get(route)
                codes[code] += 1
        phase.extra["status_codes"] = dict(codes)

    # Per-route attribution: which surface is expensive, rather than an average
    # over a mix that hides it.
    per_route: dict[str, dict] = {}
    for route in mix:
        samples = []
        for _ in range(5):
            start_cpu, start_wall = cpu_seconds(), time.monotonic()
            code, size = get(route)
            samples.append((cpu_seconds() - start_cpu, time.monotonic() - start_wall, code, size))
        per_route[route] = {
            "cpu_ms": round(1000 * sum(s[0] for s in samples) / len(samples), 3),
            "wall_ms": round(1000 * sum(s[1] for s in samples) / len(samples), 3),
            "status": samples[-1][2],
            "bytes": samples[-1][3],
        }
    (DIAGNOSTICS / "routes.json").write_text(json.dumps(per_route, indent=2))
    print(json.dumps(per_route, indent=2), flush=True)

    if MODE == "frozen":
        targets = appendable_transcripts()[:3]
        # An append is the steady-state edge: one agent writing one record.
        # Charged around the write-plus-observe span only, so the pacing that
        # separates appends is not attributed to Grove.
        with Phase("append", sampler, results) as phase:
            for index in range(40):
                with phase.charge(count=max(1, len(targets))):
                    for path in targets:
                        record = {
                            "type": "user",
                            "uuid": f"lab-{index}-{path.stem}",
                            "timestamp": time.strftime(
                                "%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()
                            ),
                            "message": {"role": "user", "content": "lab append"},
                        }
                        with path.open("a") as handle:
                            handle.write(json.dumps(record) + "\n")
                    get("/activity")
                time.sleep(0.5)
            phase.extra["transcripts"] = len(targets)

    with Phase("idle_after", sampler, results):
        time.sleep(idle_seconds)

    sampler.stop()
    sampler.dump()
    (DIAGNOSTICS / "phases.json").write_text(json.dumps(results, indent=2))
    print("lab complete", flush=True)


if __name__ == "__main__":
    main()
