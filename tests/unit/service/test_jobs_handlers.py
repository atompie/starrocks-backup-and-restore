"""`jobs/handlers.py` is now just a dispatch table over `commands/*` (see
openspec/changes/establish-command-layer) - the orchestration tests that
used to live here moved to `test_commands_backup.py`/`test_commands_restore.py`/
calling the commands directly."""

from starrocks_br.commands.backup import run_backup_full, run_backup_incremental
from starrocks_br.commands.restore import run_restore
from starrocks_br.commands.retention import run_retention
from starrocks_br.commands.schedules import run_schedule_cleanup
from starrocks_br.jobs import handlers


def test_job_handlers_map_has_all_five_types_pointing_at_commands():
    assert handlers.JOB_HANDLERS == {
        "backup_full": run_backup_full,
        "backup_incremental": run_backup_incremental,
        "restore": run_restore,
        "schedule_cleanup": run_schedule_cleanup,
        "retention": run_retention,
    }
