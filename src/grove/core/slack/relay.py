"""Posts what a Slack-started workspace's agent says back into its thread.

Devin's shape, deliberately minimal: while the agent works, the message it is
working on carries a spinner reaction; when a turn ends, the spinner comes off
and the agent's reply is posted. Tool calls, progress and routine lifecycle
events never reach the thread. Questions the agent asks are replies too, so
they are posted; the only other posts are "the session ended" and "the session
hit an error".
"""

from __future__ import annotations

import contextlib
import dataclasses
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from grove.core.agents import AgentActivityState, AgentQuestion
from grove.core.errors import GroveError
from grove.core.slack.vocabulary import LIFECYCLE_LINES

if TYPE_CHECKING:
    from grove.core.activity import DashboardDelta, SessionActivity, WorkspaceActivity
    from grove.core.registry import RepoRegistry
    from grove.core.slack.gateway import SlackGateway
    from grove.core.slack.threads import SlackThread, SlackThreadLog

# States in which the agent's turn is over and its reply can be read.
_SETTLED = frozenset({AgentActivityState.WAITING, AgentActivityState.IDLE})


class SlackRelay:
    """Turns activity-bus deltas for Slack-started workspaces into thread posts.

    Subscribes to the same bus the notification broker rides and adds no status
    computation. The bus callback only filters; reading transcripts and calling
    Slack happen on one worker thread, which also keeps a thread's posts in order.
    Every post is deduplicated by the reply's own id, recorded in the thread log,
    so a daemon restart never posts a reply twice.
    """

    def __init__(
        self,
        *,
        registry: RepoRegistry,
        gateway: SlackGateway,
        threads: SlackThreadLog,
        working_reaction: str,
    ) -> None:
        """Bind the relay to Grove and Slack.

        Args:
            registry: Finds each workspace's manager by repository.
            gateway: The Slack connection.
            threads: The thread-to-workspace map.
            working_reaction: The spinner emoji removed when a reply posts.
        """
        self._registry = registry
        self._gateway = gateway
        self._threads = threads
        self._working = working_reaction
        self._pool: ThreadPoolExecutor | None = None
        self._unsubscribe: Callable[[], None] | None = None

    def bind(
        self, subscribe: Callable[[Callable[[DashboardDelta], None]], Callable[[], None]]
    ) -> None:
        """Start the worker and subscribe to the activity bus."""
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="grove-slack-relay")
        self._unsubscribe = subscribe(self._on_delta)

    def close(self) -> None:
        """Unsubscribe and let posts already queued finish."""
        if self._unsubscribe is not None:
            with contextlib.suppress(Exception):
                self._unsubscribe()
            self._unsubscribe = None
        if self._pool is not None:
            self._pool.shutdown(wait=True, cancel_futures=True)
            self._pool = None

    def relay(self, delta: DashboardDelta) -> None:
        """Post whatever this delta owes its thread, if anything.

        Never raises: a Slack or transcript failure is logged and the next delta
        tries again, because the dedupe key is only recorded once a post lands.
        """
        thread = self._threads.live().get(delta.workspace_id)
        if thread is None:
            return
        try:
            if delta.kind == "workspace_changed":
                self._lifecycle(thread, delta.detail)
            elif delta.workspace is not None:
                self._activity(thread, delta.workspace)
        except GroveError as exc:
            logger.warning("slack relay for workspace {} failed: {}", delta.workspace_id, exc)

    # ─── the bus edge ───────────────────────────────────────────────────────

    def _on_delta(self, delta: DashboardDelta) -> None:
        """Bus callback: hand deltas for Slack threads to the worker, drop the rest."""
        pool = self._pool
        if pool is not None and delta.workspace_id in self._threads.live():
            pool.submit(self.relay, delta)

    # ─── decisions ──────────────────────────────────────────────────────────

    def _lifecycle(self, thread: SlackThread, detail: dict[str, str]) -> None:
        """Tell the thread its session ended or broke, and stop the spinner."""
        event = detail.get("event", "")
        line = LIFECYCLE_LINES.get(event)
        if line is None:
            return
        if detail.get("error"):
            line = f"{line}: {detail['error']}"
        self._post(thread, line)
        self._stop_spinner(thread)
        self._threads.update(
            thread.key,
            lambda t: dataclasses.replace(t, spinner_ts="", ended=t.ended or event == "killed"),
        )

    def _activity(self, thread: SlackThread, row: WorkspaceActivity) -> None:
        """Post a new question batch, or the reply that ended the agent's turn."""
        session = self._primary(row)
        if session is None:
            return
        open_questions = tuple(q for q in session.activity.questions if not q.answered)
        if open_questions and open_questions[0].group_id != thread.asked:
            self._ask(thread, open_questions)
            return
        if session.activity.state is AgentActivityState.ERROR and session.activity.error_detail:
            self._reply_once(thread, f"error:{session.activity.error_detail}", self._error(session))
            return
        if session.activity.state not in _SETTLED or session.activity.active_subagents:
            return
        manager = self._registry.get(Path(thread.repo_root))
        result = manager.final_result(thread.workspace_id)
        if result is None or not result.is_complete or not result.text.strip():
            return
        self._reply_once(thread, result.message_id or str(result.timestamp), result.text.strip())

    def _ask(self, thread: SlackThread, questions: tuple[AgentQuestion, ...]) -> None:
        """Post one question batch and remember a reply should answer it."""
        self._post(thread, "\n\n".join(self._question(q) for q in questions))
        self._stop_spinner(thread)
        self._threads.update(
            thread.key,
            lambda t: dataclasses.replace(
                t, asked=questions[0].group_id, awaiting=len(questions), spinner_ts=""
            ),
        )

    def _reply_once(self, thread: SlackThread, reply_id: str, text: str) -> None:
        """Post ``text`` unless ``reply_id`` was already posted, then stop the spinner."""
        if reply_id == thread.last_reply:
            return
        self._post(thread, text)
        self._stop_spinner(thread)
        self._threads.update(
            thread.key, lambda t: dataclasses.replace(t, last_reply=reply_id, spinner_ts="")
        )

    # ─── rendering and side effects ─────────────────────────────────────────

    @staticmethod
    def _primary(row: WorkspaceActivity) -> SessionActivity | None:
        """The workspace's own agent session, never a sub-agent's."""
        sessions = [s for s in row.sessions if s.session.parent_session_id is None]
        for s in sessions:
            if s.session.session_id == row.state.agent_session_id:
                return s
        return sessions[0] if sessions else None

    @staticmethod
    def _question(question: AgentQuestion) -> str:
        """One question as markdown: the ask, then its options."""
        lines = [f"**{question.header or 'Question'}:** {question.prompt}"]
        lines.extend(
            f"- {o.label}" + (f": {o.description}" if o.description else "")
            for o in question.options
        )
        lines.append("_Reply in this thread to answer._")
        return "\n".join(lines)

    @staticmethod
    def _error(session: SessionActivity) -> str:
        """The error line for a session that broke."""
        return f"{LIFECYCLE_LINES['error']}: {session.activity.error_detail}"

    def _post(self, thread: SlackThread, text: str) -> None:
        """Post into the thread. Raises, so the dedupe key is kept only on success."""
        self._gateway.post(thread.channel, thread.root_ts, text)

    def _stop_spinner(self, thread: SlackThread) -> None:
        """Take the working reaction off. Best-effort: a stuck spinner is cosmetic."""
        if not thread.spinner_ts:
            return
        try:
            self._gateway.react(thread.channel, thread.spinner_ts, self._working, on=False)
        except GroveError as exc:
            logger.debug("slack: could not remove the working reaction: {}", exc)


__all__ = ["SlackRelay"]
