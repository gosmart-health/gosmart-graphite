import uuid
from typing import Any, Dict, Optional

from pynetdicom import (
    AE,
    ALL_TRANSFER_SYNTAXES,
    StoragePresentationContexts,
    VerificationPresentationContexts,
    evt,
)
from pynetdicom.sop_class import (
    ModalityWorklistInformationFind,
    PatientRootQueryRetrieveInformationModelFind,
    PatientRootQueryRetrieveInformationModelGet,
    PatientRootQueryRetrieveInformationModelMove,
    StudyRootQueryRetrieveInformationModelFind,
    StudyRootQueryRetrieveInformationModelGet,
    StudyRootQueryRetrieveInformationModelMove,
)

from core.config import Settings, get_settings
from core.logging import get_logger
from service.dicom_service import dicom_service
from service.graph_service import graph_service
from service.media_service import media_service

logger = get_logger("gosmart_gs.dicom_net")


class PyNetDicomService:
    """Manages DICOM VNA network operations using pynetdicom (C-STORE, C-FIND, C-GET, C-MOVE, C-ECHO)."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self._ae: Optional[AE] = None
        self._server: Optional[Any] = None

    def get_ae(self) -> AE:
        """Get or initialize Application Entity with standard VNA presentation contexts."""
        if self._ae is None:
            self._ae = AE(ae_title=self.settings.DICOM_AE_TITLE)
            self._ae.maximum_pdu_size = self.settings.DICOM_MAX_PDU

            # 1. Verification (C-ECHO)
            for context in VerificationPresentationContexts:
                self._ae.add_supported_context(context.abstract_syntax)
                self._ae.add_requested_context(context.abstract_syntax)

            # 2. Storage (C-STORE SCP & SCU for all composite storage classes with all transfer syntaxes)
            for context in StoragePresentationContexts:
                self._ae.add_supported_context(context.abstract_syntax, ALL_TRANSFER_SYNTAXES)
                self._ae.add_requested_context(context.abstract_syntax, ALL_TRANSFER_SYNTAXES)

            # 3. Query/Retrieve C-FIND
            qr_find_contexts = [
                PatientRootQueryRetrieveInformationModelFind,
                StudyRootQueryRetrieveInformationModelFind,
                ModalityWorklistInformationFind,
            ]
            for uid in qr_find_contexts:
                self._ae.add_supported_context(uid)
                self._ae.add_requested_context(uid)

            # 4. Query/Retrieve C-GET
            qr_get_contexts = [
                PatientRootQueryRetrieveInformationModelGet,
                StudyRootQueryRetrieveInformationModelGet,
            ]
            for uid in qr_get_contexts:
                self._ae.add_supported_context(uid)
                self._ae.add_requested_context(uid)

            # 5. Query/Retrieve C-MOVE
            qr_move_contexts = [
                PatientRootQueryRetrieveInformationModelMove,
                StudyRootQueryRetrieveInformationModelMove,
            ]
            for uid in qr_move_contexts:
                self._ae.add_supported_context(uid)
                self._ae.add_requested_context(uid)

        return self._ae

    def echo_scu(
        self,
        host: str,
        port: int,
        calling_aet: Optional[str] = None,
        called_aet: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send C-ECHO verification request to a remote DICOM SCP."""
        ae = self.get_ae()
        if calling_aet:
            ae.ae_title = calling_aet
        else:
            ae.ae_title = self.settings.DICOM_AE_TITLE
        target_aet = called_aet or "ANY_SCP"

        assoc = ae.associate(host, port, ae_title=target_aet)
        if assoc.is_established:
            status = assoc.send_c_echo()
            assoc.release()
            is_success = bool(status and status.Status == 0x0000)
            logger.info("c_echo_scu_completed", target_aet=target_aet, host=host, port=port, success=is_success)
            msg = "Association established and C-ECHO verified" if is_success else "C-ECHO returned non-zero status"
            return {
                "success": is_success,
                "status_code": hex(status.Status) if status else "None",
                "message": msg,
            }
        else:
            logger.warning("c_echo_scu_rejected", target_aet=target_aet, host=host, port=port)
            return {
                "success": False,
                "status_code": "ASSOCIATION_REJECTED",
                "message": f"Could not establish association with {target_aet}@{host}:{port}",
            }

    def handle_c_store(self, event: Any) -> int:
        """Handle incoming C-STORE request.

        Performs immediate raw Part-10 file persistence (blackbox recording)
        before hierarchy parsing and Neo4j graph registration.
        """
        request = event.request
        dataset = event.dataset

        # 1. Attach file_meta to construct compliant Part-10 FileDataset
        file_meta = getattr(event, "file_meta", None)
        if file_meta is not None:
            dataset.file_meta = file_meta
            if hasattr(file_meta, "TransferSyntaxUID"):
                dataset.is_little_endian = file_meta.TransferSyntaxUID.is_little_endian
                dataset.is_implicit_VR = file_meta.TransferSyntaxUID.is_implicit_VR

        # 2. Determine SOP Instance UID and storage URI
        sop_uid = str(
            getattr(request, "AffectedSOPInstanceUID", None)
            or getattr(dataset, "SOPInstanceUID", None)
            or f"quarantine_{uuid.uuid4()}"
        )
        storage_root = self.settings.STORAGE_ROOT_URI.rstrip("/")
        storage_uri = f"{storage_root}/{sop_uid}.dcm"

        # 3. FIRST ORDER OF ACTION: Blackbox Recording
        try:
            media_service.write_dicom_sync(storage_uri, dataset)
            logger.info("c_store_blackbox_saved", storage_uri=storage_uri, sop_instance_uid=sop_uid)
        except Exception as e:
            logger.error("c_store_blackbox_save_failed", storage_uri=storage_uri, error=str(e), exc_info=True)
            return 0xA700  # Out of Resources

        # 4. Parse clinical hierarchy
        try:
            hierarchy = dicom_service.parse_dataset(dataset, storage_uri=storage_uri)
        except Exception as e:
            logger.error(
                "c_store_parse_failed",
                storage_uri=storage_uri,
                sop_instance_uid=sop_uid,
                error=str(e),
                exc_info=True,
            )
            return 0xC000  # Cannot Understand / Processing Failure

        # 5. Register into Neo4j Graph
        try:
            graph_service.save_hierarchy_sync(hierarchy)
            logger.info(
                "c_store_graph_indexed",
                sop_instance_uid=sop_uid,
                patient_id=hierarchy.patient.patient_id,
                study_uid=hierarchy.study.study_instance_uid,
            )
        except Exception as e:
            logger.error(
                "c_store_graph_indexing_failed",
                storage_uri=storage_uri,
                sop_instance_uid=sop_uid,
                error=str(e),
                exc_info=True,
            )
            return 0xA700  # Out of Resources / Storage Error

        return 0x0000  # SUCCESS

    def handle_c_echo(self, event: Any) -> int:
        """Handle incoming C-ECHO verification ping."""
        requestor = getattr(getattr(event, "assoc", None), "requestor", None)
        calling_aet = getattr(requestor, "ae_title", "UNKNOWN")
        logger.info("c_echo_received", calling_aet=calling_aet)
        return 0x0000

    def start_server(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        block: bool = False,
    ) -> Any:
        """Start background DICOM C-STORE and C-ECHO SCP server."""
        bind_host = host or getattr(self.settings, "DICOM_SCP_HOST", "0.0.0.0")
        bind_port = port or getattr(self.settings, "DICOM_SCP_PORT", 11112)

        handlers = [
            (evt.EVT_C_STORE, self.handle_c_store),
            (evt.EVT_C_ECHO, self.handle_c_echo),
        ]

        ae = self.get_ae()
        self._server = ae.start_server(
            (bind_host, bind_port),
            block=block,
            evt_handlers=handlers,
        )
        logger.info(
            "dicom_scp_server_started",
            host=bind_host,
            port=bind_port,
            ae_title=ae.ae_title,
        )
        return self._server

    def stop_server(self) -> None:
        """Stop running DICOM SCP server."""
        if self._server is not None:
            self._server.shutdown()
            self._server = None
            logger.info("dicom_scp_server_stopped")

    def is_running(self) -> bool:
        """Check if DICOM SCP server is currently active."""
        return self._server is not None


pynetdicom_service = PyNetDicomService()
