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

import httpx
import pytest
from click.testing import CliRunner

from starrocks_br import cli
from starrocks_br.cli_api import client as client_module


@pytest.fixture
def runner():
    return CliRunner()


def _install_mock_transport(monkeypatch, handler):
    original_client = client_module.httpx.Client

    def _client_factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original_client(*args, **kwargs)

    monkeypatch.setattr(client_module.httpx, "Client", _client_factory)


def test_help_lists_new_command_groups_without_altering_existing_ones(runner):
    result = runner.invoke(cli.cli, ["--help"])

    assert result.exit_code == 0
    for existing in ("backup", "init", "prune", "restore"):
        assert existing in result.output
    assert "api" in result.output


def test_missing_api_key_fails_clearly(runner, monkeypatch):
    monkeypatch.delenv(client_module.API_KEY_ENV_VAR, raising=False)
    monkeypatch.setenv(client_module.API_URL_ENV_VAR, "http://testserver")

    result = runner.invoke(cli.cli, ["api", "cluster", "list"])

    assert result.exit_code != 0
    assert "API key is required" in result.output


def test_server_401_is_surfaced_and_exits_non_zero(runner, monkeypatch):
    def handler(request):
        return httpx.Response(401, json={"detail": "Invalid API key"})

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setenv(client_module.API_URL_ENV_VAR, "http://testserver")
    monkeypatch.setenv(client_module.API_KEY_ENV_VAR, "wrong-key")

    result = runner.invoke(cli.cli, ["api", "cluster", "list"])

    assert result.exit_code != 0
    assert "invalid or missing api key" in result.output.lower()


def test_cluster_add_prints_created_id(runner, monkeypatch):
    def handler(request):
        assert request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(201, json={"id": 7, "name": "prod-eu"})

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setenv(client_module.API_URL_ENV_VAR, "http://testserver")
    monkeypatch.setenv(client_module.API_KEY_ENV_VAR, "test-key")

    result = runner.invoke(
        cli.cli,
        [
            "api",
            "cluster",
            "add",
            "--name",
            "prod-eu",
            "--host",
            "h",
            "--port",
            "9030",
            "--user",
            "u",
            "--password",
            "p",
        ],
    )

    assert result.exit_code == 0
    assert "id 7" in result.output


def test_job_submit_without_wait_returns_immediately(runner, monkeypatch):
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=[{"id": 1, "name": "g1", "table_count": 2}])
        return httpx.Response(202, json={"id": 5, "status": "PENDING"})

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setenv(client_module.API_URL_ENV_VAR, "http://testserver")
    monkeypatch.setenv(client_module.API_KEY_ENV_VAR, "test-key")

    result = runner.invoke(
        cli.cli, ["api", "job", "submit", "--cluster", "1", "--type", "backup-full", "--group", "g1"]
    )

    assert result.exit_code == 0
    assert "Submitted job 5" in result.output


def test_job_submit_resolves_group_name_to_id_in_payload(runner, monkeypatch):
    captured = {}

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=[{"id": 1, "name": "g1", "table_count": 2}])
        captured["body"] = request.read()
        return httpx.Response(202, json={"id": 5, "status": "PENDING"})

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setenv(client_module.API_URL_ENV_VAR, "http://testserver")
    monkeypatch.setenv(client_module.API_KEY_ENV_VAR, "test-key")

    result = runner.invoke(
        cli.cli, ["api", "job", "submit", "--cluster", "1", "--type", "backup-full", "--group", "g1"]
    )

    assert result.exit_code == 0
    assert b'"group_id":1' in captured["body"]
    assert b"g1" not in captured["body"]


def test_job_submit_with_unresolvable_group_name_fails_clearly(runner, monkeypatch):
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=[{"id": 1, "name": "g1", "table_count": 2}])
        raise AssertionError("job submission should not happen when the group name is unresolvable")

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setenv(client_module.API_URL_ENV_VAR, "http://testserver")
    monkeypatch.setenv(client_module.API_KEY_ENV_VAR, "test-key")

    result = runner.invoke(
        cli.cli,
        ["api", "job", "submit", "--cluster", "1", "--type", "backup-full", "--group", "no_such_group"],
    )

    assert result.exit_code != 0
    assert "not found" in result.output.lower()


def test_job_submit_with_wait_polls_until_terminal_and_fails_on_failure(runner, monkeypatch):
    calls = {"n": 0}

    def handler(request):
        if request.method == "GET" and request.url.path.startswith("/inventories/cluster/"):
            return httpx.Response(200, json=[{"id": 1, "name": "g1", "table_count": 2}])
        if request.method == "POST":
            return httpx.Response(202, json={"id": 5, "status": "PENDING"})
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json={"id": 5, "status": "RUNNING", "progress_pct": 40})
        return httpx.Response(
            200, json={"id": 5, "status": "FAILED", "progress_pct": None, "error_message": "boom"}
        )

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setenv(client_module.API_URL_ENV_VAR, "http://testserver")
    monkeypatch.setenv(client_module.API_KEY_ENV_VAR, "test-key")
    monkeypatch.setattr("starrocks_br.cli_api.job.time.sleep", lambda *_: None)

    result = runner.invoke(
        cli.cli,
        ["api", "job", "submit", "--cluster", "1", "--type", "backup-full", "--group", "g1", "--wait"],
    )

    assert result.exit_code == 1
    assert "failed" in result.output.lower()


