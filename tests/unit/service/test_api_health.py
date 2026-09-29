import datetime


def test_health_reports_null_last_tick_before_any_tick(api_client):
    api_client.headers.pop("Authorization")

    response = api_client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "scheduler": {"last_tick_at": None}}


def test_health_reports_the_recorded_last_tick(api_client):
    from starrocks_br.dal.metadata import scheduler_lock as lock_dal
    from starrocks_br.store.session import session_scope

    tick = datetime.datetime(2026, 9, 29, 12, 30, 0, tzinfo=datetime.timezone.utc)
    with session_scope() as session:
        lock_dal.record_last_tick(session, tick)
    api_client.headers.pop("Authorization")

    response = api_client.get("/health")

    assert response.json()["scheduler"]["last_tick_at"] == "2026-09-29T12:30:00+00:00"


def test_lock_contention_leaves_last_tick_unchanged(api_client):
    from starrocks_br.commands import schedules as commands
    from starrocks_br.dal.metadata import scheduler_lock as lock_dal
    from starrocks_br.store.session import session_scope

    tick = datetime.datetime(2026, 9, 29, 12, 30, 0, tzinfo=datetime.timezone.utc)
    with session_scope() as session:
        lock_dal.record_last_tick(session, tick)
    commands.try_acquire_scheduler_lock("host:1")

    assert commands.try_acquire_scheduler_lock("host:2").acquired is False

    body = api_client.get("/health").json()
    assert body["scheduler"]["last_tick_at"] == "2026-09-29T12:30:00+00:00"
