from repo_version_monitor.db import VersionStore


def test_product_round_trip(tmp_path) -> None:
    store = VersionStore(tmp_path / "versions.sqlite3")
    store.initialize()

    store.upsert_product("httpx", "encode/httpx", "0.28.0")
    product = store.get_product("encode/httpx")

    assert product is not None
    assert product.name == "httpx"
    assert product.latest_tag == "0.28.0"


def test_event_round_trip(tmp_path) -> None:
    store = VersionStore(tmp_path / "versions.sqlite3")
    store.initialize()
    store.upsert_product("httpx", "encode/httpx", "0.27.0")

    event_id = store.record_event("encode/httpx", "0.27.0", "0.28.0")
    store.mark_notified(event_id)

    assert event_id > 0

