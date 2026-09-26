import pytest

from starrocks_br.inventory_groups import (
    InventoryGroupAlreadyExistsError,
    InventoryGroupInUseError,
    InventoryGroupNotFoundError,
    InventoryMembershipConflictError,
    InventoryMembershipNotFoundError,
    add_membership,
    add_memberships_bulk,
    create_group,
    delete_group,
    get_group,
    get_group_id_by_name,
    group_exists,
    list_groups,
    remove_membership,
)
from starrocks_br.store.models import InventoryGroup, Schedule, TableInventory


def test_create_group_inserts_group_and_memberships(sqlite_session, make_cluster):
    cluster = make_cluster()

    created = create_group(sqlite_session, cluster.id, "prod", [("sales_db", "orders")])

    assert created["name"] == "prod"
    assert isinstance(created["id"], int)
    assert sqlite_session.query(InventoryGroup).filter_by(cluster_id=cluster.id).count() == 1
    assert sqlite_session.query(TableInventory).filter_by(cluster_id=cluster.id).count() == 1


def test_create_group_raises_when_name_exists(sqlite_session, make_cluster, make_group):
    cluster = make_cluster()
    make_group(cluster.id, "prod")

    with pytest.raises(InventoryGroupAlreadyExistsError):
        create_group(sqlite_session, cluster.id, "prod", [("sales_db", "orders")])


def test_get_group_id_by_name_resolves_existing_group(sqlite_session, make_cluster, make_group):
    cluster = make_cluster()
    group_id = make_group(cluster.id, "prod")

    assert get_group_id_by_name(sqlite_session, cluster.id, "prod") == group_id


def test_get_group_id_by_name_raises_when_unknown(sqlite_session, make_cluster):
    cluster = make_cluster()

    with pytest.raises(InventoryGroupNotFoundError):
        get_group_id_by_name(sqlite_session, cluster.id, "unknown")


def test_list_groups_returns_ids_names_and_counts(sqlite_session, make_cluster, make_group):
    cluster = make_cluster()
    prod_id = make_group(cluster.id, "prod")
    staging_id = make_group(cluster.id, "staging")
    add_membership(sqlite_session, cluster.id, prod_id, "sales_db", "orders")
    add_membership(sqlite_session, cluster.id, prod_id, "sales_db", "customers")
    add_membership(sqlite_session, cluster.id, staging_id, "sales_db", "orders")

    result = list_groups(sqlite_session, cluster.id)

    assert result == [
        {"id": prod_id, "name": "prod", "table_count": 2},
        {"id": staging_id, "name": "staging", "table_count": 1},
    ]


def test_list_groups_scoped_by_cluster(sqlite_session, make_cluster, make_group):
    cluster_a = make_cluster("cluster-a")
    cluster_b = make_cluster("cluster-b")
    group_id = make_group(cluster_a.id, "prod")
    add_membership(sqlite_session, cluster_a.id, group_id, "sales_db", "orders")

    assert list_groups(sqlite_session, cluster_b.id) == []


def test_group_exists_true_when_group_row_present(sqlite_session, make_cluster, make_group):
    cluster = make_cluster()
    group_id = make_group(cluster.id, "prod")

    assert group_exists(sqlite_session, cluster.id, group_id) is True


def test_group_exists_false_when_no_such_id(sqlite_session, make_cluster):
    cluster = make_cluster()

    assert group_exists(sqlite_session, cluster.id, 999) is False


def test_get_group_returns_dict_shape(sqlite_session, make_cluster, make_group):
    cluster = make_cluster()
    group_id = make_group(cluster.id, "prod")
    add_membership(sqlite_session, cluster.id, group_id, "sales_db", "orders")

    result = get_group(sqlite_session, cluster.id, group_id)

    assert len(result) == 1
    assert result[0]["database"] == "sales_db"
    assert result[0]["table"] == "orders"
    assert result[0]["created_at"]
    assert result[0]["updated_at"]


def test_get_group_returns_empty_list_when_no_memberships(sqlite_session, make_cluster, make_group):
    cluster = make_cluster()
    group_id = make_group(cluster.id, "prod")

    assert get_group(sqlite_session, cluster.id, group_id) == []


def test_get_group_raises_not_found_when_id_unknown(sqlite_session, make_cluster):
    cluster = make_cluster()

    with pytest.raises(InventoryGroupNotFoundError):
        get_group(sqlite_session, cluster.id, 999)


