import asyncio
import concurrent.futures
import io
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Coroutine, Dict, List, Optional, TypeVar, Union
from urllib.parse import urlparse

import fsspec
import pydicom
from fsspec import AbstractFileSystem
from pydicom.dataset import Dataset

from gosmart_graphite.core.config import Settings, get_settings
from gosmart_graphite.core.logging import get_logger
from gosmart_graphite.model.media import MediaMetadata, ParsedStorageUri, StorageScheme

logger = get_logger("gosmart_gs.media_service")

# Register 'azure' protocol with adlfs if available
try:
    import adlfs

    fsspec.register_implementation("azure", adlfs.AzureBlobFileSystem, clobber=True)
except Exception:  # pragma: no cover
    pass

T = TypeVar("T")


class MediaService:
    """Storage-agnostic media service for DICOM and media files across Cloud and Local filesystems."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        default_storage_options: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.default_storage_options = default_storage_options or {}
        self._fs_cache: Dict[str, AbstractFileSystem] = {}
        self._custom_fs_overrides: Dict[str, AbstractFileSystem] = {}

    def register_fs_override(self, scheme_or_protocol: Union[str, StorageScheme], fs: AbstractFileSystem) -> None:
        """Register a custom or mocked filesystem for testing or runtime extension."""
        key = scheme_or_protocol.value if isinstance(scheme_or_protocol, StorageScheme) else str(scheme_or_protocol)
        self._custom_fs_overrides[key.lower()] = fs
        if key.lower() in ("az", "azure"):
            self._custom_fs_overrides["az"] = fs
            self._custom_fs_overrides["azure"] = fs
        logger.info("custom_fs_override_registered", scheme=key)

    def clear_fs_overrides(self) -> None:
        """Clear all custom filesystem overrides."""
        self._custom_fs_overrides.clear()
        self._fs_cache.clear()

    # -------------------------------------------------------------------------
    # URI Parsing and Normalization
    # -------------------------------------------------------------------------

    def parse_uri(self, uri: str) -> ParsedStorageUri:
        """Parse and normalize a storage URI according to project requirements.

        Supported schemes:
        - Google Cloud Storage: gs:// (or gcs://)
        - Amazon S3: s3:// (or s3a://)
        - Azure Blob: azure:// (or az://, abfs://, abfss://)
        - Local file: file://
        - Windows UNC: Denoted by backslash (e.g. \\\\server\\share\\path or \\server\\share\\path)
        """
        raw_uri = str(uri).strip()

        # 1. Windows UNC path (starts with double or single backslash, or unc://)
        if raw_uri.startswith(("\\\\", "//")) or (
            raw_uri.startswith("\\") and len(raw_uri) > 1 and not raw_uri.startswith("\\/")
        ):
            # Normalize UNC path
            clean_unc = raw_uri.replace("/", "\\")
            if clean_unc.startswith("\\") and not clean_unc.startswith("\\\\"):
                clean_unc = "\\" + clean_unc

            parts = [p for p in clean_unc.strip("\\").split("\\") if p]
            server = parts[0] if len(parts) > 0 else "localhost"
            share = parts[1] if len(parts) > 1 else ""
            subpath = "\\".join(parts[2:]) if len(parts) > 2 else ""

            # Standardized UNC string
            normalized = f"\\\\{server}\\{share}\\{subpath}".rstrip("\\")
            # For fsspec local filesystem compatibility across OSes:
            if os.name == "nt":
                fsspec_path = normalized
            else:
                # On non-Windows platforms, map to posix representation
                fsspec_path = f"//{server}/{share}/{subpath.replace('\\', '/')}".rstrip("/")

            return ParsedStorageUri(
                raw_uri=raw_uri,
                scheme=StorageScheme.UNC,
                bucket_or_share=f"{server}/{share}" if share else server,
                path=subpath,
                normalized_uri=normalized,
                fsspec_protocol="file",
                fsspec_path=fsspec_path,
            )

        if raw_uri.lower().startswith("unc://"):
            parsed = urlparse(raw_uri)
            server = parsed.netloc
            path_parts = [p for p in parsed.path.strip("/").split("/") if p]
            share = path_parts[0] if path_parts else ""
            subpath = "/".join(path_parts[1:]) if len(path_parts) > 1 else ""
            normalized = f"\\\\{server}\\{share}\\{subpath.replace('/', '\\')}".rstrip("\\")

            if os.name == "nt":
                fsspec_path = normalized
            else:
                fsspec_path = f"//{server}/{share}/{subpath}".rstrip("/")

            return ParsedStorageUri(
                raw_uri=raw_uri,
                scheme=StorageScheme.UNC,
                bucket_or_share=f"{server}/{share}" if share else server,
                path=subpath,
                normalized_uri=normalized,
                fsspec_protocol="file",
                fsspec_path=fsspec_path,
            )

        # 2. Google Cloud Storage
        if raw_uri.lower().startswith(("gs://", "gcs://")):
            parsed = urlparse(raw_uri)
            bucket = parsed.netloc
            obj_path = parsed.path.lstrip("/")
            fsspec_path = f"{bucket}/{obj_path}".rstrip("/") if obj_path else bucket
            normalized = f"gs://{fsspec_path}"
            return ParsedStorageUri(
                raw_uri=raw_uri,
                scheme=StorageScheme.GCS,
                bucket_or_share=bucket,
                path=obj_path,
                normalized_uri=normalized,
                fsspec_protocol="gs",
                fsspec_path=fsspec_path,
            )

        # 3. Amazon S3
        if raw_uri.lower().startswith(("s3://", "s3a://")):
            parsed = urlparse(raw_uri)
            bucket = parsed.netloc
            key_path = parsed.path.lstrip("/")
            fsspec_path = f"{bucket}/{key_path}".rstrip("/") if key_path else bucket
            normalized = f"s3://{fsspec_path}"
            return ParsedStorageUri(
                raw_uri=raw_uri,
                scheme=StorageScheme.S3,
                bucket_or_share=bucket,
                path=key_path,
                normalized_uri=normalized,
                fsspec_protocol="s3",
                fsspec_path=fsspec_path,
            )

        # 4. Azure Blob (az:// or azure:// or abfs://)
        if raw_uri.lower().startswith(("az://", "azure://", "abfs://", "abfss://")):
            # Standardize az://container/blob or azure://container/blob
            parsed = urlparse(raw_uri)
            container = parsed.netloc
            blob_path = parsed.path.lstrip("/")
            fsspec_path = f"{container}/{blob_path}".rstrip("/") if blob_path else container
            if raw_uri.lower().startswith("azure://"):
                normalized = f"azure://{fsspec_path}"
                scheme = StorageScheme.AZURE
            else:
                normalized = f"az://{fsspec_path}"
                scheme = StorageScheme.AZ

            return ParsedStorageUri(
                raw_uri=raw_uri,
                scheme=scheme,
                bucket_or_share=container,
                path=blob_path,
                normalized_uri=normalized,
                fsspec_protocol="abfs",
                fsspec_path=fsspec_path,
            )

        # 5. Local file (file://) or plain path
        if raw_uri.lower().startswith("file://"):
            local_path = raw_uri[7:]
            # Handle Windows file:///C:/path or Unix file:///path
            if re.match(r"^/[a-zA-Z]:", local_path):
                local_path = local_path[1:]
            p = Path(local_path)
            normalized = f"file://{p.as_posix()}"
            return ParsedStorageUri(
                raw_uri=raw_uri,
                scheme=StorageScheme.FILE,
                bucket_or_share=None,
                path=p.as_posix(),
                normalized_uri=normalized,
                fsspec_protocol="file",
                fsspec_path=str(p),
            )

        # Default fallback: Treat as local filesystem path
        p = Path(raw_uri).resolve()
        return ParsedStorageUri(
            raw_uri=raw_uri,
            scheme=StorageScheme.FILE,
            bucket_or_share=None,
            path=p.as_posix(),
            normalized_uri=f"file://{p.as_posix()}",
            fsspec_protocol="file",
            fsspec_path=str(p),
        )

    # -------------------------------------------------------------------------
    # Cloud Credentials Resolution
    # -------------------------------------------------------------------------

    def get_cloud_storage_options(self, scheme: StorageScheme, **explicit_options: Any) -> Dict[str, Any]:
        """Resolve cloud credentials from commonly utilized environment variables for GCP, AWS, and Azure."""
        options: Dict[str, Any] = dict(
            self.default_storage_options.get(scheme.value, {})
            or (
                self.default_storage_options.get("az", {})
                if scheme in (StorageScheme.AZ, StorageScheme.AZURE)
                else self.default_storage_options.get("azure", {})
            )
        )
        options.update(explicit_options)

        if scheme == StorageScheme.GCS:
            # GCP Credentials
            # 1. Service account file
            gcp_cred_file = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
            if gcp_cred_file and "token" not in options:
                options["token"] = gcp_cred_file

            # 2. GCP Project ID
            project = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT")
            if project and "project" not in options:
                options["project"] = project

            # 3. Custom endpoint (for local emulators or testing)
            gcs_endpoint = os.getenv("GCS_ENDPOINT_URL") or os.getenv("STORAGE_EMULATOR_HOST")
            if gcs_endpoint and "endpoint_url" not in options:
                options["endpoint_url"] = gcs_endpoint

        elif scheme == StorageScheme.S3:
            # AWS Credentials
            aws_key = os.getenv("AWS_ACCESS_KEY_ID")
            aws_secret = os.getenv("AWS_SECRET_ACCESS_KEY")
            aws_token = os.getenv("AWS_SESSION_TOKEN")
            aws_region = os.getenv("AWS_DEFAULT_REGION") or os.getenv("AWS_REGION")
            aws_endpoint = os.getenv("AWS_ENDPOINT_URL") or os.getenv("AWS_S3_ENDPOINT_URL")
            aws_profile = os.getenv("AWS_PROFILE")

            if aws_key and "key" not in options:
                options["key"] = aws_key
            if aws_secret and "secret" not in options:
                options["secret"] = aws_secret
            if aws_token and "token" not in options:
                options["token"] = aws_token
            if aws_profile and "profile" not in options:
                options["profile"] = aws_profile

            client_kwargs = options.setdefault("client_kwargs", {})
            if aws_region and "region_name" not in client_kwargs:
                client_kwargs["region_name"] = aws_region
            if aws_endpoint and "endpoint_url" not in options:
                options["endpoint_url"] = aws_endpoint

        elif scheme in (StorageScheme.AZ, StorageScheme.AZURE):
            # Azure Credentials

            conn_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
            account_name = os.getenv("AZURE_STORAGE_ACCOUNT_NAME")
            account_key = os.getenv("AZURE_STORAGE_ACCOUNT_KEY")
            sas_token = os.getenv("AZURE_STORAGE_SAS_TOKEN")
            tenant_id = os.getenv("AZURE_TENANT_ID")
            client_id = os.getenv("AZURE_CLIENT_ID")
            client_secret = os.getenv("AZURE_CLIENT_SECRET")

            if conn_str and "connection_string" not in options:
                options["connection_string"] = conn_str
            if account_name and "account_name" not in options:
                options["account_name"] = account_name
            if account_key and "account_key" not in options:
                options["account_key"] = account_key
            if sas_token and "sas_token" not in options:
                options["sas_token"] = sas_token
            if tenant_id and "tenant_id" not in options:
                options["tenant_id"] = tenant_id
            if client_id and "client_id" not in options:
                options["client_id"] = client_id
            if client_secret and "client_secret" not in options:
                options["client_secret"] = client_secret

        elif scheme in (StorageScheme.FILE, StorageScheme.UNC):
            options.setdefault("auto_mkdir", True)

        return options

    def get_filesystem(
        self,
        parsed_uri: ParsedStorageUri,
        **storage_options: Any,
    ) -> AbstractFileSystem:
        """Get or initialize the appropriate fsspec filesystem instance for a given parsed URI."""
        # 1. Check custom overrides (e.g. For mock testing)
        scheme_key = parsed_uri.scheme.value.lower()
        proto_key = parsed_uri.fsspec_protocol.lower()

        if scheme_key in self._custom_fs_overrides:
            return self._custom_fs_overrides[scheme_key]
        if proto_key in self._custom_fs_overrides:
            return self._custom_fs_overrides[proto_key]

        # 2. Build cache key
        merged_options = self.get_cloud_storage_options(parsed_uri.scheme, **storage_options)
        opts_repr = tuple(sorted((k, str(v)) for k, v in merged_options.items()))
        cache_key = f"{proto_key}:{opts_repr}"

        if cache_key in self._fs_cache:
            return self._fs_cache[cache_key]

        # 3. Instantiate filesystem
        try:
            fs = fsspec.filesystem(parsed_uri.fsspec_protocol, **merged_options)
            self._fs_cache[cache_key] = fs
            return fs
        except Exception as e:
            logger.error(
                "failed_to_initialize_filesystem",
                protocol=parsed_uri.fsspec_protocol,
                scheme=parsed_uri.scheme.value,
                error=str(e),
                exc_info=True,
            )
            raise

    # -------------------------------------------------------------------------
    # Asynchronous Core Methods
    # -------------------------------------------------------------------------

    async def read(self, uri: str, **storage_options: Any) -> bytes:
        """Read all bytes from the storage resource asynchronously."""
        parsed = self.parse_uri(uri)
        fs = self.get_filesystem(parsed, **storage_options)

        def _do_read() -> bytes:
            with fs.open(parsed.fsspec_path, "rb") as f:
                return f.read()

        logger.debug("media_read_started", uri=parsed.normalized_uri, scheme=parsed.scheme.value)
        data = await asyncio.to_thread(_do_read)
        logger.debug("media_read_completed", uri=parsed.normalized_uri, size_bytes=len(data))
        return data

    async def write(
        self,
        uri: str,
        data: Union[bytes, BinaryIO, io.BytesIO],
        content_type: Optional[str] = "application/dicom",
        **storage_options: Any,
    ) -> str:
        """Write raw binary data or file-like buffer to storage asynchronously.

        Returns the normalized URI where the data was saved.
        """
        parsed = self.parse_uri(uri)
        fs = self.get_filesystem(parsed, **storage_options)

        # Extract bytes
        if hasattr(data, "read"):
            payload = data.read()
        elif isinstance(data, (bytes, bytearray)):
            payload = bytes(data)
        else:
            raise TypeError(f"Unsupported data type for write: {type(data)}")

        def _do_write() -> None:
            # Ensure parent directories exist for local file / UNC
            if parsed.scheme in (StorageScheme.FILE, StorageScheme.UNC):
                parent = Path(parsed.fsspec_path).parent
                parent.mkdir(parents=True, exist_ok=True)

            with fs.open(parsed.fsspec_path, "wb") as f:
                f.write(payload)

        logger.debug("media_write_started", uri=parsed.normalized_uri, size_bytes=len(payload))
        await asyncio.to_thread(_do_write)
        logger.info("media_write_completed", uri=parsed.normalized_uri, size_bytes=len(payload))
        return parsed.normalized_uri

    async def exists(self, uri: str, **storage_options: Any) -> bool:
        """Check if a file or resource exists at the given URI asynchronously."""
        parsed = self.parse_uri(uri)
        fs = self.get_filesystem(parsed, **storage_options)

        def _do_exists() -> bool:
            return bool(fs.exists(parsed.fsspec_path))

        return await asyncio.to_thread(_do_exists)

    async def delete(self, uri: str, recursive: bool = False, **storage_options: Any) -> bool:
        """Delete a file or resource at the given URI asynchronously."""
        parsed = self.parse_uri(uri)
        fs = self.get_filesystem(parsed, **storage_options)

        def _do_delete() -> bool:
            if not fs.exists(parsed.fsspec_path):
                return False
            fs.rm(parsed.fsspec_path, recursive=recursive)
            return True

        result = await asyncio.to_thread(_do_delete)
        logger.info("media_delete_completed", uri=parsed.normalized_uri, deleted=result)
        return result

    async def stat(self, uri: str, **storage_options: Any) -> MediaMetadata:
        """Retrieve detailed metadata for the specified storage resource asynchronously."""
        parsed = self.parse_uri(uri)
        fs = self.get_filesystem(parsed, **storage_options)

        def _do_stat() -> Dict[str, Any]:
            if not fs.exists(parsed.fsspec_path):
                raise FileNotFoundError(f"Storage resource not found: {uri}")
            return fs.info(parsed.fsspec_path)

        info = await asyncio.to_thread(_do_stat)

        # Parse timestamps
        mtime = info.get("mtime") or info.get("updated") or info.get("LastModified")
        if isinstance(mtime, (int, float)):
            last_modified = datetime.fromtimestamp(mtime, tz=timezone.utc)
        elif isinstance(mtime, datetime):
            last_modified = mtime
        else:
            last_modified = None

        ctime = info.get("created") or info.get("ctime")
        if isinstance(ctime, (int, float)):
            created_at = datetime.fromtimestamp(ctime, tz=timezone.utc)
        elif isinstance(ctime, datetime):
            created_at = ctime
        else:
            created_at = None

        name = Path(parsed.fsspec_path).name or str(info.get("name", ""))
        size_bytes = int(info.get("size", 0))
        is_dir = info.get("type") == "directory" or bool(info.get("is_dir", False))
        is_file = not is_dir

        return MediaMetadata(
            uri=parsed.normalized_uri,
            scheme=parsed.scheme,
            name=name,
            size_bytes=size_bytes,
            content_type=info.get("contentType", "application/dicom"),
            created_at=created_at,
            last_modified=last_modified,
            etag=str(info.get("etag", "")) if info.get("etag") else None,
            is_file=is_file,
            is_dir=is_dir,
            extra=info,
        )

    async def list_files(
        self,
        uri_prefix: str,
        recursive: bool = False,
        pattern: Optional[str] = None,
        **storage_options: Any,
    ) -> List[str]:
        """List storage URIs matching the prefix and optional glob pattern asynchronously."""
        parsed = self.parse_uri(uri_prefix)
        fs = self.get_filesystem(parsed, **storage_options)

        def _do_list() -> List[str]:
            path = parsed.fsspec_path
            if not fs.exists(path):
                return []

            if recursive:
                paths = fs.find(path)
            else:
                entries = fs.ls(path, detail=False)
                paths = [e for e in entries]

            # Filter out directories if any
            result_uris: List[str] = []
            for p in paths:
                # Convert back to standard URI
                if parsed.scheme == StorageScheme.FILE:
                    full_p = Path(p).resolve().as_posix()
                    item_uri = f"file://{full_p}"
                elif parsed.scheme == StorageScheme.UNC:
                    item_uri = f"\\\\{p.replace('/', '\\').lstrip('\\')}"
                elif parsed.scheme == StorageScheme.GCS:
                    item_uri = f"gs://{p.lstrip('/')}"
                elif parsed.scheme == StorageScheme.S3:
                    item_uri = f"s3://{p.lstrip('/')}"
                elif parsed.scheme in (StorageScheme.AZ, StorageScheme.AZURE):
                    prefix = "azure" if parsed.raw_uri.lower().startswith("azure://") else "az"
                    item_uri = f"{prefix}://{p.lstrip('/')}"

                else:
                    item_uri = f"{parsed.scheme.value}://{p}"

                if pattern and not re.search(pattern, item_uri):
                    continue
                result_uris.append(item_uri)
            return sorted(result_uris)

        return await asyncio.to_thread(_do_list)

    async def copy(self, src_uri: str, dst_uri: str, **storage_options: Any) -> str:
        """Copy a file or object from source URI to destination URI asynchronously.

        Supports cross-provider copying (e.g. Local to GCS, GCS to S3).
        """
        parsed_src = self.parse_uri(src_uri)
        parsed_dst = self.parse_uri(dst_uri)

        fs_src = self.get_filesystem(parsed_src, **storage_options)
        fs_dst = self.get_filesystem(parsed_dst, **storage_options)

        def _do_copy() -> None:
            if not fs_src.exists(parsed_src.fsspec_path):
                raise FileNotFoundError(f"Source file not found: {src_uri}")

            # If within the same filesystem instance, use native copy
            if fs_src == fs_dst:
                if parsed_dst.scheme in (StorageScheme.FILE, StorageScheme.UNC):
                    Path(parsed_dst.fsspec_path).parent.mkdir(parents=True, exist_ok=True)
                fs_src.copy(parsed_src.fsspec_path, parsed_dst.fsspec_path)
            else:
                # Cross-filesystem streaming copy
                if parsed_dst.scheme in (StorageScheme.FILE, StorageScheme.UNC):
                    Path(parsed_dst.fsspec_path).parent.mkdir(parents=True, exist_ok=True)

                with fs_src.open(parsed_src.fsspec_path, "rb") as s, fs_dst.open(parsed_dst.fsspec_path, "wb") as d:
                    # Stream in 1MB chunks
                    while chunk := s.read(1024 * 1024):
                        d.write(chunk)

        logger.debug("media_copy_started", src=parsed_src.normalized_uri, dst=parsed_dst.normalized_uri)
        await asyncio.to_thread(_do_copy)
        logger.info("media_copy_completed", src=parsed_src.normalized_uri, dst=parsed_dst.normalized_uri)
        return parsed_dst.normalized_uri

    async def move(self, src_uri: str, dst_uri: str, **storage_options: Any) -> str:
        """Move / rename a file or object from source URI to destination URI asynchronously."""
        copied_uri = await self.copy(src_uri, dst_uri, **storage_options)
        await self.delete(src_uri, **storage_options)
        logger.info("media_move_completed", src=src_uri, dst=copied_uri)
        return copied_uri

    # -------------------------------------------------------------------------
    # DICOM High-Level Helpers
    # -------------------------------------------------------------------------

    async def read_dicom(self, uri: str, force: bool = True, **storage_options: Any) -> Dataset:
        """Read a DICOM object from any storage URI and parse into a pydicom Dataset."""
        raw_bytes = await self.read(uri, **storage_options)

        def _do_parse() -> Dataset:
            return pydicom.dcmread(io.BytesIO(raw_bytes), force=force)

        return await asyncio.to_thread(_do_parse)

    async def write_dicom(
        self,
        uri: str,
        dataset: Dataset,
        enforce_file_format: bool = True,
        **storage_options: Any,
    ) -> str:
        """Serialize a pydicom Dataset to bytes and persist to any storage URI."""

        def _do_serialize() -> bytes:
            buf = io.BytesIO()
            dataset.save_as(buf, enforce_file_format=enforce_file_format)
            return buf.getvalue()

        payload = await asyncio.to_thread(_do_serialize)
        return await self.write(uri, payload, content_type="application/dicom", **storage_options)

    # -------------------------------------------------------------------------
    # Synchronous Execution Helpers (for Pykka Actors and Non-Async Contexts)
    # -------------------------------------------------------------------------

    @staticmethod
    def run_sync(coro: Coroutine[Any, Any, T]) -> T:
        """Safely execute an asynchronous coroutine in a synchronous context.

        Designed for Pykka actor threads (e.g. ThreadingActor) or synchronous CLI commands,
        handling active or missing event loops gracefully.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, coro).result()
        else:
            return asyncio.run(coro)

    def read_sync(self, uri: str, **storage_options: Any) -> bytes:
        """Synchronously read bytes from storage URI."""
        return self.run_sync(self.read(uri, **storage_options))

    def write_sync(
        self,
        uri: str,
        data: Union[bytes, BinaryIO, io.BytesIO],
        content_type: Optional[str] = "application/dicom",
        **storage_options: Any,
    ) -> str:
        """Synchronously write data to storage URI."""
        return self.run_sync(self.write(uri, data, content_type=content_type, **storage_options))

    def exists_sync(self, uri: str, **storage_options: Any) -> bool:
        """Synchronously check if URI exists."""
        return self.run_sync(self.exists(uri, **storage_options))

    def delete_sync(self, uri: str, recursive: bool = False, **storage_options: Any) -> bool:
        """Synchronously delete resource at URI."""
        return self.run_sync(self.delete(uri, recursive=recursive, **storage_options))

    def stat_sync(self, uri: str, **storage_options: Any) -> MediaMetadata:
        """Synchronously retrieve metadata for URI."""
        return self.run_sync(self.stat(uri, **storage_options))

    def list_files_sync(
        self,
        uri_prefix: str,
        recursive: bool = False,
        pattern: Optional[str] = None,
        **storage_options: Any,
    ) -> List[str]:
        """Synchronously list files under URI prefix."""
        return self.run_sync(self.list_files(uri_prefix, recursive=recursive, pattern=pattern, **storage_options))

    def copy_sync(self, src_uri: str, dst_uri: str, **storage_options: Any) -> str:
        """Synchronously copy resource between URIs."""
        return self.run_sync(self.copy(src_uri, dst_uri, **storage_options))

    def move_sync(self, src_uri: str, dst_uri: str, **storage_options: Any) -> str:
        """Synchronously move resource between URIs."""
        return self.run_sync(self.move(src_uri, dst_uri, **storage_options))

    def read_dicom_sync(self, uri: str, force: bool = True, **storage_options: Any) -> Dataset:
        """Synchronously read and parse DICOM dataset from URI."""
        return self.run_sync(self.read_dicom(uri, force=force, **storage_options))

    def write_dicom_sync(
        self,
        uri: str,
        dataset: Dataset,
        enforce_file_format: bool = True,
        **storage_options: Any,
    ) -> str:
        """Synchronously write DICOM dataset to URI."""
        return self.run_sync(self.write_dicom(uri, dataset, enforce_file_format=enforce_file_format, **storage_options))


# Global singleton instance
media_service = MediaService()
