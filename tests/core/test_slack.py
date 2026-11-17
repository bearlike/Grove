"""Slack integration: payload parsing, the router's pipeline, and the reply relay.

Slack itself is the only thing faked (``FakeGateway``), plus the manager verbs the
router and relay call, recorded so each test can say exactly what reached the
workspace and what reached the thread. Activity rows are the real engine IR.
"""

from __future__ import annotations

import base64
import dataclasses
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from grove.core.activity import DashboardDelta, SessionActivity, WorkspaceActivity
from grove.core.agents import (
    AgentActivity,
    AgentActivityState,
    AgentQuestion,
    AgentQuestionOption,
    AgentSession,
    FinalResult,
)
from grove.core.config import GroveConfig, SlackConfig
from grove.core.contracts.attachments import AttachmentView
from grove.core.contracts.questions import QuestionAnswerRequest
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.errors import GroveError, WorkspaceNotFound
from grove.core.slack import (
    MentionCommand,
    SlackBridge,
    SlackError,
    SlackEvent,
    SlackFile,
    SlackGateway,
    SlackMessage,
    SlackThread,
    SlackThreadLog,
)
from grove.core.slack.context import SlackContext, SlackContextReader, SlackPrompt
from grove.core.slack.events import SlackText
from grove.core.slack.relay import SlackRelay
from grove.core.slack.router import SlackRouter
from grove.core.slack.vocabulary import PROMPT_LIMIT
from grove.core.workspace import WorkspaceState, WorkspaceStatus

T0 = datetime(2026, 11, 12, 12, 0, 0, tzinfo=UTC)
BOT = "UGROVE"
ALICE = "UALICE"
MALLORY = "UMALLORY"
CHANNEL = "CDEV"
REPO = Path("/home/u/proj")


# ─── fakes: Slack, and the manager verbs the integration calls ─────────────────


class FakeGateway(SlackGateway):
    """Slack, recorded. Reads answer from canned history and threads."""

    def __init__(self) -> None:
        self._bot_user_id = BOT
        self.posts: list[tuple[str, str, str]] = []
        self.reactions: list[tuple[str, str, str, bool]] = []
        self.threads: dict[str, list[SlackMessage]] = {}
        self.channel_history: list[SlackMessage] = []
        self.files: dict[str, bytes] = {}

    def post(self, channel: str, thread_ts: str, text: str) -> None:
        self.posts.append((channel, thread_ts, text))

    def react(self, channel: str, ts: str, name: str, *, on: bool = True) -> None:
        self.reactions.append((channel, ts, name, on))

    def thread(self, channel: str, root_ts: str) -> list[SlackMessage]:
        return self.threads.get(root_ts, [])

    def history(self, channel: str, *, before_ts: str, limit: int) -> list[SlackMessage]:
        return self.channel_history[-limit:] if limit else []

    def user_name(self, user_id: str) -> str:
        return {ALICE: "alice", BOT: "grove"}.get(user_id, user_id)

    def channel_name(self, channel: str) -> str:
        return "dev"

    def permalink(self, channel: str, ts: str) -> str:
        return f"https://acme.slack.com/archives/{channel}/p{ts}"

    def download(self, file: SlackFile, *, max_bytes: int) -> bytes | None:
        data = self.files.get(file.name)
        return data if data is not None and len(data) <= max_bytes else None

    def texts(self) -> list[str]:
        return [text for _, _, text in self.posts]


def _state(status: WorkspaceStatus = WorkspaceStatus.ACTIVE) -> WorkspaceState:
    return WorkspaceState(
        id="ws1",
        title="fix login",
        repo_root=str(REPO),
        branch="grove/fix-login",
        base_branch="main",
        worktree_path=str(REPO / ".worktrees/fix-login"),
        tmux_session="grove-fix-login",
        agent_name="claude",
        status=status,
        created_at=T0,
        updated_at=T0,
        agent_session_id="s1",
    )


