from typing import Any, Dict, Optional

from pynetdicom import (
    AE,
    StoragePresentationContexts,
    VerificationPresentationContexts,
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

logger = get_logger("gosmart_gs.dicom_net")


class PyNetDicomService:
    """Manages DICOM VNA network operations using pynetdicom (C-STORE, C-FIND, C-GET, C-MOVE, C-ECHO)."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self._ae: Optional[AE] = None
        self._server = None

    def get_ae(self) -> AE:
        """Get or initialize Application Entity with standard VNA presentation contexts."""
        if self._ae is None:
            self._ae = AE(ae_title=self.settings.DICOM_AE_TITLE)
            self._ae.maximum_pdu_size = self.settings.DICOM_MAX_PDU

            # 1. Verification (C-ECHO)
            for context in VerificationPresentationContexts:
                self._ae.add_supported_context(context.abstract_syntax)
                self._ae.add_requested_context(context.abstract_syntax)

            # 2. Storage (C-STORE SCP & SCU for all composite storage classes)
            for context in StoragePresentationContexts:
                self._ae.add_supported_context(context.abstract_syntax)
                self._ae.add_requested_context(context.abstract_syntax)

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
        source_aet = calling_aet or self.settings.DICOM_AE_TITLE
        target_aet = called_aet or "ANY_SCP"

        assoc = ae.associate(host, port, ae_title=target_aet, calling_aet=source_aet)
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


pynetdicom_service = PyNetDicomService()
