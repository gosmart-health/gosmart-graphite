import asyncio
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import fsspec
import pytest

from gosmart_graphite.model.media import StorageScheme
from gosmart_graphite.service.media_service import MediaService, media_service


@pytest.fixture
def mr_dir() -> Path:
    """Path to the 10 MR DICOM test assets."""
    return Path(__file__).parent.parent / "assets" / "mr"


@pytest.fixture
def mr_files(mr_dir: Path) -> list[Path]:
    """List of all 10 MR DICOM files."""
    files = sorted(mr_dir.glob("*.dcm"))
    assert len(files) == 10, f"Expected 10 MR files, found {len(files)}"
    return files


# =============================================================================
# 1. URI Parsing and Normalization Tests
# =============================================================================


def test_parse_uri_schemes():
    # Local file with file://
    p_file = media_service.parse_uri("file:///tmp/dicom/test.dcm")
    assert p_file.scheme == StorageScheme.FILE
    assert p_file.fsspec_protocol == "file"
    assert p_file.normalized_uri == "file:///tmp/dicom/test.dcm"

    # Local file plain path
    p_plain = media_service.parse_uri("/tmp/dicom/test.dcm")
    assert p_plain.scheme == StorageScheme.FILE
    assert p_plain.fsspec_protocol == "file"

    # Windows UNC double backslash
    p_unc = media_service.parse_uri(r"\\hospital-nas\imaging_share\mr_study\img1.dcm")
    assert p_unc.scheme == StorageScheme.UNC
    assert p_unc.bucket_or_share == "hospital-nas/imaging_share"
    assert p_unc.path == r"mr_study\img1.dcm"
    assert p_unc.normalized_uri == r"\\hospital-nas\imaging_share\mr_study\img1.dcm"

    # Windows UNC single backslash
    p_unc_single = media_service.parse_uri(r"\hospital-nas\imaging_share\mr_study\img1.dcm")
    assert p_unc_single.scheme == StorageScheme.UNC
    assert p_unc_single.normalized_uri == r"\\hospital-nas\imaging_share\mr_study\img1.dcm"

    # Windows UNC with unc:// prefix
    p_unc_prefix = media_service.parse_uri("unc://hospital-nas/imaging_share/mr_study/img1.dcm")
    assert p_unc_prefix.scheme == StorageScheme.UNC
    assert p_unc_prefix.normalized_uri == r"\\hospital-nas\imaging_share\mr_study\img1.dcm"

    # Google Cloud Storage (gs:// and gcs://)
    p_gcs1 = media_service.parse_uri("gs://dicom-bucket/patients/p101/mr.dcm")
    assert p_gcs1.scheme == StorageScheme.GCS
    assert p_gcs1.bucket_or_share == "dicom-bucket"
    assert p_gcs1.path == "patients/p101/mr.dcm"
    assert p_gcs1.normalized_uri == "gs://dicom-bucket/patients/p101/mr.dcm"

    p_gcs2 = media_service.parse_uri("gcs://dicom-bucket/patients/p101/mr.dcm")
    assert p_gcs2.scheme == StorageScheme.GCS
    assert p_gcs2.normalized_uri == "gs://dicom-bucket/patients/p101/mr.dcm"

    # Amazon S3 (s3://)
    p_s3 = media_service.parse_uri("s3://aws-pacs-bucket/2026/09/slice1.dcm")
    assert p_s3.scheme == StorageScheme.S3
    assert p_s3.bucket_or_share == "aws-pacs-bucket"
    assert p_s3.path == "2026/09/slice1.dcm"
    assert p_s3.normalized_uri == "s3://aws-pacs-bucket/2026/09/slice1.dcm"

    # Azure Blob: support both az:// and azure://
    p_az = media_service.parse_uri("az://dicom-container/studies/study1.dcm")
    assert p_az.scheme == StorageScheme.AZ
    assert p_az.bucket_or_share == "dicom-container"
    assert p_az.path == "studies/study1.dcm"
    assert p_az.normalized_uri == "az://dicom-container/studies/study1.dcm"

    p_azure = media_service.parse_uri("azure://dicom-container/studies/study1.dcm")
    assert p_azure.scheme == StorageScheme.AZURE
    assert p_azure.bucket_or_share == "dicom-container"
    assert p_azure.path == "studies/study1.dcm"
    assert p_azure.normalized_uri == "azure://dicom-container/studies/study1.dcm"


# =============================================================================
# 2. Cloud Credentials Environment Variables Resolution Tests
# =============================================================================


