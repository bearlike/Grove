"""The server-level instructions string every MCP client shows the model.

Kept in its own module rather than inline in ``server.py`` because it is not
wiring, it is the one piece of prose in this package aimed at a model instead of
a reader. Tool docstrings describe a single call; this describes the *posture* a
connected agent should take, and it is spent on every connection, so length is a
real cost and each line has to buy its place.

Two audiences read it, which is why it carries two blocks. An ORCHESTRATOR needs
to know what the tool surface is for and where to start with nothing in hand. An
agent working INSIDE a Grove workspace needs the task-phase contract, and this is
the highest-leverage place to hand it over — an MCP client surfaces server
instructions automatically, so the agent receives it without anyone remembering
to say it.

**The path is NAMED in the launch env, never derived from the cwd.** A
worktree hosts one agent only in the simple case — ROOT-placed workspaces share
the repo root, and ``grove agent add`` runs several agents in one container — so
Grove composes a per-agent path and publishes it as ``GROVE_PHASE_FILE``. ONE
line of this string buys the agent out of both ambiguities: it never resolves a
relative path against a cwd that may be a subdirectory, and it cannot collide
with a co-tenant. The literal path stays as the fallback, and only as that,
because it is what an agent Grove did not launch would still get right.

**The phase block leads with the FILE, and that ordering is load-bearing.** A
containerized agent — Grove's default runtime — can reach neither the daemon
(loopback-bound, no host-gateway route out of the container) nor the ``grove``
CLI (the package is not mounted in), so the two conveniences named at the end of
the block are exactly the ones that are missing where it matters most. Presenting
them first would teach the most common runtime to try the one channel that
cannot work. The file always works, because the worktree is bind-mounted and
writing a file is the one capability every coding agent has.

The vocabulary is restated here as literal prose rather than derived from
:data:`grove.core.phase.PHASE_ORDER`. Deriving it would produce a sentence
assembled at import time for a string that changes about as often as the enum
does, and the enum is a closed set of six by deliberate design.
"""

from __future__ import annotations

from typing import Final

SERVER_INSTRUCTIONS: Final[str] = """\
<grove>
Grove runs coding agents in isolated workspaces. One workspace is one git
worktree plus one tmux session, on this host or inside a container.

Start with grove_list_projects. It takes no arguments and it is where you get
the repo paths every other tool wants. Then grove_list_workspaces for the fleet,
grove_get_workspace for one, and grove_peek_workspace for a bounded live
snapshot. Lifecycle is grove_create_workspace, grove_pause_workspace,
grove_resume_workspace, grove_respawn_workspace and grove_kill_workspace. Steer
a running agent with grove_send_workspace_message. Destructive tools take their
inputs explicitly and never guess.
</grove>

<task_phase>
If you are working inside a Grove workspace, keep its task phase current. A
human watching twenty workspaces reads it to tell "still working out the
problem" from "opening the PR" without attaching to any of them. Nothing in your
transcript says that, so only you can report it.

WRITE THE FILE. It is the only channel that works everywhere.

    path     the absolute path in the GROVE_PHASE_FILE environment variable
             (unset? .grove/phase.json at the top of your worktree)
    content  {"phase": "implementing", "note": "wiring the parser"}

The note is optional, one line, under 200 characters. Write no timestamp. Grove
takes the time from the file itself.

REPORT AT A TRANSITION, never on a schedule. Write the file when what you are
doing changes, which is a handful of times across a whole task.

    scoping       reading the ticket and the code, working out what the job is
    planning      you understand the problem and are choosing an approach
    implementing  you are editing files
    verifying     running tests, linters or the build, reviewing your own diff
    delivering    committing, pushing, opening or updating the pull request
    done          handed off, nothing left for you to do

Moving backwards is a correct report, not a failure. If verifying shows the
design was wrong, say planning again.

The write is best-effort. If it fails, keep going with the real work and try
again at the next transition. Never let phase reporting block, retry-loop, or
derail the task you were given.

Where they exist, `grove phase <phase> --note "..."` and
grove_set_workspace_phase do the same thing more conveniently. Both are often
unreachable from inside a container, so the file is what you fall back to and
the file never needs anything to be reachable.
</task_phase>
"""

__all__ = ["SERVER_INSTRUCTIONS"]
