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
from pydantic import ValidationError

from starrocks_br.api.schemas import RepositoryCreate, RepositoryRead


def test_repository_read_never_includes_credential_fields():
    field_names = set(RepositoryRead.model_fields)

    assert "access_key" not in field_names
    assert "secret_key" not in field_names


@pytest.mark.parametrize("missing_field", ["name", "location", "access_key", "secret_key", "endpoint"])
def test_repository_create_requires_field(missing_field):
    payload = {
        "name": "my_repo",
        "location": "s3://bucket/path",
        "access_key": "AK",
        "secret_key": "SK",
        "endpoint": "https://s3.amazonaws.com",
    }
    del payload[missing_field]

    with pytest.raises(ValidationError):
        RepositoryCreate(**payload)


def test_repository_create_accepts_optional_region():
    payload = RepositoryCreate(
        name="my_repo",
        location="s3://bucket/path",
        access_key="AK",
        secret_key="SK",
        endpoint="https://s3.amazonaws.com",
    )

    assert payload.region is None
