from ... import utils


def submit(db, command: str) -> None:
    """Execute a BACKUP command against StarRocks."""
    db.execute(command)


def show_backup(db, database: str) -> list:
    """Run SHOW BACKUP FROM <database> and return the raw rows."""
    return db.query(f"SHOW BACKUP FROM {utils.quote_identifier(database)}")
