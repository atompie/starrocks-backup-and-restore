"""Standalone S3 endpoint/credential verification, independent of any cluster.

Mirrors `api.routes._cluster_connect.verify_connection`'s shape: always
return a `RepositoryVerifyResponse`, never raise, and never let the secret
key leak into the returned message.
"""

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, ConnectTimeoutError, EndpointConnectionError

from .api.schemas import RepositoryVerifyResponse

DEFAULT_VERIFY_TIMEOUT_SECONDS = 5


def _parse_bucket(location: str) -> str | None:
    """Extract the bucket name from an `s3://bucket[/prefix]` location.

    Returns None if `location` does not identify a bucket.
    """
    if not location.startswith("s3://"):
        return None
    remainder = location[len("s3://") :]
    bucket = remainder.split("/", 1)[0]
    return bucket or None


def _redact(message: str, secret_key: str) -> str:
    if secret_key and secret_key in message:
        return message.replace(secret_key, "***")
    return message


def verify_s3_connection(
    location: str,
    access_key: str,
    secret_key: str,
    endpoint: str,
    region: str | None,
    *,
    timeout: int = DEFAULT_VERIFY_TIMEOUT_SECONDS,
) -> RepositoryVerifyResponse:
    """Attempt a real `head_bucket` call and report success/failure without raising."""
    bucket = _parse_bucket(location)
    if bucket is None:
        return RepositoryVerifyResponse(
            success=False,
            message=f"Invalid location '{location}': expected 's3://<bucket>[/prefix]'",
        )

    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region or "us-east-1",
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=timeout,
            retries={"max_attempts": 1},
        ),
    )

    try:
        client.head_bucket(Bucket=bucket)
    except ClientError as e:
        status_code = e.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        error_code = e.response.get("Error", {}).get("Code")
        if status_code == 403 or error_code in ("403", "InvalidAccessKeyId", "SignatureDoesNotMatch"):
            message = f"Authentication failed for bucket '{bucket}': {error_code or status_code}"
        elif status_code == 404 or error_code in ("404", "NoSuchBucket"):
            message = f"Bucket '{bucket}' not found at endpoint '{endpoint}'"
        else:
            message = f"S3 request failed: {_redact(str(e), secret_key)}"
        return RepositoryVerifyResponse(success=False, message=_redact(message, secret_key))
    except (EndpointConnectionError, ConnectTimeoutError) as e:
        return RepositoryVerifyResponse(
            success=False,
            message=f"Could not reach endpoint '{endpoint}': {_redact(str(e), secret_key)}",
        )
    except Exception as e:
        return RepositoryVerifyResponse(
            success=False,
            message=f"S3 verification failed: {_redact(str(e), secret_key)}",
        )
    else:
        return RepositoryVerifyResponse(success=True, message="Endpoint reachable and credentials valid")
