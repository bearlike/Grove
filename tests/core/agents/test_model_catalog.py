"""`resolve_models` composes the one catalog every create surface offers.

The property under test is the asymmetry between the two sources: discovery is
capped because nobody chose what a tool publishes, a configured list is not
because somebody typed it. A cap applied to both is invisible — a truncated
list looks exactly like a complete one — so this file is the only thing
standing between that and an operator concluding Grove ignores its own config.
"""

from __future__ import annotations

import pytest

from grove.core.agents import MODEL_CATALOG_CAP, resolve_models
from grove.core.agents.registry import CONTEXT_VARIANT_SUFFIX


@pytest.fixture
def _no_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refuse any live probe, so a test asserting on `configured` cannot pass
    by accidentally reaching the adapter (or shelling out to `codex`)."""

    def _boom(self: object, command: str) -> tuple[str, ...]:
        raise AssertionError("discovery must not run when a catalog is configured")

    monkeypatch.setattr("grove.core.agents.claude_code.ClaudeCodeAdapter.available_models", _boom)


@pytest.mark.usefixtures("_no_discovery")
def test_a_configured_catalog_is_offered_whole_past_the_discovery_cap() -> None:
    # Shaped like the real gateway catalog measured on the reference host: 22
    # models under one prefix, more than twice the cap.
    configured = tuple(f"anthropic-model-{i}" for i in range(22))
    assert len(configured) > MODEL_CATALOG_CAP

    offered = resolve_models(kind="claude_code", command="claude", configured=configured)

    assert offered == configured


@pytest.mark.usefixtures("_no_discovery")
def test_a_configured_catalog_keeps_its_order_and_drops_duplicates() -> None:
    offered = resolve_models(
        kind="claude_code",
        command="claude",
        configured=("opus", "haiku", "opus", "", "sonnet"),
    )
    # Order is the operator's stated preference, so it survives; the empty
    # string is not a model id and never reaches a picker.
    assert offered == ("opus", "haiku", "sonnet")


def test_discovery_is_still_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    discovered = tuple(f"discovered-{i}" for i in range(MODEL_CATALOG_CAP + 5))
    monkeypatch.setattr(
        "grove.core.agents.claude_code.ClaudeCodeAdapter.available_models",
        lambda self, command: discovered,
    )

    offered = resolve_models(kind="claude_code", command="claude", configured=())

    assert offered == discovered[:MODEL_CATALOG_CAP]


@pytest.mark.usefixtures("_no_discovery")
def test_a_bracketed_gateway_id_survives_the_catalog_untouched() -> None:
    """`anthropic-opus-5[1m]` is a real, current id on the reference host.

    It is here because every layer that touches a model id is tempted to
    sanitize it, and this shape is the one that gets sanitized away — the
    brackets read as suspicious to anyone who has not seen the real catalog.
    """
    offered = resolve_models(
        kind="claude_code",
        command="claude",
        configured=("anthropic-opus-5[1m]", "anthropic-qwen3.8-max-preview[1m]"),
    )
    assert offered == ("anthropic-opus-5[1m]", "anthropic-qwen3.8-max-preview[1m]")


@pytest.mark.usefixtures("_no_discovery")
def test_a_redundant_context_variant_pair_is_offered_once() -> None:
    """Both halves of `x` / `x[1m]` exist for Claude Code, not for a reader.

    Measured on the reference gateway: all 7 pairs report identical input and
    output rates and an identical ``max_input_tokens``, so the plain id is the
    same choice twice. The MARKED id survives because it is the one that names
    the capability — offering only the plain half would silently take the
    extended window away from everyone picking off a list.
    """
    offered = resolve_models(
        kind="claude_code",
        command="claude",
        configured=(
            "anthropic-opus-5",
            "anthropic-opus-5[1m]",
            "anthropic-glm-5.3",
            "anthropic-sonnet-5",
            "anthropic-sonnet-5[1m]",
        ),
    )

    assert offered == ("anthropic-opus-5[1m]", "anthropic-glm-5.3", "anthropic-sonnet-5[1m]")


@pytest.mark.usefixtures("_no_discovery")
def test_an_unpaired_id_is_untouched_whichever_half_it_is() -> None:
    # The fold keys on the PAIR being present, never on the suffix alone: a
    # catalog that publishes only one half of a model has no redundancy to
    # remove, and dropping either would delete a model outright.
    offered = resolve_models(
        kind="claude_code",
        command="claude",
        configured=("anthropic-gpt-6-astra", "anthropic-qwen3.8-max-preview[1m]", "opus"),
    )

    assert offered == ("anthropic-gpt-6-astra", "anthropic-qwen3.8-max-preview[1m]", "opus")


def test_the_fold_runs_before_the_discovery_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Otherwise a catalog of pairs spends half its allowance on duplicates.

    Twelve ids, six pairs: capped first this offers 5 real models (10 rows
    minus the folded half), folded first it offers all 6.
    """
    discovered = tuple(
        f"gw-model-{i}{suffix}" for i in range(6) for suffix in ("", CONTEXT_VARIANT_SUFFIX)
    )
    assert len(discovered) > MODEL_CATALOG_CAP
    monkeypatch.setattr(
        "grove.core.agents.claude_code.ClaudeCodeAdapter.available_models",
        lambda self, command: discovered,
    )

    offered = resolve_models(kind="claude_code", command="claude", configured=())

    assert offered == tuple(f"gw-model-{i}{CONTEXT_VARIANT_SUFFIX}" for i in range(6))
