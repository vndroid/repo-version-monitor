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

    removed = store.sync_config_hash("hash-1", {("github", "", "encode/httpx", None)})

    assert removed == []
    assert store.get_meta("config_hash") == "hash-1"
    assert store.get_product("gone/gone") is not None


def test_sync_config_hash_unchanged_is_noop(tmp_path) -> None:
    store = VersionStore(tmp_path / "versions.sqlite3")
    store.sync_config_hash("hash-1", {("github", "", "a/a", None)})
    store.upsert_product("old", "gone/gone", "1.0")

    assert store.sync_config_hash("hash-1", {("github", "", "a/a", None)}) == []
    assert store.get_product("gone/gone") is not None


def test_sync_config_hash_change_prunes_stale_products(tmp_path) -> None:
    store = VersionStore(tmp_path / "versions.sqlite3")
    store.sync_config_hash(
        "hash-1", {("github", "", "encode/httpx", None), ("github", "", "gone/gone", None)}
    )
    store.upsert_product("httpx", "encode/httpx", "0.28.0")
    store.upsert_product("old", "gone/gone", "1.0")
    store.record_event("gone/gone", None, "1.0")

    removed = store.sync_config_hash("hash-2", {("github", "", "encode/httpx", None)})

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

    removed = store.prune_products_not_in({("github", "", "postgres/postgres", None)})

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


def test_same_repository_isolated_per_provider(tmp_path) -> None:
    store = VersionStore(tmp_path / "versions.sqlite3")
    store.initialize()
    store.upsert_product("hub", "acme/tool", "1.0.0")
    store.upsert_product("lab", "acme/tool", "2.0.0", provider="gitlab")

    assert store.get_product("acme/tool").latest_tag == "1.0.0"
    assert store.get_product("acme/tool", provider="gitlab").latest_tag == "2.0.0"

    removed = store.prune_products_not_in({("github", "", "acme/tool", None)})

    assert removed == ["gitlab:acme/tool"]
    assert store.get_product("acme/tool") is not None
    assert store.get_product("acme/tool", provider="gitlab") is None


def test_same_repository_isolated_per_instance(tmp_path) -> None:
    store = VersionStore(tmp_path / "versions.sqlite3")
    store.initialize()
    store.upsert_product("public", "acme/tool", "1.0.0", provider="gitlab")
    store.upsert_product(
        "internal", "acme/tool", "2.0.0", provider="gitlab", external_url="https://jihulab.com"
    )

    assert store.get_product("acme/tool", provider="gitlab").latest_tag == "1.0.0"
    assert (
        store.get_product(
            "acme/tool", provider="gitlab", external_url="https://jihulab.com"
        ).latest_tag
        == "2.0.0"
    )

    removed = store.prune_products_not_in({("gitlab", "", "acme/tool", None)})

    assert removed == ["gitlab:jihulab.com/acme/tool"]
    assert store.get_product("acme/tool", provider="gitlab") is not None
    assert (
        store.get_product("acme/tool", provider="gitlab", external_url="https://jihulab.com")
        is None
    )


def test_migrates_pre_external_url_schema(tmp_path) -> None:
    import sqlite3

    db_path = tmp_path / "versions.sqlite3"
    connection = sqlite3.connect(db_path)
    connection.executescript(
        """
        CREATE TABLE products (
            provider TEXT NOT NULL DEFAULT 'github',
            repository TEXT NOT NULL,
            branch TEXT NOT NULL DEFAULT '',
            name TEXT NOT NULL,
            latest_tag TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (provider, repository, branch)
        );
        CREATE TABLE tag_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL DEFAULT 'github',
            repository TEXT NOT NULL,
            branch TEXT NOT NULL DEFAULT '',
            old_tag TEXT,
            new_tag TEXT NOT NULL,
            detected_at TEXT NOT NULL,
            notified_at TEXT
        );
        INSERT INTO products VALUES ('gitlab', 'acme/tool', '@v13', 'tool13', '13.2', 't');
        INSERT INTO tag_events (provider, repository, branch, old_tag, new_tag, detected_at)
        VALUES ('gitlab', 'acme/tool', '@v13', NULL, '13.2', 't');
        """
    )
    connection.commit()
    connection.close()

    store = VersionStore(db_path)
    store.initialize()

    # Existing rows belong to the public instance.
    migrated = store.get_product("acme/tool", "v13", provider="gitlab")
    assert migrated is not None
    assert (migrated.latest_tag, migrated.external_url) == ("13.2", "")
    events = store.list_unnotified_events()
    assert (events[0].product_name, events[0].external_url) == ("tool13", "")


def test_migrates_pre_provider_schema(tmp_path) -> None:
    import sqlite3

    db_path = tmp_path / "versions.sqlite3"
    connection = sqlite3.connect(db_path)
    connection.executescript(
        """
        CREATE TABLE products (
            repository TEXT NOT NULL,
            branch TEXT NOT NULL DEFAULT '',
            name TEXT NOT NULL,
            latest_tag TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (repository, branch)
        );
        CREATE TABLE tag_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repository TEXT NOT NULL,
            branch TEXT NOT NULL DEFAULT '',
            old_tag TEXT,
            new_tag TEXT NOT NULL,
            detected_at TEXT NOT NULL,
            notified_at TEXT
        );
        INSERT INTO products VALUES ('encode/httpx', '', 'httpx', '0.28.0', 't');
        INSERT INTO products VALUES ('postgres/postgres', '@v13', 'pg13', '13.9', 't');
        INSERT INTO tag_events (repository, branch, old_tag, new_tag, detected_at)
        VALUES ('encode/httpx', '', '0.27.0', '0.28.0', 't');
        """
    )
    connection.commit()
    connection.close()

    store = VersionStore(db_path)
    store.initialize()

    # Old rows become github rows; keys keep working.
    assert store.get_product("encode/httpx").latest_tag == "0.28.0"
    assert store.get_product("postgres/postgres", "v13").latest_tag == "13.9"
    events = store.list_unnotified_events()
    assert events[0].provider == "github"
    assert events[0].new_tag == "0.28.0"
