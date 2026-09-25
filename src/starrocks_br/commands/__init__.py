"""Application command layer.

Each module here holds the single implementation of one application
operation (backup, restore, prune, cluster/repository/schedule management).
Commands take plain domain objects (a `Cluster` row, a `Session`, primitive
params) and either return a plain result (dict/tuple) or raise a
`starrocks_br.exceptions.StarRocksBRError` subclass - never an HTTP or CLI
concept. `cli.py` and `api/routes/*.py` are both thin adapters over these
commands; see openspec/changes/establish-command-layer for the rationale.
"""

from . import backup, clusters, jobs, prune, repositories, restore, schedules

__all__ = ["backup", "clusters", "jobs", "prune", "repositories", "restore", "schedules"]
