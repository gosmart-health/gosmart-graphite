import ssl
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from neo4j import NotificationDisabledClassification, NotificationMinimumSeverity
from neo4j.exceptions import ServiceUnavailable

from gosmart_graphite.db.neo4j import Neo4jClient, _resolve_driver_uri_and_tls


@pytest.mark.anyio
async def test_verify_connectivity_success():
    """Verify that verify_connectivity returns True when the driver succeeds."""
    client = Neo4jClient()
    mock_driver = AsyncMock()
    mock_driver.verify_connectivity = AsyncMock(return_value=None)

    with patch("gosmart_graphite.db.neo4j.AsyncGraphDatabase.driver", return_value=mock_driver):
        is_connected = await client.verify_connectivity()
        assert is_connected is True
        mock_driver.verify_connectivity.assert_awaited_once()

    await client.close()


def test_resolve_driver_uri_and_tls_schemes() -> None:
    """Verify '+s://' URIs inject strict TLS context with certifi while plain schemes stay untouched."""
    plain_uri, plain_kwargs = _resolve_driver_uri_and_tls("bolt://localhost:7687")
    assert plain_uri == "bolt://localhost:7687"
    assert plain_kwargs == {}

    aura_uri, aura_kwargs = _resolve_driver_uri_and_tls("neo4j+s://example.databases.neo4j.io")
    assert aura_uri == "neo4j://example.databases.neo4j.io"
    ctx = aura_kwargs.get("ssl_context")
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


@pytest.mark.anyio
async def test_verify_connectivity_failure():
    """Verify that verify_connectivity gracefully returns False when connection fails."""
    client = Neo4jClient()
    mock_driver = AsyncMock()
    mock_driver.verify_connectivity = AsyncMock(side_effect=ServiceUnavailable("Connection refused"))

    with patch("gosmart_graphite.db.neo4j.AsyncGraphDatabase.driver", return_value=mock_driver):
        is_connected = await client.verify_connectivity()
        assert is_connected is False

    await client.close()


@pytest.mark.anyio
async def test_driver_notification_suppression_config():
    """Verify that both async and sync drivers configure notification suppression for schema notices."""
    client = Neo4jClient()
    mock_async_driver = AsyncMock()
    mock_sync_driver = MagicMock()

    with patch(
        "gosmart_graphite.db.neo4j.AsyncGraphDatabase.driver",
        return_value=mock_async_driver,
    ) as mock_async_factory:
        await client.connect()
        _, async_kwargs = mock_async_factory.call_args
        assert async_kwargs.get("notifications_min_severity") == NotificationMinimumSeverity.WARNING
        assert async_kwargs.get("notifications_disabled_classifications") == [NotificationDisabledClassification.SCHEMA]

    with patch("gosmart_graphite.db.neo4j.GraphDatabase.driver", return_value=mock_sync_driver) as mock_sync_factory:
        client.get_sync_driver()
        _, sync_kwargs = mock_sync_factory.call_args
        assert sync_kwargs.get("notifications_min_severity") == NotificationMinimumSeverity.WARNING
        assert sync_kwargs.get("notifications_disabled_classifications") == [NotificationDisabledClassification.SCHEMA]

    await client.close()


def test_multi_loop_async_driver_isolation() -> None:
    """Verify Neo4jClient isolates async drivers per event loop and closes only the active loop's driver."""
    import asyncio
    import threading

    client = Neo4jClient()
    driver_main = AsyncMock(name="driver_main")
    driver_worker = AsyncMock(name="driver_worker")
    created_drivers = [driver_main, driver_worker]

    loop_main = asyncio.new_event_loop()
    loop_worker = asyncio.new_event_loop()
    try:
        with patch("gosmart_graphite.db.neo4j.AsyncGraphDatabase.driver", side_effect=created_drivers) as mock_factory:
            # Main loop acquires driver_main
            got_main_1 = loop_main.run_until_complete(client.get_async_driver())
            assert got_main_1 is driver_main

            # Worker thread with its own event loop acquires driver_worker without discarding driver_main
            worker_result: list[object] = []

            def _worker() -> None:
                asyncio.set_event_loop(loop_worker)
                got_worker = loop_worker.run_until_complete(client.get_async_driver())
                worker_result.append(got_worker)
                loop_worker.run_until_complete(client.close_current_loop_driver())

            t = threading.Thread(target=_worker)
            t.start()
            t.join(timeout=5.0)

            assert worker_result == [driver_worker]
            driver_worker.close.assert_awaited_once()
            driver_main.close.assert_not_awaited()

            # Main loop still gets its original driver_main (no duplicate creation)
            got_main_2 = loop_main.run_until_complete(client.get_async_driver())
            assert got_main_2 is driver_main
            assert mock_factory.call_count == 2

            loop_main.run_until_complete(client.close())
            driver_main.close.assert_awaited_once()
    finally:
        loop_worker.close()
        loop_main.close()
