from typing import Any, Dict, Optional

import pykka

from core.logging import get_logger
from service.dicom_service import dicom_service
from service.graph_service import graph_service

logger = get_logger("gosmart_gs.actor")


class DicomIngestionActor(pykka.ThreadingActor):
    """Pykka Actor for processing and storing DICOM files asynchronously."""

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
            try:
                hierarchy = dicom_service.parse_file(file_path)
                hierarchy.test_id = test_id
                hierarchy.acted_by = acted_by
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
                return {"success": False, "error": str(e)}

        elif msg_type == "INGEST_MOCK":
            patient_id = message.get("patient_id", "MOCK_PATIENT")
            modality = message.get("modality", "MR")
            test_id = message.get("test_id")
            acted_by = message.get("acted_by", "actor")
            try:
                ds = dicom_service.create_mock_dicom(patient_id=patient_id, modality=modality)
                hierarchy = dicom_service.parse_dataset(ds)
                hierarchy.test_id = test_id
                hierarchy.acted_by = acted_by
                saved_uri = dicom_service.save_raw_dicom(ds, hierarchy.instance.sop_instance_uid)
                hierarchy.instance.storage_uri = saved_uri
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
