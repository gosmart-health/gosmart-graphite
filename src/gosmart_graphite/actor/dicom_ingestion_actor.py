from typing import Any, Dict, Optional

import pykka
from pydantic import ValidationError

from gosmart_graphite.actor.audit_actor import record_audit
from gosmart_graphite.core.logging import get_logger
from gosmart_graphite.model.actor_message import (
    DicomIngestionMessage,
    DicomMockIngestionMessage,
    FhirIngestionMessage,
)
from gosmart_graphite.model.audit import AuditAction
from gosmart_graphite.service.dicom_service import dicom_service

logger = get_logger("gosmart_gs.actor")


class DicomIngestionActor(pykka.ThreadingActor):
    """Pykka Actor for processing and storing DICOM files asynchronously."""

    use_daemon_thread = True

    def __init__(self) -> None:
        from gosmart_graphite.service import GroupService

        super().__init__()
        self.processed_count: int = 0
        self.last_status: str = "INITIALIZED"
        self.group_service = GroupService()

    def on_start(self) -> None:
        logger.info("actor_started", actor_urn=self.actor_ref.actor_urn)

    def on_stop(self) -> None:
        logger.info("actor_stopped", actor_urn=self.actor_ref.actor_urn)

    def on_receive(self, message: Any) -> Any:
        if isinstance(message, DicomIngestionMessage):
            logger.debug("actor_message_received", msg_type=message.type)
            return self._handle_ingest_file(message)

        if isinstance(message, DicomMockIngestionMessage):
            logger.debug("actor_message_received", msg_type=message.type)
            return self._handle_ingest_mock(message)

        if not isinstance(message, dict):
            logger.warning("actor_unsupported_message_type", msg_type=type(message).__name__)
            return {"success": False, "error": f"Unsupported message type: {type(message).__name__}"}

        msg_type = message.get("type")
        logger.debug("actor_message_received", msg_type=msg_type)

        if msg_type == "PING":
            return {"status": "PONG", "processed_count": self.processed_count}

        elif msg_type == "INGEST_FILE":
            try:
                ingest_msg = DicomIngestionMessage.model_validate(message)
            except ValidationError as e:
                logger.warning("actor_invalid_message", msg_type=msg_type, error=str(e))
                return {"success": False, "error": f"Invalid DicomIngestionMessage: {e}"}
            return self._handle_ingest_file(ingest_msg)

        elif msg_type == "INGEST_MOCK":
            try:
                mock_msg = DicomMockIngestionMessage.model_validate(message)
            except ValidationError as e:
                logger.warning("actor_invalid_message", msg_type=msg_type, error=str(e))
                return {"success": False, "error": f"Invalid DicomMockIngestionMessage: {e}"}
            return self._handle_ingest_mock(mock_msg)

        else:
            logger.warning("actor_unrecognized_message", msg_type=msg_type)
            return {"error": f"Unknown message type: {msg_type}"}

    def _handle_ingest_file(self, msg: DicomIngestionMessage) -> Dict[str, Any]:
        from gosmart_graphite.service.graph_service import graph_service
        file_path = msg.file_path
        test_id = msg.test_id
        acted_by = msg.acted_by
        source = msg.source
        source_identity = msg.source_identity
        destination = msg.destination
        destination_identity = msg.destination_identity
        group_id = msg.group_id
        try:
            hierarchy = dicom_service.parse_file(
                file_path,
                source=source,
                source_identity=source_identity,
                destination=destination,
                destination_identity=destination_identity,
            )
            hierarchy.test_id = test_id
            hierarchy.acted_by = acted_by

            # Resolve Group association if not provided in message
            hierarchy.group_id = group_id or self.group_service.lost_and_found_group
            result = None
            try:
                result = graph_service.save_hierarchy_sync(hierarchy)
            except Exception as db_err:
                logger.warning("neo4j_sync_save_skipped", error=str(db_err))

            self.processed_count += 1
            self.last_status = "SUCCESS"
            logger.info(
                "actor_ingest_file_success",
                patient_id=hierarchy.patient.patient_id,
                study_instance_uid=hierarchy.study.study_instance_uid,
                sop_instance_uid=hierarchy.instance.sop_instance_uid,
                test_id=test_id,
                processed_count=self.processed_count,
            )
            record_audit(
                action=AuditAction.INSTANCE_INGESTED,
                actor_id=source_identity or f"user:{acted_by}",
                actor=acted_by,
                target_id=f"instance:{hierarchy.instance.sop_instance_uid}",
                target=f"SOP Instance {hierarchy.instance.sop_instance_uid}",
                status_code=200,
                status_message="DICOM file parsed and registered into graph",
                severity="INFO",
                result="File ingestion succeeded",
                details={
                    "file_path": str(file_path),
                    "patient_id": hierarchy.patient.patient_id,
                    "study_instance_uid": hierarchy.study.study_instance_uid,
                },
                test_id=test_id,
            )
            study_uid = hierarchy.study.study_instance_uid
            if study_uid:
                try:
                    from gosmart_graphite.actor.fhir_actor import get_fhir_actor

                    fhir_actor = get_fhir_actor()
                    if not fhir_actor:
                        raise ValueError("FHIR actor not available")
                    fhir_actor.tell(
                        FhirIngestionMessage(
                            patient_id=hierarchy.patient.patient_id,
                            accession_number=hierarchy.study.accession_number,
                            study_uid=study_uid,
                            series_uid=hierarchy.series.series_instance_uid,
                            test_id=test_id,
                            acted_by=acted_by,
                            group_id=hierarchy.group_id,
                        )
                    )
                except Exception as fhir_err:
                    logger.error(
                        "fhir_actor_notify_failed",
                        study_uid=study_uid,
                        error=str(fhir_err),
                        exc_info=True,
                    )
            return {
                "success": True,
                "patient_id": hierarchy.patient.patient_id,
                "study_instance_uid": hierarchy.study.study_instance_uid,
                "sop_instance_uid": hierarchy.instance.sop_instance_uid,
                "test_id": test_id,
                "graph_result": result,
            }
        except Exception as e:
            logger.error("actor_ingest_file_failed", file_path=file_path, error=str(e), exc_info=True)
            self.last_status = f"ERROR: {str(e)}"
            record_audit(
                action=AuditAction.INSTANCE_INGESTED,
                actor_id=source_identity or f"user:{acted_by}",
                actor=acted_by,
                target=str(file_path),
                status_code=500,
                status_message=f"File ingestion failed: {str(e)}",
                severity="ERROR",
                result=str(e),
                test_id=test_id,
            )
            return {"success": False, "error": str(e)}

    def _handle_ingest_mock(self, msg: DicomMockIngestionMessage) -> Dict[str, Any]:
        # Avoiding circular import
        from gosmart_graphite.service.graph_service import graph_service
        patient_id = msg.patient_id
        modality = msg.modality
        test_id = msg.test_id
        acted_by = msg.acted_by
        source = msg.source
        source_identity = msg.source_identity or f"user:{acted_by}"
        destination_identity = msg.destination_identity
        try:
            ds = dicom_service.create_mock_dicom(patient_id=patient_id, modality=modality)
            hierarchy = dicom_service.parse_dataset(
                ds,
                source=source,
                source_identity=source_identity,
                destination_identity=destination_identity,
            )
            hierarchy.test_id = test_id
            hierarchy.acted_by = acted_by
            saved_uri = dicom_service.save_raw_dicom(ds, hierarchy.instance.sop_instance_uid)
            hierarchy.instance.storage_uri = saved_uri
            hierarchy.instance.destination = f"file:{saved_uri}"
            result = None
            try:
                result = graph_service.save_hierarchy_sync(hierarchy)
            except Exception as db_err:
                logger.warning("neo4j_sync_save_skipped", error=str(db_err))

            self.processed_count += 1
            self.last_status = "SUCCESS"
            logger.info(
                "actor_ingest_mock_success",
                patient_id=hierarchy.patient.patient_id,
                modality=modality,
                storage_uri=saved_uri,
                processed_count=self.processed_count,
            )
            record_audit(
                action=AuditAction.DATASET_MOCK_INGESTED,
                actor_id=source_identity or f"user:{acted_by}",
                actor=acted_by,
                target_id=f"instance:{hierarchy.instance.sop_instance_uid}",
                target=f"Patient {patient_id} ({modality})",
                status_code=200,
                status_message="Mock DICOM generated, persisted, and registered",
                severity="INFO",
                result="Mock ingestion succeeded",
                details={"storage_uri": saved_uri, "patient_id": patient_id, "modality": modality},
                test_id=test_id,
            )
            return {
                "success": True,
                "patient_id": hierarchy.patient.patient_id,
                "study_instance_uid": hierarchy.study.study_instance_uid,
                "sop_instance_uid": hierarchy.instance.sop_instance_uid,
                "storage_uri": saved_uri,
                "graph_result": result,
            }
        except Exception as e:
            logger.error("actor_ingest_mock_failed", error=str(e), exc_info=True)
            self.last_status = f"ERROR: {str(e)}"
            record_audit(
                action=AuditAction.DATASET_MOCK_INGESTED,
                actor_id=source_identity or f"user:{acted_by}",
                actor=acted_by,
                target=f"Patient {patient_id}",
                status_code=500,
                status_message=f"Mock ingestion failed: {str(e)}",
                severity="ERROR",
                result=str(e),
                test_id=test_id,
            )
            return {"success": False, "error": str(e)}


# Global Actor Reference management
# noinspection DuplicatedCode
_ingest_actor_ref: Optional[pykka.ActorRef] = None


def get_dicom_actor() -> pykka.ActorRef | None:
    """Get or start the singleton DicomIngestionActor."""
    global _ingest_actor_ref
    if _ingest_actor_ref is None or not _ingest_actor_ref.is_alive():
        _ingest_actor_ref = DicomIngestionActor.start()
    return _ingest_actor_ref


def stop_dicom_actor() -> None:
    """Stop the DicomIngestionActor if running."""
    global _ingest_actor_ref
    if _ingest_actor_ref is not None and _ingest_actor_ref.is_alive():
        _ingest_actor_ref.stop()
        _ingest_actor_ref = None
