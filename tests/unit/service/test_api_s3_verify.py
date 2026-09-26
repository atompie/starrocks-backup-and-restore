from botocore.exceptions import ClientError, EndpointConnectionError

VERIFY_PAYLOAD = {
    "location": "s3://test-bucket/prefix",
    "access_key": "AK",
    "secret_key": "top-secret-secret-key",
    "endpoint": "http://localhost:9000",
    "region": "us-east-1",
}


class FakeS3Client:
    def __init__(self, head_bucket_error=None):
        self.head_bucket_error = head_bucket_error
        self.head_bucket_calls = []

    def head_bucket(self, Bucket):  # noqa: N803 - matches boto3's client signature
        self.head_bucket_calls.append(Bucket)
        if self.head_bucket_error:
            raise self.head_bucket_error


def _patch_client(monkeypatch, fake_client):
    from starrocks_br import s3_verify

    monkeypatch.setattr(s3_verify.boto3, "client", lambda *args, **kwargs: fake_client)


def _client_error(status_code, code):
    return ClientError(
        {"Error": {"Code": code, "Message": code}, "ResponseMetadata": {"HTTPStatusCode": status_code}},
        "HeadBucket",
    )


def test_verify_success(api_client, monkeypatch):
    fake_client = FakeS3Client()
    _patch_client(monkeypatch, fake_client)

    response = api_client.post("/repositories/verify", json=VERIFY_PAYLOAD)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert fake_client.head_bucket_calls == ["test-bucket"]


def test_verify_forbidden_is_reported_as_auth_failure(api_client, monkeypatch):
    fake_client = FakeS3Client(head_bucket_error=_client_error(403, "InvalidAccessKeyId"))
    _patch_client(monkeypatch, fake_client)

    response = api_client.post("/repositories/verify", json=VERIFY_PAYLOAD)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert "auth" in body["message"].lower()


def test_verify_missing_bucket_is_reported_as_not_found(api_client, monkeypatch):
    fake_client = FakeS3Client(head_bucket_error=_client_error(404, "NoSuchBucket"))
    _patch_client(monkeypatch, fake_client)

    response = api_client.post("/repositories/verify", json=VERIFY_PAYLOAD)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert "not found" in body["message"].lower()


def test_verify_unreachable_endpoint_is_not_a_500(api_client, monkeypatch):
    fake_client = FakeS3Client(
        head_bucket_error=EndpointConnectionError(endpoint_url=VERIFY_PAYLOAD["endpoint"])
    )
    _patch_client(monkeypatch, fake_client)

    response = api_client.post("/repositories/verify", json=VERIFY_PAYLOAD)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert "reach" in body["message"].lower()


def test_verify_never_leaks_secret_key(api_client, monkeypatch):
    secret = VERIFY_PAYLOAD["secret_key"]

    for error in (
        _client_error(403, "InvalidAccessKeyId"),
        _client_error(404, "NoSuchBucket"),
        EndpointConnectionError(endpoint_url=VERIFY_PAYLOAD["endpoint"]),
        RuntimeError(f"unexpected failure, secret was {secret}"),
    ):
        fake_client = FakeS3Client(head_bucket_error=error)
        _patch_client(monkeypatch, fake_client)

        response = api_client.post("/repositories/verify", json=VERIFY_PAYLOAD)

        assert response.status_code == 200
        assert secret not in response.text


def test_verify_malformed_location_is_rejected_without_500(api_client, monkeypatch):
    fake_client = FakeS3Client()
    _patch_client(monkeypatch, fake_client)

    payload = {**VERIFY_PAYLOAD, "location": "not-an-s3-url"}
    response = api_client.post("/repositories/verify", json=payload)

    assert response.status_code in (200, 422)
    if response.status_code == 200:
        assert response.json()["success"] is False
    assert fake_client.head_bucket_calls == []
