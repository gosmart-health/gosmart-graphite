"""Integration test for the configured Neo4j connection."""

import pytest

from gosmart_graphite.db.neo4j import Neo4jClient


@pytest.mark.anyio
async def test_verify_connectivity_real_client():
    """Verify that verify_connectivity on real client returns a boolean without raising unhandled errors."""
    client = Neo4jClient()
    try:
        result = await client.verify_connectivity()
        assert isinstance(result, bool)
    finally:
        await client.close()
