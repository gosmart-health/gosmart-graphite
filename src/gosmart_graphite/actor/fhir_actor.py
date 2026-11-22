import asyncio
from typing import Any, Dict, Optional

import pykka
from pydantic import ValidationError

from gosmart_graphite.actor.embedding_actor import notify_embedding
from gosmart_graphite.core.logging import get_logger
from gosmart_graphite.model.actor_message import EmbeddingMethod, FhirIngestionMessage
from gosmart_graphite.service.fhir_service import EncRelevantResources, FHIRBatchFetcher, FhirService

logger = get_logger("gosmart_gs.fhir_actor")


class FhirActor(pykka.ThreadingActor):
    """Pykka Actor for handling FHIR operations asynchronously (fire-and-forget).

    Receives a FhirIngestionMessage (or compatible dict) with patient_id,
    accession_number, study_uid, series_uid, and group_id, resolves the
    group-scoped FHIRConfiguration (falling back to global settings), fetches
    the associated FHIR encounter resource bundle, registers nodes and
    cross-domain DICOM relationships (including :Patient -[:SAME_AS]-> :FHIR_Patient),
    and links created :Encounter nodes to their target :Group via :BELONGS_TO
    into Neo4j.
    """

    use_daemon_thread = True

    def __init__(self, fhir_service: Optional[FhirService] = None) -> None:
        super().__init__()
        self.processed_count: int = 0
        self.last_status: str = "INITIALIZED"
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._fhir_service: Optional[FhirService] = fhir_service

    def on_start(self) -> None:
        logger.info("fhir_actor_started", actor_urn=self.actor_ref.actor_urn)
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

    def on_stop(self) -> None:
        logger.info("fhir_actor_stopped", actor_urn=self.actor_ref.actor_urn)
        if self._loop and not self._loop.is_closed():
            if self._fhir_service is not None:
                try:
                    self._loop.run_until_complete(self._fhir_service.fetcher.close())
                except Exception as why:
                    logger.error("Failed to close FHIR fetcher", why=why)
            try:
                from gosmart_graphite.db.neo4j import neo4j_client

                self._loop.run_until_complete(neo4j_client.close_current_loop_driver())
            except Exception as why:
                logger.error("Failed to close FHIR actor Neo4j async driver", why=why)
            self._loop.close()

    def _resolve_fhir_service(
        self,
        group_id: Optional[str] = None,
        test_id: Optional[str] = None,
    ) -> FhirService:
        """Resolve a FhirService using group-scoped FHIRConfiguration, falling back to global FHIR_BASE_URL."""
        if self._fhir_service is not None:
            return self._fhir_service

        base_url: Optional[str] = None
        try:
            from gosmart_graphite.service.fhir_config_service import fhir_config_service

            cfg = fhir_config_service.resolve_config_for_group_sync(
                group_id=group_id,
                test_id=test_id,
            )
            if cfg and cfg.url and cfg.url.strip():
                base_url = cfg.url.strip()
                logger.debug(
                    "fhir_actor_resolved_group_config",
                    group_id=group_id,
                    fhir_url=base_url,
                )
        except Exception as cfg_err:
            logger.warning(
                "fhir_actor_group_config_resolve_failed",
                group_id=group_id,
                error=str(cfg_err),
            )

        if base_url:
            return FhirService(fetcher=FHIRBatchFetcher(base_url=base_url))
        return FhirService()

    async def register_graph(
        self,
        resources: EncRelevantResources,
        fhir: Optional[FhirService] = None,
        test_id: Optional[str] = None,
        acted_by: str = "fhir_actor",
    ) -> Dict[str, Any]:
        """Upsert fetched FHIR resources and cross-domain DICOM relationships into Neo4j."""
        svc = fhir or self._fhir_service or FhirService()
        return await svc.upsert_relevant_resources(
            resources,
            test_id=test_id,
            acted_by=acted_by,
        )

    @staticmethod
    def _has_resources(resources: Optional[EncRelevantResources]) -> bool:
        """Check whether an EncRelevantResources bundle contains any domain resources."""
        if resources is None:
            return False
        return bool(
            resources.encounter
            or resources.patient
            or resources.imaging_studies
            or resources.conditions
            or resources.diagnostic_reports
            or resources.observations
            or resources.procedures
            or resources.serviceRequests
            or resources.locations
            or resources.organizations
            or resources.practitioners
        )

    def _link_encounters_to_group(
        self,
        upsert_result: Dict[str, Any],
        fhir: FhirService,
        group_id: Optional[str],
        study_uid: str,
        series_uid: str,
        test_id: Optional[str],
        acted_by: str,
    ) -> None:
        """Link upserted Encounter node(s) to the target Group via BELONGS_TO.

        Logs an error and allows ingestion to continue if group_id is empty,
        references a non-existing Group node, or references a soft-deleted/inactive Group.
        """
        encounter_ids = [
            node["id"]
            for node in upsert_result.get("nodes", [])
            if isinstance(node, dict) and node.get("label") == "Encounter" and node.get("id")
        ]
        normalized_group_id = group_id.strip() if isinstance(group_id, str) else ""

        if not normalized_group_id:
            logger.error(
                "fhir_encounter_group_link_failed",
                status="EMPTY_GROUP_ID",
                reason="group_id is empty; skipping Encounter BELONGS_TO Group relationship and continuing ingestion",
                group_id=group_id,
                encounter_ids=encounter_ids,
                study_uid=study_uid,
                series_uid=series_uid,
            )
            return

        if not encounter_ids or not self._loop:
            return

        for encounter_id in encounter_ids:
            try:
                link_result = self._loop.run_until_complete(
                    fhir.link_encounter_to_group(
                        encounter_id=encounter_id,
                        group_id=normalized_group_id,
                        test_id=test_id,
                        acted_by=acted_by,
                    )
                )
            except Exception as link_err:
                logger.error(
                    "fhir_encounter_group_link_failed",
                    status="LINK_ERROR",
                    reason="Unexpected error linking Encounter to Group via BELONGS_TO; continuing ingestion",
                    group_id=normalized_group_id,
                    encounter_id=encounter_id,
                    study_uid=study_uid,
                    series_uid=series_uid,
                    error=str(link_err),
                    exc_info=True,
                )
                continue

            if link_result.get("linked") and link_result.get("relationship"):
                rel_info = link_result["relationship"]
                upsert_result.setdefault("relationships", []).append(rel_info)
                rel_ids = upsert_result.setdefault("relationship_ids", [])
                if rel_info["id"] not in rel_ids:
                    rel_ids.append(rel_info["id"])
                logger.info(
                    "fhir_encounter_linked_to_group",
                    encounter_id=encounter_id,
                    group_id=normalized_group_id,
                    relation_id=rel_info["id"],
                    action=rel_info["action"],
                    study_uid=study_uid,
                    series_uid=series_uid,
                )
            elif link_result.get("status") == "GROUP_INACTIVE_OR_DELETED":
                logger.error(
                    "fhir_encounter_group_link_failed",
                    status="GROUP_INACTIVE_OR_DELETED",
                    reason=(
                        "Group node is soft-deleted or inactive (possible misconfiguration or inadvertent removal); "
                        "skipping Encounter BELONGS_TO Group relationship and continuing ingestion"
                    ),
                    group_id=normalized_group_id,
                    group_action=link_result.get("group_action"),
                    group_is_active=link_result.get("group_is_active"),
                    encounter_id=encounter_id,
                    study_uid=study_uid,
                    series_uid=series_uid,
                )
            elif link_result.get("status") == "GROUP_NOT_FOUND":
                logger.error(
                    "fhir_encounter_group_link_failed",
                    status="GROUP_NOT_FOUND",
                    reason=(
                        "Group node does not exist for group_id; "
                        "skipping Encounter BELONGS_TO Group relationship and continuing ingestion"
                    ),
                    group_id=normalized_group_id,
                    encounter_id=encounter_id,
                    study_uid=study_uid,
                    series_uid=series_uid,
                )
            else:
                logger.error(
                    "fhir_encounter_group_link_failed",
                    status=link_result.get("status", "UNKNOWN"),
                    reason=link_result.get(
                        "reason",
                        "Failed to link Encounter to Group via BELONGS_TO; continuing ingestion",
                    ),
                    group_id=normalized_group_id,
                    encounter_id=encounter_id,
                    study_uid=study_uid,
                    series_uid=series_uid,
                )

    def _notify_diagnostic_report_embeddings(
        self,
        *,
        resources: EncRelevantResources,
        group_id: Optional[str],
        study_uid: str,
        series_uid: str,
        test_id: Optional[str],
        acted_by: str,
    ) -> None:
        """Dispatch fire-and-forget embedding tasks for persisted DiagnosticReport resources."""
        if not resources.diagnostic_reports:
            return

        for report in resources.diagnostic_reports:
            report_id = getattr(report, "id", None)
            try:
                notify_embedding(
                    report,
                    group_id=group_id,
                    test_id=test_id,
                    method=EmbeddingMethod.DIAGNOSTIC_REPORT,
                    acted_by=acted_by,
                )
                logger.debug(
                    "fhir_actor_embedding_notified",
                    diagnostic_report_id=report_id,
                    group_id=group_id,
                    study_uid=study_uid,
                    series_uid=series_uid,
                )
            except Exception as exc:
                logger.error(
                    "fhir_actor_embedding_notify_failed",
                    diagnostic_report_id=report_id,
                    group_id=group_id,
                    study_uid=study_uid,
                    series_uid=series_uid,
                    error=str(exc),
                    exc_info=True,
                )

    def on_receive(self, message: Any) -> Any:
        """Handle incoming FhirIngestionMessage (or control/dict payload)."""
        if isinstance(message, FhirIngestionMessage):
            fhir_msg = message
        elif isinstance(message, dict):
            msg_type = message.get("type")
            if msg_type == "PING":
                return {"status": "PONG", "processed_count": self.processed_count}
            if msg_type == "STATUS":
                return {
                    "processed_count": self.processed_count,
                    "last_status": self.last_status,
                }
            try:
                fhir_msg = FhirIngestionMessage.model_validate(message)
            except ValidationError as e:
                logger.warning("fhir_actor_invalid_message", error=str(e))
                return {"success": False, "error": f"Invalid FhirIngestionMessage: {e}"}
        else:
            logger.warning("fhir_actor_unsupported_message_type", msg_type=type(message).__name__)
            return {"success": False, "error": f"Unsupported message type: {type(message).__name__}"}

        patient_id = fhir_msg.patient_id
        accession_number = fhir_msg.accession_number or ""
        study_uid = fhir_msg.study_uid
        series_uid = fhir_msg.series_uid
        test_id = fhir_msg.test_id
        acted_by = fhir_msg.acted_by
        group_id = fhir_msg.group_id

        logger.debug(
            "fhir_actor_message_received",
            patient_id=patient_id,
            accession_number=accession_number,
            study_uid=study_uid,
            series_uid=series_uid,
            group_id=group_id,
        )

        upsert_result: Optional[Dict[str, Any]] = None
        if study_uid and self._loop:
            fhir = self._resolve_fhir_service(group_id=group_id, test_id=test_id)
            try:
                resources = self._loop.run_until_complete(fhir.fetch_relevant_resources_of_study(study_uid))
                if self._has_resources(resources):
                    logger.info(
                        f"Got the FHIR resources for Study: {study_uid}",
                        study_uid=study_uid,
                        series_uid=series_uid,
                        encounter_id=getattr(resources.encounter, "id", None),
                        patient_id=getattr(resources.patient, "id", None),
                        imaging_studies=len(resources.imaging_studies),
                        diagnostic_reports=len(resources.diagnostic_reports),
                        observations=len(resources.observations),
                        procedures=len(resources.procedures),
                        conditions=len(resources.conditions),
                        service_requests=len(resources.serviceRequests),
                        locations=len(resources.locations),
                        organizations=len(resources.organizations),
                        practitioners=len(resources.practitioners),
                        errors=resources.errors,
                    )
                    upsert_result = self._loop.run_until_complete(
                        self.register_graph(
                            resources,
                            fhir=fhir,
                            test_id=test_id,
                            acted_by=acted_by,
                        )
                    )
                    if upsert_result:
                        self._link_encounters_to_group(
                            upsert_result=upsert_result,
                            fhir=fhir,
                            group_id=group_id,
                            study_uid=study_uid,
                            series_uid=series_uid,
                            test_id=test_id,
                            acted_by=acted_by,
                        )
                        logger.info(
                            "fhir_graph_registered",
                            study_uid=study_uid,
                            series_uid=series_uid,
                            nodes_count=len(upsert_result.get("node_ids", [])),
                            relationships_count=len(upsert_result.get("relationship_ids", [])),
                        )
                        self._notify_diagnostic_report_embeddings(
                            resources=resources,
                            group_id=group_id,
                            study_uid=study_uid,
                            series_uid=series_uid,
                            test_id=test_id,
                            acted_by=acted_by,
                        )
                    else:
                        logger.error(
                            "fhir_graph_registration_failed",
                            study_uid=study_uid,
                            series_uid=series_uid,
                        )
                    self.last_status = "SUCCESS"
                else:
                    logger.info(
                        f"Did not get the FHIR resources for Study: {study_uid}",
                        study_uid=study_uid,
                        series_uid=series_uid,
                        errors=resources.errors if resources else [],
                    )
                    self.last_status = "NOT_FOUND"
            except Exception as e:
                logger.error(
                    "fhir_actor_processing_failed",
                    study_uid=study_uid,
                    series_uid=series_uid,
                    error=str(e),
                    exc_info=True,
                )
                self.last_status = f"ERROR: {str(e)}"
                self.processed_count += 1
                return {"success": False, "study_uid": study_uid, "series_uid": series_uid, "error": str(e)}
            finally:
                if self._fhir_service is None:
                    try:
                        self._loop.run_until_complete(fhir.fetcher.close())
                    except Exception as why:
                        logger.error(
                            "fhir_actor_close_failed",
                            study_uid=study_uid,
                            series_uid=series_uid,
                            error=str(why),
                            exc_info=True,
                        )
        else:
            self.last_status = "IDLE"

        self.processed_count += 1
        return {
            "success": True,
            "study_uid": study_uid,
            "series_uid": series_uid,
            "status": self.last_status,
            "upsert_result": upsert_result,
        }


