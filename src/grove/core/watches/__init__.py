"""Watches: register a callback, halt, and be woken when it happens.

The public surface is deliberately small. A predicate is added by subclassing
:class:`Watcher` (binding its own predicate variant) and listing it in the
:class:`WatcherRegistry` the daemon builds; nothing else in this package
changes, and no surface does.
"""

from grove.core.watches.ci import CiWatcher
from grove.core.watches.command import CommandWatcher
from grove.core.watches.log import WatchLog
from grove.core.watches.mailbox import MailboxWatchCourier
from grove.core.watches.scheduler import WatchScheduler
from grove.core.watches.subscriptions import TicketSubscriptions
from grove.core.watches.ticket import TicketWatcher
from grove.core.watches.watcher import TimerWatcher, Watcher, WatcherRegistry

__all__ = [
    "CiWatcher",
    "CommandWatcher",
    "MailboxWatchCourier",
    "TicketSubscriptions",
    "TicketWatcher",
    "TimerWatcher",
    "WatchLog",
    "WatchScheduler",
    "Watcher",
    "WatcherRegistry",
]
