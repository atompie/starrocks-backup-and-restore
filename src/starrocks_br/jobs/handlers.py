"""Job type -> command dispatch table.

The actual application logic lives in `starrocks_br.commands` (the single
implementation of each use case - see openspec/changes/establish-command-layer).
This module only maps job-type strings to those command functions for
`JobBackend` implementations (`jobs/thread_backend.py` today) to dispatch on.
"""

from collections.abc import Callable
from typing import Any

from ..commands.backup import run_backup_full, run_backup_incremental
from ..commands.prune import run_prune
from ..commands.restore import run_restore
from ..store.models import Cluster

OnProgress = Callable[[dict], None] | None

JOB_HANDLERS: dict[str, Callable[[Cluster, dict[str, Any], int, OnProgress], dict]] = {
    "backup_full": run_backup_full,
    "backup_incremental": run_backup_incremental,
    "restore": run_restore,
    "prune": run_prune,
}
