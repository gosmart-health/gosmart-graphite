from pathlib import Path
from typing import Any, Dict, Optional

import pykka
from pydantic import ValidationError

from gosmart_graphite.actor.audit_actor import record_audit
from gosmart_graphite.core.config import get_settings
from gosmart_graphite.core.logging import get_logger
from gosmart_graphite.model.actor_message import (
    DicomIngestionMessage,
    DicomMockIngestionMessage,
    FhirIngestionMessage,
)
from gosmart_graphite.model.audit import AuditAction
from gosmart_graphite.model.media import ParsedStorageUri, StorageScheme
from gosmart_graphite.service.dicom_service import dicom_service
from gosmart_graphite.service.media_service import media_service

logger = get_logger("gosmart_gs.actor")


def _extract_calling_aet(source_identity: Optional[str]) -> Optional[str]:
    """Extract calling AE Title from a source_identity string like 'ae:CT_SCANNER'."""
    if not source_identity or not isinstance(source_identity, str):
        return None
    stripped = source_identity.strip()
    if stripped.lower().startswith("ae:"):
        aet = stripped[3:].strip()
        return aet or None
    return None


def _normalize_local_file_path(file_path: str) -> str:
    """Convert a 'file://' URI into a plain local filesystem path when needed."""
    parsed = media_service.parse_uri(file_path)
    if parsed.scheme == StorageScheme.FILE:
        return parsed.fsspec_path
    return file_path


def _is_same_storage_location(parsed_src: ParsedStorageUri, parsed_dst: ParsedStorageUri) -> bool:
    """Return True if two parsed URIs refer to the exact same object/file location."""
    if parsed_src.normalized_uri == parsed_dst.normalized_uri:
        return True
    if parsed_src.scheme == StorageScheme.FILE and parsed_dst.scheme == StorageScheme.FILE:
        try:
            return Path(parsed_src.fsspec_path).resolve() == Path(parsed_dst.fsspec_path).resolve()
        except OSError:
            return False
    return False


