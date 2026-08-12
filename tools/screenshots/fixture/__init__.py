"""The declarative demo world every screenshot is built from.

`demo.json` holds the content; the models here validate it at load and carry the
pure behaviour over it. NOTHING in this package touches the filesystem beyond
reading its own fixture, imports no side-effecting Grove module, and knows
nothing about JSONL, tmux, git or a browser — which is what lets a test load the
whole world in milliseconds and what lets `planter/` and `driver/` consume the
same objects without either reaching into the other.
"""

from .history import HistoryCorpus, HistoryCurve, HistoryDay, HistoryProvider, HistoryToolName
from .transcript import ToolStep, Transcript, Turn
from .weights import weighted
from .workspace import DemoAgent, DemoPhase, DemoTicket, DemoWorkspace, TicketClaim
from .world import DEMO_PATH, AdapterKind, DemoWorld, ModelPrices, Pricing, Repos, Tempo

__all__ = [
    "DEMO_PATH",
    "AdapterKind",
    "DemoAgent",
    "DemoPhase",
    "DemoTicket",
    "DemoWorkspace",
    "DemoWorld",
    "HistoryCorpus",
    "HistoryCurve",
    "HistoryDay",
    "HistoryProvider",
    "HistoryToolName",
    "ModelPrices",
    "Pricing",
    "Repos",
    "Tempo",
    "TicketClaim",
    "ToolStep",
    "Transcript",
    "Turn",
    "weighted",
]
