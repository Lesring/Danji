from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

_engine = None
_session_factory = None
_url: str | None = None


class Base(DeclarativeBase):
    pass


def reset_engine() -> None:
    global _engine, _session_factory, _url
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
    _url = None


def _ensure_text_column(conn, table: str, column: str) -> None:
    tables = {row[0] for row in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")}
    if table not in tables:
        return
    columns = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")


def ensure_schema() -> None:
    engine = get_engine()
    _ensure_task_history(engine)
    with engine.begin() as conn:
        _ensure_text_column(conn, "sources", "summary_text")
        _ensure_text_column(conn, "customer_intakes", "summary_text")
        tables = {row[0] for row in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")}
        if "orders" not in tables:
            return
        columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(orders)")}
        if "deleted_at" not in columns:
            conn.exec_driver_sql("ALTER TABLE orders ADD COLUMN deleted_at VARCHAR(32)")
        if "delivered_qty" not in columns:
            conn.exec_driver_sql("ALTER TABLE orders ADD COLUMN delivered_qty INTEGER NOT NULL DEFAULT 0")


def _ensure_task_history(engine) -> None:
    raw = engine.raw_connection()
    try:
        cursor = raw.cursor()
        cursor.execute("PRAGMA foreign_keys=OFF")
        tables = {row[0] for row in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "tasks" not in tables:
            raw.commit()
            return
        info = list(cursor.execute("PRAGMA table_info(tasks)"))
        names = {row[1] for row in info}
        order_id = next(row for row in info if row[1] == "order_id")
        if order_id[3] == 0 and "order_no" in names:
            raw.commit()
            return
        if order_id[3] == 0:
            cursor.execute("ALTER TABLE tasks ADD COLUMN order_no VARCHAR(40) NOT NULL DEFAULT ''")
        else:
            cursor.execute(
                """
                CREATE TABLE tasks_archive (
                    id INTEGER PRIMARY KEY,
                    order_id INTEGER REFERENCES orders(id),
                    order_no VARCHAR(40) NOT NULL DEFAULT '',
                    suggestion_key VARCHAR(60) NOT NULL DEFAULT '',
                    title VARCHAR(200) NOT NULL,
                    owner VARCHAR(80) NOT NULL DEFAULT '',
                    due_date VARCHAR(10),
                    status VARCHAR(20) NOT NULL DEFAULT '待处理',
                    reason TEXT NOT NULL DEFAULT '',
                    event_ids_json TEXT NOT NULL DEFAULT '',
                    source_id INTEGER REFERENCES sources(id),
                    suggestion_id INTEGER,
                    created_at VARCHAR(32) NOT NULL,
                    updated_at VARCHAR(32) NOT NULL
                )
                """
            )
            cursor.execute(
                """
                INSERT INTO tasks_archive (
                    id, order_id, order_no, suggestion_key, title, owner, due_date, status, reason,
                    event_ids_json, source_id, suggestion_id, created_at, updated_at
                )
                SELECT
                    id, order_id,
                    COALESCE((SELECT order_no FROM orders WHERE orders.id = tasks.order_id), ''),
                    suggestion_key, title, owner, due_date, status, reason,
                    event_ids_json, source_id, suggestion_id, created_at, updated_at
                FROM tasks
                """
            )
            cursor.execute("DROP TABLE tasks")
            cursor.execute("ALTER TABLE tasks_archive RENAME TO tasks")
        cursor.execute(
            """
            UPDATE tasks
            SET order_no = COALESCE((SELECT order_no FROM orders WHERE orders.id = tasks.order_id), order_no)
            WHERE order_id IS NOT NULL AND order_no = ''
            """
        )
        raw.commit()
    finally:
        raw.close()


def get_engine():
    global _engine, _session_factory, _url
    url = get_settings().database_url
    if _engine is not None and _url == url:
        return _engine
    if _engine is not None:
        _engine.dispose()
    if url.startswith("sqlite:///"):
        path = url.removeprefix("sqlite:///")
        if path and path != ":memory:":
            from pathlib import Path

            Path(path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    _engine = engine
    _session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    _url = url
    return engine


def open_session() -> Session:
    get_engine()
    assert _session_factory is not None
    return _session_factory()


def get_db() -> Iterator[Session]:
    db = open_session()
    try:
        yield db
    finally:
        db.close()
