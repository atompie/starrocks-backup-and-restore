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

"""Pluggable job execution backend registry.

Per design.md Decision 3: a JobBackend only knows how to get a job's work
*triggered* (enqueue(job_id)); the actual work is the backend-agnostic
JOB_HANDLERS map in handlers.py. Today only "thread" is registered. Adding
a future Kafka/Redis-backed backend means implementing this Protocol and
registering it here - no change to API routes or handlers.
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class JobBackend(Protocol):
    name: str

    def enqueue(self, job_id: int) -> None:
        """Arrange for the job with this id to be executed.

        Must not block waiting for the job to finish - job status is
        observed later via the jobs table (GET /jobs/{id}), not via this
        call's return value.
        """
        ...


class UnknownBackendError(ValueError):
    def __init__(self, name: str, enabled: list[str]):
        self.name = name
        self.enabled = enabled
        super().__init__(
            f"Execution backend '{name}' is not enabled on this server. "
            f"Enabled backends: {', '.join(enabled) or '(none)'}"
        )


class BackendRegistry:
    """Holds the set of enabled JobBackend instances and the server default."""

    def __init__(self, backends: dict[str, JobBackend], default_backend: str):
        if default_backend not in backends:
            raise UnknownBackendError(default_backend, list(backends))
        self._backends = backends
        self.default_backend = default_backend

    @property
    def enabled_backends(self) -> list[str]:
        return list(self._backends)

    def resolve(self, requested: str | None, cluster_default: str | None = None) -> str:
        """Resolve a backend name: request override -> cluster default -> server default."""
        name = requested or cluster_default or self.default_backend
        if name not in self._backends:
            raise UnknownBackendError(name, self.enabled_backends)
        return name

    def get(self, name: str) -> JobBackend:
        if name not in self._backends:
            raise UnknownBackendError(name, self.enabled_backends)
        return self._backends[name]


_registry: BackendRegistry | None = None


def set_registry(registry: BackendRegistry) -> None:
    global _registry
    _registry = registry


def get_registry() -> BackendRegistry:
    if _registry is None:
        raise RuntimeError(
            "Job backend registry has not been initialized; call "
            "starrocks_br.jobs.backend.set_registry() during server startup"
        )
    return _registry


def reset_registry() -> None:
    """Clear the global registry - used by tests that build a fresh app per test."""
    global _registry
    _registry = None
