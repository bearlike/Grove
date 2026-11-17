"""Turns a person's Slack message into a workspace action.

The Slack face of issue-ops: the same grammar (``CommandParser``), the same
lifecycle seams (``create`` / ``send_message`` / ``pause`` / ``resume`` /
``kill``), and no lifecycle logic of its own. The pipeline, each drop terminal:

1. **dedupe** on ``(channel, ts)``: one message arrives as both an
   ``app_mention`` and a ``message`` event.
2. **aside**: a message marked ``!aside`` or ``(aside)`` is never forwarded.
3. **channel**: Grove works only in ``slack.allowed_channels``. Elsewhere an
   allowed user who addresses it is told the conversation's id; nobody else
   hears anything.
4. **not addressed**: a plain reply in a live, unmuted thread steers its
   workspace (or answers the agent's open question); anything else is ignored.
5. **permission**: only ``slack.allowed_users`` may drive Grove; anyone else
   gets a reply naming their member id.
6. **route**: ``mute`` / ``unmute``, a lifecycle verb, steering the thread's
   workspace, or creating one.
"""

from __future__ import annotations

import dataclasses
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from grove.core.contracts.branch_plan import AutoBranch, BranchPlan, RootBranch
from grove.core.contracts.questions import QuestionAnswerItem, QuestionAnswerRequest
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.errors import GroveError, WorkspaceNotFound
from grove.core.issueops.engine import RecentKeys
from grove.core.slack.context import SlackContextReader, SlackPrompt
from grove.core.slack.events import MentionCommand, SlackEvent, SlackText
from grove.core.slack.threads import SlackThread
from grove.core.slack.vocabulary import SlackKeywords, SlackReactions
from grove.core.workspace import LIVE_STATUSES, Runtime

if TYPE_CHECKING:
    from grove.core.config import SlackConfig, SlackWorkspaceConfig
    from grove.core.manager import WorkspaceManager
    from grove.core.registry import RepoRegistry
    from grove.core.slack.gateway import SlackGateway
    from grove.core.slack.threads import SlackThreadLog

_DEDUPE_CAPACITY = 2048
# Creates take minutes; this bounds how many run at once, never how many queue.
_WORKERS = 4


