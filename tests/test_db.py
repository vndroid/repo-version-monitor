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

    removed = store.sync_config_hash("hash-1", {"encode/httpx"})

    assert removed == []
    assert store.get_meta("config_hash") == "hash-1"
    assert store.get_product("gone/gone") is not None


def test_sync_config_hash_unchanged_is_noop(tmp_path) -> None:
    store = VersionStore(tmp_path / "versions.sqlite3")
    store.sync_config_hash("hash-1", {"a/a"})
    store.upsert_product("old", "gone/gone", "1.0")

    assert store.sync_config_hash("hash-1", {"a/a"}) == []
    assert store.get_product("gone/gone") is not None


def test_sync_config_hash_change_prunes_stale_products(tmp_path) -> None:
    store = VersionStore(tmp_path / "versions.sqlite3")
    store.sync_config_hash("hash-1", {"encode/httpx", "gone/gone"})
    store.upsert_product("httpx", "encode/httpx", "0.28.0")
    store.upsert_product("old", "gone/gone", "1.0")
    store.record_event("gone/gone", None, "1.0")

    removed = store.sync_config_hash("hash-2", {"encode/httpx"})

    assert removed == ["gone/gone"]
    assert store.get_product("gone/gone") is None
    assert store.get_product("encode/httpx") is not None
    assert store.list_unnotified_events() == []
    assert store.get_meta("config_hash") == "hash-2"
