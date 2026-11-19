from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from gosmart_graphite.actor.dicom_ingestion_actor import DicomIngestionActor
from gosmart_graphite.actor.fhir_actor import FhirActor, notify_fhir
from gosmart_graphite.core.config import Settings
from gosmart_graphite.model.actor_message import (
    DicomIngestionMessage,
    DicomMockIngestionMessage,
    FhirIngestionMessage,
)
from gosmart_graphite.model.audit import AuditAction
from gosmart_graphite.model.auth import GroupNode
from gosmart_graphite.model.dicom import (
    DicomHierarchy,
    InstanceModel,
    PatientModel,
    SeriesModel,
    StudyModel,
)
from gosmart_graphite.model.graph import FHIRConfigurationNode
from gosmart_graphite.service.dicom_service import DicomService, dicom_service
from gosmart_graphite.service.fhir_config_service import FHIRConfigService
from gosmart_graphite.service.fhir_service import EncRelevantResources
from gosmart_graphite.service.graph_service import GraphService
from gosmart_graphite.service.group_service import GroupService
from gosmart_graphite.service.media_service import MediaService
from gosmart_graphite.service.pynetdicom_service import PyNetDicomService


def test_actor_lifecycle_and_messaging():
    actor = DicomIngestionActor.start()
    try:
        assert actor.is_alive()

        # Test PING
        ping_res = actor.ask({"type": "PING"}, timeout=2.0)
        assert ping_res["status"] == "PONG"
        assert ping_res["processed_count"] == 0

        # Test Unknown message handling
        unknown_res = actor.ask({"type": "UNKNOWN_ACTION"}, timeout=2.0)
        assert "error" in unknown_res

        # Test unsupported non-dict/non-model message
        unsupported_res = actor.ask("invalid_string_payload", timeout=2.0)
        assert unsupported_res["success"] is False
        assert "Unsupported message type" in unsupported_res["error"]
    finally:
        actor.stop()
        assert not actor.is_alive()


def test_dicom_ingestion_message_validation():
    msg = DicomIngestionMessage.model_validate({"file_path": Path("/tmp/test.dcm"), "acted_by": None})
    assert msg.type == "INGEST_FILE"
    assert msg.file_path == "/tmp/test.dcm"
    assert msg.acted_by == "actor"
    assert msg.group_id is None
    assert msg.storage_root_uri is None
    assert msg.cleanup_source is False

    with pytest.raises(ValidationError):
        DicomIngestionMessage(file_path="")

    with pytest.raises(ValidationError):
        DicomIngestionMessage(file_path="   ")


def test_dicom_mock_ingestion_message_defaults():
    msg = DicomMockIngestionMessage(acted_by="tester")
    assert msg.type == "INGEST_MOCK"
    assert msg.patient_id == "MOCK_PATIENT"
    assert msg.modality == "MR"
    assert msg.source == "file:mock"
    assert msg.source_identity == "user:tester"
    assert msg.destination_identity == "service:mock"


def test_dicom_ingestion_actor_rejects_invalid_dict_and_accepts_model():
    actor = DicomIngestionActor.start()
    try:
        # Missing file_path in dict triggers Pydantic validation error
        invalid_res = actor.ask({"type": "INGEST_FILE"}, timeout=2.0)
        assert invalid_res["success"] is False
        assert "Invalid DicomIngestionMessage" in invalid_res["error"]

        # Valid DicomIngestionMessage routed through _handle_ingest_file
        with patch.object(DicomIngestionActor, "_handle_ingest_file", return_value={"success": True}) as mock_handler:
            res = actor.ask(DicomIngestionMessage(file_path="/tmp/sample.dcm", test_id="t1"), timeout=2.0)
            assert res == {"success": True}
            mock_handler.assert_called_once()
            received_msg = mock_handler.call_args[0][0]
            assert isinstance(received_msg, DicomIngestionMessage)
            assert received_msg.file_path == "/tmp/sample.dcm"
            assert received_msg.test_id == "t1"
    finally:
        actor.stop()