class DicomIngestionActor(pykka.ThreadingActor):
    """Pykka Actor for processing and storing DICOM files asynchronously."""

    use_daemon_thread = True

    def __init__(self) -> None:
        from gosmart_graphite.service.group_service import GroupService

        super().__init__()
        self.processed_count: int = 0
        self.last_status: str = "INITIALIZED"
        self.group_service = GroupService()

    def on_start(self) -> None:
        logger.info("actor_started", actor_urn=self.actor_ref.actor_urn)

    def on_stop(self) -> None:
        logger.info("actor_stopped", actor_urn=self.actor_ref.actor_urn)

    def _resolve_group_id(
        self,
        explicit_group_id: Optional[str],
        calling_aet: Optional[str],
    ) -> str:
        """Resolve Group deterministic ID from explicit group_id, calling AE lookup, or LostAndFound fallback."""
        if explicit_group_id and explicit_group_id.strip():
            return explicit_group_id.strip()

        if calling_aet:
            try:
                grp = self.group_service.get_group_for_ae_title_sync(calling_aet)
                if grp and grp.get("id"):
                    return str(grp["id"])
            except Exception as grp_err:
                logger.warning(
                    "actor_ae_group_lookup_failed",
                    calling_aet=calling_aet,
                    error=str(grp_err),
                )

        lost_and_found = self.group_service.ensure_lost_and_found_group_sync()
        return str(lost_and_found.id)

    def _resolve_storage_root_uri(
        self,
        explicit_storage_root: Optional[str],
        calling_aet: Optional[str],
    ) -> str:
        """Resolve destination storage_root_uri from message, calling AE DICOMConfiguration, or global settings."""
        if explicit_storage_root and explicit_storage_root.strip():
            return explicit_storage_root.strip()

        if calling_aet:
            try:
                cfg_root = self.group_service.get_storage_root_for_ae_title_sync(calling_aet)
                if cfg_root and cfg_root.strip():
                    return cfg_root.strip()
            except Exception as cfg_err:
                logger.warning(
                    "actor_ae_storage_root_lookup_failed",
                    calling_aet=calling_aet,
                    error=str(cfg_err),
                )

        return get_settings().STORAGE_ROOT_URI

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
        local_path = _normalize_local_file_path(file_path)
        test_id = msg.test_id
        acted_by = msg.acted_by
        source = msg.source
        source_identity = msg.source_identity
        destination = msg.destination
        destination_identity = msg.destination_identity
        calling_aet = _extract_calling_aet(source_identity)
        persisted_uri: Optional[str] = None
        try:
            # 1. Parse DICOM hierarchy from the local staged/source file
            hierarchy = dicom_service.parse_file(
                local_path,
                source=source,
                source_identity=source_identity,
                destination=destination,
                destination_identity=destination_identity,
            )
            hierarchy.test_id = test_id
            hierarchy.acted_by = acted_by

            # 2. Resolve Group association (explicit -> AE Title -> LostAndFound)
            hierarchy.group_id = self._resolve_group_id(
                explicit_group_id=msg.group_id,
                calling_aet=calling_aet,
            )

            # 3. Persist DICOM object from local source to destination storage (bucket or filesystem)
            # Always perform a full copy (never a local-to-local move optimization) to mirror production bucket writes.
            storage_root = self._resolve_storage_root_uri(
                explicit_storage_root=msg.storage_root_uri,
                calling_aet=calling_aet,
            )
            sop_uid = hierarchy.instance.sop_instance_uid
            target_uri = f"{storage_root.rstrip('/')}/{sop_uid}.dcm"

            parsed_src = media_service.parse_uri(local_path)
            parsed_dst = media_service.parse_uri(target_uri)
            same_location = _is_same_storage_location(parsed_src, parsed_dst)

            if same_location:
                persisted_uri = parsed_dst.normalized_uri
            else:
                persisted_uri = media_service.copy_sync(parsed_src.normalized_uri, target_uri)

            hierarchy.instance.storage_uri = persisted_uri
            if not destination:
                hierarchy.instance.destination = persisted_uri

            # 4. Register hierarchy and Group relations into Neo4j Graph
            result = graph_service.save_hierarchy_sync(hierarchy)

            # 5. Clean up local temporary staging file only after storage & graph persistence succeed
            if msg.cleanup_source and not same_location and parsed_src.scheme == StorageScheme.FILE:
                try:
                    Path(parsed_src.fsspec_path).unlink(missing_ok=True)
                except Exception as cleanup_err:
                    logger.warning(
                        "actor_staging_cleanup_failed",
                        source_path=parsed_src.fsspec_path,
                        error=str(cleanup_err),
                    )

            self.processed_count += 1
            self.last_status = "SUCCESS"
            logger.info(
                "actor_ingest_file_success",
                patient_id=hierarchy.patient.patient_id,
                study_instance_uid=hierarchy.study.study_instance_uid,
                sop_instance_uid=sop_uid,
                storage_uri=persisted_uri,
                group_id=hierarchy.group_id,
                test_id=test_id,
                processed_count=self.processed_count,
            )
            record_audit(
                action=AuditAction.INSTANCE_INGESTED,
                actor_id=source_identity or f"user:{acted_by}",
                actor=acted_by,
                target_id=f"instance:{sop_uid}",
                target=f"SOP Instance {sop_uid}",
                status_code=200,
                status_message="DICOM file persisted to storage and registered into graph",
                severity="INFO",
                result="File ingestion succeeded",
                details={
                    "file_path": str(file_path),
                    "storage_uri": persisted_uri,
                    "patient_id": hierarchy.patient.patient_id,
                    "study_instance_uid": hierarchy.study.study_instance_uid,
                    "series_instance_uid": hierarchy.series.series_instance_uid,
                    "group_id": hierarchy.group_id,
                },
                test_id=test_id,
            )

            # 6. Trigger asynchronous FHIR association (links :Patient -[:SAME_AS]-> :FHIR_Patient)
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
                "sop_instance_uid": sop_uid,
                "storage_uri": persisted_uri,
                "group_id": hierarchy.group_id,
                "test_id": test_id,
                "graph_result": result,
            }
        except Exception as e:
            logger.error(
                "actor_ingest_file_failed",
                file_path=file_path,
                storage_uri=persisted_uri,
                error=str(e),
                exc_info=True,
            )
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
                details={
                    "source_path": str(file_path),
                    "storage_uri": persisted_uri,
                    "error": str(e),
                    "error_type": type(e).__name__,
                },
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
