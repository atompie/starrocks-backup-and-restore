import pytest

from starrocks_br.inventory_groups import (
    InventoryGroupNotFoundError,
    InventoryMembershipConflictError,
    InventoryMembershipNotFoundError,
    add_membership,
    add_memberships_bulk,
    bootstrap_table_inventory,
    delete_group,
    get_group,
    group_exists,
    list_groups,
    remove_membership,
)
from starrocks_br.store.models import TableInventory


def test_list_groups_returns_counts(sqlite_session, make_cluster):
    cluster = make_cluster()
    add_membership(sqlite_session, cluster.id, "prod", "sales_db", "orders")
    add_membership(sqlite_session, cluster.id, "prod", "sales_db", "customers")
    add_membership(sqlite_session, cluster.id, "staging", "sales_db", "orders")

    result = list_groups(sqlite_session, cluster.id)

    assert result == [
        {"name": "prod", "table_count": 2},
        {"name": "staging", "table_count": 1},
    ]


def test_list_groups_scoped_by_cluster(sqlite_session, make_cluster):
    cluster_a = make_cluster("cluster-a")
    cluster_b = make_cluster("cluster-b")
    add_membership(sqlite_session, cluster_a.id, "prod", "sales_db", "orders")

    assert list_groups(sqlite_session, cluster_b.id) == []


def test_group_exists_true_when_rows_found(sqlite_session, make_cluster):
    cluster = make_cluster()
    add_membership(sqlite_session, cluster.id, "prod", "sales_db", "orders")

    assert group_exists(sqlite_session, cluster.id, "prod") is True


def test_group_exists_false_when_no_rows(sqlite_session, make_cluster):
    cluster = make_cluster()

    assert group_exists(sqlite_session, cluster.id, "unknown") is False


def test_get_group_returns_dict_shape(sqlite_session, make_cluster):
    cluster = make_cluster()
    add_membership(sqlite_session, cluster.id, "prod", "sales_db", "orders")

    result = get_group(sqlite_session, cluster.id, "prod")

    assert len(result) == 1
    assert result[0]["database"] == "sales_db"
    assert result[0]["table"] == "orders"
    assert result[0]["created_at"]
    assert result[0]["updated_at"]


def test_get_group_returns_empty_list_when_no_rows(sqlite_session, make_cluster):
    cluster = make_cluster()

    assert get_group(sqlite_session, cluster.id, "unknown") == []


def test_add_membership_inserts_when_absent(sqlite_session, make_cluster):
    cluster = make_cluster()

    result = add_membership(sqlite_session, cluster.id, "prod", "sales_db", "orders")

    assert result == {"group": "prod", "database": "sales_db", "table": "orders"}
    row = sqlite_session.query(TableInventory).filter_by(cluster_id=cluster.id).one()
    assert row.inventory_group == "prod"
    assert row.database_name == "sales_db"
    assert row.table_name == "orders"


def test_add_membership_raises_conflict_when_present(sqlite_session, make_cluster):
    cluster = make_cluster()
    add_membership(sqlite_session, cluster.id, "prod", "sales_db", "orders")

    with pytest.raises(InventoryMembershipConflictError):
        add_membership(sqlite_session, cluster.id, "prod", "sales_db", "orders")

    assert sqlite_session.query(TableInventory).filter_by(cluster_id=cluster.id).count() == 1


def test_add_membership_scoped_by_cluster(sqlite_session, make_cluster):
    cluster_a = make_cluster("cluster-a")
    cluster_b = make_cluster("cluster-b")
    add_membership(sqlite_session, cluster_a.id, "prod", "sales_db", "orders")

    # Same (group, database, table) on a different cluster is not a conflict.
    result = add_membership(sqlite_session, cluster_b.id, "prod", "sales_db", "orders")

    assert result == {"group": "prod", "database": "sales_db", "table": "orders"}


def test_add_memberships_bulk_skips_conflicts(sqlite_session, make_cluster):
    cluster = make_cluster()
    add_membership(sqlite_session, cluster.id, "prod", "sales_db", "customers")

    result = add_memberships_bulk(
        sqlite_session, cluster.id, "prod", [("sales_db", "orders"), ("sales_db", "customers")]
    )

    assert result == [{"group": "prod", "database": "sales_db", "table": "orders"}]
    assert sqlite_session.query(TableInventory).filter_by(cluster_id=cluster.id).count() == 2


def test_remove_membership_deletes_when_present(sqlite_session, make_cluster):
    cluster = make_cluster()
    add_membership(sqlite_session, cluster.id, "prod", "sales_db", "orders")

    remove_membership(sqlite_session, cluster.id, "prod", "sales_db", "orders")

    assert sqlite_session.query(TableInventory).filter_by(cluster_id=cluster.id).count() == 0


def test_remove_membership_raises_not_found_when_absent(sqlite_session, make_cluster):
    cluster = make_cluster()

    with pytest.raises(InventoryMembershipNotFoundError):
        remove_membership(sqlite_session, cluster.id, "prod", "sales_db", "orders")


def test_delete_group_deletes_and_returns_count(sqlite_session, make_cluster):
    cluster = make_cluster()
    add_membership(sqlite_session, cluster.id, "prod", "sales_db", "orders")
    add_membership(sqlite_session, cluster.id, "prod", "sales_db", "customers")

    result = delete_group(sqlite_session, cluster.id, "prod")

    assert result == 2
    assert sqlite_session.query(TableInventory).filter_by(cluster_id=cluster.id).count() == 0


def test_delete_group_raises_not_found_when_zero_rows(sqlite_session, make_cluster):
    cluster = make_cluster()

    with pytest.raises(InventoryGroupNotFoundError):
        delete_group(sqlite_session, cluster.id, "unknown")


def test_bootstrap_table_inventory_adds_entries(sqlite_session, make_cluster):
    cluster = make_cluster()

    bootstrap_table_inventory(sqlite_session, cluster.id, [("prod", "sales_db", "orders")])

    assert group_exists(sqlite_session, cluster.id, "prod") is True


def test_bootstrap_table_inventory_is_idempotent(sqlite_session, make_cluster):
    cluster = make_cluster()
    add_membership(sqlite_session, cluster.id, "prod", "sales_db", "orders")

    # Re-running with an already-existing entry is a no-op, not an error.
    bootstrap_table_inventory(sqlite_session, cluster.id, [("prod", "sales_db", "orders")])

    assert sqlite_session.query(TableInventory).filter_by(cluster_id=cluster.id).count() == 1


def test_bootstrap_table_inventory_handles_empty_entries(sqlite_session, make_cluster):
    cluster = make_cluster()

    bootstrap_table_inventory(sqlite_session, cluster.id, [])

    assert sqlite_session.query(TableInventory).filter_by(cluster_id=cluster.id).count() == 0
