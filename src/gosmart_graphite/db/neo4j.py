import asyncio
import logging
import ssl
import threading
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from neo4j import (
    AsyncDriver,
    AsyncGraphDatabase,
    Driver,
    GraphDatabase,
    NotificationDisabledClassification,
    NotificationMinimumSeverity,
)

from gosmart_graphite.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


def _resolve_driver_uri_and_tls(uri: str) -> Tuple[str, Dict[str, Any]]:
    """Build a strict TLS SSLContext combining system/env CAs with certifi for '+s://' URIs.

    Neo4j Aura uses certificates chained to CAs (such as SSL.com TLS RSA Root CA 2022) that
    may not exist in older OS bundles or custom SSL_CERT_FILE bundles, while 'neo4j+s://' /
    'bolt+s://' URIs forbid passing custom ssl_context/trusted_certificates directly.
    """
    lower_uri = uri.lower()
    if lower_uri.startswith("neo4j+s://"):
        base_uri = "neo4j://" + uri[len("neo4j+s://") :]
    elif lower_uri.startswith("bolt+s://"):
        base_uri = "bolt://" + uri[len("bolt+s://") :]
    else:
        return uri, {}

    try:
        import certifi
    except ImportError:
        return uri, {}

    ssl_context = ssl.create_default_context()
    ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
    ssl_context.load_verify_locations(cafile=certifi.where())
    return base_uri, {"ssl_context": ssl_context}


class Neo4jClient:
    """Manages Neo4j driver lifecycles and query execution across event loops and threads."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self._lock = threading.Lock()
        self._async_drivers: Dict[Optional[asyncio.AbstractEventLoop], AsyncDriver] = {}
        self._sync_driver: Optional[Driver] = None

    @staticmethod
    def _get_current_loop() -> Optional[asyncio.AbstractEventLoop]:
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            return None

    def _prune_closed_loops_locked(self) -> None:
        closed_loops = [loop for loop in self._async_drivers if loop is not None and loop.is_closed()]
        for loop in closed_loops:
            self._async_drivers.pop(loop, None)

    def _build_async_driver(self) -> AsyncDriver:
        logger.info("Initializing Neo4j Async Driver with URI: %s", self.settings.DB_CONNECTION_URI)
        password = (
            self.settings.DB_PASSWORD.get_secret_value()
            if hasattr(self.settings.DB_PASSWORD, "get_secret_value")
            else str(self.settings.DB_PASSWORD)
        )
        auth = (self.settings.DB_USER, password) if password else None
        driver_uri, tls_kwargs = _resolve_driver_uri_and_tls(self.settings.DB_CONNECTION_URI)
        return AsyncGraphDatabase.driver(
            driver_uri,
            auth=auth,
            max_connection_lifetime=self.settings.DB_MAX_CONNECTION_LIFETIME,
            max_connection_pool_size=self.settings.DB_MAX_CONNECTION_POOL_SIZE,
            notifications_min_severity=NotificationMinimumSeverity.WARNING,
            notifications_disabled_classifications=[NotificationDisabledClassification.SCHEMA],
            **tls_kwargs,
        )

    async def connect(self) -> None:
        """Initialize Neo4j async driver for the current event loop."""
        current_loop = self._get_current_loop()
        with self._lock:
            self._prune_closed_loops_locked()
            if current_loop not in self._async_drivers:
                self._async_drivers[current_loop] = self._build_async_driver()

    def get_sync_driver(self) -> Driver:
        """Obtain a synchronous driver (useful for Pykka threading actors)."""
        with self._lock:
            if self._sync_driver is None:
                password = (
                    self.settings.DB_PASSWORD.get_secret_value()
                    if hasattr(self.settings.DB_PASSWORD, "get_secret_value")
                    else str(self.settings.DB_PASSWORD)
                )
                auth = (self.settings.DB_USER, password) if password else None
                driver_uri, tls_kwargs = _resolve_driver_uri_and_tls(self.settings.DB_CONNECTION_URI)
                self._sync_driver = GraphDatabase.driver(
                    driver_uri,
                    auth=auth,
                    max_connection_lifetime=self.settings.DB_MAX_CONNECTION_LIFETIME,
                    max_connection_pool_size=self.settings.DB_MAX_CONNECTION_POOL_SIZE,
                    notifications_min_severity=NotificationMinimumSeverity.WARNING,
                    notifications_disabled_classifications=[NotificationDisabledClassification.SCHEMA],
                    **tls_kwargs,
                )
            return self._sync_driver

    async def verify_connectivity(self) -> bool:
        """Check if Neo4j instance is reachable."""
        try:
            driver = await self.get_async_driver()
            if driver:
                await driver.verify_connectivity()
                return True
        except Exception as e:
            logger.warning("Neo4j connectivity check failed: %s", e)
            return False
        return False

    async def get_async_driver(self) -> AsyncDriver:
        """Obtain or initialize the asynchronous driver bound to the current event loop."""
        current_loop = self._get_current_loop()
        with self._lock:
            self._prune_closed_loops_locked()
            driver = self._async_drivers.get(current_loop)
            if driver is None:
                driver = self._build_async_driver()
                self._async_drivers[current_loop] = driver
            return driver

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

    async def close_current_loop_driver(self) -> None:
        """Close and remove only the async driver bound to the currently running event loop."""
        current_loop = self._get_current_loop()
        with self._lock:
            driver = self._async_drivers.pop(current_loop, None)
        if driver is not None:
            try:
                logger.info("Closing Neo4j Async Driver")
                await driver.close()
            except Exception as e:
                logger.debug("async_driver_close_warning: %s", e)

    async def close(self) -> None:
        """Close driver connections."""
        await self.close_current_loop_driver()
        with self._lock:
            remaining = list(self._async_drivers.items())
            self._async_drivers.clear()
            sync_driver = self._sync_driver
            self._sync_driver = None

        for loop, driver in remaining:
            if loop is not None and loop.is_closed():
                continue
            try:
                logger.info("Closing Neo4j Async Driver")
                await driver.close()
            except Exception as e:
                logger.debug("async_driver_close_warning: %s", e)

        if sync_driver is not None:
            try:
                logger.info("Closing Neo4j Sync Driver")
                sync_driver.close()
            except Exception as e:
                logger.debug("sync_driver_close_warning: %s", e)


# Singleton instance
neo4j_client = Neo4jClient()


async def get_neo4j() -> AsyncGenerator[Neo4jClient, None]:
    """FastAPI dependency for accessing Neo4j client."""
    yield neo4j_client