def test_dicom_ingestion_actor_notifies_fhir_on_ingest_file_complete():
    hierarchy = DicomHierarchy(
        patient=PatientModel(patient_id="PT-100"),
        study=StudyModel(study_instance_uid="1.2.840.100", accession_number="ACC-100"),
        series=SeriesModel(series_instance_uid="1.2.840.200", modality="CT"),
        instance=InstanceModel(sop_instance_uid="1.2.840.300"),
    )
    mock_fhir_actor = MagicMock()

    actor = DicomIngestionActor.start()
    try:
        with (
            patch.object(DicomService, "parse_file", return_value=hierarchy),
            patch.object(MediaService, "copy_sync", return_value="gs://prod-bucket/1.2.840.300.dcm") as mock_copy,
            patch.object(GraphService, "save_hierarchy_sync", return_value={"status": "saved"}),
            patch("gosmart_graphite.actor.dicom_ingestion_actor.record_audit"),
            patch("gosmart_graphite.actor.fhir_actor.get_fhir_actor", return_value=mock_fhir_actor),
        ):
            res = actor.ask(
                DicomIngestionMessage(
                    file_path="/tmp/sample.dcm",
                    test_id="test_fhir_hook",
                    acted_by="tester",
                    group_id="grp_1",
                    storage_root_uri="gs://prod-bucket",
                ),
                timeout=2.0,
            )

        assert res["success"] is True
        assert res["patient_id"] == "PT-100"
        assert res["study_instance_uid"] == "1.2.840.100"
        assert res["sop_instance_uid"] == "1.2.840.300"
        assert res["storage_uri"] == "gs://prod-bucket/1.2.840.300.dcm"
        assert res["group_id"] == "grp_1"
        assert hierarchy.instance.storage_uri == "gs://prod-bucket/1.2.840.300.dcm"
        mock_copy.assert_called_once()
        mock_fhir_actor.tell.assert_called_once()
        sent_msg = mock_fhir_actor.tell.call_args[0][0]
        assert isinstance(sent_msg, FhirIngestionMessage)
        assert sent_msg.patient_id == "PT-100"
        assert sent_msg.accession_number == "ACC-100"
        assert sent_msg.study_uid == "1.2.840.100"
        assert sent_msg.series_uid == "1.2.840.200"
        assert sent_msg.test_id == "test_fhir_hook"
        assert sent_msg.acted_by == "tester"
        assert sent_msg.group_id == "grp_1"
    finally:
        actor.stop()


def test_dicom_ingestion_actor_does_not_notify_fhir_on_ingest_file_failure():
    mock_fhir_actor = MagicMock()
    actor = DicomIngestionActor.start()
    try:
        with (
            patch.object(DicomService, "parse_file", side_effect=RuntimeError("corrupt file")),
            patch("gosmart_graphite.actor.dicom_ingestion_actor.record_audit"),
            patch("gosmart_graphite.actor.fhir_actor.get_fhir_actor", return_value=mock_fhir_actor),
        ):
            res = actor.ask(
                DicomIngestionMessage(
                    file_path="/tmp/bad.dcm",
                    test_id="test_fhir_hook_fail",
                    acted_by="tester",
                ),
                timeout=2.0,
            )

        assert res["success"] is False
        assert "corrupt file" in res["error"]
        mock_fhir_actor.tell.assert_not_called()
    finally:
        actor.stop()