@dataclass
class FakeManager:
    """The workspace verbs, recorded."""

    status: WorkspaceStatus = WorkspaceStatus.ACTIVE
    created: list[CreateWorkspaceRequest] = field(default_factory=list)
    sent: list[tuple[str, str, tuple[str, ...]]] = field(default_factory=list)
    answered: list[QuestionAnswerRequest] = field(default_factory=list)
    verbs: list[str] = field(default_factory=list)
    result: FinalResult | None = None
    gone: bool = False
    create_error: str = ""
    answer_error: str = ""
    # The project's merged config, as `WorkspaceManager.config` serves it.
    config: GroveConfig = field(default_factory=GroveConfig)

    def create(self, request: CreateWorkspaceRequest) -> WorkspaceState:
        if self.create_error:
            raise GroveError(self.create_error)
        self.created.append(request)
        return _state()

    def reconciled(self, workspace_id: str) -> WorkspaceState:
        if self.gone:
            raise WorkspaceNotFound(workspace_id)
        return _state(self.status)

    def get(self, workspace_id: str) -> WorkspaceState:
        return self.reconciled(workspace_id)

    def send_message(
        self, workspace_id: str, text: str, *, attachments: tuple[str, ...] = ()
    ) -> None:
        self.sent.append((workspace_id, text, tuple(attachments)))

    def add_attachment(self, workspace_id: str, name: str, data: bytes) -> AttachmentView:
        return AttachmentView(id=f"att-{name}", name=name, path=f"/a/{name}", bytes=len(data))

    def answer_question(self, workspace_id: str, request: QuestionAnswerRequest) -> None:
        if self.answer_error:
            raise GroveError(self.answer_error)
        self.answered.append(request)

    def pause(self, workspace_id: str) -> None:
        self.verbs.append("pause")

    def resume(self, workspace_id: str) -> None:
        self.verbs.append("resume")

    def kill(self, workspace_id: str) -> None:
        self.verbs.append("kill")

    def final_result(self, workspace_id: str) -> FinalResult | None:
        return self.result


@dataclass
class FakeRegistry:
    manager: FakeManager

    def get(self, repo_root: Path) -> FakeManager:
        return self.manager


@dataclass
class Rig:
    gateway: FakeGateway
    manager: FakeManager
    threads: SlackThreadLog
    router: SlackRouter
    relay: SlackRelay


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    gateway = FakeGateway()
    manager = FakeManager()
    registry: Any = FakeRegistry(manager)
    threads = SlackThreadLog(tmp_path / "slack-threads.json")
    cfg = SlackConfig(
        enabled=True,
        allowed_users=[ALICE],
        allowed_channels=[CHANNEL],
        default_project=str(REPO),
        channel_projects={"COTHER": "/home/u/other"},
        context_messages=5,
        deep_link_base_url="http://grove.example",
        working_reaction="loading",
    )
    reader = SlackContextReader(gateway, history=5, max_attachment_bytes=1000)
    router = SlackRouter(
        cfg=cfg, registry=registry, gateway=gateway, threads=threads, reader=reader
    )
    relay = SlackRelay(
        registry=registry, gateway=gateway, threads=threads, working_reaction="loading"
    )
    return Rig(gateway, manager, threads, router, relay)


def _event(
    text: str,
    *,
    ts: str = "100.1",
    thread_ts: str | None = None,
    user: str = ALICE,
    addressed: bool | None = None,
    files: tuple[SlackFile, ...] = (),
    channel: str = CHANNEL,
) -> SlackEvent:
    return SlackEvent(
        channel=channel,
        ts=ts,
        thread_ts=thread_ts,
        user=user,
        text=text,
        files=files,
        addressed=f"<@{BOT}>" in text if addressed is None else addressed,
    )