def test_add_membership_inserts_when_absent(sqlite_session, make_cluster, make_group):
    cluster = make_cluster()
    group_id = make_group(cluster.id, "prod")

    result = add_membership(sqlite_session, cluster.id, group_id, "sales_db", "orders")

    assert result == {"group_id": group_id, "database": "sales_db", "table": "orders"}
    row = sqlite_session.query(TableInventory).filter_by(cluster_id=cluster.id).one()
    assert row.inventory_group_id == group_id
    assert row.database_name == "sales_db"
    assert row.table_name == "orders"


def test_add_membership_raises_conflict_when_present(sqlite_session, make_cluster, make_group):
    cluster = make_cluster()
    group_id = make_group(cluster.id, "prod")
    add_membership(sqlite_session, cluster.id, group_id, "sales_db", "orders")

    with pytest.raises(InventoryMembershipConflictError):
        add_membership(sqlite_session, cluster.id, group_id, "sales_db", "orders")

    assert sqlite_session.query(TableInventory).filter_by(cluster_id=cluster.id).count() == 1


def test_add_membership_scoped_by_cluster(sqlite_session, make_cluster, make_group):
    cluster_a = make_cluster("cluster-a")
    cluster_b = make_cluster("cluster-b")
    group_a_id = make_group(cluster_a.id, "prod")
    group_b_id = make_group(cluster_b.id, "prod")
    add_membership(sqlite_session, cluster_a.id, group_a_id, "sales_db", "orders")

    # Same (group name, database, table) on a different cluster's own group id is not a conflict.
    result = add_membership(sqlite_session, cluster_b.id, group_b_id, "sales_db", "orders")

    assert result == {"group_id": group_b_id, "database": "sales_db", "table": "orders"}


def test_add_memberships_bulk_skips_conflicts(sqlite_session, make_cluster, make_group):
    cluster = make_cluster()
    group_id = make_group(cluster.id, "prod")
    add_membership(sqlite_session, cluster.id, group_id, "sales_db", "customers")

    result = add_memberships_bulk(
        sqlite_session, cluster.id, group_id, [("sales_db", "orders"), ("sales_db", "customers")]
    )

    assert result == [{"group_id": group_id, "database": "sales_db", "table": "orders"}]
    assert sqlite_session.query(TableInventory).filter_by(cluster_id=cluster.id).count() == 2


def test_remove_membership_deletes_when_present(sqlite_session, make_cluster, make_group):
    cluster = make_cluster()
    group_id = make_group(cluster.id, "prod")
    add_membership(sqlite_session, cluster.id, group_id, "sales_db", "orders")

    remove_membership(sqlite_session, cluster.id, group_id, "sales_db", "orders")

    assert sqlite_session.query(TableInventory).filter_by(cluster_id=cluster.id).count() == 0


def test_remove_membership_raises_not_found_when_absent(sqlite_session, make_cluster, make_group):
    cluster = make_cluster()
    group_id = make_group(cluster.id, "prod")

    with pytest.raises(InventoryMembershipNotFoundError):
        remove_membership(sqlite_session, cluster.id, group_id, "sales_db", "orders")


def test_delete_group_deletes_memberships_and_group_and_returns_count(
    sqlite_session, make_cluster, make_group
):
    cluster = make_cluster()
    group_id = make_group(cluster.id, "prod")
    add_membership(sqlite_session, cluster.id, group_id, "sales_db", "orders")
    add_membership(sqlite_session, cluster.id, group_id, "sales_db", "customers")

    result = delete_group(sqlite_session, cluster.id, group_id)

    assert result == 2
    assert sqlite_session.query(TableInventory).filter_by(cluster_id=cluster.id).count() == 0
    assert sqlite_session.get(InventoryGroup, group_id) is None


def test_delete_group_raises_not_found_when_id_unknown(sqlite_session, make_cluster):
    cluster = make_cluster()

    with pytest.raises(InventoryGroupNotFoundError):
        delete_group(sqlite_session, cluster.id, 999)


def test_delete_group_raises_in_use_when_schedule_references_it(sqlite_session, make_cluster, make_group):
    import datetime

    cluster = make_cluster()
    group_id = make_group(cluster.id, "prod")
    schedule = Schedule(
        cluster_id=cluster.id,
        job_type="backup_full",
        inventory_group_id=group_id,
        repository="repo",
        cadence="0 1 * * *",
        next_run_at=datetime.datetime.now(datetime.timezone.utc),
    )
    sqlite_session.add(schedule)
    sqlite_session.commit()

    with pytest.raises(InventoryGroupInUseError, match=str(schedule.id)):
        delete_group(sqlite_session, cluster.id, group_id)

    # Nothing was deleted.
    assert sqlite_session.get(InventoryGroup, group_id) is not None