def test_dicom_ingestion_actor_ae_group_and_storage_copy_and_cleanup(tmp_path: Path):
    """Verify AE->Group lookup, AE storage_root_uri copy (no local move shortcut), and staging cleanup."""
    staging_dir = tmp_path / "staging"
    dest_dir = tmp_path / "dest_storage"
    staging_dir.mkdir()

    ds = dicom_service.create_mock_dicom(patient_id="PT-AE-1", modality="MR")
    sop_uid = str(ds.SOPInstanceUID)
    staged_file = staging_dir / f"{sop_uid}_abcd1234.dcm"
    ds.save_as(str(staged_file), enforce_file_format=True)

    mock_fhir_actor = MagicMock()
    saved_hierarchies: list[DicomHierarchy] = []

    def _capture_save(h: DicomHierarchy):
        saved_hierarchies.append(h)
        return {"p_id": "p1"}

    actor = DicomIngestionActor.start()
    orig_copy_sync = MediaService.copy_sync
    try:
        with (
            patch.object(GroupService, "get_group_for_ae_title_sync", return_value={"id": "grp_clinic_a"}) as mock_grp,
            patch.object(GroupService, "get_storage_root_for_ae_title_sync", return_value=str(dest_dir)) as mock_root,
            patch.object(MediaService, "copy_sync", autospec=True, side_effect=orig_copy_sync) as spy_copy,
            patch.object(GraphService, "save_hierarchy_sync", side_effect=_capture_save),
            patch("gosmart_graphite.actor.dicom_ingestion_actor.record_audit"),
            patch("gosmart_graphite.actor.fhir_actor.get_fhir_actor", return_value=mock_fhir_actor),
        ):
            res = actor.ask(
                DicomIngestionMessage(
                    file_path=str(staged_file),
                    acted_by="CT_SCANNER_A",
                    source="dimse:10.0.0.5",
                    source_identity="ae:CT_SCANNER_A",
                    destination="dimse:127.0.0.1:11112",
                    destination_identity="ae:GOSMART_GRAPHITE",
                    cleanup_source=True,
                ),
                timeout=5.0,
            )

        assert res["success"] is True
        assert res["group_id"] == "grp_clinic_a"
        mock_grp.assert_called_once_with("CT_SCANNER_A")
        mock_root.assert_called_once_with("CT_SCANNER_A")
        spy_copy.assert_called_once()

        dest_file = dest_dir / f"{sop_uid}.dcm"
        assert dest_file.exists(), "DICOM file must be copied to destination storage"
        assert not staged_file.exists(), "Local staging file must be removed after successful copy & graph save"

        assert len(saved_hierarchies) == 1
        h = saved_hierarchies[0]
        assert h.group_id == "grp_clinic_a"
        assert h.instance.storage_uri == f"file://{dest_file.resolve().as_posix()}"
        assert h.instance.destination == "dimse:127.0.0.1:11112"

        mock_fhir_actor.tell.assert_called_once()
        fhir_msg = mock_fhir_actor.tell.call_args[0][0]
        assert fhir_msg.group_id == "grp_clinic_a"
        assert fhir_msg.patient_id == "PT-AE-1"
    finally:
        actor.stop()


def test_dicom_ingestion_actor_falls_back_to_lost_and_found_id_string(tmp_path: Path):
    """Verify unmapped AE falls back to LostAndFound string ID (not GroupNode object)."""
    staging_dir = tmp_path / "staging"
    dest_dir = tmp_path / "fallback_storage"
    staging_dir.mkdir()

    ds = dicom_service.create_mock_dicom(patient_id="PT-LOST-1", modality="CT")
    sop_uid = str(ds.SOPInstanceUID)
    staged_file = staging_dir / f"{sop_uid}_11112222.dcm"
    ds.save_as(str(staged_file), enforce_file_format=True)

    lost_and_found = GroupNode(name="LostAndFound", code="LOST_AND_FOUND", group_type="SYSTEM")
    mock_fhir_actor = MagicMock()
    saved_hierarchies: list[DicomHierarchy] = []

    actor = DicomIngestionActor.start()
    try:
        with (
            patch.object(GroupService, "get_group_for_ae_title_sync", return_value=None),
            patch.object(GroupService, "get_storage_root_for_ae_title_sync", return_value=None),
            patch.object(GroupService, "ensure_lost_and_found_group_sync", return_value=lost_and_found) as mock_laf,
            patch(
                "gosmart_graphite.actor.dicom_ingestion_actor.get_settings",
                return_value=Settings(STORAGE_ROOT_URI=f"file://{dest_dir}"),
            ),
            patch.object(GraphService, "save_hierarchy_sync", side_effect=lambda h: saved_hierarchies.append(h) or {}),
            patch("gosmart_graphite.actor.dicom_ingestion_actor.record_audit"),
            patch("gosmart_graphite.actor.fhir_actor.get_fhir_actor", return_value=mock_fhir_actor),
        ):
            res = actor.ask(
                DicomIngestionMessage(
                    file_path=str(staged_file),
                    acted_by="UNMAPPED_SCU",
                    source="dimse:127.0.0.1",
                    source_identity="ae:UNMAPPED_SCU",
                    cleanup_source=True,
                ),
                timeout=5.0,
            )

        assert res["success"] is True
        mock_laf.assert_called_once()
        assert isinstance(res["group_id"], str)
        assert res["group_id"] == lost_and_found.id
        assert saved_hierarchies[0].group_id == lost_and_found.id
        assert (dest_dir / f"{sop_uid}.dcm").exists()
        assert not staged_file.exists()

        mock_fhir_actor.tell.assert_called_once()
        assert mock_fhir_actor.tell.call_args[0][0].group_id == lost_and_found.id
    finally:
        actor.stop()