def _bind(rig: Rig, **fields: Any) -> SlackThread:
    thread = SlackThread(
        channel=CHANNEL, root_ts="100.1", workspace_id="ws1", repo_root=str(REPO), **fields
    )
    rig.threads.put(thread)
    return thread


# ─── parsing ─────────────────────────────────────────────────────────────────


def test_mention_anywhere_is_stripped_and_options_are_read() -> None:
    parsed = MentionCommand.parse(
        f"<@{BOT}> !new !project api !agent codex fix the login", bot_user_id=BOT
    )
    assert (parsed.new, parsed.project, parsed.agent) == (True, "api", "codex")
    assert parsed.command is not None and parsed.command.kind == "prompt"
    assert parsed.command.text == "fix the login"

    mid = MentionCommand.parse(f"hey <@{BOT}> can you look", bot_user_id=BOT)
    assert mid.command is not None and mid.command.text == "hey can you look"


@pytest.mark.parametrize(
    ("text", "kind", "verb"),
    [("stop", "verb", "stop"), ("status", "verb", "status"), ("", "usage", None)],
)
def test_issueops_verbs_mean_the_same_in_slack(text: str, kind: str, verb: str | None) -> None:
    parsed = MentionCommand.parse(f"<@{BOT}> {text}", bot_user_id=BOT)
    assert parsed.command is not None
    assert (parsed.command.kind, parsed.command.verb) == (kind, verb)


def test_mute_is_a_slack_only_thread_verb() -> None:
    parsed = MentionCommand.parse(f"<@{BOT}> Mute", bot_user_id=BOT)
    assert parsed.thread_verb == "mute" and parsed.command is None


def test_from_event_ignores_bots_edits_and_itself() -> None:
    base = {"type": "message", "channel": CHANNEL, "ts": "1.0", "user": ALICE, "text": "hi"}
    assert SlackEvent.from_event(base, bot_user_id=BOT) is not None
    assert SlackEvent.from_event({**base, "bot_id": "B1"}, bot_user_id=BOT) is None
    assert SlackEvent.from_event({**base, "subtype": "message_changed"}, bot_user_id=BOT) is None
    assert SlackEvent.from_event({**base, "user": BOT}, bot_user_id=BOT) is None


def test_from_event_marks_mentions_and_dms_as_addressed() -> None:
    base = {"type": "message", "channel": CHANNEL, "ts": "1.0", "user": ALICE}
    plain = SlackEvent.from_event({**base, "text": "hi"}, bot_user_id=BOT)
    mention = SlackEvent.from_event({**base, "text": f"<@{BOT}> hi"}, bot_user_id=BOT)
    dm = SlackEvent.from_event({**base, "text": "hi", "channel_type": "im"}, bot_user_id=BOT)
    assert plain is not None and not plain.addressed
    assert mention is not None and mention.addressed
    assert dm is not None and dm.addressed


def test_shortcut_uses_the_message_as_the_request_and_the_clicker_as_sender() -> None:
    payload = {
        "type": "message_action",
        "callback_id": "grove_start_session",
        "channel": {"id": CHANNEL},
        "user": {"id": ALICE},
        "message": {"ts": "5.0", "user": "UBOB", "text": "the build is red"},
    }
    event = SlackEvent.from_shortcut(payload)
    assert event is not None
    assert (event.user, event.text, event.addressed, event.shortcut) == (
        ALICE,
        "the build is red",
        True,
        True,
    )
    assert SlackEvent.from_shortcut({**payload, "callback_id": "other"}) is None


def test_plain_text_decodes_mentions_links_and_entities() -> None:
    text = "<@UALICE> see <https://x.io|the doc> in <#C1|dev> &lt;now&gt;"
    assert SlackText.plain(text, name_of=lambda u: "alice") == (
        "@alice see the doc (https://x.io) in #dev <now>"
    )