def test_cloud_credentials_resolution(monkeypatch):
    # GCP Credentials
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/etc/gcp/key.json")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-gcp-project")
    monkeypatch.setenv("GCS_ENDPOINT_URL", "http://localhost:4443")

    gcs_opts = media_service.get_cloud_storage_options(StorageScheme.GCS)
    assert gcs_opts["token"] == "/etc/gcp/key.json"
    assert gcs_opts["project"] == "test-gcp-project"
    assert gcs_opts["endpoint_url"] == "http://localhost:4443"

    # AWS Credentials
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAIOSFODNN7EXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-west-2")
    monkeypatch.setenv("AWS_ENDPOINT_URL", "http://localhost:9000")

    s3_opts = media_service.get_cloud_storage_options(StorageScheme.S3)
    assert s3_opts["key"] == "AKIAIOSFODNN7EXAMPLE"
    assert s3_opts["secret"] == "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
    assert s3_opts["client_kwargs"]["region_name"] == "us-west-2"
    assert s3_opts["endpoint_url"] == "http://localhost:9000"

    # Azure Credentials
    monkeypatch.setenv(
        "AZURE_STORAGE_CONNECTION_STRING",
        "DefaultEndpointsProtocol=https;AccountName=testacc;AccountKey=testkey;EndpointSuffix=core.windows.net",
    )
    azure_opts = media_service.get_cloud_storage_options(StorageScheme.AZURE)
    assert "DefaultEndpointsProtocol" in azure_opts["connection_string"]


# =============================================================================
# 3. Local File System Tests with 10 Real MR DICOM Test Assets
# =============================================================================


@pytest.mark.anyio
async def test_local_read_all_10_mr_files(mr_files: list[Path]):
    """Test reading all 10 MR DICOM files using async media_service.read."""
    for file_path in mr_files:
        uri = f"file://{file_path.resolve()}"
        data = await media_service.read(uri)
        assert len(data) == file_path.stat().st_size
        assert data.startswith(b"\x00" * 128 + b"DICM") or b"DICM" in data[:132]


@pytest.mark.anyio
async def test_local_read_dicom_all_10_mr_files(mr_files: list[Path]):
    """Test parsing all 10 MR DICOM datasets using async media_service.read_dicom."""
    patient_ids = set()
    sop_uids = set()

    for file_path in mr_files:
        uri = f"file://{file_path.resolve()}"
        ds = await media_service.read_dicom(uri)

        # Validate DICOM header attributes
        assert ds.Modality == "MR"
        assert ds.PatientID == "GSH-90967971"
        patient_ids.add(ds.PatientID)
        sop_uids.add(ds.SOPInstanceUID)

    assert len(patient_ids) == 1
    assert len(sop_uids) == 10, "Each of the 10 MR files must have a unique SOPInstanceUID"


@pytest.mark.anyio
async def test_local_write_stat_list_delete_cycle(mr_files: list[Path]):
    """Test full CRUD lifecycle (write, stat, exists, list_files, copy, move, delete) on local storage."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)

        # 1. Write all 10 MR files to temp storage
        written_uris = []
        for i, f in enumerate(mr_files):
            target_uri = f"file://{(tmp_path / f'slice_{i:02d}.dcm').resolve()}"
            raw_bytes = f.read_bytes()
            saved_uri = await media_service.write(target_uri, raw_bytes)
            written_uris.append(saved_uri)
            assert saved_uri == target_uri
            assert await media_service.exists(target_uri)

        # 2. Verify stat for written files
        first_uri = written_uris[0]
        meta = await media_service.stat(first_uri)
        assert meta.size_bytes == mr_files[0].stat().st_size
        assert meta.is_file is True
        assert meta.scheme == StorageScheme.FILE

        # 3. List files
        listed = await media_service.list_files(f"file://{tmp_path.resolve()}")
        assert len(listed) == 10

        # Pattern filtering
        filtered = await media_service.list_files(f"file://{tmp_path.resolve()}", pattern=r"slice_0[0-2]\.dcm")
        assert len(filtered) == 3

        # 4. Copy file
        copy_dest = f"file://{(tmp_path / 'slice_00_copy.dcm').resolve()}"
        await media_service.copy(first_uri, copy_dest)
        assert await media_service.exists(copy_dest)
        copy_meta = await media_service.stat(copy_dest)
        assert copy_meta.size_bytes == meta.size_bytes

        # 5. Move file
        move_dest = f"file://{(tmp_path / 'slice_00_moved.dcm').resolve()}"
        await media_service.move(copy_dest, move_dest)
        assert not await media_service.exists(copy_dest)
        assert await media_service.exists(move_dest)

        # 6. Delete files
        deleted = await media_service.delete(move_dest)
        assert deleted is True
        assert not await media_service.exists(move_dest)


@pytest.mark.anyio
async def test_local_write_dicom_dataset(mr_files: list[Path]):
    """Test serializing and writing a pydicom Dataset directly via media_service.write_dicom."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src_uri = f"file://{mr_files[0].resolve()}"
        ds = await media_service.read_dicom(src_uri)

        # Modify an attribute
        ds.PatientComments = "Validated by GoSmart MediaService"

        dst_uri = f"file://{(Path(tmpdir) / 'modified_mr.dcm').resolve()}"
        saved_uri = await media_service.write_dicom(dst_uri, ds)
        assert saved_uri == dst_uri

        # Re-read and check
        ds_read = await media_service.read_dicom(dst_uri)
        assert ds_read.PatientComments == "Validated by GoSmart MediaService"
        assert ds_read.SOPInstanceUID == ds.SOPInstanceUID