def test_schedule_add_resolves_group_name_to_id(runner, monkeypatch):
    captured = {}

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=[{"id": 3, "name": "g1", "table_count": 1}])
        captured["body"] = request.read()
        return httpx.Response(201, json={"id": 9, "next_run_at": "2026-01-01T00:00:00Z"})

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setenv(client_module.API_URL_ENV_VAR, "http://testserver")
    monkeypatch.setenv(client_module.API_KEY_ENV_VAR, "test-key")

    result = runner.invoke(
        cli.cli,
        [
            "api",
            "schedule",
            "add",
            "--cluster",
            "1",
            "--type",
            "backup_full",
            "--group",
            "g1",
            "--repository",
            "s3_repo",
            "--cadence",
            "0 1 * * *",
        ],
    )

    assert result.exit_code == 0
    assert "Created schedule 9" in result.output
    assert b'"inventory_group_id":3' in captured["body"]


def test_schedule_add_with_unresolvable_group_name_fails_clearly(runner, monkeypatch):
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=[{"id": 3, "name": "g1", "table_count": 1}])
        raise AssertionError("schedule creation should not happen when the group name is unresolvable")

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setenv(client_module.API_URL_ENV_VAR, "http://testserver")
    monkeypatch.setenv(client_module.API_KEY_ENV_VAR, "test-key")

    result = runner.invoke(
        cli.cli,
        [
            "api",
            "schedule",
            "add",
            "--cluster",
            "1",
            "--type",
            "backup_full",
            "--group",
            "no_such_group",
            "--repository",
            "s3_repo",
            "--cadence",
            "0 1 * * *",
        ],
    )

    assert result.exit_code != 0
    assert "not found" in result.output.lower()


def test_schedule_run_due_reports_triggered_count(runner, monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"triggered_job_ids": [1, 2], "triggered_count": 2})

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setenv(client_module.API_URL_ENV_VAR, "http://testserver")
    monkeypatch.setenv(client_module.API_KEY_ENV_VAR, "test-key")

    result = runner.invoke(cli.cli, ["api", "schedule", "run-due"])

    assert result.exit_code == 0
    assert "Triggered 2 job(s)" in result.output


def test_schedule_run_due_unreachable_api_exits_non_zero(runner, monkeypatch):
    def handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setenv(client_module.API_URL_ENV_VAR, "http://testserver")
    monkeypatch.setenv(client_module.API_KEY_ENV_VAR, "test-key")

    result = runner.invoke(cli.cli, ["api", "schedule", "run-due"])

    assert result.exit_code != 0
    assert "could not reach api server" in result.output.lower()


def test_repository_help_lists_add_list_remove(runner):
    result = runner.invoke(cli.cli, ["api", "repository", "--help"])

    assert result.exit_code == 0
    for sub in ("add", "list", "remove"):
        assert sub in result.output


def test_repository_add_creates_via_api(runner, monkeypatch):
    captured = {}

    def handler(request):
        captured["body"] = request.read()
        return httpx.Response(
            201,
            json={"name": "my_repo", "location": "s3://b/p", "broker": "", "is_read_only": False, "error": None},
        )

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setenv(client_module.API_URL_ENV_VAR, "http://testserver")
    monkeypatch.setenv(client_module.API_KEY_ENV_VAR, "test-key")

    result = runner.invoke(
        cli.cli,
        [
            "api",
            "repository",
            "add",
            "--cluster",
            "1",
            "--name",
            "my_repo",
            "--location",
            "s3://b/p",
            "--access-key",
            "AK",
            "--secret-key",
            "SK",
            "--endpoint",
            "https://s3.amazonaws.com",
        ],
    )

    assert result.exit_code == 0
    assert "Created repository 'my_repo'" in result.output
    assert b"my_repo" in captured["body"]


def test_repository_list_prints_name_location_and_error_status(runner, monkeypatch):
    def handler(request):
        return httpx.Response(
            200,
            json=[
                {
                    "name": "good_repo",
                    "location": "s3://b/p1",
                    "broker": "",
                    "is_read_only": False,
                    "error": None,
                },
                {
                    "name": "broken_repo",
                    "location": "s3://b/p2",
                    "broker": "",
                    "is_read_only": False,
                    "error": "auth failed",
                },
            ],
        )

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setenv(client_module.API_URL_ENV_VAR, "http://testserver")
    monkeypatch.setenv(client_module.API_KEY_ENV_VAR, "test-key")

    result = runner.invoke(cli.cli, ["api", "repository", "list", "--cluster", "1"])

    assert result.exit_code == 0
    assert "good_repo" in result.output
    assert "s3://b/p1" in result.output
    assert "broken_repo" in result.output
    assert "auth failed" in result.output


def test_repository_remove_blocked_by_snapshots_exits_non_zero_without_retry(runner, monkeypatch):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(409, json={"detail": "Repository 'my_repo' still holds snapshot data"})

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setenv(client_module.API_URL_ENV_VAR, "http://testserver")
    monkeypatch.setenv(client_module.API_KEY_ENV_VAR, "test-key")

    result = runner.invoke(cli.cli, ["api", "repository", "remove", "--cluster", "1", "my_repo"])

    assert result.exit_code != 0
    assert "snapshot data" in result.output.lower()
    assert calls["n"] == 1


def test_api_serve_builds_app_and_starts_uvicorn(runner, monkeypatch, api_env):
    calls = {}

    def fake_run(app, host, port):
        calls["app"] = app
        calls["host"] = host
        calls["port"] = port

    monkeypatch.setattr("uvicorn.run", fake_run)

    result = runner.invoke(cli.cli, ["api", "serve", "--host", "0.0.0.0", "--port", "9999"])

    assert result.exit_code == 0
    assert calls["host"] == "0.0.0.0"
    assert calls["port"] == 9999
    assert calls["app"] is not None
