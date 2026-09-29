import pytest

from starrocks_br import runtime_config as cfg


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (cfg.HEARTBEAT_ENV_VAR, cfg.STALE_ENV_VAR, cfg.LOCK_TIMEOUT_ENV_VAR, cfg.RETENTION_MAX_ENV_VAR):
        monkeypatch.delenv(name, raising=False)


def test_defaults():
    assert cfg.get_job_heartbeat_seconds() == 30
    assert cfg.get_job_stale_seconds() == 180
    assert cfg.get_scheduler_lock_timeout_seconds() == 300


def test_stale_default_grows_with_a_large_heartbeat(monkeypatch):
    monkeypatch.setenv(cfg.HEARTBEAT_ENV_VAR, "120")

    assert cfg.get_job_stale_seconds() == 360


def test_stale_below_three_times_heartbeat_is_rejected(monkeypatch):
    monkeypatch.setenv(cfg.HEARTBEAT_ENV_VAR, "30")
    monkeypatch.setenv(cfg.STALE_ENV_VAR, "89")

    with pytest.raises(cfg.InvalidTimingConfigError, match="at least 3x"):
        cfg.get_job_stale_seconds()


def test_stale_at_exactly_three_times_heartbeat_is_accepted(monkeypatch):
    monkeypatch.setenv(cfg.HEARTBEAT_ENV_VAR, "30")
    monkeypatch.setenv(cfg.STALE_ENV_VAR, "90")

    assert cfg.get_job_stale_seconds() == 90


@pytest.mark.parametrize("value", ["abc", "0", "-5"])
def test_non_positive_or_non_integer_values_are_rejected(monkeypatch, value):
    monkeypatch.setenv(cfg.HEARTBEAT_ENV_VAR, value)

    with pytest.raises(cfg.InvalidTimingConfigError):
        cfg.get_job_heartbeat_seconds()


def test_lock_timeout_is_configurable(monkeypatch):
    monkeypatch.setenv(cfg.LOCK_TIMEOUT_ENV_VAR, "45")

    assert cfg.get_scheduler_lock_timeout_seconds() == 45


def test_retention_max_seconds_defaults_to_thirty_minutes():
    assert cfg.get_retention_max_seconds() == 1800


def test_retention_max_seconds_is_configurable(monkeypatch):
    monkeypatch.setenv(cfg.RETENTION_MAX_ENV_VAR, "60")

    assert cfg.get_retention_max_seconds() == 60


@pytest.mark.parametrize("value", ["abc", "0", "-5"])
def test_invalid_retention_max_seconds_is_rejected(monkeypatch, value):
    monkeypatch.setenv(cfg.RETENTION_MAX_ENV_VAR, value)

    with pytest.raises(cfg.InvalidTimingConfigError):
        cfg.get_retention_max_seconds()