# =============================================================================
# 4. Google Cloud Storage (GCS) Tests
# =============================================================================


@pytest.mark.anyio
async def test_gcs_operations_with_all_10_mr_files(mr_files: list[Path]):
    """Test GCS operations with simulated GCS bucket using in-memory fsspec filesystem."""
    # Create isolated MediaService instance with mocked GCS filesystem
    svc = MediaService()
    mem_gcs = fsspec.filesystem("memory")
    svc.register_fs_override(StorageScheme.GCS, mem_gcs)

    bucket = "gosmart-mr-dicom-bucket"

    # 1. Upload all 10 MR files to GCS
    gcs_uris = []
    for f in mr_files:
        gcs_uri = f"gs://{bucket}/mr_series/{f.name}"
        raw_bytes = f.read_bytes()
        saved_uri = await svc.write(gcs_uri, raw_bytes)
        gcs_uris.append(saved_uri)
        assert saved_uri == gcs_uri
        assert await svc.exists(gcs_uri)

    # 2. Validate GCS stat and metadata
    first_gcs = gcs_uris[0]
    meta = await svc.stat(first_gcs)
    assert meta.scheme == StorageScheme.GCS
    assert meta.size_bytes == mr_files[0].stat().st_size
    assert meta.name == mr_files[0].name

    # 3. Read raw bytes back from GCS
    data = await svc.read(first_gcs)
    assert len(data) == mr_files[0].stat().st_size

    # 4. Read all 10 DICOMs from GCS and verify DICOM headers
    sop_uids = set()
    for gcs_uri in gcs_uris:
        ds = await svc.read_dicom(gcs_uri)
        assert ds.Modality == "MR"
        assert ds.PatientID == "GSH-90967971"
        sop_uids.add(ds.SOPInstanceUID)
    assert len(sop_uids) == 10

    # 5. List files in GCS
    listed = await svc.list_files(f"gs://{bucket}/mr_series")
    assert len(listed) == 10

    # 6. Cross-storage copy: GCS -> Local
    with tempfile.TemporaryDirectory() as tmpdir:
        local_dst = f"file://{(Path(tmpdir) / 'downloaded.dcm').resolve()}"
        await svc.copy(first_gcs, local_dst)
        assert await svc.exists(local_dst)
        ds_local = await svc.read_dicom(local_dst)
        assert ds_local.PatientID == "GSH-90967971"

    # 7. GCS native copy and move
    copy_gcs = f"gs://{bucket}/mr_series/copy_of_first.dcm"
    await svc.copy(first_gcs, copy_gcs)
    assert await svc.exists(copy_gcs)

    moved_gcs = f"gs://{bucket}/mr_series/moved_first.dcm"
    await svc.move(copy_gcs, moved_gcs)
    assert not await svc.exists(copy_gcs)
    assert await svc.exists(moved_gcs)

    # 8. Delete from GCS
    deleted = await svc.delete(moved_gcs)
    assert deleted is True
    assert not await svc.exists(moved_gcs)


# =============================================================================
# 5. AWS S3 and Azure Blob Scaffolding Tests
# =============================================================================