def test_prompt_drops_the_oldest_messages_to_fit() -> None:
    context = SlackContext(
        channel="dev",
        author="alice",
        permalink="",
        transcript=("--- old ---\n" + "x" * 12_000, "--- new ---\nthe actual bug"),
    )
    prompt = SlackPrompt.render("{thread}\n{command_text}\n{unknown}", context, command_text="fix")
    assert "the actual bug" in prompt and "--- old ---" not in prompt
    assert "{unknown}" in prompt
    assert len(prompt) <= PROMPT_LIMIT


# ─── the thread log ──────────────────────────────────────────────────────────


def test_thread_log_survives_a_reopen_and_updates_in_place(tmp_path: Path) -> None:
    path = tmp_path / "t.json"
    SlackThreadLog(path).put(
        SlackThread(channel="C", root_ts="1.0", workspace_id="w", repo_root="/r")
    )
    reopened = SlackThreadLog(path)
    reopened.update("C:1.0", lambda t: dataclasses.replace(t, muted=True))
    got = SlackThreadLog(path).get("C", "1.0")
    assert got is not None and got.muted
    assert SlackThreadLog(path).live() == {"w": got}
    assert reopened.update("C:missing", lambda t: t) is None


# ─── the router ──────────────────────────────────────────────────────────────


def test_top_level_mention_creates_a_workspace_with_the_conversation(rig: Rig) -> None:
    rig.gateway.channel_history = [
        SlackMessage(ts="99.0", user="UBOB", text="login 500s since 9am")
    ]
    rig.gateway.files = {"trace.log": b"stack"}
    trace = SlackFile(name="trace.log", url="https://files.slack.com/t", size=5)

    rig.router.handle(_event(f"<@{BOT}> fix the login", files=(trace,)))

    [request] = rig.manager.created
    assert request.title == "fix the login"
    assert request.initial_prompt is not None
    assert "login 500s since 9am" in request.initial_prompt
    assert "alice asked:\nfix the login" in request.initial_prompt
    assert [a.name for a in request.attachments] == ["trace.log"]
    assert base64.b64decode(request.attachments[0].content_base64) == b"stack"
    thread = rig.threads.get(CHANNEL, "100.1")
    assert thread is not None and thread.workspace_id == "ws1" and thread.spinner_ts == "100.1"
    assert (CHANNEL, "100.1", "eyes", True) in rig.gateway.reactions
    assert (CHANNEL, "100.1", "loading", True) in rig.gateway.reactions
    assert rig.gateway.texts() == [
        "Working on it in `proj`. [Open in Grove](http://grove.example/w/ws1)"
    ]


def test_mention_inside_a_thread_reads_the_whole_thread(rig: Rig) -> None:
    rig.gateway.threads["90.0"] = [
        SlackMessage(ts="90.0", user="UBOB", text="checkout fails on Safari"),
        SlackMessage(ts="95.0", user=ALICE, text=f"<@{BOT}> take a look"),
    ]
    rig.router.handle(_event(f"<@{BOT}> take a look", ts="95.0", thread_ts="90.0"))
    [request] = rig.manager.created
    assert request.initial_prompt is not None
    assert "checkout fails on Safari" in request.initial_prompt
    assert rig.threads.get(CHANNEL, "90.0") is not None


def test_someone_not_allowed_gets_their_member_id_and_nothing_starts(rig: Rig) -> None:
    rig.router.handle(_event(f"<@{BOT}> rm -rf prod", user=MALLORY))
    assert rig.manager.created == []
    assert MALLORY in rig.gateway.texts()[0]


def test_a_thread_reply_steers_and_moves_the_spinner(rig: Rig) -> None:
    _bind(rig, spinner_ts="100.1")
    rig.router.handle(_event("use the v2 endpoint", ts="101.0", thread_ts="100.1"))
    assert rig.manager.sent == [("ws1", "alice replied in Slack:\nuse the v2 endpoint", ())]
    assert (CHANNEL, "100.1", "loading", False) in rig.gateway.reactions
    assert (CHANNEL, "101.0", "loading", True) in rig.gateway.reactions
    thread = rig.threads.get(CHANNEL, "100.1")
    assert thread is not None and thread.spinner_ts == "101.0"


