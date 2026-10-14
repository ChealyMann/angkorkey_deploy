from flask_migrate import stamp, upgrade
from sqlalchemy import inspect


def test_migration_is_idempotent_over_create_all(app, db_session):
    # Mirrors production: create_all() already made every table, DB stamped at old head.
    stamp(revision="d0f67f256061")
    upgrade()  # must not raise "table already exists"
    tables = inspect(db_session.engine).get_table_names()
    for t in ("customer", "orders", "order_item", "order_status_log"):
        assert t in tables
    assert db_session.session.execute(db_session.text("select version_num from alembic_version")).scalar() == "7c1e4b2a9f30"


def test_migration_creates_tables_when_missing(app, db_session):
    for t in ("order_status_log", "order_item", "orders", "customer"):
        db_session.session.execute(db_session.text(f"DROP TABLE IF EXISTS {t}"))
    db_session.session.commit()
    stamp(revision="d0f67f256061")
    upgrade()
    assert "orders" in inspect(db_session.engine).get_table_names()
