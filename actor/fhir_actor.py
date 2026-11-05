import asyncio
from typing import Any, Dict, Optional

import pykka

from core.logging import get_logger
from service import FhirService

logger = get_logger("gosmart_gs.fhir_actor")


class FhirActor(pykka.ThreadingActor):
    """Pykka Actor for handling FHIR operations asynchronously (fire-and-forget).

    Receives patient_id and accession_number strings.
    """

    use_daemon_thread = True

    def __init__(self) -> None:
        super().__init__()
        self.processed_count: int = 0
        self.last_status: str = "INITIALIZED"
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def on_start(self) -> None:
        logger.info("fhir_actor_started", actor_urn=self.actor_ref.actor_urn)
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)


    def on_stop(self) -> None:
        logger.info("fhir_actor_stopped", actor_urn=self.actor_ref.actor_urn)
        if self._loop and not self._loop.is_closed():
            self._loop.close()


    def on_receive(self, message: Any) -> None:
        """Handle incoming fire-and-forget message containing patient_id and accession_number."""
        if not isinstance(message, dict):
            logger.warning("fhir_actor_unsupported_message_type", msg_type=type(message).__name__)
            return

        patient_id = message.get("patient_id", "")
        accession_number = message.get("accession_number", "")
        study_uid = message.get("study_uid", "")

        logger.debug(
            "fhir_actor_message_received",
            patient_id=patient_id,
            accession_number=accession_number,
            study_uid=study_uid,
        )

        if study_uid and self._loop:
            fhir = FhirService()
            resources = self._loop.run_until_complete(fhir.fetch_relevant_resources_of_study(study_uid))
            if resources:
                # Pass this on to Graph registration
                # self._loop.run_until_complete(self.register_graph(resources))
                logger.info(f"Got the FHIR resources for Study: {study_uid}")
            else:
                logger.info(f"Did not get the FHIR resources for Study: {study_uid}")

        self.processed_count += 1
        self.last_status = "IDLE"

        # Fire-and-forget: placeholder for future processing
        pass


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


def notify_fhir(patient_id: str = "", accession_number: str = "", study_uid: str = "") -> None:
    """Convenience fire-and-forget helper to send patient_id and accession_number to FhirActor."""
    try:
        actor_ref = get_fhir_actor()
        actor_ref.tell(
            {
                "patient_id": str(patient_id),
                "accession_number": str(accession_number),
                "study_uid": str(study_uid),
            }
        )
    except Exception as e:
        logger.error("notify_fhir_failed", error=str(e), exc_info=True)