@pytest.mark.parametrize(
    ("text", "bound"),
    [
        ("use the v2 endpoint (aside)", {}),
        ("use the v2 endpoint", {"muted": True}),
        ("use the v2 endpoint", {"ended": True}),
    ],
)
def test_asides_muted_and_ended_threads_are_not_forwarded(
    rig: Rig, text: str, bound: dict[str, Any]
) -> None:
    _bind(rig, **bound)
    rig.router.handle(_event(text, ts="101.0", thread_ts="100.1"))
    assert rig.manager.sent == []


def test_chatter_from_someone_not_allowed_is_ignored_silently(rig: Rig) -> None:
    _bind(rig)
    rig.router.handle(_event("lol", ts="101.0", thread_ts="100.1", user=MALLORY))
    assert rig.manager.sent == [] and rig.gateway.posts == []


def test_mention_in_a_live_thread_steers_unless_new(rig: Rig) -> None:
    _bind(rig)
    rig.router.handle(_event(f"<@{BOT}> also fix logout", ts="101.0", thread_ts="100.1"))
    assert rig.manager.created == [] and len(rig.manager.sent) == 1

    rig.router.handle(_event(f"<@{BOT}> !new start over", ts="102.0", thread_ts="100.1"))
    assert len(rig.manager.created) == 1


def test_reply_to_an_open_question_answers_it(rig: Rig) -> None:
    _bind(rig, asked="toolu_1", awaiting=2)
    rig.router.handle(_event("yes, ship it", ts="101.0", thread_ts="100.1"))
    [request] = rig.manager.answered
    assert (request.session_id, request.tool_use_id, len(request.answers)) == ("s1", "toolu_1", 2)
    assert rig.manager.sent == []
    thread = rig.threads.get(CHANNEL, "100.1")
    assert thread is not None and thread.awaiting == 0


def test_a_question_answered_elsewhere_falls_back_to_a_message(rig: Rig) -> None:
    _bind(rig, asked="toolu_1", awaiting=1)
    rig.manager.answer_error = "not pending"
    rig.router.handle(_event("yes", ts="101.0", thread_ts="100.1"))
    assert len(rig.manager.sent) == 1


def test_verbs_drive_the_threads_workspace(rig: Rig) -> None:
    _bind(rig)
    rig.router.handle(_event(f"<@{BOT}> stop", ts="101.0", thread_ts="100.1"))
    assert rig.manager.verbs == ["kill"]
    assert (CHANNEL, "101.0", "ok_hand", True) in rig.gateway.reactions


def test_a_paused_workspace_is_not_steered(rig: Rig) -> None:
    _bind(rig)
    rig.manager.status = WorkspaceStatus.PAUSED
    rig.router.handle(_event("go on", ts="101.0", thread_ts="100.1"))
    assert rig.manager.sent == [] and "resume" in rig.gateway.texts()[0]


def test_project_keyword_picks_only_among_the_configured_projects(rig: Rig) -> None:
    rig.router.handle(_event(f"<@{BOT}> !project nope do it"))
    assert rig.manager.created == []
    assert "other, proj" in rig.gateway.texts()[0]


def test_an_unlisted_channel_starts_nothing_and_names_itself_to_an_allowed_user(
    rig: Rig,
) -> None:
    rig.router.handle(_event(f"<@{BOT}> fix it", channel="CELSEWHERE"))
    rig.router.handle(_event(f"<@{BOT}> fix it", ts="100.2", channel="CELSEWHERE", user=MALLORY))
    assert rig.manager.created == []
    assert rig.gateway.texts() == [rig.gateway.texts()[0]]
    assert "CELSEWHERE" in rig.gateway.texts()[0]


