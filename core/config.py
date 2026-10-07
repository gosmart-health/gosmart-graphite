from functools import lru_cache
from typing import Optional

from pydantic import AliasChoices, Field, SecretStr
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
    #
    # NOTE: To read SecretStr from environment variables, use .get_secret_value() method,
    # e.g. os.environ.get("GOSMART_GS_DB_PASSWORD").get_secret_value()
    DB_PASSWORD: SecretStr = Field(default="password", description="Neo4j password")
    DB_DATABASE: str = Field(default="neo4j", description="Neo4j database name")
    DB_MAX_CONNECTION_LIFETIME: int = Field(default=3600, description="Max connection lifetime in seconds")
    DB_MAX_CONNECTION_POOL_SIZE: int = Field(default=50, description="Max connection pool size")

    # Storage Root URI
    STORAGE_ROOT_URI: str = Field(
        default="file:///tmp/gosmart_dicom_storage",
        description="Storage root URI (e.g. file:///path, gs://..., s3://..., az://..., \\\\unc\\share)",
    )

    # Cloud Storage Credentials & Endpoints (Supports both standard SDK env vars and GOSMART_GS_ prefix)
    GOOGLE_APPLICATION_CREDENTIALS: Optional[SecretStr] = Field(
        default=None,
        validation_alias=AliasChoices("GOOGLE_APPLICATION_CREDENTIALS", "GOSMART_GS_GOOGLE_APPLICATION_CREDENTIALS"),
        description="Path to Google Cloud Service Account credentials JSON",
    )
    GOOGLE_CLOUD_PROJECT: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("GOOGLE_CLOUD_PROJECT", "GCP_PROJECT", "GOSMART_GS_GOOGLE_CLOUD_PROJECT"),
        description="Google Cloud Project ID",
    )
    GCS_ENDPOINT_URL: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("GCS_ENDPOINT_URL", "STORAGE_EMULATOR_HOST", "GOSMART_GS_GCS_ENDPOINT_URL"),
        description="Custom GCS endpoint or emulator host",
    )

    AWS_ACCESS_KEY_ID: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("AWS_ACCESS_KEY_ID", "GOSMART_GS_AWS_ACCESS_KEY_ID"),
        description="AWS Access Key ID",
    )
    AWS_SECRET_ACCESS_KEY: Optional[SecretStr] = Field(
        default=None,
        validation_alias=AliasChoices("AWS_SECRET_ACCESS_KEY", "GOSMART_GS_AWS_SECRET_ACCESS_KEY"),
        description="AWS Secret Access Key",
    )
    AWS_SESSION_TOKEN: Optional[SecretStr] = Field(
        default=None,
        validation_alias=AliasChoices("AWS_SESSION_TOKEN", "GOSMART_GS_AWS_SESSION_TOKEN"),
        description="AWS Temporary Session Token",
    )
    AWS_DEFAULT_REGION: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("AWS_DEFAULT_REGION", "AWS_REGION", "GOSMART_GS_AWS_DEFAULT_REGION"),
        description="AWS Region name",
    )
    AWS_ENDPOINT_URL: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("AWS_ENDPOINT_URL", "AWS_S3_ENDPOINT_URL", "GOSMART_GS_AWS_ENDPOINT_URL"),
        description="Custom S3 endpoint URL (MinIO/LocalStack)",
    )

    AZURE_STORAGE_CONNECTION_STRING: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("AZURE_STORAGE_CONNECTION_STRING", "GOSMART_GS_AZURE_STORAGE_CONNECTION_STRING"),
        description="Azure Blob Storage Connection String",
    )
    AZURE_STORAGE_ACCOUNT_NAME: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("AZURE_STORAGE_ACCOUNT_NAME", "GOSMART_GS_AZURE_STORAGE_ACCOUNT_NAME"),
        description="Azure Storage Account Name",
    )
    AZURE_STORAGE_ACCOUNT_KEY: Optional[SecretStr] = Field(
        default=None,
        validation_alias=AliasChoices("AZURE_STORAGE_ACCOUNT_KEY", "GOSMART_GS_AZURE_STORAGE_ACCOUNT_KEY"),
        description="Azure Storage Account Key",
    )
    AZURE_STORAGE_SAS_TOKEN: Optional[SecretStr] = Field(
        default=None,
        validation_alias=AliasChoices("AZURE_STORAGE_SAS_TOKEN", "GOSMART_GS_AZURE_STORAGE_SAS_TOKEN"),
        description="Azure Storage SAS Token",
    )

    # DICOM Network (pynetdicom) Settings
    DICOM_AE_TITLE: str = Field(default="GOSMART_GS", description="Application Entity Title")
    DICOM_SCP_PORT: int = Field(default=11112, description="DICOM C-STORE/C-ECHO SCP Port")
    DICOM_MAX_PDU: int = Field(default=16382, description="Maximum PDU length in bytes")

    # Authentication & JWT Security Settings
    JWT_SECRET_KEY: SecretStr = Field(
        default="gosmart-dicom-graph-server-dev-jwt-secret-key-change-in-production-min32chars",
        description="Secret key for signing and verifying JWT tokens",
    )
    JWT_ALGORITHM: str = Field(default="HS256", description="JWT signing algorithm (e.g. HS256, RS256)")
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(
        default=60, description="Access token expiration time in minutes (default: 60)"
    )
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = Field(
        default=30, description="Refresh token expiration time in days (default: 30)"
    )
    API_KEY_PREFIX: str = Field(default="gs_live_", description="Standard prefix for generated API keys")
    GOOGLE_OAUTH_CLIENT_ID: Optional[str] = Field(
        default=None, description="Google OAuth2 / OIDC Client ID for federated authentication"
    )
    GOOGLE_OAUTH_CLIENT_SECRET: Optional[SecretStr] = Field(default=None, description="Google OAuth2 Client Secret")

    # Audit Tracking Settings
    AUDIT_RETENTION_DAYS: int = Field(
        default=365,
        description="Audit log retention period in days (default: 365 days / 1 year)",
    )
    AUDIT_LOG_TO_DB: bool = Field(
        default=True,
        description="Enable Neo4j database persistence for audit logs",
    )


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()
