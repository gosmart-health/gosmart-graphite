from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from gosmart_graphite.actor.dicom_ingestion_actor import DicomIngestionActor
from gosmart_graphite.actor.fhir_actor import FhirActor, notify_fhir
from gosmart_graphite.model.actor_message import (
    DicomIngestionMessage,
    DicomMockIngestionMessage,
    FhirIngestionMessage,
)
from gosmart_graphite.model.dicom import (
    DicomHierarchy,
    InstanceModel,
    PatientModel,
    SeriesModel,
    StudyModel,
)
from gosmart_graphite.service.dicom_service import DicomService
from gosmart_graphite.service.graph_service import GraphService


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
                ),
                timeout=2.0,
            )

        assert res["success"] is True
        assert res["patient_id"] == "PT-100"
        assert res["study_instance_uid"] == "1.2.840.100"
        assert res["sop_instance_uid"] == "1.2.840.300"
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