def test_dicom_ingestion_actor_preserves_staging_and_skips_fhir_on_neo4j_failure(tmp_path: Path):
    """When Neo4j graph registration fails, staging file is kept and FhirActor is NOT notified."""
    staging_dir = tmp_path / "staging"
    dest_dir = tmp_path / "dest_storage"
    staging_dir.mkdir()

    ds = dicom_service.create_mock_dicom(patient_id="PT-DBFAIL", modality="CT")
    sop_uid = str(ds.SOPInstanceUID)
    staged_file = staging_dir / f"{sop_uid}_dbfail.dcm"
    ds.save_as(str(staged_file), enforce_file_format=True)

    mock_fhir_actor = MagicMock()
    actor = DicomIngestionActor.start()
    try:
        with (
            patch.object(GroupService, "get_group_for_ae_title_sync", return_value={"id": "grp_1"}),
            patch.object(GroupService, "get_storage_root_for_ae_title_sync", return_value=str(dest_dir)),
            patch.object(GraphService, "save_hierarchy_sync", side_effect=RuntimeError("Neo4j unavailable")),
            patch("gosmart_graphite.actor.dicom_ingestion_actor.record_audit") as mock_audit,
            patch("gosmart_graphite.actor.fhir_actor.get_fhir_actor", return_value=mock_fhir_actor),
        ):
            res = actor.ask(
                DicomIngestionMessage(
                    file_path=str(staged_file),
                    acted_by="SCU_FAIL",
                    source="dimse:127.0.0.1",
                    source_identity="ae:SCU_FAIL",
                    cleanup_source=True,
                ),
                timeout=5.0,
            )

        assert res["success"] is False
        assert "Neo4j unavailable" in res["error"]
        assert staged_file.exists(), "Local staging file must be preserved on Neo4j failure for recovery"
        assert (dest_dir / f"{sop_uid}.dcm").exists(), "Copied destination file remains intact"
        mock_fhir_actor.tell.assert_not_called()
        mock_audit.assert_called_once()
        assert mock_audit.call_args.kwargs["status_code"] == 500
    finally:
        actor.stop()


