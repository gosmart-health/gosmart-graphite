from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class StorageScheme(str, Enum):
    """Supported storage URI schemes."""

    FILE = "file"
    UNC = "unc"
    GCS = "gs"
    S3 = "s3"
    AZ = "az"
    AZURE = "azure"


class ParsedStorageUri(BaseModel):
    """Detailed metadata and components of a parsed storage URI."""

    raw_uri: str = Field(..., description="Original raw URI or path")
    scheme: StorageScheme = Field(..., description="Normalized storage scheme")
    bucket_or_share: Optional[str] = Field(None, description="Bucket, container, or network share name")
    path: str = Field(..., description="Path within the bucket, share, or local filesystem")
    normalized_uri: str = Field(..., description="Standardized URI string")
    fsspec_protocol: str = Field(..., description="Protocol string recognized by fsspec")
    fsspec_path: str = Field(..., description="Path passed to fsspec filesystem methods")


class MediaMetadata(BaseModel):
    """File and media metadata retrieved from storage."""

    uri: str = Field(..., description="Full storage URI of the resource")
    scheme: StorageScheme = Field(..., description="Storage scheme")
    name: str = Field(..., description="File name or final path component")
    size_bytes: int = Field(default=0, description="Size in bytes")
    content_type: Optional[str] = Field(default="application/dicom", description="MIME content type")
    created_at: Optional[datetime] = Field(default=None, description="Creation timestamp if available")
    last_modified: Optional[datetime] = Field(default=None, description="Last modification timestamp if available")
    etag: Optional[str] = Field(default=None, description="Entity tag or hash if available")
    is_file: bool = Field(default=True, description="True if target is a file")
    is_dir: bool = Field(default=False, description="True if target is a directory or prefix")
    extra: Dict[str, Any] = Field(default_factory=dict, description="Provider-specific raw metadata")
