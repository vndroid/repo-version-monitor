from repo_version_monitor.db import VersionStore


def test_product_round_trip(tmp_path) -> None:
    store = VersionStore(tmp_path / "versions.sqlite3")
    store.initialize()

    store.upsert_product("httpx", "encode/httpx", "0.28.0")
    product = store.get_product("encode/httpx")

    assert product is not None
    assert product.name == "httpx"
    assert product.latest_tag == "0.28.0"


def test_list_products(tmp_path) -> None:
    store = VersionStore(tmp_path / "versions.sqlite3")
    store.initialize()

    store.upsert_product("httpx", "encode/httpx", "0.28.0")
    store.upsert_product("FastAPI", "fastapi/fastapi", "0.116.0")

    products = store.list_products()

    assert [product.repository for product in products] == ["fastapi/fastapi", "encode/httpx"]


def test_event_round_trip(tmp_path) -> None:
    store = VersionStore(tmp_path / "versions.sqlite3")
    store.initialize()
    store.upsert_product("httpx", "encode/httpx", "0.27.0")

    event_id = store.record_event("encode/httpx", "0.27.0", "0.28.0")
    store.mark_notified(event_id)

    assert event_id > 0


def test_list_unnotified_events(tmp_path) -> None:
    store = VersionStore(tmp_path / "versions.sqlite3")
    store.initialize()
    store.upsert_product("httpx", "encode/httpx", "0.28.0")

    notified_id = store.record_event("encode/httpx", "0.26.0", "0.27.0")
    store.mark_notified(notified_id)
    pending_id = store.record_event("encode/httpx", "0.27.0", "0.28.0")

    events = store.list_unnotified_events()

    assert [event.event_id for event in events] == [pending_id]
    assert events[0].product_name == "httpx"
    assert events[0].old_tag == "0.27.0"
    assert events[0].new_tag == "0.28.0"

    store.mark_notified(pending_id)
    assert store.list_unnotified_events() == []


def test_list_unnotified_events_without_db_file(tmp_path) -> None:
    store = VersionStore(tmp_path / "missing.sqlite3")

    assert store.list_unnotified_events() == []


def test_sync_config_hash_first_time_records_without_pruning(tmp_path) -> None:
    store = VersionStore(tmp_path / "versions.sqlite3")
    store.initialize()
    store.upsert_product("old", "gone/gone", "1.0")

    removed = store.sync_config_hash("hash-1", {("encode/httpx", None)})

    assert removed == []
    assert store.get_meta("config_hash") == "hash-1"
    assert store.get_product("gone/gone") is not None


def test_sync_config_hash_unchanged_is_noop(tmp_path) -> None:
    store = VersionStore(tmp_path / "versions.sqlite3")
    store.sync_config_hash("hash-1", {("a/a", None)})
    store.upsert_product("old", "gone/gone", "1.0")

    assert store.sync_config_hash("hash-1", {("a/a", None)}) == []
    assert store.get_product("gone/gone") is not None


def test_sync_config_hash_change_prunes_stale_products(tmp_path) -> None:
    store = VersionStore(tmp_path / "versions.sqlite3")
    store.sync_config_hash("hash-1", {("encode/httpx", None), ("gone/gone", None)})
    store.upsert_product("httpx", "encode/httpx", "0.28.0")
    store.upsert_product("old", "gone/gone", "1.0")
    store.record_event("gone/gone", None, "1.0")

    removed = store.sync_config_hash("hash-2", {("encode/httpx", None)})

    assert removed == ["gone/gone"]
    assert store.get_product("gone/gone") is None
    assert store.get_product("encode/httpx") is not None
    assert store.list_unnotified_events() == []
    assert store.get_meta("config_hash") == "hash-2"


def test_branch_stored_as_separate_column(tmp_path) -> None:
    import sqlite3

    store = VersionStore(tmp_path / "versions.sqlite3")
    store.initialize()
    store.upsert_product("pg", "postgres/postgres", "13.9", branch="v13")
    store.upsert_product("pg", "postgres/postgres", "16.1")

    # Two independent rows for the same repository.
    assert store.get_product("postgres/postgres", "v13").latest_tag == "13.9"
    assert store.get_product("postgres/postgres").latest_tag == "16.1"

    # Raw storage: repository stays clean, branch column holds '@v13'.
    connection = sqlite3.connect(tmp_path / "versions.sqlite3")
    rows = connection.execute(
        "SELECT repository, branch FROM products ORDER BY branch"
    ).fetchall()
    connection.close()
    assert rows == [("postgres/postgres", ""), ("postgres/postgres", "@v13")]


def test_prune_only_removes_matching_branch(tmp_path) -> None:
    store = VersionStore(tmp_path / "versions.sqlite3")
    store.initialize()
    store.upsert_product("pg13", "postgres/postgres", "13.9", branch="v13")
    store.upsert_product("pg", "postgres/postgres", "16.1")

    removed = store.prune_products_not_in({("postgres/postgres", None)})

    assert removed == ["postgres/postgres@v13"]
    assert store.get_product("postgres/postgres") is not None
    assert store.get_product("postgres/postgres", "v13") is None


def test_migrates_legacy_composite_keys(tmp_path) -> None:
    import sqlite3

    db_path = tmp_path / "versions.sqlite3"
    connection = sqlite3.connect(db_path)
    connection.executescript(
        """
        CREATE TABLE products (
            repository TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            latest_tag TEXT,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE tag_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repository TEXT NOT NULL,
            old_tag TEXT,
            new_tag TEXT NOT NULL,
            detected_at TEXT NOT NULL,
            notified_at TEXT
        );
        INSERT INTO products VALUES ('grafana/grafana@13', 'grafana13', '13.2', 't');
        INSERT INTO products VALUES ('encode/httpx', 'httpx', '0.28.0', 't');
        INSERT INTO tag_events (repository, old_tag, new_tag, detected_at)
        VALUES ('grafana/grafana@13', NULL, '13.2', 't');
        """
    )
    connection.commit()
    connection.close()

    store = VersionStore(db_path)
    store.initialize()

    migrated = store.get_product("grafana/grafana", "13")
    assert migrated is not None
    assert migrated.latest_tag == "13.2"
    assert store.get_product("encode/httpx") is not None
    events = store.list_unnotified_events()
    assert events[0].repository == "grafana/grafana"
    assert events[0].branch == "13"