def test_handle_c_store_stages_locally_and_dispatches_single_actor_call(tmp_path: Path):
    """Verify handle_c_store captures to local staging dir, calls actor.tell once, and returns 0x0000."""
    settings = Settings(DICOM_STAGING_DIR=str(tmp_path / "c_store_staging"))
    svc = PyNetDicomService(settings=settings)

    ds = dicom_service.create_mock_dicom(patient_id="PT-CSTORE-1", modality="MR")
    sop_uid = str(ds.SOPInstanceUID)

    mock_event = MagicMock()
    mock_event.request.AffectedSOPInstanceUID = sop_uid
    mock_event.dataset = ds
    mock_event.file_meta = ds.file_meta
    mock_event.assoc.requestor.address = "10.1.2.3"
    mock_event.assoc.requestor.ae_title = "MODALITY_A   "
    mock_event.assoc.acceptor.address = "127.0.0.1"
    mock_event.assoc.acceptor.port = 11112
    mock_event.assoc.acceptor.ae_title = "GOSMART_GRAPHITE"

    mock_actor = MagicMock()
    mock_actor.is_alive.return_value = True

    with (
        patch("gosmart_graphite.actor.dicom_ingestion_actor.get_dicom_actor", return_value=mock_actor),
        patch("gosmart_graphite.service.pynetdicom_service.record_audit") as mock_audit,
        patch("gosmart_graphite.service.pynetdicom_service.group_service") as mock_group_svc,
        patch("gosmart_graphite.service.pynetdicom_service.graph_service") as mock_graph_svc,
    ):
        status = svc.handle_c_store(mock_event)

    assert status == 0x0000
    mock_group_svc.get_group_for_ae_title_sync.assert_not_called()
    mock_graph_svc.save_hierarchy_sync.assert_not_called()

    mock_actor.tell.assert_called_once()
    msg = mock_actor.tell.call_args[0][0]
    assert isinstance(msg, DicomIngestionMessage)
    assert Path(msg.file_path).exists()
    assert Path(msg.file_path).parent == (tmp_path / "c_store_staging").resolve()
    assert msg.acted_by == "MODALITY_A"
    assert msg.source == "dimse:10.1.2.3"
    assert msg.source_identity == "ae:MODALITY_A"
    assert msg.destination == "dimse:127.0.0.1:11112"
    assert msg.destination_identity == "ae:GOSMART_GRAPHITE"
    assert msg.cleanup_source is True

    mock_audit.assert_called_once()
    assert mock_audit.call_args.kwargs["action"] == AuditAction.DICOM_C_STORE_INGESTED
    assert mock_audit.call_args.kwargs["actor_id"] == "ae:MODALITY_A"
    assert mock_audit.call_args.kwargs["actor"] == "MODALITY_A"
    assert mock_audit.call_args.kwargs["target_id"] == f"instance:{sop_uid}"


def test_handle_c_store_audits_source_path_and_returns_0xa700_on_actor_handoff_failure(tmp_path: Path):
    """If actor handoff fails after local staging save, audit source_path + reason and return 0xA700."""
    settings = Settings(DICOM_STAGING_DIR=str(tmp_path / "c_store_staging"))
    svc = PyNetDicomService(settings=settings)

    ds = dicom_service.create_mock_dicom(patient_id="PT-HANDOFF-FAIL", modality="CT")
    sop_uid = str(ds.SOPInstanceUID)

    mock_event = MagicMock()
    mock_event.request.AffectedSOPInstanceUID = sop_uid
    mock_event.dataset = ds
    mock_event.file_meta = ds.file_meta
    mock_event.assoc.requestor.address = "10.1.2.4"
    mock_event.assoc.requestor.ae_title = "MODALITY_B"
    mock_event.assoc.acceptor.address = "127.0.0.1"
    mock_event.assoc.acceptor.port = 11112
    mock_event.assoc.acceptor.ae_title = "GOSMART_GRAPHITE"

    mock_actor = MagicMock()
    mock_actor.is_alive.return_value = True
    mock_actor.tell.side_effect = RuntimeError("Actor mailbox full")

    with (
        patch("gosmart_graphite.actor.dicom_ingestion_actor.get_dicom_actor", return_value=mock_actor),
        patch("gosmart_graphite.service.pynetdicom_service.record_audit") as mock_audit,
    ):
        status = svc.handle_c_store(mock_event)

    assert status == 0xA700
    mock_audit.assert_called_once()
    audit_kwargs = mock_audit.call_args.kwargs
    assert audit_kwargs["action"] == AuditAction.DICOM_C_STORE_FAILED
    assert audit_kwargs["severity"] == "ERROR"
    assert audit_kwargs["status_code"] == 500
    details = audit_kwargs["details"]
    assert "source_path" in details
    assert Path(details["source_path"]).exists(), "Staged DICOM file must stay on disk for support recovery"
    assert "Actor mailbox full" in details["failure_reason"]
    assert details["error_type"] == "RuntimeError"


