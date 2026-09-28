import datetime

from starrocks_br.dal.metadata import schedules


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _make_schedule(sqlite_session, cluster_id, group_id, **overrides):
    kwargs = dict(
        cluster_id=cluster_id,
        job_type="backup_full",
        inventory_group_id=group_id,
        repository="repo",
        cadence="0 0 * * *",
        backend="thread",
        enabled=True,
        next_run_at=_utcnow(),
    )
    kwargs.update(overrides)
    return schedules.create(sqlite_session, **kwargs)


def test_create_persists_schedule(sqlite_session, make_cluster, make_group):
    cluster = make_cluster()
    group_id = make_group(cluster.id)

    schedule = _make_schedule(sqlite_session, cluster.id, group_id)

    assert schedule.id is not None
    assert schedules.get(sqlite_session, cluster.id, schedule.id).repository == "repo"


def test_list_for_cluster_scopes_by_cluster(sqlite_session, make_cluster, make_group):
    cluster_a = make_cluster("a")
    cluster_b = make_cluster("b")
    group_a = make_group(cluster_a.id)
    group_b = make_group(cluster_b.id)
    schedule_a = _make_schedule(sqlite_session, cluster_a.id, group_a)
    _make_schedule(sqlite_session, cluster_b.id, group_b)

    result = schedules.list_for_cluster(sqlite_session, cluster_a.id)

    assert [s.id for s in result] == [schedule_a.id]


def test_get_returns_none_for_wrong_cluster(sqlite_session, make_cluster, make_group):
    cluster_a = make_cluster("a")
    cluster_b = make_cluster("b")
    group_a = make_group(cluster_a.id)
    schedule = _make_schedule(sqlite_session, cluster_a.id, group_a)

    assert schedules.get(sqlite_session, cluster_b.id, schedule.id) is None


def test_update_fields_applies_changes(sqlite_session, make_cluster, make_group):
    cluster = make_cluster()
    group_id = make_group(cluster.id)
    schedule = _make_schedule(sqlite_session, cluster.id, group_id)

    updated = schedules.update_fields(sqlite_session, schedule, {"enabled": False})

    assert updated.enabled is False


def test_delete_removes_schedule(sqlite_session, make_cluster, make_group):
    cluster = make_cluster()
    group_id = make_group(cluster.id)
    schedule = _make_schedule(sqlite_session, cluster.id, group_id)
    schedule_id = schedule.id

    schedules.delete(sqlite_session, schedule)
    sqlite_session.flush()

    assert schedules.get(sqlite_session, cluster.id, schedule_id) is None


def test_due_schedules_only_returns_enabled_and_due(sqlite_session, make_cluster, make_group):
    cluster = make_cluster()
    group_id = make_group(cluster.id)
    due = _make_schedule(
        sqlite_session, cluster.id, group_id, next_run_at=_utcnow() - datetime.timedelta(minutes=1)
    )
    _make_schedule(
        sqlite_session, cluster.id, group_id, next_run_at=_utcnow() + datetime.timedelta(hours=1)
    )
    _make_schedule(
        sqlite_session,
        cluster.id,
        group_id,
        enabled=False,
        next_run_at=_utcnow() - datetime.timedelta(minutes=1),
    )

    result = schedules.due_schedules(sqlite_session, _utcnow())

    assert [s.id for s in result] == [due.id]


def test_due_schedules_excludes_one_shot(sqlite_session, make_cluster, make_group):
    cluster = make_cluster()
    group_id = make_group(cluster.id)
    _make_schedule(sqlite_session, cluster.id, group_id, cadence=None, next_run_at=None)

    result = schedules.due_schedules(sqlite_session, _utcnow())

    assert result == []


def test_advance_next_run_at_succeeds_on_matching_value(sqlite_session, make_cluster, make_group):
    cluster = make_cluster()
    group_id = make_group(cluster.id)
    previously_due_at = _utcnow() - datetime.timedelta(minutes=1)
    schedule = _make_schedule(sqlite_session, cluster.id, group_id, next_run_at=previously_due_at)
    new_next_run_at = _utcnow() + datetime.timedelta(hours=1)

    rowcount = schedules.advance_next_run_at(
        sqlite_session, schedule.id, previously_due_at, new_next_run_at
    )

    assert rowcount == 1
    sqlite_session.refresh(schedule)
    assert schedule.next_run_at.replace(tzinfo=datetime.timezone.utc) == new_next_run_at


def test_advance_next_run_at_is_idempotent_when_already_advanced(
    sqlite_session, make_cluster, make_group
):
    cluster = make_cluster()
    group_id = make_group(cluster.id)
    previously_due_at = _utcnow() - datetime.timedelta(minutes=1)
    schedule = _make_schedule(sqlite_session, cluster.id, group_id, next_run_at=previously_due_at)

    schedules.advance_next_run_at(
        sqlite_session, schedule.id, previously_due_at, _utcnow() + datetime.timedelta(hours=1)
    )

    rowcount = schedules.advance_next_run_at(
        sqlite_session, schedule.id, previously_due_at, _utcnow() + datetime.timedelta(hours=2)
    )

    assert rowcount == 0
