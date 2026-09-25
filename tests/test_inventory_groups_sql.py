import pytest

from starrocks_br.inventory_groups import (
    InventoryGroupNotFoundError,
    InventoryMembershipConflictError,
    InventoryMembershipNotFoundError,
    add_membership,
    add_memberships_bulk,
    delete_group,
    get_group,
    group_exists,
    list_groups,
    remove_membership,
)


def test_list_groups_parses_tuple_rows(mocker):
    db = mocker.Mock()
    db.query.return_value = [("prod", 2), ("staging", 1)]

    result = list_groups(db)

    assert result == [
        {"name": "prod", "table_count": 2},
        {"name": "staging", "table_count": 1},
    ]


def test_list_groups_parses_dict_rows(mocker):
    db = mocker.Mock()
    db.query.return_value = [{"inventory_group": "prod", "count": 2}]

    result = list_groups(db)

    assert result == [{"name": "prod", "table_count": 2}]


def test_group_exists_true_when_rows_found(mocker):
    db = mocker.Mock()
    db.query.return_value = [(1,)]

    assert group_exists(db, "prod") is True


def test_group_exists_false_when_no_rows(mocker):
    db = mocker.Mock()
    db.query.return_value = []

    assert group_exists(db, "unknown") is False


def test_group_exists_quotes_group_name_safely(mocker):
    db = mocker.Mock()
    db.query.return_value = []

    group_exists(db, "o'brien")

    executed_sql = db.query.call_args[0][0]
    assert "'o''brien'" in executed_sql


def test_get_group_returns_dict_shape(mocker):
    db = mocker.Mock()
    db.query.return_value = [("sales_db", "orders", "2025-01-01 00:00:00", "2025-01-01 00:00:00")]

    result = get_group(db, "prod")

    assert result == [
        {
            "database": "sales_db",
            "table": "orders",
            "created_at": "2025-01-01 00:00:00",
            "updated_at": "2025-01-01 00:00:00",
        }
    ]


def test_get_group_returns_empty_list_when_no_rows(mocker):
    db = mocker.Mock()
    db.query.return_value = []

    assert get_group(db, "unknown") == []


def test_add_membership_inserts_when_absent(mocker):
    db = mocker.Mock()
    db.query.return_value = []

    result = add_membership(db, "prod", "sales_db", "orders")

    assert result == {"group": "prod", "database": "sales_db", "table": "orders"}
    db.execute.assert_called_once()
    executed_sql = db.execute.call_args[0][0]
    assert "INSERT INTO ops.table_inventory" in executed_sql
    assert "'prod'" in executed_sql
    assert "'sales_db'" in executed_sql
    assert "'orders'" in executed_sql


def test_add_membership_raises_conflict_when_present(mocker):
    db = mocker.Mock()
    db.query.return_value = [(1,)]

    with pytest.raises(InventoryMembershipConflictError):
        add_membership(db, "prod", "sales_db", "orders")

    db.execute.assert_not_called()


def test_add_membership_quotes_values_safely(mocker):
    db = mocker.Mock()
    db.query.return_value = []

    add_membership(db, "o'brien_group", "sales_db", "orders")

    executed_sql = db.execute.call_args[0][0]
    assert "'o''brien_group'" in executed_sql


def test_add_memberships_bulk_skips_conflicts(mocker):
    db = mocker.Mock()
    # First entry: not present (insert succeeds). Second entry: already present (skip).
    db.query.side_effect = [[], [(1,)]]

    result = add_memberships_bulk(db, "prod", [("sales_db", "orders"), ("sales_db", "customers")])

    assert result == [{"group": "prod", "database": "sales_db", "table": "orders"}]
    db.execute.assert_called_once()


def test_remove_membership_deletes_when_present(mocker):
    db = mocker.Mock()
    db.query.return_value = [(1,)]

    remove_membership(db, "prod", "sales_db", "orders")

    db.execute.assert_called_once()
    executed_sql = db.execute.call_args[0][0]
    assert "DELETE FROM ops.table_inventory" in executed_sql


def test_remove_membership_raises_not_found_when_absent(mocker):
    db = mocker.Mock()
    db.query.return_value = []

    with pytest.raises(InventoryMembershipNotFoundError):
        remove_membership(db, "prod", "sales_db", "orders")

    db.execute.assert_not_called()


def test_delete_group_deletes_and_returns_count(mocker):
    db = mocker.Mock()
    db.query.return_value = [(3,)]

    result = delete_group(db, "prod")

    assert result == 3
    db.execute.assert_called_once()
    executed_sql = db.execute.call_args[0][0]
    assert "DELETE FROM ops.table_inventory" in executed_sql


def test_delete_group_raises_not_found_when_zero_rows(mocker):
    db = mocker.Mock()
    db.query.return_value = [(0,)]

    with pytest.raises(InventoryGroupNotFoundError):
        delete_group(db, "unknown")

    db.execute.assert_not_called()
