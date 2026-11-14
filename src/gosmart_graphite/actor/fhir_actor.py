import asyncio
from typing import Any, Dict, Optional

import pykka

from gosmart_graphite.core.logging import get_logger
from gosmart_graphite.service import EncRelevantResources, FhirService

logger = get_logger("gosmart_gs.fhir_actor")


class FhirActor(pykka.ThreadingActor):
    """Pykka Actor for handling FHIR operations asynchronously (fire-and-forget).

    Receives patient_id, accession_number, study_uid, and series_uid strings, fetches the
    associated FHIR encounter resource bundle, and registers nodes and
    relationships into Neo4j.
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
                except Exception:
                    pass
            self._loop.close()

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
        )

    def on_receive(self, message: Any) -> Any:
        """Handle incoming message containing patient_id, accession_number, study_uid, and series_uid."""
        if not isinstance(message, dict):
            logger.warning("fhir_actor_unsupported_message_type", msg_type=type(message).__name__)
            return {"success": False, "error": f"Unsupported message type: {type(message).__name__}"}

        msg_type = message.get("type")
        if msg_type == "PING":
            return {"status": "PONG", "processed_count": self.processed_count}
        if msg_type == "STATUS":
            return {
                "processed_count": self.processed_count,
                "last_status": self.last_status,
            }

        patient_id = message.get("patient_id", "")
        accession_number = message.get("accession_number", "")
        study_uid = message.get("study_uid", "")
        series_uid = message.get("series_uid", "")
        test_id = message.get("test_id")
        acted_by = message.get("acted_by") or "fhir_actor"

        logger.debug(
            "fhir_actor_message_received",
            patient_id=patient_id,
            accession_number=accession_number,
            study_uid=study_uid,
            series_uid=series_uid,
        )

        upsert_result: Optional[Dict[str, Any]] = None
        if study_uid and self._loop:
            fhir = self._fhir_service or FhirService()
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
                    logger.info(
                        "fhir_graph_registered",
                        study_uid=study_uid,
                        series_uid=series_uid,
                        nodes_count=len(upsert_result.get("node_ids", [])),
                        relationships_count=len(upsert_result.get("relationship_ids", [])),
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
                    except Exception:
                        pass
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
_fhir_actor_ref: Optional[pykka.ActorRef] = None


def get_fhir_actor() -> pykka.ActorRef:
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
    accession_number: str = "",
    study_uid: str = "",
    series_uid: str = "",
    test_id: Optional[str] = None,
    acted_by: str = "fhir_actor",
) -> None:
    """Convenience fire-and-forget helper to send patient_id, accession_number,
    study_uid, and series_uid to FhirActor.
    """
    try:
        actor_ref = get_fhir_actor()
        actor_ref.tell(
            {
                "patient_id": str(patient_id),
                "accession_number": str(accession_number),
                "study_uid": str(study_uid),
                "series_uid": str(series_uid),
                "test_id": test_id,
                "acted_by": acted_by,
            }
        )
    except Exception as e:
        logger.error("notify_fhir_failed", error=str(e), exc_info=True)
