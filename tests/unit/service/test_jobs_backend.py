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

import pytest

from starrocks_br.jobs.backend import BackendRegistry, UnknownBackendError


class _DummyBackend:
    def __init__(self, name):
        self.name = name
        self.enqueued = []

    def enqueue(self, job_id: int) -> None:
        self.enqueued.append(job_id)


@pytest.fixture
def registry():
    return BackendRegistry({"thread": _DummyBackend("thread"), "kafka": _DummyBackend("kafka")}, "thread")


def test_resolve_uses_server_default_when_nothing_requested(registry):
    assert registry.resolve(None) == "thread"


def test_resolve_uses_request_override(registry):
    assert registry.resolve("kafka") == "kafka"


def test_resolve_uses_cluster_default_over_server_default(registry):
    assert registry.resolve(None, cluster_default="kafka") == "kafka"


def test_resolve_request_override_wins_over_cluster_default(registry):
    assert registry.resolve("thread", cluster_default="kafka") == "thread"


def test_resolve_unknown_backend_raises(registry):
    with pytest.raises(UnknownBackendError):
        registry.resolve("redis")


def test_get_returns_backend_instance(registry):
    backend = registry.get("kafka")
    assert backend.name == "kafka"


def test_constructing_with_unknown_default_raises():
    with pytest.raises(UnknownBackendError):
        BackendRegistry({"thread": _DummyBackend("thread")}, "kafka")


def test_enabled_backends_lists_all_registered_names(registry):
    assert set(registry.enabled_backends) == {"thread", "kafka"}
