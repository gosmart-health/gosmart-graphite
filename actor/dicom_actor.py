from typing import Any, Dict, Optional

import pykka

from actor.audit_actor import record_audit
from core.logging import get_logger
from model.audit import AuditAction
from service.dicom_service import dicom_service
from service.graph_service import graph_service

logger = get_logger("gosmart_gs.actor")


class DicomIngestionActor(pykka.ThreadingActor):
    """Pykka Actor for processing and storing DICOM files asynchronously."""

    use_daemon_thread = True

    def __init__(self) -> None:
        super().__init__()
        self.processed_count: int = 0
        self.last_status: str = "INITIALIZED"

    def on_start(self) -> None:
        logger.info("actor_started", actor_urn=self.actor_ref.actor_urn)

    def on_stop(self) -> None:
        logger.info("actor_stopped", actor_urn=self.actor_ref.actor_urn)

    def on_receive(self, message: Dict[str, Any]) -> Any:
        msg_type = message.get("type")
        logger.debug("actor_message_received", msg_type=msg_type)

        if msg_type == "PING":
            return {"status": "PONG", "processed_count": self.processed_count}

        elif msg_type == "INGEST_FILE":
            file_path = message.get("file_path")
            test_id = message.get("test_id")
            acted_by = message.get("acted_by", "actor")
            source = message.get("source")
            source_identity = message.get("source_identity")
            destination = message.get("destination")
            destination_identity = message.get("destination_identity")
            group_id = message.get("group_id")
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
                if not group_id:
                    from service.group_service import group_service

                    if source_identity and source_identity.startswith("ae:"):
                        caller_aet = source_identity[3:].strip()
                        grp = group_service.get_group_for_ae_title_sync(caller_aet)
                        if grp and grp.get("id"):
                            group_id = grp["id"]
                    if not group_id:
                        catch_all = group_service.get_or_create_catch_all_group_sync()
                        if catch_all and catch_all.get("id"):
                            group_id = catch_all["id"]
                hierarchy.group_id = group_id
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

        elif msg_type == "INGEST_MOCK":
            patient_id = message.get("patient_id", "MOCK_PATIENT")
            modality = message.get("modality", "MR")
            test_id = message.get("test_id")
            acted_by = message.get("acted_by", "actor")
            source = message.get("source", "file:mock")
            source_identity = message.get("source_identity", f"user:{acted_by}")
            destination_identity = message.get("destination_identity", "service:mock")
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

        else:
            logger.warning("actor_unrecognized_message", msg_type=msg_type)
            return {"error": f"Unknown message type: {msg_type}"}


# Global Actor Reference management
_ingest_actor_ref: Optional[pykka.ActorRef] = None


def get_dicom_actor() -> pykka.ActorRef:
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
