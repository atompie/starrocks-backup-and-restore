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
from cryptography.fernet import Fernet

from starrocks_br.store import crypto


@pytest.fixture
def encryption_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv(crypto.ENCRYPTION_KEY_ENV_VAR, key)
    crypto.reset_key_cache()
    yield key
    crypto.reset_key_cache()


def test_encrypt_then_decrypt_round_trips(encryption_key):
    plaintext = "s3cr3t-password"
    token = crypto.encrypt_password(plaintext)

    assert token != plaintext
    assert crypto.decrypt_password(token) == plaintext


def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv(crypto.ENCRYPTION_KEY_ENV_VAR, raising=False)
    crypto.reset_key_cache()

    with pytest.raises(crypto.EncryptionKeyMissingError):
        crypto.encrypt_password("x")

    crypto.reset_key_cache()


def test_decrypting_garbage_raises_value_error(encryption_key):
    with pytest.raises(ValueError):
        crypto.decrypt_password("not-a-valid-token")
