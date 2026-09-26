# Copyright 2025 deep-bi
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

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

JOB_HANDLERS: dict[str, Callable[[Cluster, dict[str, Any], OnProgress], dict]] = {
    "backup_full": run_backup_full,
    "backup_incremental": run_backup_incremental,
    "restore": run_restore,
    "prune": run_prune,
}
