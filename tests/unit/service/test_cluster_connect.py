from starrocks_br.api.routes import _cluster_connect


def test_verify_connection_success(monkeypatch):
    monkeypatch.setattr(_cluster_connect.db_module.StarRocksDB, "connect", lambda self: None)
    monkeypatch.setattr(_cluster_connect.db_module.StarRocksDB, "close", lambda self: None)

    result = _cluster_connect.verify_connection(
        host="sr.internal", port=9030, user="root", password="s3cret", database="sales_db"
    )

    assert result.success is True
    assert result.message == "Connection successful"


def test_verify_connection_failure_does_not_leak_password(monkeypatch):
    def fake_connect(self):
        raise ConnectionError(f"Access denied for user 'root'@'sr.internal' (using password: s3cret)")

    monkeypatch.setattr(_cluster_connect.db_module.StarRocksDB, "connect", fake_connect)
    monkeypatch.setattr(_cluster_connect.db_module.StarRocksDB, "close", lambda self: None)

    result = _cluster_connect.verify_connection(
        host="sr.internal", port=9030, user="root", password="s3cret", database="sales_db"
    )

    assert result.success is False
    assert "s3cret" not in result.message
    assert "Access denied" in result.message
