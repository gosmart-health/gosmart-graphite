from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GOSMART_GS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Server Core Settings
    APP_NAME: str = Field(default="GoSmart DICOM Graph Server", description="Application Name")
    APP_VERSION: str = Field(default="0.1.0", description="API Version")
    DEBUG: bool = Field(default=False, description="Debug mode flag")
    HOST: str = Field(default="0.0.0.0", description="FastAPI host binding")
    PORT: int = Field(default=8000, description="FastAPI HTTP port")
    RELOAD: bool = Field(default=True, description="Uvicorn auto-reload")
    API_V1_PREFIX: str = Field(default="/api/v1", description="API v1 route prefix")

    # Logging Configuration (structlog & rotation)
    LOG_LEVEL: str = Field(default="INFO", description="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)")
    LOG_FORMAT: str = Field(default="json", description="Console log format: 'json' or 'console'")
    LOG_DIR: str = Field(default="./logs", description="Directory where rotating log files are saved")
    LOG_FILE: str = Field(default="gosmart_gs.log", description="Base name for log file")
    LOG_ROTATION_DAYS: int = Field(default=7, description="Auto-rotate log files every N days (default: 7)")
    LOG_BACKUP_COUNT: int = Field(default=8, description="Number of rotated 7-day backup files to keep")
    LOG_TO_FILE: bool = Field(default=True, description="Enable rotating file logging")

    # Graph Database (Neo4j)
    DB_CONNECTION_URI: str = Field(
        default="bolt://localhost:7687",
        description="Neo4j connection URI (e.g. bolt://localhost:7687 or neo4j://...)",
    )
    DB_USER: str = Field(default="neo4j", description="Neo4j username")
    DB_PASSWORD: str = Field(default="password", description="Neo4j password")
    DB_DATABASE: str = Field(default="neo4j", description="Neo4j database name")
    DB_MAX_CONNECTION_LIFETIME: int = Field(default=3600, description="Max connection lifetime in seconds")
    DB_MAX_CONNECTION_POOL_SIZE: int = Field(default=50, description="Max connection pool size")

    # Storage Root URI
    STORAGE_ROOT_URI: str = Field(
        default="file:///tmp/gosmart_dicom_storage",
        description="Storage root URI for DICOM binaries (e.g., file:///path or s3://bucket/path)",
    )

    # DICOM Network (pynetdicom) Settings
    DICOM_AE_TITLE: str = Field(default="GOSMART_GS", description="Application Entity Title")
    DICOM_SCP_PORT: int = Field(default=11112, description="DICOM C-STORE/C-ECHO SCP Port")
    DICOM_MAX_PDU: int = Field(default=16382, description="Maximum PDU length in bytes")


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()