# Global Actor Reference management
# noinspection DuplicatedCode
_fhir_actor_ref: Optional[pykka.ActorRef] = None


def get_fhir_actor() -> pykka.ActorRef | None:
    """Get or start the singleton FhirActor."""
    global _fhir_actor_ref
    if _fhir_actor_ref is None or not _fhir_actor_ref.is_alive():
        _fhir_actor_ref = FhirActor.start()
    return _fhir_actor_ref


def stop_fhir_actor() -> None:
    """Stop the FhirActor if running."""
    global _fhir_actor_ref
    if _fhir_actor_ref is not None and _fhir_actor_ref.is_alive():
        _fhir_actor_ref.stop()
        _fhir_actor_ref = None


def notify_fhir(
    patient_id: str = "",
    accession_number: Optional[str] = "",
    study_uid: str = "",
    series_uid: str = "",
    test_id: Optional[str] = None,
    acted_by: str = "fhir_actor",
    group_id: Optional[str] = None,
) -> None:
    """Convenience fire-and-forget helper to send patient_id, accession_number,
    study_uid, series_uid, and group_id to FhirActor.
    """
    try:
        actor_ref = get_fhir_actor()
        if actor_ref:
            actor_ref.tell(
                FhirIngestionMessage(
                    patient_id=patient_id,
                    accession_number=accession_number,
                    study_uid=study_uid,
                    series_uid=series_uid,
                    test_id=test_id,
                    acted_by=acted_by,
                    group_id=group_id,
                )
            )
    except Exception as e:
        logger.error("notify_fhir_failed", error=str(e), exc_info=True)
