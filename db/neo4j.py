import logging
from typing import Any, AsyncGenerator, Dict, List, Optional

from core.config import Settings, get_settings
from neo4j import AsyncDriver, AsyncGraphDatabase, Driver, GraphDatabase

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
            password = (
                self.settings.DB_PASSWORD.get_secret_value()
                if hasattr(self.settings.DB_PASSWORD, "get_secret_value")
                else str(self.settings.DB_PASSWORD)
            )
            auth = (self.settings.DB_USER, password) if password else None
            self._async_driver = AsyncGraphDatabase.driver(
                self.settings.DB_CONNECTION_URI,
                auth=auth,
                max_connection_lifetime=self.settings.DB_MAX_CONNECTION_LIFETIME,
                max_connection_pool_size=self.settings.DB_MAX_CONNECTION_POOL_SIZE,
            )

    def get_sync_driver(self) -> Driver:
        """Obtain a synchronous driver (useful for Pykka threading actors)."""
        if self._sync_driver is None:
            password = (
                self.settings.DB_PASSWORD.get_secret_value()
                if hasattr(self.settings.DB_PASSWORD, "get_secret_value")
                else str(self.settings.DB_PASSWORD)
            )
            auth = (self.settings.DB_USER, password) if password else None
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

    async def get_async_driver(self) -> AsyncDriver:
        """Obtain or initialize the asynchronous driver."""
        if self._async_driver is None:
            await self.connect()
        return self._async_driver

    async def execute_query(
        self,
        query: str,
        parameters: Optional[Dict[str, Any]] = None,
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Run a Cypher query and return list of records as dictionaries."""
        driver = await self.get_async_driver()
        target_db = database or self.settings.DB_DATABASE
        async with driver.session(database=target_db) as session:
            result = await session.run(query, parameters or {})
            records = [record.data() async for record in result]
            return records

    async def execute_write_tx(
        self,
        transaction_fn: Any,
        database: Optional[str] = None,
    ) -> Any:
        """Execute a unit of work inside an explicit managed async write transaction."""
        driver = await self.get_async_driver()
        target_db = database or self.settings.DB_DATABASE
        async with driver.session(database=target_db) as session:
            return await session.execute_write(transaction_fn)

    def execute_write_tx_sync(
        self,
        transaction_fn: Any,
        database: Optional[str] = None,
    ) -> Any:
        """Execute a unit of work synchronously inside an explicit write transaction."""
        driver = self.get_sync_driver()
        target_db = database or self.settings.DB_DATABASE
        with driver.session(database=target_db) as session:
            return session.execute_write(transaction_fn)

    async def close(self) -> None:
        """Close driver connections."""
        if self._async_driver is not None:
            try:
                logger.info("Closing Neo4j Async Driver")
                await self._async_driver.close()
            except Exception as e:
                logger.debug("async_driver_close_warning", error=str(e))
            finally:
                self._async_driver = None
        if self._sync_driver is not None:
            try:
                logger.info("Closing Neo4j Sync Driver")
                self._sync_driver.close()
            except Exception as e:
                logger.debug("sync_driver_close_warning", error=str(e))
            finally:
                self._sync_driver = None


# Singleton instance
neo4j_client = Neo4jClient()


async def get_neo4j() -> AsyncGenerator[Neo4jClient, None]:
    """FastAPI dependency for accessing Neo4j client."""
    yield neo4j_client
