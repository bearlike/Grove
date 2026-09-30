"""A human's rating of one agent turn, recorded as Langfuse scores on that turn's trace.

The webapp's thumbs are the only annotation a person gives while the work is
still in front of them, so the rating lands on the trace the transcript replay
already exported for that turn instead of in a Grove-side store nobody grades
from. The trace id is re-derived with :func:`grove.core.trace.turn_trace_id` —
the same derivation the replay spends — so no id crosses the wire and a rating
can never name a trace the replay would not have produced.

Every score id is DERIVED from the trace, the score name and (for a reason)
the reason itself. Langfuse's ``POST /api/public/scores`` upserts by id, so a
changed vote overwrites the previous one rather than stacking a second verdict
beside it. Withdrawn reasons are deleted by the same ids, which is what keeps a
turn from carrying a reason its rater has since unticked.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal

from loguru import logger

Rating = Literal["positive", "negative"]

RATING_SCORE: Final = "user-feedback"
"""BOOLEAN score: ``1`` thumbs up, ``0`` thumbs down. The note rides its comment."""

REASON_SCORE: Final = "user-feedback-reason"
"""CATEGORICAL score, one per reason a thumbs-DOWN names, so a cohort filters on one."""

PRAISE_SCORE: Final = "user-feedback-praise"
"""CATEGORICAL score, one per reason a thumbs-UP names.

A separate rubric rather than more categories on ``user-feedback-reason``,
because a category's meaning must not depend on which way the thumb pointed:
"what went wrong" and "what went well" are two questions, and one rubric
answering both is one a judge cannot be calibrated against.
"""

REASON_SCORES: Final[dict[Rating, str]] = {"positive": PRAISE_SCORE, "negative": REASON_SCORE}

_TIMEOUT_SECONDS: Final = 5.0


def score_id(trace_id: str, name: str, value: str = "") -> str:
    """The stable id of one score on one trace — what makes a re-vote an overwrite."""
    return hashlib.sha256(f"grove/score/{trace_id}/{name}/{value}".encode()).hexdigest()[:32]


@dataclass(slots=True, frozen=True)
class TurnFeedback:
    """One rating, already resolved to the trace it annotates."""

    # A score names exactly ONE target (Langfuse 400s on traceId + sessionId);
    # the trace already belongs to its session.
    trace_id: str
    rating: Rating
    reasons: tuple[str, ...] = ()
    note: str = ""

    def scores(self) -> list[dict[str, object]]:
        """The score bodies to upsert: the verdict, then one per ticked reason."""
        verdict: dict[str, object] = {
            "id": score_id(self.trace_id, RATING_SCORE),
            "traceId": self.trace_id,
            "name": RATING_SCORE,
            "dataType": "BOOLEAN",
            "value": 1 if self.rating == "positive" else 0,
            "source": "ANNOTATION",
        }
        if self.note:
            verdict["comment"] = self.note
        name = REASON_SCORES[self.rating]
        reasons: list[dict[str, object]] = [
            {
                "id": score_id(self.trace_id, name, reason),
                "traceId": self.trace_id,
                "name": name,
                "dataType": "CATEGORICAL",
                "value": reason,
                "source": "ANNOTATION",
            }
            for reason in self.reasons
        ]
        return [verdict, *reasons]

    def withdrawn(self, catalogs: Mapping[Rating, Sequence[str]]) -> list[str]:
        """Score ids of every reason, in EITHER rubric, not ticked this time.

        Both rubrics, because a vote can flip: a turn rated down with reasons
        and then rated up must not keep carrying the reasons it was rated down
        for.
        """
        kept = {score_id(self.trace_id, REASON_SCORES[self.rating], r) for r in self.reasons}
        return [
            stale
            for rating, catalog in catalogs.items()
            for reason in catalog
            if (stale := score_id(self.trace_id, REASON_SCORES[rating], reason)) not in kept
        ]


@dataclass(slots=True, frozen=True)
class LangfuseScores:
    """Posts scores with the ready-made ``Basic …`` header the OTLP exporter already uses."""

    host: str
    auth_header: str

    def record(self, feedback: TurnFeedback, catalogs: Mapping[Rating, Sequence[str]]) -> None:
        """Upsert the verdict and reasons, then delete reasons the rater unticked.

        Langfuse refuses an ``ANNOTATION`` score without the ``configId`` of
        the rubric it grades against, so the ids are looked up by score name
        first — once per rating, which is a human click, so nothing is cached.
        Raises :class:`MissingScoreConfig` when a rubric does not exist (the
        message names it), and any transport error as-is, so the route can say
        the rating did not land. Deleting an absent score succeeds (measured),
        so withdrawing a reason that was never ticked is harmless.
        """
        configs = self._config_ids()
        scores = feedback.scores()
        # Checked before the first write, so a missing rubric never leaves a
        # verdict recorded without the reasons that came with it.
        missing = next((str(s["name"]) for s in scores if s["name"] not in configs), None)
        if missing is not None:
            raise MissingScoreConfig(missing)
        for body in scores:
            self._send(
                "POST", "/api/public/scores", {**body, "configId": configs[str(body["name"])]}
            )
        for stale in feedback.withdrawn(catalogs):
            self._send("DELETE", f"/api/public/scores/{stale}", None)
        logger.info(
            "feedback recorded: trace={} rating={} reasons={}",
            feedback.trace_id,
            feedback.rating,
            len(feedback.reasons),
        )

    def _config_ids(self) -> dict[str, str]:
        """Active score config id by name. One page of 100 covers any real project."""
        payload = self._send("GET", "/api/public/score-configs?limit=100", None)
        rows = payload.get("data") if isinstance(payload, dict) else None
        return {
            row["name"]: row["id"]
            for row in rows or []
            if isinstance(row, dict) and not row.get("isArchived")
        }

    def _send(self, method: str, path: str, body: dict[str, object] | None) -> object:
        request = urllib.request.Request(
            f"{self.host.rstrip('/')}{path}",
            method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Authorization": self.auth_header, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            raw = response.read()
        return json.loads(raw) if raw else None


class MissingScoreConfig(LookupError):
    """The Langfuse project has no score config (rubric) with this name."""

    def __init__(self, name: str) -> None:
        super().__init__(
            f"Langfuse has no score config named {name!r}; create it "
            f"(see `grove.core.telemetry.feedback`) before rating turns"
        )
        self.name = name


__all__ = [
    "PRAISE_SCORE",
    "RATING_SCORE",
    "REASON_SCORE",
    "REASON_SCORES",
    "LangfuseScores",
    "MissingScoreConfig",
    "Rating",
    "TurnFeedback",
    "score_id",
]
