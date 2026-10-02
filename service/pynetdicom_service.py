import socket
import ssl
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydicom.dataset import Dataset
from pydicom.uid import (
    UID,
    ExplicitVRLittleEndian,
    ImplicitVRLittleEndian,
    JPEG2000Lossless,
    UID_dictionary,
)
from pynetdicom import (
    AE,
    ALL_TRANSFER_SYNTAXES,
    StoragePresentationContexts,
    VerificationPresentationContexts,
    evt,
)
from pynetdicom.presentation import PresentationContext, build_context
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


def _normalize_key(s: str) -> str:
    """Normalize UID names or keywords for insensitive matching."""
    return s.lower().replace(" ", "").replace("_", "").replace("-", "")


# Build fast lookup dictionary for UIDs, keywords, and display names
_UID_LOOKUP: Dict[str, UID] = {}
for _k_uid, _entry in UID_dictionary.items():
    _name, _, _, _, _keyword = _entry
    _norm_uid = _normalize_key(_k_uid)
    _UID_LOOKUP[_norm_uid] = UID(_k_uid)
    if _name:
        _UID_LOOKUP[_normalize_key(_name)] = UID(_k_uid)
        _UID_LOOKUP[_normalize_key(_name.replace("sop class", ""))] = UID(_k_uid)
    if _keyword:
        _UID_LOOKUP[_normalize_key(_keyword)] = UID(_k_uid)
        _UID_LOOKUP[_normalize_key(_keyword + "sopclass")] = UID(_k_uid)


def resolve_uid(value: str) -> Optional[UID]:
    """Resolve a UID string, keyword, or display name to a canonical pydicom UID."""
    cleaned = value.strip()
    norm = _normalize_key(cleaned)
    if norm in _UID_LOOKUP:
        return _UID_LOOKUP[norm]
    # Direct dotted numeric UID check
    if cleaned.replace(".", "").isdigit() and "." in cleaned:
        return UID(cleaned)
    return None


class SCPInstance:
    """Represents an active or draining DICOM SCP server instance."""

    def __init__(
        self,
        config_id: str,
        ae_title: str,
        host: str,
        port: int,
        config: Dict[str, Any],
        ae: AE,
        server: Optional[Any] = None,
        status: str = "RUNNING",
        error_message: Optional[str] = None,
    ) -> None:
        self.config_id = config_id
        self.ae_title = ae_title
        self.host = host
        self.port = port
        self.config = config
        self.ae = ae
        self.server = server
        self.status = status
        self.error_message = error_message
        self.created_at = datetime.now(timezone.utc)

    @property
    def active_associations(self) -> List[Any]:
        """Return currently active association threads for this server."""
        if self.server is not None and hasattr(self.server, "active_associations"):
            try:
                return self.server.active_associations
            except Exception:
                return []
        return []

    def graceful_stop(self) -> None:
        """Close listening socket to reject new associations while allowing in-flight associations to complete."""
        if self.status in ("DRAINING", "STOPPED"):
            return
        self.status = "DRAINING"
        if self.server is not None:
            try:
                logger.info(
                    "scp_graceful_stop_initiated",
                    config_id=self.config_id,
                    ae_title=self.ae_title,
                    port=self.port,
                    active_associations=len(self.active_associations),
                )
                self.server.shutdown()
            except Exception as e:
                logger.warning("scp_server_shutdown_error", config_id=self.config_id, error=str(e))

    def is_drained(self) -> bool:
        """Check if all active association threads have concluded."""
        return len(self.active_associations) == 0

    def to_dict(self) -> Dict[str, Any]:
        """Serialize status summary for monitoring and API."""
        return {
            "config_id": self.config_id,
            "ae_title": self.ae_title,
            "host": self.host,
            "port": self.port,
            "status": self.status,
            "error_message": self.error_message,
            "active_associations_count": len(self.active_associations),
            "created_at": self.created_at.isoformat(),
        }


def _clean_cfind_param(val: Any) -> Optional[str]:
    """Normalize C-FIND request parameter.

    In DICOM C-FIND, zero-length attributes and single wildcard '*' or empty strings
    indicate return keys or open matching, which should not restrict the query.
    """
    if val is None:
        return None
    s = str(val).strip()
    if not s or s == "*":
        return None
    return s