def test_allow_lists_are_closed_until_set_and_open_only_on_the_wildcard() -> None:
    closed = SlackConfig()
    assert not closed.allows_user(ALICE) and not closed.allows_channel(CHANNEL)
    opened = SlackConfig(allowed_users=["*"], allowed_channels=["*"])
    assert opened.allows_user(MALLORY) and opened.allows_channel("CELSEWHERE")


def test_the_projects_own_slack_section_sets_the_launch_and_the_prompt(rig: Rig) -> None:
    rig.manager.config = GroveConfig.model_validate(
        {
            "slack": {
                "prompt_template": "PROJECT PROMPT {command_text}",
                "workspace": {
                    "agent": "codex",
                    "model": "gpt-5",
                    "runtime": "container",
                    "branch_mode": "root",
                    "skip_init": True,
                },
            }
        }
    )
    rig.router.handle(_event(f"<@{BOT}> fix it"))
    [request] = rig.manager.created
    assert request.initial_prompt == "PROJECT PROMPT fix it"
    assert (request.agent_name, request.model, request.skip_init) == ("codex", "gpt-5", True)
    assert request.runtime == "container"
    assert request.branch_plan is not None and request.branch_plan.kind == "root"


def test_unset_launch_options_are_left_for_the_project_defaults_to_answer(rig: Rig) -> None:
    rig.router.handle(_event(f"<@{BOT}> fix it"))
    [request] = rig.manager.created
    assert (request.agent_name, request.model, request.runtime) == (None, None, None)
    # The branch is the one option Slack answers itself: never the shared checkout.
    assert request.branch_plan is not None and request.branch_plan.kind == "auto"


def test_a_failed_create_says_so_and_binds_nothing(rig: Rig) -> None:
    rig.manager.create_error = "no such agent"
    rig.router.handle(_event(f"<@{BOT}> fix it"))
    assert (CHANNEL, "100.1", "x", True) in rig.gateway.reactions
    assert "no such agent" in rig.gateway.texts()[0]
    assert rig.threads.get(CHANNEL, "100.1") is None


def test_submit_dedupes_the_mention_and_message_twins(rig: Rig) -> None:
    handled: list[SlackEvent] = []
    rig.router.handle = handled.append  # type: ignore[method-assign]
    event = _event(f"<@{BOT}> fix it")
    rig.router.submit(event)
    rig.router.submit(event)
    rig.router.close()
    assert len(handled) == 1


# ─── the relay ───────────────────────────────────────────────────────────────


def _session(
    state: AgentActivityState, *, questions: tuple[AgentQuestion, ...] = ()
) -> SessionActivity:
    return SessionActivity(
        session=AgentSession(
            session_id="s1",
            transcript_path=None,
            adapter_kind="claude_code",
            provenance="grove_launched",
        ),
        activity=AgentActivity(state=state, questions=questions),
    )


def _delta(session: SessionActivity) -> DashboardDelta:
    row = WorkspaceActivity(
        state=_state(),
        sessions=(session,),
        base_ahead=0,
        base_behind=0,
        diff_added=0,
        diff_removed=0,
        dirty_files=0,
        pane_target=None,
        recent_commits=(),
        observed_at=T0,
    )
    return DashboardDelta(kind="session_activity", seq=1, workspace_id="ws1", workspace=row)


def _result(text: str, *, complete: bool = True, mid: str = "m1") -> FinalResult:
    return FinalResult(text=text, is_complete=complete, message_id=mid, timestamp=T0)


def test_a_finished_turn_posts_the_reply_once_and_stops_the_spinner(rig: Rig) -> None:
    _bind(rig, spinner_ts="100.1")
    rig.manager.result = _result("Fixed it in PR 12.")
    rig.relay.relay(_delta(_session(AgentActivityState.WAITING)))
    rig.relay.relay(_delta(_session(AgentActivityState.WAITING)))
    assert rig.gateway.texts() == ["Fixed it in PR 12."]
    assert (CHANNEL, "100.1", "loading", False) in rig.gateway.reactions


