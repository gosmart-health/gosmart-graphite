import logging
from typing import Any, AsyncGenerator, Dict, List, Optional
from neo4j import AsyncDriver, AsyncGraphDatabase, Driver, GraphDatabase
from core.config import Settings, get_settings

logger = logging.getLogger(__name__)


class Neo4jClient:
    """Manages Neo4j driver lifecycles and query execution."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self._async_driver: Optional[AsyncDriver] = None
        self._sync_driver: Optional[Driver] = None

    async def connect(self) -> None:
        """Initialize Neo4j driver connections."""
        if self._async_driver is None:
            logger.info("Initializing Neo4j Async Driver with URI: %s", self.settings.DB_CONNECTION_URI)
            auth = (self.settings.DB_USER, self.settings.DB_PASSWORD) if self.settings.DB_PASSWORD else None
            self._async_driver = AsyncGraphDatabase.driver(
                self.settings.DB_CONNECTION_URI,
                auth=auth,
                max_connection_lifetime=self.settings.DB_MAX_CONNECTION_LIFETIME,
                max_connection_pool_size=self.settings.DB_MAX_CONNECTION_POOL_SIZE,
            )

    def get_sync_driver(self) -> Driver:
        """Obtain a synchronous driver (useful for Pykka threading actors)."""
        if self._sync_driver is None:
            auth = (self.settings.DB_USER, self.settings.DB_PASSWORD) if self.settings.DB_PASSWORD else None
            self._sync_driver = GraphDatabase.driver(
                self.settings.DB_CONNECTION_URI,
                auth=auth,
                max_connection_lifetime=self.settings.DB_MAX_CONNECTION_LIFETIME,
                max_connection_pool_size=self.settings.DB_MAX_CONNECTION_POOL_SIZE,
            )
        return self._sync_driver

    async def verify_connectivity(self) -> bool:
        """Check if Neo4j instance is reachable."""
        try:
            if not self._async_driver:
                await self.connect()
            if self._async_driver:
                await self._async_driver.verify_connectivity()
                return True
        except Exception as e:
            logger.warning("Neo4j connectivity check failed: %s", e)
            return False
        return False

    async def init_schema_constraints(self) -> None:
        """Create standard DICOM graph schema constraints & indexes."""
        constraints = [
            "CREATE CONSTRAINT patient_id_unique IF NOT EXISTS FOR (p:Patient) REQUIRE p.patient_id IS UNIQUE",
            "CREATE CONSTRAINT study_uid_unique IF NOT EXISTS FOR (s:Study) REQUIRE s.study_instance_uid IS UNIQUE",
            "CREATE CONSTRAINT series_uid_unique IF NOT EXISTS FOR (se:Series) REQUIRE se.series_instance_uid IS UNIQUE",
            "CREATE CONSTRAINT instance_uid_unique IF NOT EXISTS FOR (i:Instance) REQUIRE i.sop_instance_uid IS UNIQUE",
        ]
        if not await self.verify_connectivity():
            logger.warning("Skipping schema constraint creation: Neo4j not reachable at %s", self.settings.DB_CONNECTION_URI)
            return

        async with self._async_driver.session(database=self.settings.DB_DATABASE) as session:
            for constraint in constraints:
                try:
                    await session.run(constraint)
                    logger.debug("Applied constraint: %s", constraint)
                except Exception as e:
                    logger.warning("Failed to apply constraint '%s': %s", constraint, e)

    async def execute_query(
        self,
        query: str,
        parameters: Optional[Dict[str, Any]] = None,
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Run a Cypher query and return list of records as dictionaries."""
        if not self._async_driver:
            await self.connect()

        target_db = database or self.settings.DB_DATABASE
        async with self._async_driver.session(database=target_db) as session:
            result = await session.run(query, parameters or {})
            records = [record.data() async for record in result]
            return records

    async def close(self) -> None:
        """Close driver connections."""
        if self._async_driver is not None:
            logger.info("Closing Neo4j Async Driver")
            await self._async_driver.close()
            self._async_driver = None
        if self._sync_driver is not None:
            logger.info("Closing Neo4j Sync Driver")
            self._sync_driver.close()
            self._sync_driver = None


# Singleton instance
neo4j_client = Neo4jClient()


async def get_neo4j() -> AsyncGenerator[Neo4jClient, None]:
    """FastAPI dependency for accessing Neo4j client."""
    yield neo4j_client