def is_local_address(host: str) -> bool:
    """Check if a host address belongs to local machine interfaces."""
    if not host or host in ("0.0.0.0", "127.0.0.1", "localhost", "::", "::1"):
        return True
    try:
        local_ips = {addr[4][0] for addr in socket.getaddrinfo(socket.gethostname(), None)}
        local_ips.update({"127.0.0.1", "0.0.0.0", "::1"})
        return host in local_ips
    except Exception:
        return False


class PyNetDicomService:
    """Manages DICOM VNA network operations and dynamic multi-SCP instances."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self._instances: Dict[str, SCPInstance] = {}
        self._draining_instances: List[SCPInstance] = []
        self._lock = threading.Lock()
        self._client_ae: Optional[AE] = None

    @property
    def _server(self) -> Optional[Any]:
        """Backward-compatibility: return server of the first running SCP instance."""
        for inst in self._instances.values():
            if inst.server is not None and inst.status == "RUNNING":
                return inst.server
        return None

    @property
    def _ae(self) -> AE:
        """Backward-compatibility: return client AE."""
        return self.get_ae()

    def get_ae(self) -> AE:
        """Get or initialize Application Entity configured for client/outbound SCU operations."""
        if self._client_ae is None:
            ae = AE(ae_title=self.settings.DICOM_AE_TITLE)
            ae.maximum_pdu_size = self.settings.DICOM_MAX_PDU

            # 1. Verification (C-ECHO)
            for context in VerificationPresentationContexts:
                ae.add_supported_context(context.abstract_syntax)
                ae.add_requested_context(context.abstract_syntax)

            # 2. Storage
            for context in StoragePresentationContexts:
                ae.add_supported_context(context.abstract_syntax, ALL_TRANSFER_SYNTAXES)
                ae.add_requested_context(context.abstract_syntax, ALL_TRANSFER_SYNTAXES)

            # 3. Query/Retrieve C-FIND
            qr_find = [
                PatientRootQueryRetrieveInformationModelFind,
                StudyRootQueryRetrieveInformationModelFind,
                ModalityWorklistInformationFind,
            ]
            for uid in qr_find:
                ae.add_supported_context(uid)
                ae.add_requested_context(uid)

            # 4. Query/Retrieve C-GET
            qr_get = [
                PatientRootQueryRetrieveInformationModelGet,
                StudyRootQueryRetrieveInformationModelGet,
            ]
            for uid in qr_get:
                ae.add_supported_context(uid)
                ae.add_requested_context(uid)

            # 5. Query/Retrieve C-MOVE
            qr_move = [
                PatientRootQueryRetrieveInformationModelMove,
                StudyRootQueryRetrieveInformationModelMove,
            ]
            for uid in qr_move:
                ae.add_supported_context(uid)
                ae.add_requested_context(uid)

            self._client_ae = ae

        return self._client_ae

    def build_presentation_contexts(
        self,
        dicom_services: Optional[List[str]] = None,
        transfer_syntaxes: Optional[List[str]] = None,
    ) -> List[PresentationContext]:
        """Build presentation contexts strictly honoring configured service classes and transfer syntaxes."""
        # 1. Resolve Transfer Syntaxes
        ts_list: List[str] = []
        raw_ts = transfer_syntaxes or ["*"]
        if "*" in raw_ts:
            ts_list = [str(ts) for ts in ALL_TRANSFER_SYNTAXES]
        else:
            for ts_item in raw_ts:
                resolved = resolve_uid(ts_item)
                if resolved:
                    ts_list.append(str(resolved))
                else:
                    logger.warning("unknown_transfer_syntax_ignored", syntax=ts_item)
            if not ts_list:
                ts_list = [str(ts) for ts in ALL_TRANSFER_SYNTAXES]

        # 2. Resolve Service Classes / SOP Classes
        raw_services = dicom_services or ["*"]
        contexts: List[PresentationContext] = []

        if "*" in raw_services:
            # Full standard suite
            for ctx in VerificationPresentationContexts:
                contexts.append(build_context(ctx.abstract_syntax, ts_list))
            for ctx in StoragePresentationContexts:
                cx = build_context(ctx.abstract_syntax, ts_list)
                cx.scu_role = True
                cx.scp_role = True
                contexts.append(cx)

            qr_services = [
                PatientRootQueryRetrieveInformationModelFind,
                StudyRootQueryRetrieveInformationModelFind,
                ModalityWorklistInformationFind,
                PatientRootQueryRetrieveInformationModelGet,
                StudyRootQueryRetrieveInformationModelGet,
                PatientRootQueryRetrieveInformationModelMove,
                StudyRootQueryRetrieveInformationModelMove,
            ]
            for sop_uid in qr_services:
                contexts.append(build_context(sop_uid, ts_list))
        else:
            # Strictly honor configured SOP Classes
            for svc in raw_services:
                resolved_uid = resolve_uid(svc)
                if resolved_uid:
                    contexts.append(build_context(str(resolved_uid), ts_list))
                else:
                    logger.warning("unknown_service_class_ignored", service=svc)

        return contexts

    def start_scp_for_config(
        self,
        config: Dict[str, Any],
        block: bool = False,
    ) -> Optional[SCPInstance]:
        """Instantiate and start a new SCP server for a given DicomConfiguration."""
        config_id = config.get("id", str(uuid.uuid4()))
        roles = [r.upper() for r in config.get("roles", [])]
        if "SCP" not in roles:
            logger.info("skip_scp_start_not_scp_role", config_id=config_id, roles=roles)
            return None

        ae_title = config.get("ae_title", "*")
        host = config.get("host") or getattr(self.settings, "DICOM_SCP_HOST", "0.0.0.0")
        port = int(config.get("port") or getattr(self.settings, "DICOM_SCP_PORT", 11112))
        is_promiscuous = bool(config.get("is_promiscuous", ae_title == "*"))

        # Skip local socket binding for remote hosts (e.g. external SCP configurations)
        if config.get("is_local") is False or not is_local_address(host):
            logger.info("skip_scp_start_remote_host", config_id=config_id, host=host, port=port)
            return None

        # Format compliant AE Title (max 16 ASCII chars)
        resolved_aet = (self.settings.DICOM_AE_TITLE if ae_title == "*" else ae_title)[:16].strip()

        # Initialize dedicated Application Entity for this SCP
        ae = AE(ae_title=resolved_aet)
        ae.maximum_pdu_size = self.settings.DICOM_MAX_PDU

        if is_promiscuous or ae_title == "*":
            ae.require_called_aet = False
        else:
            ae.require_called_aet = [resolved_aet]

        # Register requested storage contexts on AE for outbound C-MOVE sub-operations
        for cx in StoragePresentationContexts:
            ae.add_requested_context(
                cx.abstract_syntax,
                [JPEG2000Lossless, ExplicitVRLittleEndian, ImplicitVRLittleEndian],
            )

        # Build negotiated Presentation Contexts strictly honoring configuration
        contexts = self.build_presentation_contexts(
            dicom_services=config.get("dicom_services"),
            transfer_syntaxes=config.get("transfer_syntaxes"),
        )

        handlers = [
            (evt.EVT_C_STORE, self.handle_c_store),
            (evt.EVT_C_ECHO, self.handle_c_echo),
            (evt.EVT_C_FIND, self.handle_c_find),
            (evt.EVT_C_MOVE, self.handle_c_move),
            (evt.EVT_C_GET, self.handle_c_get),
        ]

        # Setup TLS if configured
        ssl_context = None
        if config.get("tls_enabled"):
            try:
                cert_path = config.get("tls_cert_path")
                key_path = config.get("tls_key_path")
                ca_path = config.get("tls_ca_path")
                ciphers = config.get("tls_ciphers")

                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                if cert_path and key_path:
                    ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)
                if ca_path:
                    ctx.load_verify_locations(cafile=ca_path)
                    ctx.verify_mode = ssl.CERT_REQUIRED
                if ciphers:
                    ctx.set_ciphers(":".join(ciphers))
                ssl_context = ctx
            except Exception as e:
                logger.error("scp_tls_init_failed", config_id=config_id, error=str(e), exc_info=True)
                instance = SCPInstance(
                    config_id=config_id,
                    ae_title=resolved_aet,
                    host=host,
                    port=port,
                    config=config,
                    ae=ae,
                    server=None,
                    status="ERROR",
                    error_message=f"TLS initialization failed: {str(e)}",
                )
                with self._lock:
                    self._instances[config_id] = instance
                return instance

        # Start non-blocking server
        try:
            server = ae.start_server(
                (host, port),
                block=block,
                ssl_context=ssl_context,
                evt_handlers=handlers,
                contexts=contexts,
            )
            instance = SCPInstance(
                config_id=config_id,
                ae_title=resolved_aet,
                host=host,
                port=port,
                config=config,
                ae=ae,
                server=server,
                status="RUNNING",
            )
            logger.info(
                "dicom_scp_instance_started",
                config_id=config_id,
                ae_title=resolved_aet,
                host=host,
                port=port,
                is_promiscuous=is_promiscuous,
                contexts_count=len(contexts),
            )
            with self._lock:
                self._instances[config_id] = instance
            return instance
        except Exception as e:
            logger.error(
                "dicom_scp_instance_start_failed",
                config_id=config_id,
                ae_title=resolved_aet,
                host=host,
                port=port,
                error=str(e),
                exc_info=True,
            )
            instance = SCPInstance(
                config_id=config_id,
                ae_title=resolved_aet,
                host=host,
                port=port,
                config=config,
                ae=ae,
                server=None,
                status="ERROR",
                error_message=str(e),
            )
            with self._lock:
                self._instances[config_id] = instance
            return instance

    def on_configuration_added(self, config: Dict[str, Any]) -> Optional[SCPInstance]:
        """Handle Added SCP configuration: Instantiate and launch a new SCP instance."""
        config_id = config.get("id")
        if not config_id:
            return None
        roles = [r.upper() for r in config.get("roles", [])]
        if "SCP" not in roles:
            return None

        with self._lock:
            existing = self._instances.get(config_id)
        if existing and existing.status == "RUNNING":
            return self.on_configuration_changed(config_id, config)

        return self.start_scp_for_config(config)

    def on_configuration_changed(self, config_id: str, new_config: Dict[str, Any]) -> Optional[SCPInstance]:
        """Handle Changed SCP configuration: Maintain in-flight associations and start new SCP."""
        with self._lock:
            old_instance = self._instances.pop(config_id, None)

        if old_instance:
            logger.info(
                "scp_configuration_changed",
                config_id=config_id,
                old_aet=old_instance.ae_title,
                new_aet=new_config.get("ae_title"),
            )
            old_instance.graceful_stop()
            with self._lock:
                self._draining_instances.append(old_instance)

        roles = [r.upper() for r in new_config.get("roles", [])]
        if "SCP" in roles:
            new_instance = self.start_scp_for_config(new_config)
            return new_instance
        return None

    def on_configuration_removed(self, config_id: str) -> Optional[SCPInstance]:
        """Handle Removed SCP configuration: Maintain in-flight associations until done."""
        with self._lock:
            old_instance = self._instances.pop(config_id, None)

        if old_instance:
            logger.info(
                "scp_configuration_removed",
                config_id=config_id,
                ae_title=old_instance.ae_title,
            )
            old_instance.graceful_stop()
            with self._lock:
                self._draining_instances.append(old_instance)
            return old_instance
        return None

    def update_scp_for_config(self, config: Dict[str, Any]) -> Optional[SCPInstance]:
        """Update an existing SCP configuration dynamically, draining the old instance."""
        config_id = config.get("id")
        if not config_id:
            return None
        return self.on_configuration_changed(config_id, config)

    def stop_scp_for_config(self, config_id: str, graceful: bool = True) -> Optional[SCPInstance]:
        """Stop an SCP instance for a given configuration ID."""
        return self.on_configuration_removed(config_id)

    def cleanup_drained_instances(self) -> None:
        """Prune draining instances whose in-flight associations have fully completed."""
        with self._lock:
            still_draining: List[SCPInstance] = []
            for inst in self._draining_instances:
                if inst.is_drained():
                    inst.status = "STOPPED"
                    logger.info(
                        "scp_instance_drained_and_concluded",
                        config_id=inst.config_id,
                        ae_title=inst.ae_title,
                    )
                else:
                    still_draining.append(inst)
            self._draining_instances = still_draining

    def sync_from_configurations(self, configs: List[Dict[str, Any]]) -> None:
        """Synchronize active SCP instances with a list of configurations from Neo4j."""
        self.cleanup_drained_instances()
        active_ids = set()

        for cfg in configs:
            cid = cfg.get("id")
            if not cid:
                continue
            roles = [r.upper() for r in cfg.get("roles", [])]
            if "SCP" in roles:
                active_ids.add(cid)
                with self._lock:
                    existing = self._instances.get(cid)
                if not existing or existing.status != "RUNNING":
                    self.start_scp_for_config(cfg)

        # Stop instances that are no longer active
        with self._lock:
            current_ids = list(self._instances.keys())
        for cid in current_ids:
            if cid not in active_ids:
                self.on_configuration_removed(cid)

    def stop_all(self) -> None:
        """Stop all active and draining SCP instances (e.g. during application shutdown)."""
        with self._lock:
            all_instances = list(self._instances.values()) + list(self._draining_instances)
            self._instances.clear()
            self._draining_instances.clear()

        for inst in all_instances:
            try:
                inst.graceful_stop()
                inst.status = "STOPPED"
            except Exception as e:
                logger.warning("scp_stop_all_error", config_id=inst.config_id, error=str(e))
        logger.info("all_scp_servers_stopped")

    def get_instance(self, config_id: str) -> Optional[SCPInstance]:
        """Retrieve an active or draining SCP instance by configuration ID."""
        with self._lock:
            if config_id in self._instances:
                return self._instances[config_id]
            for inst in self._draining_instances:
                if inst.config_id == config_id:
                    return inst
        return None

    def list_instances(self) -> List[Dict[str, Any]]:
        """List status dictionaries for all active and draining SCP instances."""
        self.cleanup_drained_instances()
        with self._lock:
            return [inst.to_dict() for inst in self._instances.values()] + [
                inst.to_dict() for inst in self._draining_instances
            ]

    list_scp_instances = list_instances

    def is_running(self) -> bool:
        """Check if any SCP server instance is currently active."""
        with self._lock:
            return any(inst.status == "RUNNING" and inst.server is not None for inst in self._instances.values())

    def start_server(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        block: bool = False,
        ae_title: Optional[str] = None,
        contexts: Optional[List[PresentationContext]] = None,
    ) -> Any:
        """Backward-compatible helper to start an ad-hoc or default SCP server."""
        bind_host = host or getattr(self.settings, "DICOM_SCP_HOST", "0.0.0.0")
        bind_port = port or getattr(self.settings, "DICOM_SCP_PORT", 11112)
        bind_aet = ae_title or getattr(self.settings, "DICOM_AE_TITLE", "GOSMART_GS")

        cfg = {
            "id": f"server_{bind_host}_{bind_port}",
            "ae_title": bind_aet,
            "roles": ["SCP"],
            "host": bind_host,
            "port": bind_port,
            "is_promiscuous": (bind_aet == "*"),
            "dicom_services": ["*"],
            "transfer_syntaxes": ["*"],
        }
        inst = self.start_scp_for_config(cfg, block=block)
        return inst.server if inst else None

    def stop_server(self) -> None:
        """Backward-compatible helper to stop running SCP servers."""
        self.stop_all()

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

        # 4. Parse clinical hierarchy & attach stream provenance
        assoc = getattr(event, "assoc", None)
        requestor = getattr(assoc, "requestor", None)
        client_ip = getattr(requestor, "address", "unknown")
        calling_aet = getattr(requestor, "ae_title", "UNKNOWN")

        acceptor = getattr(assoc, "acceptor", None)
        server_ip = getattr(acceptor, "address", getattr(self.settings, "DICOM_SCP_HOST", "0.0.0.0"))
        server_port = getattr(acceptor, "port", getattr(self.settings, "DICOM_SCP_PORT", 11112))
        called_aet = getattr(acceptor, "ae_title", getattr(self.settings, "DICOM_AE_TITLE", "GOSMART_GS"))

        try:
            hierarchy = dicom_service.parse_dataset(dataset, storage_uri=storage_uri)
            hierarchy.instance.source = f"dimse:{client_ip}"
            hierarchy.instance.source_identity = f"ae:{calling_aet}"
            hierarchy.instance.destination = f"dimse:{server_ip}:{server_port}"
            hierarchy.instance.destination_identity = f"ae:{called_aet}"
            hierarchy.acted_by = f"ae:{calling_aet}"
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

    def handle_c_find(self, event: Any):
        """Handle incoming C-FIND Query/Retrieve request.

        Supports PATIENT, STUDY, SERIES, and IMAGE/INSTANCE query levels against the Neo4j Graph.
        """
        requestor = getattr(getattr(event, "assoc", None), "requestor", None)
        calling_aet = getattr(requestor, "ae_title", "UNKNOWN")
        identifier = getattr(event, "identifier", None)
        if identifier is None:
            logger.warning("c_find_no_identifier", calling_aet=calling_aet)
            yield (0xC000, None)
            return

        level = getattr(identifier, "QueryRetrieveLevel", "STUDY")
        norm_level = str(level).upper()
        logger.info("c_find_received", calling_aet=calling_aet, level=norm_level)

        try:
            if norm_level == "PATIENT":
                pid = _clean_cfind_param(getattr(identifier, "PatientID", None))
                pname = _clean_cfind_param(getattr(identifier, "PatientName", None))
                matches = graph_service.find_patient_records_sync(
                    patient_id=pid,
                    patient_name=pname,
                )
                for rec in matches:
                    if getattr(event, "is_cancelled", False):
                        yield (0xFE00, None)
                        return
                    ds = Dataset()
                    ds.QueryRetrieveLevel = "PATIENT"
                    ds.PatientID = rec.get("patient_id") or ""
                    ds.PatientName = rec.get("patient_name") or ""
                    ds.PatientBirthDate = rec.get("patient_birth_date") or ""
                    ds.PatientSex = rec.get("patient_sex") or ""
                    yield (0xFF00, ds)

            elif norm_level == "STUDY":
                pid = _clean_cfind_param(getattr(identifier, "PatientID", None))
                pname = _clean_cfind_param(getattr(identifier, "PatientName", None))
                suid = _clean_cfind_param(getattr(identifier, "StudyInstanceUID", None))
                sdate = _clean_cfind_param(getattr(identifier, "StudyDate", None))
                acc = _clean_cfind_param(getattr(identifier, "AccessionNumber", None))
                matches = graph_service.search_studies_qido_sync(
                    patient_id=pid,
                    patient_name=pname,
                    study_uid=suid,
                    study_date=sdate,
                    accession_number=acc,
                    include_tests=True,
                )
                for rec in matches:
                    if getattr(event, "is_cancelled", False):
                        yield (0xFE00, None)
                        return
                    ds = Dataset()
                    ds.QueryRetrieveLevel = "STUDY"
                    ds.StudyInstanceUID = rec.get("study_instance_uid") or ""
                    ds.StudyDate = rec.get("study_date") or ""
                    ds.StudyTime = rec.get("study_time") or ""
                    ds.StudyDescription = rec.get("study_description") or ""
                    ds.AccessionNumber = rec.get("accession_number") or ""
                    ds.StudyID = rec.get("study_id") or ""
                    ds.PatientID = rec.get("patient_id") or ""
                    ds.PatientName = rec.get("patient_name") or ""
                    ds.PatientBirthDate = rec.get("patient_birth_date") or ""
                    ds.PatientSex = rec.get("patient_sex") or ""
                    mods = rec.get("modalities") or []
                    if mods:
                        ds.ModalitiesInStudy = "\\".join(mods)
                    ds.NumberOfStudyRelatedSeries = int(rec.get("num_series", 0))
                    ds.NumberOfStudyRelatedInstances = int(rec.get("num_instances", 0))
                    yield (0xFF00, ds)

            elif norm_level == "SERIES":
                suid = _clean_cfind_param(getattr(identifier, "StudyInstanceUID", None))
                seuid = _clean_cfind_param(getattr(identifier, "SeriesInstanceUID", None))
                mod = _clean_cfind_param(getattr(identifier, "Modality", None))
                matches = graph_service.search_series_qido_sync(
                    study_uid=suid,
                    series_uid=seuid,
                    modality=mod,
                    include_tests=True,
                )
                for rec in matches:
                    if getattr(event, "is_cancelled", False):
                        yield (0xFE00, None)
                        return
                    ds = Dataset()
                    ds.QueryRetrieveLevel = "SERIES"
                    ds.StudyInstanceUID = rec.get("study_instance_uid") or ""
                    ds.SeriesInstanceUID = rec.get("series_instance_uid") or ""
                    ds.Modality = rec.get("modality") or ""
                    ds.SeriesNumber = int(rec.get("series_number") or 1)
                    ds.SeriesDescription = rec.get("series_description") or ""
                    ds.BodyPartExamined = rec.get("body_part_examined") or ""
                    ds.NumberOfSeriesRelatedInstances = int(rec.get("num_instances", 0))
                    yield (0xFF00, ds)

            elif norm_level in ("IMAGE", "INSTANCE"):
                suid = _clean_cfind_param(getattr(identifier, "StudyInstanceUID", None))
                seuid = _clean_cfind_param(getattr(identifier, "SeriesInstanceUID", None))
                sopuid = _clean_cfind_param(getattr(identifier, "SOPInstanceUID", None))
                matches = graph_service.search_instances_qido_sync(
                    study_uid=suid,
                    series_uid=seuid,
                    sop_uid=sopuid,
                    include_tests=True,
                )
                for rec in matches:
                    if getattr(event, "is_cancelled", False):
                        yield (0xFE00, None)
                        return
                    ds = Dataset()
                    ds.QueryRetrieveLevel = "IMAGE"
                    ds.StudyInstanceUID = rec.get("study_instance_uid") or ""
                    ds.SeriesInstanceUID = rec.get("series_instance_uid") or ""
                    ds.SOPInstanceUID = rec.get("sop_instance_uid") or ""
                    ds.SOPClassUID = rec.get("sop_class_uid") or ""
                    ds.InstanceNumber = int(rec.get("instance_number") or 1)
                    yield (0xFF00, ds)

            else:
                logger.warning("c_find_unsupported_level", level=norm_level)
                yield (0xA900, None)  # Identifier does not match SOP Class
                return

        except Exception as e:
            logger.error("c_find_execution_failed", error=str(e), exc_info=True)
            yield (0xC000, None)
            return

        yield (0x0000, None)  # Success

    def build_outbound_move_contexts(self, matching_instances: List[Dict[str, Any]]) -> List[PresentationContext]:
        """Build optimized presentation contexts for outbound C-MOVE C-STORE sub-operations.

        Proposes dedicated presentation contexts for each matching instance's native transfer
        syntax to ensure peers like Micro DICOM negotiate the exact required syntax rather than
        falling back to suboptimal formats. Also includes uncompressed transfer syntax fallbacks.
        """
        contexts: List[PresentationContext] = []
        seen = set()

        for inst in matching_instances:
            sop_class = inst.get("sop_class_uid")
            ts_uid = inst.get("transfer_syntax_uid")
            if not sop_class:
                continue

            if not ts_uid:
                storage_uri = inst.get("storage_uri")
                if storage_uri:
                    try:
                        ds = media_service.read_dicom_sync(storage_uri)
                        ts_uid = getattr(getattr(ds, "file_meta", None), "TransferSyntaxUID", None)
                    except Exception:
                        pass

            if sop_class and ts_uid:
                key = (str(sop_class), str(ts_uid))
                if key not in seen and len(contexts) < 120:
                    seen.add(key)
                    contexts.append(build_context(sop_class, [ts_uid]))

            for fallback_ts in (ExplicitVRLittleEndian, ImplicitVRLittleEndian):
                key = (str(sop_class), str(fallback_ts))
                if key not in seen and len(contexts) < 120:
                    seen.add(key)
                    contexts.append(build_context(sop_class, [fallback_ts]))

        for cx in StoragePresentationContexts:
            if len(contexts) >= 128:
                break
            if cx.abstract_syntax not in [c.abstract_syntax for c in contexts]:
                contexts.append(build_context(cx.abstract_syntax, [ExplicitVRLittleEndian, ImplicitVRLittleEndian]))

        return contexts or list(StoragePresentationContexts)

    def handle_c_move(self, event: Any):
        """Handle incoming C-MOVE retrieve request.

        Queries matching instances and orchestrates secondary C-STORE sub-operations to destination AE.
        """
        dest_aet = getattr(event, "move_destination", None)
        requestor = getattr(getattr(event, "assoc", None), "requestor", None)
        calling_aet = getattr(requestor, "ae_title", "UNKNOWN")
        identifier = getattr(event, "identifier", None)

        if not dest_aet or identifier is None:
            logger.warning("c_move_missing_parameters", dest_aet=dest_aet)
            yield (None, None)
            return

        # HAZ-009 Loop Protection: Destination cannot be local SCP AE title
        acceptor = getattr(getattr(event, "assoc", None), "acceptor", None)
        local_aet = getattr(acceptor, "ae_title", getattr(self.settings, "DICOM_AE_TITLE", "GOSMART_GS"))
        if dest_aet.strip() == local_aet.strip():
            logger.warning("c_move_loop_detected", dest_aet=dest_aet, local_aet=local_aet)
            yield (None, None)
            return

        # Resolve destination AE in Neo4j
        dest_info = graph_service.find_destination_ae_sync(dest_aet)
        requestor_address = getattr(requestor, "address", None)
        if not dest_info:
            if requestor_address and requestor_address not in ("127.0.0.1", "localhost", "::1"):
                logger.info("c_move_destination_fallback", dest_aet=dest_aet, fallback_host=requestor_address)
                dest_host = requestor_address
                dest_port = 11113
            else:
                logger.warning("c_move_destination_unknown", dest_aet=dest_aet)
                yield (None, None)  # Signals 0xA801 Move Destination Unknown
                return
        else:
            dest_host = dest_info["host"]
            dest_port = dest_info["port"]
            if (
                dest_host in ("127.0.0.1", "localhost", "0.0.0.0", "::1")
                and requestor_address
                and requestor_address not in ("127.0.0.1", "localhost", "::1")
            ):
                logger.info("c_move_reroute_to_remote_requestor", dest_aet=dest_aet, dest_host=requestor_address)
                dest_host = requestor_address

        level = getattr(identifier, "QueryRetrieveLevel", "STUDY")
        norm_level = str(level).upper()
        pid = getattr(identifier, "PatientID", None)
        suid = getattr(identifier, "StudyInstanceUID", None)
        seuid = getattr(identifier, "SeriesInstanceUID", None)
        sopuid = getattr(identifier, "SOPInstanceUID", None)

        matching_instances = graph_service.find_instances_for_retrieve_sync(
            level=norm_level,
            patient_id=pid,
            study_uid=suid,
            series_uid=seuid,
            sop_uid=sopuid,
            include_tests=True,
        )

        logger.info(
            "c_move_started",
            calling_aet=calling_aet,
            dest_aet=dest_aet,
            dest_host=dest_host,
            dest_port=dest_port,
            matches_count=len(matching_instances),
        )

        # 1. First yield: Destination (host, port, {"contexts": outbound_contexts})
        outbound_contexts = self.build_outbound_move_contexts(matching_instances)
        yield (dest_host, dest_port, {"contexts": outbound_contexts})

        # 2. Second yield: Number of C-STORE sub-operations
        num_subops = len(matching_instances)
        yield num_subops

        # 3. Stream each instance to destination Storage SCP
        for inst in matching_instances:
            if getattr(event, "is_cancelled", False):
                yield (0xFE00, None)
                return
            storage_uri = inst.get("storage_uri")
            if not storage_uri:
                continue
            try:
                ds = media_service.read_dicom_sync(storage_uri)
                yield (0xFF00, ds)
            except Exception as read_err:
                logger.error("c_move_instance_read_failed", storage_uri=storage_uri, error=str(read_err))

    def handle_c_get(self, event: Any):
        """Handle incoming C-GET retrieve request.

        Transmits matching instances across the same association via C-STORE sub-operations.
        """
        requestor = getattr(getattr(event, "assoc", None), "requestor", None)
        calling_aet = getattr(requestor, "ae_title", "UNKNOWN")
        identifier = getattr(event, "identifier", None)
        if identifier is None:
            yield 0
            return

        level = getattr(identifier, "QueryRetrieveLevel", "STUDY")
        norm_level = str(level).upper()
        pid = getattr(identifier, "PatientID", None)
        suid = getattr(identifier, "StudyInstanceUID", None)
        seuid = getattr(identifier, "SeriesInstanceUID", None)
        sopuid = getattr(identifier, "SOPInstanceUID", None)

        matching_instances = graph_service.find_instances_for_retrieve_sync(
            level=norm_level,
            patient_id=pid,
            study_uid=suid,
            series_uid=seuid,
            sop_uid=sopuid,
            include_tests=True,
        )

        logger.info(
            "c_get_started",
            calling_aet=calling_aet,
            level=norm_level,
            matches_count=len(matching_instances),
        )

        # 1. First yield: Total number of sub-operations
        num_subops = len(matching_instances)
        yield num_subops

        # 2. Stream instances across current association
        for inst in matching_instances:
            if getattr(event, "is_cancelled", False):
                yield (0xFE00, None)
                return
            storage_uri = inst.get("storage_uri")
            if not storage_uri:
                continue
            try:
                ds = media_service.read_dicom_sync(storage_uri)
                yield (0xFF00, ds)
            except Exception as read_err:
                logger.error("c_get_instance_read_failed", storage_uri=storage_uri, error=str(read_err))


pynetdicom_service = PyNetDicomService()
