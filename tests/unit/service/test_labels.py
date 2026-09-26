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

import datetime as dt
from datetime import datetime

from starrocks_br import labels
from starrocks_br.store.models import BackupHistory


def _add_history(session, cluster_id, label):
    session.add(
        BackupHistory(
            cluster_id=cluster_id,
            label=label,
            backup_type="full",
            status="FINISHED",
            repository="repo",
            started_at=dt.datetime(2025, 1, 1),
            finished_at=dt.datetime(2025, 1, 1, 1),
        )
    )
    session.commit()


def test_should_generate_auto_label_with_no_conflicts(sqlite_session, make_cluster):
    cluster = make_cluster()

    result = labels.determine_backup_label(sqlite_session, cluster.id, "incremental", "mydb")

    today = datetime.now().strftime("%Y%m%d")
    assert result == f"mydb_{today}_incremental"


def test_should_generate_auto_label_with_conflicts(sqlite_session, make_cluster):
    cluster = make_cluster()
    today = datetime.now().strftime("%Y%m%d")
    base_label = f"mydb_{today}_incremental"
    _add_history(sqlite_session, cluster.id, base_label)

    result = labels.determine_backup_label(sqlite_session, cluster.id, "incremental", "mydb")

    assert result == f"{base_label}_r1"


def test_should_generate_auto_label_with_multiple_conflicts(sqlite_session, make_cluster):
    cluster = make_cluster()
    today = datetime.now().strftime("%Y%m%d")
    base_label = f"mydb_{today}_full"
    for candidate in (base_label, f"{base_label}_r1", f"{base_label}_r2"):
        _add_history(sqlite_session, cluster.id, candidate)

    result = labels.determine_backup_label(sqlite_session, cluster.id, "full", "mydb")

    assert result == f"{base_label}_r3"


def test_should_handle_custom_label_with_no_conflicts(sqlite_session, make_cluster):
    cluster = make_cluster()

    result = labels.determine_backup_label(sqlite_session, cluster.id, "incremental", "mydb", "my-custom-backup")

    assert result == "my-custom-backup"


def test_should_handle_custom_label_with_conflicts(sqlite_session, make_cluster):
    cluster = make_cluster()
    _add_history(sqlite_session, cluster.id, "my-custom-backup")

    result = labels.determine_backup_label(sqlite_session, cluster.id, "incremental", "mydb", "my-custom-backup")

    assert result == "my-custom-backup_r1"


def test_should_handle_custom_label_with_multiple_conflicts(sqlite_session, make_cluster):
    cluster = make_cluster()
    for candidate in ("release-backup", "release-backup_r1", "release-backup_r2"):
        _add_history(sqlite_session, cluster.id, candidate)

    result = labels.determine_backup_label(sqlite_session, cluster.id, "full", "mydb", "release-backup")

    assert result == "release-backup_r3"


def test_should_handle_different_backup_types(sqlite_session, make_cluster):
    cluster = make_cluster()

    inc_result = labels.determine_backup_label(sqlite_session, cluster.id, "incremental", "mydb")
    full_result = labels.determine_backup_label(sqlite_session, cluster.id, "full", "mydb")

    today = datetime.now().strftime("%Y%m%d")
    assert inc_result == f"mydb_{today}_incremental"
    assert full_result == f"mydb_{today}_full"


def test_should_handle_different_database_names(sqlite_session, make_cluster):
    cluster = make_cluster()

    result1 = labels.determine_backup_label(sqlite_session, cluster.id, "incremental", "db1")
    result2 = labels.determine_backup_label(sqlite_session, cluster.id, "incremental", "db2")

    today = datetime.now().strftime("%Y%m%d")
    assert result1 == f"db1_{today}_incremental"
    assert result2 == f"db2_{today}_incremental"


def test_should_handle_none_custom_name(sqlite_session, make_cluster):
    cluster = make_cluster()

    result = labels.determine_backup_label(sqlite_session, cluster.id, "full", "mydb", None)

    today = datetime.now().strftime("%Y%m%d")
    assert result == f"mydb_{today}_full"


def test_should_handle_empty_custom_name(sqlite_session, make_cluster):
    cluster = make_cluster()

    result = labels.determine_backup_label(sqlite_session, cluster.id, "full", "mydb", "")

    today = datetime.now().strftime("%Y%m%d")
    assert result == f"mydb_{today}_full"


def test_should_handle_database_query_error(sqlite_session, make_cluster, mocker):
    cluster = make_cluster()
    mocker.patch.object(sqlite_session, "scalars", side_effect=Exception("Database connection failed"))

    result = labels.determine_backup_label(sqlite_session, cluster.id, "incremental", "mydb", "my-backup")

    assert result == "my-backup"


def test_labels_scoped_by_cluster(sqlite_session, make_cluster):
    cluster_a = make_cluster("cluster-a")
    cluster_b = make_cluster("cluster-b")
    _add_history(sqlite_session, cluster_a.id, "shared-label")

    # cluster_b has no conflicting history row, so it gets the base label unchanged.
    result = labels.determine_backup_label(sqlite_session, cluster_b.id, "full", "mydb", "shared-label")

    assert result == "shared-label"