@pytest.mark.anyio
async def test_s3_scaffolding(mr_files: list[Path]):
    """Test S3 scaffolding operations with simulated S3 filesystem."""
    svc = MediaService()
    mem_s3 = fsspec.filesystem("memory")
    svc.register_fs_override(StorageScheme.S3, mem_s3)

    s3_uri = "s3://clinical-dicom-s3/test_mr.dcm"
    raw_data = mr_files[0].read_bytes()

    # Write
    saved = await svc.write(s3_uri, raw_data)
    assert saved == s3_uri
    assert await svc.exists(s3_uri)

    # Stat
    stat = await svc.stat(s3_uri)
    assert stat.scheme == StorageScheme.S3
    assert stat.size_bytes == len(raw_data)

    # Read DICOM
    ds = await svc.read_dicom(s3_uri)
    assert ds.Modality == "MR"

    # Delete
    assert await svc.delete(s3_uri)
    assert not await svc.exists(s3_uri)


@pytest.mark.anyio
async def test_azure_scaffolding(mr_files: list[Path]):
    """Test Azure Blob scaffolding operations supporting both az:// and azure://."""
    svc = MediaService()
    mem_az = fsspec.filesystem("memory")
    svc.register_fs_override(StorageScheme.AZ, mem_az)

    raw_data = mr_files[0].read_bytes()

    # 1. Test az:// prefix
    az_uri = "az://clinical-dicom-container/test_mr_az.dcm"
    saved_az = await svc.write(az_uri, raw_data)
    assert saved_az == az_uri
    assert await svc.exists(az_uri)

    stat_az = await svc.stat(az_uri)
    assert stat_az.scheme == StorageScheme.AZ
    assert stat_az.size_bytes == len(raw_data)

    ds_az = await svc.read_dicom(az_uri)
    assert ds_az.Modality == "MR"

    # 2. Test azure:// prefix
    azure_uri = "azure://clinical-dicom-container/test_mr_azure.dcm"
    saved_azure = await svc.write(azure_uri, raw_data)
    assert saved_azure == azure_uri
    assert await svc.exists(azure_uri)

    stat_azure = await svc.stat(azure_uri)
    assert stat_azure.scheme == StorageScheme.AZURE
    assert stat_azure.size_bytes == len(raw_data)

    ds_azure = await svc.read_dicom(azure_uri)
    assert ds_azure.Modality == "MR"

    # 3. Clean up
    assert await svc.delete(az_uri)
    assert not await svc.exists(az_uri)
    assert await svc.delete(azure_uri)
    assert not await svc.exists(azure_uri)


# =============================================================================
# 6. Pykka Actor Compatibility & Synchronous Helper Execution Tests
# =============================================================================


def test_sync_methods_and_thread_execution(mr_files: list[Path]):
    """Test synchronous methods and execution from background worker threads (like Pykka actors)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        dst = f"file://{(Path(tmpdir) / 'sync_test.dcm').resolve()}"
        sample_bytes = mr_files[0].read_bytes()

        # Synchronous write
        saved = media_service.write_sync(dst, sample_bytes)
        assert saved == dst
        assert media_service.exists_sync(dst)

        # Synchronous stat
        meta = media_service.stat_sync(dst)
        assert meta.size_bytes == len(sample_bytes)

        # Synchronous read_dicom
        ds = media_service.read_dicom_sync(dst)
        assert ds.Modality == "MR"

        # Synchronous list_files
        listed = media_service.list_files_sync(f"file://{Path(tmpdir).resolve()}")
        assert len(listed) == 1

        # Synchronous delete
        assert media_service.delete_sync(dst)
        assert not media_service.exists_sync(dst)


def test_async_method_called_from_separate_thread_with_run_sync(mr_files: list[Path]):
    """Ensure that Pykka ThreadingActors or worker threads can safely execute MediaService methods."""

    def worker_job():
        # This runs in a new OS thread without an asyncio event loop, exactly like a Pykka ThreadingActor
        uri = f"file://{mr_files[0].resolve()}"
        # Test calling run_sync directly
        data = media_service.run_sync(media_service.read(uri))
        assert len(data) == mr_files[0].stat().st_size

        ds = media_service.run_sync(media_service.read_dicom(uri))
        assert ds.PatientID == "GSH-90967971"
        return True

    with ThreadPoolExecutor(max_workers=2) as executor:
        future = executor.submit(worker_job)
        assert future.result(timeout=5.0) is True


def test_run_sync_when_event_loop_is_already_active():
    """Verify run_sync does not deadlock or crash even if called while an event loop is running."""

    async def nested_caller():
        # Call run_sync from INSIDE an active asyncio loop
        res = media_service.run_sync(asyncio.sleep(0.01, result="OK"))
        assert res == "OK"

    asyncio.run(nested_caller())
