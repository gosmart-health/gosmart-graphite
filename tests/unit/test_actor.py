from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from gosmart_graphite.actor.dicom_ingestion_actor import DicomIngestionActor
from gosmart_graphite.actor.fhir_actor import FhirActor, notify_fhir
from gosmart_graphite.core.config import Settings
from gosmart_graphite.model.actor_message import (
    DicomAssociationReleasedMessage,
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
    msg = DicomIngestionMessage.model_validate(
        {"file_path": Path("/tmp/test.dcm"), "acted_by": None, "association_id": "   "}
    )
    assert msg.type == "INGEST_FILE"
    assert msg.file_path == "/tmp/test.dcm"
    assert msg.acted_by == "actor"
    assert msg.group_id is None
    assert msg.storage_root_uri is None
    assert msg.cleanup_source is False
    assert msg.association_id is None

    msg_with_assoc = DicomIngestionMessage(file_path="/tmp/test.dcm", association_id="  dimse:abc  ")
    assert msg_with_assoc.association_id == "dimse:abc"

    with pytest.raises(ValidationError):
        DicomIngestionMessage(file_path="")

    with pytest.raises(ValidationError):
        DicomIngestionMessage(file_path="   ")

    rel_msg = DicomAssociationReleasedMessage(association_id="  dimse:abc  ")
    assert rel_msg.type == "ASSOCIATION_RELEASED"
    assert rel_msg.association_id == "dimse:abc"

    with pytest.raises(ValidationError):
        DicomAssociationReleasedMessage(association_id="")

    with pytest.raises(ValidationError):
        DicomAssociationReleasedMessage(association_id="   ")


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


def test_dicom_ingestion_actor_deduplicates_fhir_by_association_study_series_and_clears_on_release():
    """Within an association, FHIR pull runs once per (association_id, study_uid, series_uid) and resets on release."""

    def _make_hierarchy(study_uid: str, series_uid: str, sop_uid: str) -> DicomHierarchy:
        return DicomHierarchy(
            patient=PatientModel(patient_id="PT-DEDUP"),
            study=StudyModel(study_instance_uid=study_uid, accession_number="ACC-DEDUP"),
            series=SeriesModel(series_instance_uid=series_uid, modality="CT"),
            instance=InstanceModel(sop_instance_uid=sop_uid),
        )

    hierarchies = [
        _make_hierarchy("1.2.840.1", "1.2.840.1.1", "1.2.840.1.1.1"),  # assoc_1, s1, se1 -> notify #1
        _make_hierarchy("1.2.840.1", "1.2.840.1.1", "1.2.840.1.1.2"),  # assoc_1, s1, se1 -> SKIP
        _make_hierarchy("1.2.840.1", "1.2.840.1.2", "1.2.840.1.2.1"),  # assoc_1, s1, se2 -> notify #2
        _make_hierarchy("1.2.840.2", "1.2.840.1.1", "1.2.840.2.1.1"),  # assoc_1, s2, se1 -> notify #3
        _make_hierarchy("1.2.840.1", "1.2.840.1.1", "1.2.840.1.1.3"),  # assoc_2, s1, se1 -> notify #4
        _make_hierarchy("1.2.840.1", "1.2.840.1.1", "1.2.840.1.1.4"),  # assoc_1 after release -> notify #5
    ]
    mock_fhir_actor = MagicMock()
    actor = DicomIngestionActor.start()
    try:
        with (
            patch.object(DicomService, "parse_file", side_effect=hierarchies),
            patch.object(MediaService, "copy_sync", return_value="file:///tmp/out.dcm"),
            patch.object(GraphService, "save_hierarchy_sync", return_value={"status": "saved"}),
            patch("gosmart_graphite.actor.dicom_ingestion_actor.record_audit"),
            patch("gosmart_graphite.actor.fhir_actor.get_fhir_actor", return_value=mock_fhir_actor),
        ):
            r1 = actor.ask(
                DicomIngestionMessage(file_path="/tmp/1.dcm", group_id="g1", association_id="assoc_1"),
                timeout=2.0,
            )
            r2 = actor.ask(
                DicomIngestionMessage(file_path="/tmp/2.dcm", group_id="g1", association_id="assoc_1"),
                timeout=2.0,
            )
            r3 = actor.ask(
                DicomIngestionMessage(file_path="/tmp/3.dcm", group_id="g1", association_id="assoc_1"),
                timeout=2.0,
            )
            r4 = actor.ask(
                DicomIngestionMessage(file_path="/tmp/4.dcm", group_id="g1", association_id="assoc_1"),
                timeout=2.0,
            )
            r5 = actor.ask(
                DicomIngestionMessage(file_path="/tmp/5.dcm", group_id="g1", association_id="assoc_2"),
                timeout=2.0,
            )

            assert r1["fhir_notified"] is True
            assert r2["fhir_notified"] is False
            assert r3["fhir_notified"] is True
            assert r4["fhir_notified"] is True
            assert r5["fhir_notified"] is True
            assert mock_fhir_actor.tell.call_count == 4

            # Release assoc_1 via typed message -> clears 3 entries
            rel1 = actor.ask(DicomAssociationReleasedMessage(association_id="assoc_1"), timeout=2.0)
            assert rel1 == {"success": True, "association_id": "assoc_1", "cleared_count": 3}

            # Same (assoc_1, s1, se1) now triggers FHIR pull again
            r6 = actor.ask(
                DicomIngestionMessage(file_path="/tmp/6.dcm", group_id="g1", association_id="assoc_1"),
                timeout=2.0,
            )
            assert r6["fhir_notified"] is True
            assert mock_fhir_actor.tell.call_count == 5

            # Release assoc_2 via dict message
            rel2 = actor.ask({"type": "ASSOCIATION_RELEASED", "association_id": "assoc_2"}, timeout=2.0)
            assert rel2 == {"success": True, "association_id": "assoc_2", "cleared_count": 1}

            # Invalid dict release message rejected cleanly
            bad_rel = actor.ask({"type": "ASSOCIATION_RELEASED", "association_id": "   "}, timeout=2.0)
            assert bad_rel["success"] is False
            assert "Invalid DicomAssociationReleasedMessage" in bad_rel["error"]
    finally:
        actor.stop()


def test_dicom_ingestion_actor_always_notifies_fhir_when_association_id_is_none():
    """When association_id is None, every ingested file triggers FHIR notification (backward compatible)."""
    h1 = DicomHierarchy(
        patient=PatientModel(patient_id="PT-NO-ASSOC"),
        study=StudyModel(study_instance_uid="1.2.840.99", accession_number="ACC-99"),
        series=SeriesModel(series_instance_uid="1.2.840.99.1", modality="MR"),
        instance=InstanceModel(sop_instance_uid="1.2.840.99.1.1"),
    )
    h2 = DicomHierarchy(
        patient=PatientModel(patient_id="PT-NO-ASSOC"),
        study=StudyModel(study_instance_uid="1.2.840.99", accession_number="ACC-99"),
        series=SeriesModel(series_instance_uid="1.2.840.99.1", modality="MR"),
        instance=InstanceModel(sop_instance_uid="1.2.840.99.1.2"),
    )
    mock_fhir_actor = MagicMock()
    actor = DicomIngestionActor.start()
    try:
        with (
            patch.object(DicomService, "parse_file", side_effect=[h1, h2]),
            patch.object(MediaService, "copy_sync", return_value="file:///tmp/out.dcm"),
            patch.object(GraphService, "save_hierarchy_sync", return_value={"status": "saved"}),
            patch("gosmart_graphite.actor.dicom_ingestion_actor.record_audit"),
            patch("gosmart_graphite.actor.fhir_actor.get_fhir_actor", return_value=mock_fhir_actor),
        ):
            r1 = actor.ask(DicomIngestionMessage(file_path="/tmp/a.dcm", group_id="g1"), timeout=2.0)
            r2 = actor.ask(DicomIngestionMessage(file_path="/tmp/b.dcm", group_id="g1"), timeout=2.0)
        assert r1["fhir_notified"] is True
        assert r2["fhir_notified"] is True
        assert mock_fhir_actor.tell.call_count == 2
    finally:
        actor.stop()


def test_pynetdicom_c_store_association_id_and_release_lifecycle(tmp_path: Path):
    """Verify PyNetDicomService assigns stable association_id per Association and emits release once."""
    settings = Settings(DICOM_STAGING_DIR=str(tmp_path / "assoc_staging"))
    svc = PyNetDicomService(settings=settings)

    def _make_event(assoc_obj: MagicMock, sop_suffix: str) -> MagicMock:
        ds = dicom_service.create_mock_dicom(patient_id=f"PT-{sop_suffix}", modality="CT")
        ev = MagicMock()
        ev.request.AffectedSOPInstanceUID = str(ds.SOPInstanceUID)
        ev.dataset = ds
        ev.file_meta = ds.file_meta
        ev.assoc = assoc_obj
        return ev

    assoc_a = MagicMock()
    assoc_a.requestor.address = "10.0.0.1"
    assoc_a.requestor.ae_title = "SCU_A"
    assoc_a.acceptor.address = "127.0.0.1"
    assoc_a.acceptor.port = 11112
    assoc_a.acceptor.ae_title = "GOSMART_GRAPHITE"

    assoc_b = MagicMock()
    assoc_b.requestor.address = "10.0.0.2"
    assoc_b.requestor.ae_title = "SCU_B"
    assoc_b.acceptor.address = "127.0.0.1"
    assoc_b.acceptor.port = 11112
    assoc_b.acceptor.ae_title = "GOSMART_GRAPHITE"

    assoc_echo_only = MagicMock()

    mock_actor = MagicMock()
    mock_actor.is_alive.return_value = True

    with (
        patch("gosmart_graphite.actor.dicom_ingestion_actor.get_dicom_actor", return_value=mock_actor),
        patch("gosmart_graphite.service.pynetdicom_service.record_audit"),
    ):
        assert svc.handle_c_store(_make_event(assoc_a, "A1")) == 0x0000
        assert svc.handle_c_store(_make_event(assoc_a, "A2")) == 0x0000
        assert svc.handle_c_store(_make_event(assoc_b, "B1")) == 0x0000

        # Simulate both EVT_RELEASED and EVT_CONN_CLOSE firing for assoc_a (should notify once)
        rel_event_a = MagicMock(assoc=assoc_a)
        svc.handle_association_released(rel_event_a)
        svc.handle_association_released(rel_event_a)

        # Simulate release for an association that never did C-STORE (should NOT notify)
        svc.handle_association_released(MagicMock(assoc=assoc_echo_only))

    sent_messages = [call.args[0] for call in mock_actor.tell.call_args_list]
    assert len(sent_messages) == 4
    m_a1, m_a2, m_b1, m_rel_a = sent_messages
    assert isinstance(m_a1, DicomIngestionMessage)
    assert isinstance(m_a2, DicomIngestionMessage)
    assert isinstance(m_b1, DicomIngestionMessage)
    assert isinstance(m_rel_a, DicomAssociationReleasedMessage)
    assert m_a1.association_id is not None and m_a1.association_id.startswith("dimse:")
    assert m_a1.association_id == m_a2.association_id
    assert m_b1.association_id is not None and m_b1.association_id != m_a1.association_id
    assert m_rel_a.association_id == m_a1.association_id


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
    msg = FhirIngestionMessage.model_validate({"study_uid": "1.2.3", "accession_number": None, "acted_by": None})
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


def test_fhir_actor_links_created_encounter_to_group():
    """Verify FhirActor.on_receive creates BELONGS_TO from created Encounter to Group."""
    from fhir.resources.R4B.encounter import Encounter

    enc = Encounter.model_validate(
        {
            "resourceType": "Encounter",
            "id": "enc-actor-1",
            "status": "finished",
            "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "AMB"},
        }
    )
    resources = EncRelevantResources(encounter=enc)
    mock_fhir = MagicMock()
    mock_fhir.fetcher = MagicMock()
    mock_fhir.fetcher.close = AsyncMock()
    mock_fhir.fetch_relevant_resources_of_study = AsyncMock(return_value=resources)
    mock_fhir.upsert_relevant_resources = AsyncMock(
        return_value={
            "nodes": [{"id": "enc-det-id-1", "label": "Encounter", "uid": "fhir:enc-actor-1", "action": "CREATE"}],
            "relationships": [],
            "node_ids": ["enc-det-id-1"],
            "relationship_ids": [],
        }
    )
    mock_fhir.link_encounter_to_group = AsyncMock(
        return_value={
            "linked": True,
            "status": "LINKED",
            "group_id": "grp_clinic_a",
            "encounter_id": "enc-det-id-1",
            "relationship": {
                "id": "rel-belongs-to-1",
                "type": "BELONGS_TO",
                "start_node_id": "enc-det-id-1",
                "end_node_id": "grp_clinic_a",
                "action": "CREATE",
            },
        }
    )

    actor = FhirActor.start(fhir_service=mock_fhir)
    try:
        res = actor.ask(
            FhirIngestionMessage(
                patient_id="PT-1",
                study_uid="1.2.840.1000",
                series_uid="1.2.840.1000.1",
                group_id="grp_clinic_a",
                test_id="t_actor_link",
                acted_by="tester",
            ),
            timeout=3.0,
        )

        assert res["success"] is True
        assert res["status"] == "SUCCESS"
        mock_fhir.link_encounter_to_group.assert_awaited_once_with(
            encounter_id="enc-det-id-1",
            group_id="grp_clinic_a",
            test_id="t_actor_link",
            acted_by="tester",
        )
        assert res["upsert_result"]["relationship_ids"] == ["rel-belongs-to-1"]
        assert res["upsert_result"]["relationships"][0]["type"] == "BELONGS_TO"
        assert res["upsert_result"]["relationships"][0]["start_node_id"] == "enc-det-id-1"
        assert res["upsert_result"]["relationships"][0]["end_node_id"] == "grp_clinic_a"
    finally:
        actor.stop()


@pytest.mark.parametrize("empty_group_id", [None, "", "   "])
def test_fhir_actor_logs_error_and_continues_when_group_id_empty(empty_group_id):
    """Verify empty group_id logs an error with group_id and continues FHIR ingestion."""
    from fhir.resources.R4B.encounter import Encounter

    enc = Encounter.model_validate(
        {
            "resourceType": "Encounter",
            "id": "enc-empty-grp-1",
            "status": "finished",
            "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "AMB"},
        }
    )
    mock_fhir = MagicMock()
    mock_fhir.fetcher = MagicMock()
    mock_fhir.fetcher.close = AsyncMock()
    mock_fhir.fetch_relevant_resources_of_study = AsyncMock(return_value=EncRelevantResources(encounter=enc))
    mock_fhir.upsert_relevant_resources = AsyncMock(
        return_value={
            "nodes": [{"id": "enc-det-empty", "label": "Encounter", "uid": "fhir:enc-empty-grp-1", "action": "CREATE"}],
            "relationships": [],
            "node_ids": ["enc-det-empty"],
            "relationship_ids": [],
        }
    )
    mock_fhir.link_encounter_to_group = AsyncMock()

    actor = FhirActor.start(fhir_service=mock_fhir)
    try:
        with patch("gosmart_graphite.actor.fhir_actor.logger") as mock_logger:
            res = actor.ask(
                FhirIngestionMessage(
                    patient_id="PT-EMPTY",
                    study_uid="1.2.840.2000",
                    series_uid="1.2.840.2000.1",
                    group_id=empty_group_id,
                    test_id="t_actor_empty_grp",
                ),
                timeout=3.0,
            )

        assert res["success"] is True
        assert res["status"] == "SUCCESS"
        assert res["upsert_result"]["node_ids"] == ["enc-det-empty"]
        mock_fhir.upsert_relevant_resources.assert_awaited_once()
        mock_fhir.link_encounter_to_group.assert_not_called()

        error_calls = [
            call
            for call in mock_logger.error.call_args_list
            if call.args and call.args[0] == "fhir_encounter_group_link_failed"
        ]
        assert len(error_calls) == 1
        assert error_calls[0].kwargs["status"] == "EMPTY_GROUP_ID"
        assert error_calls[0].kwargs["group_id"] == empty_group_id
    finally:
        actor.stop()


@pytest.mark.parametrize(
    ("link_status", "extra_fields"),
    [
        ("GROUP_NOT_FOUND", {}),
        ("GROUP_INACTIVE_OR_DELETED", {"group_action": "DELETE", "group_is_active": False}),
    ],
)
def test_fhir_actor_logs_error_and_continues_when_group_missing_or_inactive(link_status, extra_fields):
    """Verify non-existing or soft-deleted/inactive group_id logs clear error with group_id and continues ingestion."""
    from fhir.resources.R4B.encounter import Encounter

    enc = Encounter.model_validate(
        {
            "resourceType": "Encounter",
            "id": "enc-bad-grp-1",
            "status": "finished",
            "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "AMB"},
        }
    )
    missing_group_id = "grp_nonexistent_42"
    mock_fhir = MagicMock()
    mock_fhir.fetcher = MagicMock()
    mock_fhir.fetcher.close = AsyncMock()
    mock_fhir.fetch_relevant_resources_of_study = AsyncMock(return_value=EncRelevantResources(encounter=enc))
    mock_fhir.upsert_relevant_resources = AsyncMock(
        return_value={
            "nodes": [{"id": "enc-det-bad", "label": "Encounter", "uid": "fhir:enc-bad-grp-1", "action": "CREATE"}],
            "relationships": [],
            "node_ids": ["enc-det-bad"],
            "relationship_ids": [],
        }
    )
    mock_fhir.link_encounter_to_group = AsyncMock(
        return_value={
            "linked": False,
            "status": link_status,
            "group_id": missing_group_id,
            "encounter_id": "enc-det-bad",
            **extra_fields,
        }
    )

    actor = FhirActor.start(fhir_service=mock_fhir)
    try:
        with patch("gosmart_graphite.actor.fhir_actor.logger") as mock_logger:
            res = actor.ask(
                FhirIngestionMessage(
                    patient_id="PT-BAD-GRP",
                    study_uid="1.2.840.3000",
                    series_uid="1.2.840.3000.1",
                    group_id=missing_group_id,
                    test_id="t_actor_bad_grp",
                ),
                timeout=3.0,
            )

        assert res["success"] is True
        assert res["status"] == "SUCCESS"
        assert res["upsert_result"]["node_ids"] == ["enc-det-bad"]
        assert res["upsert_result"]["relationship_ids"] == []
        mock_fhir.link_encounter_to_group.assert_awaited_once()

        error_calls = [
            call
            for call in mock_logger.error.call_args_list
            if call.args and call.args[0] == "fhir_encounter_group_link_failed"
        ]
        assert len(error_calls) == 1
        assert error_calls[0].kwargs["status"] == link_status
        assert error_calls[0].kwargs["group_id"] == missing_group_id
        if link_status == "GROUP_INACTIVE_OR_DELETED":
            assert error_calls[0].kwargs["group_action"] == "DELETE"
            assert error_calls[0].kwargs["group_is_active"] is False
            assert "soft-deleted or inactive" in error_calls[0].kwargs["reason"]
        else:
            assert "does not exist" in error_calls[0].kwargs["reason"]
    finally:
        actor.stop()
