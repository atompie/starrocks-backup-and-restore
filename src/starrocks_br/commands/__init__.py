"""Application command layer.

Each module here holds the single implementation of one application
operation (backup, restore, retention, cluster/repository/schedule management).
Commands take plain domain objects (a `Cluster` row, a `Session`, primitive
params) and either return a plain result (dict/tuple) or raise a
`starrocks_br.exceptions.StarRocksBRError` subclass - never an HTTP
concept. `api/routes/*.py` is a thin adapter over these commands; see
openspec/changes/establish-command-layer for the rationale.
"""

from . import backup, clusters, jobs, repositories, restore, retention, schedules

__all__ = ["backup", "clusters", "jobs", "repositories", "restore", "retention", "schedules"]
