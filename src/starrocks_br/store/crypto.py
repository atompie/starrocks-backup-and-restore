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

"""Encrypts cluster passwords at rest in the metadata store.

Uses a server-side key (STARROCKS_BR_DB_ENCRYPTION_KEY), independent of the
API's bearer-token auth key, so key rotation for each concern stays
separate. The key must be a urlsafe-base64-encoded 32-byte value, as
produced by `Fernet.generate_key()`.
"""

import os
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

ENCRYPTION_KEY_ENV_VAR = "STARROCKS_BR_DB_ENCRYPTION_KEY"


class EncryptionKeyMissingError(RuntimeError):
    def __init__(self):
        super().__init__(
            f"{ENCRYPTION_KEY_ENV_VAR} must be set to encrypt/decrypt cluster passwords. "
            f"Generate one with: python -c \"from cryptography.fernet import Fernet; "
            f'print(Fernet.generate_key().decode())"'
        )


@lru_cache(maxsize=1)
def _get_fernet() -> Fernet:
    key = os.getenv(ENCRYPTION_KEY_ENV_VAR)
    if not key:
        raise EncryptionKeyMissingError()
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_password(plaintext: str) -> str:
    """Encrypt a cluster password for storage. Returns a urlsafe-base64 token string."""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_password(token: str) -> str:
    """Decrypt a stored cluster password token back to plaintext."""
    try:
        return _get_fernet().decrypt(token.encode()).decode()
    except InvalidToken as e:
        raise ValueError("Stored password could not be decrypted (wrong or rotated key)") from e


def reset_key_cache() -> None:
    """Clear the cached Fernet instance - used by tests that change the key env var."""
    _get_fernet.cache_clear()