class SlackRouter:
    """Routes Slack messages to workspace verbs. ``handle`` is the whole policy."""

    def __init__(
        self,
        *,
        cfg: SlackConfig,
        registry: RepoRegistry,
        gateway: SlackGateway,
        threads: SlackThreadLog,
        reader: SlackContextReader,
    ) -> None:
        """Bind the router to Grove and Slack.

        Args:
            cfg: The daemon's ``slack`` section: who may use Grove, where, and
                on which project. Launch options and the prompt come from the
                chosen project's own ``slack`` section instead.
            registry: Finds a repository's manager.
            gateway: The Slack connection.
            threads: The thread-to-workspace map.
            reader: Reads a request's conversation and files.
        """
        self._cfg = cfg
        self._registry = registry
        self._gateway = gateway
        self._threads = threads
        self._reader = reader
        self._recent = RecentKeys(_DEDUPE_CAPACITY)
        self._pool = ThreadPoolExecutor(max_workers=_WORKERS, thread_name_prefix="grove-slack")
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def submit(self, event: SlackEvent) -> None:
        """Queue one event. Called on the socket's threads, so it returns at once."""
        if not self._recent.check_and_add((event.channel, event.ts)):
            self._pool.submit(self._guarded, event)

    def close(self) -> None:
        """Stop taking work. A create already running finishes on its own."""
        self._pool.shutdown(wait=False, cancel_futures=True)

    def handle(self, event: SlackEvent) -> None:
        """Run one event through the pipeline. Messages in one thread run one at a time,
        so a reply that lands while its workspace is being created waits for it."""
        if event.is_aside:
            return
        allowed = self._cfg.allows_user(event.user)
        if not self._cfg.allows_channel(event.channel):
            if event.addressed and allowed:
                self._say(
                    event,
                    "I'm not set up to work in this conversation. Add its ID "
                    f"`{event.channel}` to `slack.allowed_channels`.",
                )
            return
        with self._thread_lock(event):
            thread = self._threads.get(event.channel, event.root_ts)
            live = thread if thread is not None and not thread.ended else None
            if not event.addressed:
                # Chatter from someone not allowed is simply not for Grove: no reply.
                if live is not None and not live.muted and event.in_thread and allowed:
                    self._steer(live, event, event.text)
                return
            if not allowed:
                self._say(
                    event,
                    "You can't drive Grove from Slack yet. Ask whoever runs Grove to add "
                    f"your member ID `{event.user}` to `slack.allowed_users`.",
                )
                return
            self._route(live, event, MentionCommand.parse(event.text, bot_user_id=self._bot))

    # ─── routing ────────────────────────────────────────────────────────────

    def _route(self, live: SlackThread | None, event: SlackEvent, mention: MentionCommand) -> None:
        """Dispatch a parsed request addressed to Grove."""
        if mention.thread_verb:
            self._mute(live, event, muted=mention.thread_verb == SlackKeywords.MUTE)
            return
        command = mention.command
        if command is None or command.kind == "usage":
            reason = command.reason if command is not None else "say what you need"
            self._say(event, f"I didn't understand that: {reason}.")
            return
        if command.kind == "verb":
            self._verb(live, event, str(command.verb))
            return
        if live is not None and not mention.new:
            self._steer(live, event, command.text)
            return
        self._create(event, mention, command.text)

    def _mute(self, live: SlackThread | None, event: SlackEvent, *, muted: bool) -> None:
        """Stop (or resume) forwarding plain replies in this thread."""
        if live is None:
            self._say(event, "There's no session in this thread.")
            return
        self._threads.update(live.key, lambda t: dataclasses.replace(t, muted=muted))
        self._react(event.channel, event.ts, SlackReactions.DONE)

    def _verb(self, live: SlackThread | None, event: SlackEvent, verb: str) -> None:
        """``status``, ``pause``, ``resume`` or ``stop`` on this thread's workspace."""
        if live is None:
            self._say(event, "There's no session in this thread.")
            return
        manager = self._registry.get(Path(live.repo_root))
        try:
            if verb == "status":
                state = manager.reconciled(live.workspace_id)
                self._say(event, f"`{state.title}` is {state.status}.{self._link(state.id)}")
                return
            if verb == "pause":
                manager.pause(live.workspace_id)
            elif verb == "resume":
                manager.resume(live.workspace_id)
            else:
                manager.kill(live.workspace_id)
        except GroveError as exc:
            self._say(event, f"Could not {verb} the session: {exc}")
            return
        self._react(event.channel, event.ts, SlackReactions.DONE)

    def _steer(self, live: SlackThread, event: SlackEvent, text: str) -> None:
        """Send a thread reply to the workspace: as an answer if the agent asked, else
        as a follow-up message. Moves the spinner onto the reply."""
        manager = self._registry.get(Path(live.repo_root))
        try:
            state = manager.reconciled(live.workspace_id)
        except WorkspaceNotFound:
            self._threads.update(live.key, lambda t: dataclasses.replace(t, ended=True))
            self._say(event, "That session has ended. Mention me to start a new one.")
            return
        if state.status not in LIVE_STATUSES:
            self._say(event, f"The session is {state.status}. Mention me with `resume` first.")
            return
        body = self._steer_text(manager, live, event, text)
        try:
            if not (live.awaiting and self._answer(manager, live, body)):
                manager.send_message(live.workspace_id, body.text, attachments=body.attachments)
        except GroveError as exc:
            self._say(event, f"Could not reach the session: {exc}")
            return
        self._move_spinner(live, event)

    def _answer(self, manager: WorkspaceManager, live: SlackThread, body: _Steer) -> bool:
        """Answer the agent's open question batch with the reply's text.

        Returns:
            Whether it was answered. ``False`` when the question is no longer
            pending (answered in the terminal, say), so the reply goes as a
            plain message instead.
        """
        state = manager.get(live.workspace_id)
        request = QuestionAnswerRequest(
            session_id=state.agent_session_id or "",
            tool_use_id=live.asked,
            answers=[QuestionAnswerItem(text=body.text)] * live.awaiting,
        )
        try:
            manager.answer_question(live.workspace_id, request)
        except GroveError as exc:
            logger.info("slack: question no longer answerable, sending as a message: {}", exc)
            return False
        finally:
            self._threads.update(live.key, lambda t: dataclasses.replace(t, awaiting=0))
        return True

    def _create(self, event: SlackEvent, mention: MentionCommand, text: str) -> None:
        """Start a workspace with the conversation as its first prompt."""
        root = self._project(event, mention)
        if root is None:
            return
        self._react(event.channel, event.ts, SlackReactions.RECEIVED)
        manager = self._registry.get(root)
        # The project's own `slack` section, so its config layers can override
        # the launch options and the prompt. Access was decided above, from ours.
        project = manager.config.slack
        launch = project.workspace
        context = self._reader.read(event)
        command_text = SlackText.plain(text, name_of=self._gateway.user_name)
        request = CreateWorkspaceRequest(
            agent_name=mention.agent or launch.agent,
            model=launch.model,
            effort=launch.effort,
            runtime=Runtime(launch.runtime) if launch.runtime else None,
            remote_runtime=launch.remote_runtime,
            brief=launch.brief,
            native=launch.native,
            skip_init=launch.skip_init,
            title=(command_text.splitlines() or ["Slack request"])[0][:80].strip()
            or "Slack request",
            description=f"Started from Slack by {context.author} in #{context.channel}",
            branch_plan=self._branch_plan(launch),
            initial_prompt=SlackPrompt.render(
                project.prompt_template, context, command_text=command_text
            ),
            attachments=list(context.attachments),
        )
        try:
            state = manager.create(request)
        except GroveError as exc:
            self._react(event.channel, event.ts, SlackReactions.FAILED)
            self._say(event, f"Could not start a session: {exc}")
            return
        self._threads.put(
            SlackThread(
                channel=event.channel,
                root_ts=event.root_ts,
                workspace_id=state.id,
                repo_root=str(root),
                spinner_ts=event.ts,
            )
        )
        self._react(event.channel, event.ts, self._cfg.working_reaction)
        link = self._link(state.id)
        if link:
            self._say(event, f"Working on it in `{root.name}`.{link}")

    # ─── helpers over this router's state ───────────────────────────────────

    @property
    def _bot(self) -> str:
        return self._gateway.bot_user_id

    def _project(self, event: SlackEvent, mention: MentionCommand) -> Path | None:
        """The repository this request works in, or ``None`` after saying why not."""
        if mention.project:
            # Only projects the Slack config names: `!project` picks among them,
            # it never widens what Slack can reach.
            roots = self._cfg.projects
            for root in roots:
                if root.name.lower() == mention.project.lower():
                    return root
            names = ", ".join(sorted(r.name for r in roots)) or "none"
            self._say(event, f"I don't know a project called `{mention.project}`. Known: {names}.")
            return None
        raw = self._cfg.channel_projects.get(event.channel) or self._cfg.default_project
        if not raw:
            self._say(
                event,
                "No project is set for this channel. Add `!project <name>` to the message, "
                "or set `slack.channel_projects` or `slack.default_project`.",
            )
            return None
        return Path(raw).expanduser()

    @staticmethod
    def _branch_plan(launch: SlackWorkspaceConfig) -> BranchPlan:
        """The branch plan ``slack.workspace`` asks for: the checkout, or a new branch."""
        if launch.branch_mode == "root":
            return RootBranch()
        return AutoBranch(base_ref=launch.base_ref or "HEAD")

    def _steer_text(
        self, manager: WorkspaceManager, live: SlackThread, event: SlackEvent, text: str
    ) -> _Steer:
        """The reply as the agent should read it, with its files stored alongside."""
        author = self._gateway.user_name(event.user)
        plain = SlackText.plain(text, name_of=self._gateway.user_name).strip()
        fetched, linked = self._reader.files(event.files)
        ids: list[str] = []
        for name, data in fetched:
            try:
                ids.append(manager.add_attachment(live.workspace_id, name, data).id)
            except GroveError as exc:
                linked.append(f"{name} (could not be stored: {exc})")
        extra = "".join(f"\nToo large to copy: {line}" for line in linked)
        return _Steer(text=f"{author} replied in Slack:\n{plain}{extra}", attachments=tuple(ids))

    def _move_spinner(self, live: SlackThread, event: SlackEvent) -> None:
        """Put the working reaction on this reply, taking it off the previous one."""
        if live.spinner_ts and live.spinner_ts != event.ts:
            self._react(event.channel, live.spinner_ts, self._cfg.working_reaction, on=False)
        self._react(event.channel, event.ts, self._cfg.working_reaction)
        self._threads.update(live.key, lambda t: dataclasses.replace(t, spinner_ts=event.ts))

    def _link(self, workspace_id: str) -> str:
        """`` Open in Grove`` as a markdown link, or empty with no dashboard URL."""
        base = self._cfg.deep_link_base_url.rstrip("/")
        return f" [Open in Grove]({base}/w/{workspace_id})" if base else ""

    def _thread_lock(self, event: SlackEvent) -> threading.Lock:
        key = SlackThread.key_of(event.channel, event.root_ts)
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())

    def _guarded(self, event: SlackEvent) -> None:
        """Worker entry: a failure is logged, never lost in a future nobody reads."""
        try:
            self.handle(event)
        except Exception:
            logger.exception("slack: handling message {} in {} failed", event.ts, event.channel)

    # ─── best-effort Slack writes ───────────────────────────────────────────

    def _say(self, event: SlackEvent, text: str) -> None:
        try:
            self._gateway.post(event.channel, event.root_ts, text)
        except GroveError as exc:
            logger.warning("slack: reply in {} failed: {}", event.channel, exc)

    def _react(self, channel: str, ts: str, name: str, *, on: bool = True) -> None:
        try:
            self._gateway.react(channel, ts, name, on=on)
        except GroveError as exc:
            logger.debug("slack: reaction {} failed: {}", name, exc)


@dataclasses.dataclass(frozen=True, slots=True)
class _Steer:
    """A thread reply ready for the agent: its text and stored attachment ids."""

    text: str
    attachments: tuple[str, ...] = ()


__all__ = ["SlackRouter"]
