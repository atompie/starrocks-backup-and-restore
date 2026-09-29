import datetime

from sqlalchemy import select

from starrocks_br.dal.metadata import scheduler_lock as lock_dal
from starrocks_br.store.models import SchedulerLock

NOW = datetime.datetime(2026, 9, 29, 12, 0, 0)
LEASE = datetime.timedelta(seconds=300)


def test_free_lock_is_acquired(sqlite_session):
    acquired, recovered = lock_dal.try_acquire(sqlite_session, "host:1", NOW, NOW + LEASE)

    assert (acquired, recovered) == (True, False)
    row = sqlite_session.get(SchedulerLock, 1)
    assert row.holder == "host:1"
    assert row.expires_at == NOW + LEASE


def test_active_lock_blocks_a_second_holder(sqlite_session):
    lock_dal.try_acquire(sqlite_session, "host:1", NOW, NOW + LEASE)

    acquired, recovered = lock_dal.try_acquire(
        sqlite_session, "host:2", NOW + datetime.timedelta(seconds=10), NOW + LEASE
    )

    assert (acquired, recovered) == (False, False)
    assert sqlite_session.get(SchedulerLock, 1).holder == "host:1"


def test_expired_lock_is_reclaimed_and_reported_as_stale(sqlite_session):
    lock_dal.try_acquire(sqlite_session, "host:1", NOW, NOW + LEASE)
    later = NOW + LEASE + datetime.timedelta(seconds=1)

    acquired, recovered = lock_dal.try_acquire(sqlite_session, "host:2", later, later + LEASE)

    assert (acquired, recovered) == (True, True)
    assert sqlite_session.get(SchedulerLock, 1).holder == "host:2"


def test_released_lock_can_be_taken_without_a_stale_report(sqlite_session):
    lock_dal.try_acquire(sqlite_session, "host:1", NOW, NOW + LEASE)
    assert lock_dal.release(sqlite_session, "host:1") is True

    acquired, recovered = lock_dal.try_acquire(sqlite_session, "host:2", NOW, NOW + LEASE)

    assert (acquired, recovered) == (True, False)


def test_release_by_a_non_holder_leaves_the_lock(sqlite_session):
    lock_dal.try_acquire(sqlite_session, "host:2", NOW, NOW + LEASE)

    assert lock_dal.release(sqlite_session, "host:1") is False
    assert sqlite_session.get(SchedulerLock, 1).holder == "host:2"


def test_singleton_row_is_created_once(sqlite_session):
    lock_dal.try_acquire(sqlite_session, "host:1", NOW, NOW + LEASE)
    lock_dal.release(sqlite_session, "host:1")
    lock_dal.try_acquire(sqlite_session, "host:1", NOW, NOW + LEASE)

    assert len(sqlite_session.scalars(select(SchedulerLock)).all()) == 1


def test_last_tick_is_none_until_recorded(sqlite_session):
    assert lock_dal.get_last_tick_at(sqlite_session) is None

    lock_dal.record_last_tick(sqlite_session, NOW)

    assert lock_dal.get_last_tick_at(sqlite_session) == NOW


def test_last_tick_survives_acquire_and_release(sqlite_session):
    lock_dal.record_last_tick(sqlite_session, NOW)

    lock_dal.try_acquire(sqlite_session, "host:1", NOW, NOW + LEASE)
    lock_dal.release(sqlite_session, "host:1")

    assert lock_dal.get_last_tick_at(sqlite_session) == NOW