def test_a_working_agent_or_an_unfinished_reply_posts_nothing(rig: Rig) -> None:
    _bind(rig)
    rig.manager.result = _result("Looking…")
    rig.relay.relay(_delta(_session(AgentActivityState.WORKING)))
    rig.manager.result = _result("calling a tool", complete=False)
    rig.relay.relay(_delta(_session(AgentActivityState.WAITING)))
    assert rig.gateway.posts == []


def test_a_question_is_posted_once_and_awaits_a_reply(rig: Rig) -> None:
    _bind(rig)
    question = AgentQuestion(
        id="q1",
        group_id="toolu_1",
        kind="single_select",
        prompt="Ship it?",
        header="Deploy",
        options=(AgentQuestionOption(label="Yes"), AgentQuestionOption(label="No")),
    )
    session = _session(AgentActivityState.BLOCKED, questions=(question,))
    rig.relay.relay(_delta(session))
    rig.relay.relay(_delta(session))
    [text] = rig.gateway.texts()
    assert "**Deploy:** Ship it?" in text and "- Yes" in text
    thread = rig.threads.get(CHANNEL, "100.1")
    assert thread is not None and (thread.asked, thread.awaiting) == ("toolu_1", 1)


def test_a_killed_workspace_ends_the_thread(rig: Rig) -> None:
    _bind(rig)
    rig.relay.relay(
        DashboardDelta(
            kind="workspace_changed", seq=1, workspace_id="ws1", detail={"event": "killed"}
        )
    )
    rig.relay.relay(
        DashboardDelta(
            kind="workspace_changed", seq=2, workspace_id="ws1", detail={"event": "paused"}
        )
    )
    assert rig.gateway.texts() == ["Session ended. Mention me again to start a new one."]
    assert rig.threads.live() == {}


def test_unbound_workspaces_are_never_relayed(rig: Rig) -> None:
    rig.manager.result = _result("hello")
    rig.relay.relay(_delta(_session(AgentActivityState.WAITING)))
    assert rig.gateway.posts == []


# ─── the gateway's one local rule, and the bridge's construction ─────────────


def test_download_refuses_to_send_the_token_off_slack() -> None:
    gateway = SlackGateway(bot_token="xoxb-test", app_token="xapp-test")
    evil = SlackFile(name="x", url="https://attacker.example/f", size=1)
    with pytest.raises(SlackError, match="refusing"):
        gateway.download(evil, max_bytes=10)


def test_bridge_is_off_when_disabled_or_missing_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    registry: Any = FakeRegistry(FakeManager())
    assert SlackBridge.from_config(SlackConfig(), registry=registry) is None
    monkeypatch.delenv("GROVE_SLACK_BOT_TOKEN", raising=False)
    monkeypatch.setenv("GROVE_SLACK_APP_TOKEN", "xapp-test")
    assert SlackBridge.from_config(SlackConfig(enabled=True), registry=registry) is None


def test_bridge_reads_tokens_from_its_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GROVE_SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("GROVE_SLACK_APP_TOKEN", raising=False)
    monkeypatch.setattr("grove.core.paths.slack_threads_path", lambda: tmp_path / "t.json")
    env = tmp_path / "slack.env"
    env.write_text("GROVE_SLACK_BOT_TOKEN=xoxb-a\nGROVE_SLACK_APP_TOKEN=xapp-b\n")
    registry: Any = FakeRegistry(FakeManager())
    bridge = SlackBridge.from_config(
        SlackConfig(enabled=True, env_file=str(env)), registry=registry
    )
    assert bridge is not None


def test_slack_is_a_config_section_with_one_env_source() -> None:
    assert GroveConfig().slack.enabled is False
    with pytest.raises(ValueError, match="mutually exclusive"):
        SlackConfig(env_file="a.env", env_command="printenv")
