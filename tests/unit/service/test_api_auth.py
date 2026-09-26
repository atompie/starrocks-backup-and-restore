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


def test_request_without_token_is_401(api_client):
    api_client.headers.pop("Authorization")

    response = api_client.get("/clusters")

    assert response.status_code == 401


def test_request_with_wrong_token_is_401(api_client):
    api_client.headers.update({"Authorization": "Bearer wrong-token"})

    response = api_client.get("/clusters")

    assert response.status_code == 401


def test_request_with_correct_token_succeeds(api_client):
    response = api_client.get("/clusters")

    assert response.status_code == 200


def test_health_endpoint_requires_no_token(api_client):
    api_client.headers.pop("Authorization")

    response = api_client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_server_fails_to_start_without_api_key(api_env, monkeypatch):
    from starrocks_br.api.app import StartupConfigError, create_app

    monkeypatch.delenv("STARROCKS_BR_API_KEY", raising=False)

    with pytest.raises(StartupConfigError, match="STARROCKS_BR_API_KEY"):
        create_app()


def test_server_fails_to_start_without_encryption_key(api_env, monkeypatch):
    from starrocks_br.api.app import StartupConfigError, create_app

    monkeypatch.delenv("STARROCKS_BR_DB_ENCRYPTION_KEY", raising=False)

    with pytest.raises(StartupConfigError, match="STARROCKS_BR_DB_ENCRYPTION_KEY"):
        create_app()