def test_fhir_ingestion_message_and_actor_validation():
    msg = FhirIngestionMessage.model_validate(
        {"study_uid": "1.2.3", "accession_number": None, "acted_by": None}
    )
    assert msg.study_uid == "1.2.3"
    assert msg.accession_number == ""
    assert msg.acted_by == "fhir_actor"
    assert msg.group_id is None

    with pytest.raises(ValidationError):
        FhirIngestionMessage(study_uid=12345)  # type: ignore[arg-type]

    actor = FhirActor.start()
    try:
        assert actor.ask({"type": "PING"}, timeout=2.0) == {"status": "PONG", "processed_count": 0}
        assert actor.ask({"type": "STATUS"}, timeout=2.0) == {
            "processed_count": 0,
            "last_status": "INITIALIZED",
        }

        # Invalid dict payload rejected via Pydantic validation
        invalid_res = actor.ask({"study_uid": ["bad"]}, timeout=2.0)
        assert invalid_res["success"] is False
        assert "Invalid FhirIngestionMessage" in invalid_res["error"]

        # Valid FhirIngestionMessage (empty study_uid -> IDLE)
        res = actor.ask(FhirIngestionMessage(patient_id="PT-1", study_uid=""), timeout=2.0)
        assert res["success"] is True
        assert res["status"] == "IDLE"
    finally:
        actor.stop()


def test_fhir_actor_resolves_group_scoped_fhir_configuration():
    """Verify FhirActor resolves group-scoped FHIRConfiguration URL via fhir_config_service."""
    group_cfg = FHIRConfigurationNode(url="https://clinic-a.fhir.example.org/r4")
    captured_urls: list[str] = []

    class _DummyFetcher:
        def __init__(self, base_url: str = "") -> None:
            self.base_url = base_url
            captured_urls.append(base_url)

        async def close(self) -> None:
            return None

    class _DummyFhirService:
        def __init__(self, fetcher=None) -> None:
            self.fetcher = fetcher or _DummyFetcher()

        fetch_relevant_resources_of_study = AsyncMock(return_value=EncRelevantResources())

    actor = FhirActor.start()
    try:
        with (
            patch.object(
                FHIRConfigService,
                "resolve_config_for_group_sync",
                return_value=group_cfg,
            ) as mock_resolve,
            patch("gosmart_graphite.actor.fhir_actor.FHIRBatchFetcher", _DummyFetcher),
            patch("gosmart_graphite.actor.fhir_actor.FhirService", _DummyFhirService),
        ):
            res = actor.ask(
                FhirIngestionMessage(
                    patient_id="PT-1",
                    study_uid="1.2.840.999",
                    series_uid="1.2.840.999.1",
                    group_id="grp_clinic_a",
                    test_id="t_fhir_cfg",
                ),
                timeout=3.0,
            )

        assert res["success"] is True
        assert res["status"] == "NOT_FOUND"
        mock_resolve.assert_called_once_with(group_id="grp_clinic_a", test_id="t_fhir_cfg")
        assert captured_urls == ["https://clinic-a.fhir.example.org/r4"]
    finally:
        actor.stop()


def test_notify_fhir_sends_pydantic_message():
    mock_actor = MagicMock()
    with patch("gosmart_graphite.actor.fhir_actor.get_fhir_actor", return_value=mock_actor):
        notify_fhir(
            patient_id="PT-123",
            accession_number=None,
            study_uid="1.2.840.1",
            series_uid="1.2.840.2",
            test_id="test_1",
            acted_by="scp",
            group_id="grp_42",
        )

    mock_actor.tell.assert_called_once()
    sent_msg = mock_actor.tell.call_args[0][0]
    assert isinstance(sent_msg, FhirIngestionMessage)
    assert sent_msg.patient_id == "PT-123"
    assert sent_msg.accession_number == ""
    assert sent_msg.study_uid == "1.2.840.1"
    assert sent_msg.series_uid == "1.2.840.2"
    assert sent_msg.test_id == "test_1"
    assert sent_msg.acted_by == "scp"
    assert sent_msg.group_id == "grp_42"
