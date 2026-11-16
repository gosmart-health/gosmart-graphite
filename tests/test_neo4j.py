import ssl
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from neo4j.exceptions import ServiceUnavailable

from gosmart_graphite.db.neo4j import Neo4jClient, _resolve_driver_uri_and_tls
from neo4j import NotificationDisabledClassification, NotificationMinimumSeverity


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
async def test_verify_connectivity_real_client():
    """Verify that verify_connectivity on real client returns a boolean without raising unhandled errors."""
    client = Neo4jClient()
    try:
        result = await client.verify_connectivity()
        assert isinstance(result, bool)
    finally:
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
