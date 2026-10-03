import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import pydicom
import pykka
from pydicom.dataset import Dataset
from pydicom.uid import (
    ExplicitVRLittleEndian,
    ImplicitVRLittleEndian,
)
from pynetdicom import AE, StoragePresentationContexts
from pynetdicom.presentation import PresentationContext, build_context

from core.config import get_settings
from core.logging import get_logger
from model.graph import TransferStatusNode
from service.graph_service import graph_service

logger = get_logger("gosmart_gs.store_scu_actor")


def build_store_contexts(datasets: List[Dataset]) -> List[PresentationContext]:
    """Build presentation contexts proposing dedicated single native transfer syntax per context.

    Prevents SCPs like MicroDICOM (MDICOM) from choosing suboptimal or rejected transfer
    syntaxes when multiple syntaxes are bundled into a single presentation context.
    """
    contexts: List[PresentationContext] = []
    seen: Set[Tuple[str, str]] = set()

    # 1. Propose exact native transfer syntax per dataset as dedicated individual contexts
    for ds in datasets:
        sop_class = str(getattr(ds, "SOPClassUID", ""))
        if not sop_class:
            continue

        file_meta = getattr(ds, "file_meta", None)
        ts_uid = str(getattr(file_meta, "TransferSyntaxUID", ExplicitVRLittleEndian))

        key = (sop_class, ts_uid)
        if key not in seen and len(contexts) < 120:
            seen.add(key)
            contexts.append(build_context(sop_class, [ts_uid]))

        # Dedicated single-syntax fallback contexts
        for fallback_ts in (ExplicitVRLittleEndian, ImplicitVRLittleEndian):
            fb_key = (sop_class, str(fallback_ts))
            if fb_key not in seen and len(contexts) < 120:
                seen.add(fb_key)
                contexts.append(build_context(sop_class, [fallback_ts]))

    # 2. Add remaining standard Storage presentation contexts if space permits
    for cx in StoragePresentationContexts:
        if len(contexts) >= 128:
            break
        sop_uid = str(cx.abstract_syntax)
        for ts_uid in (ExplicitVRLittleEndian, ImplicitVRLittleEndian):
            key = (sop_uid, str(ts_uid))
            if key not in seen and len(contexts) < 128:
                seen.add(key)
                contexts.append(build_context(sop_uid, [ts_uid]))

    return contexts or list(StoragePresentationContexts)


class DicomStoreWorkerActor(pykka.ThreadingActor):
    """Worker actor executing a single outbound DICOM Store SCU transfer job."""

    def __init__(self, worker_id: int) -> None:
        super().__init__()
        self.worker_id = worker_id
        self.settings = get_settings()

    def on_receive(self, message: Dict[str, Any]) -> Any:
        msg_type = message.get("type", "").upper()
        if msg_type == "STORE":
            return self._handle_store(message)
        elif msg_type == "PING":
            return {"status": "PONG", "worker_id": self.worker_id}
        else:
            return {"error": f"Unknown message type: {msg_type}"}

    def _resolve_dataset(self, inst: Union[str, Path, Dataset, Dict[str, Any]]) -> Tuple[Optional[Dataset], str, int]:
        """Resolve instance input to a Dataset, objectRef URI, and byte size."""
        if isinstance(inst, Dataset):
            sop_uid = str(getattr(inst, "SOPInstanceUID", "unknown"))
            storage_uri = f"memory://{sop_uid}.dcm"
            try:
                raw_bytes = len(inst.as_bytes())
            except Exception:
                raw_bytes = 0
            return inst, storage_uri, raw_bytes

        if isinstance(inst, (str, Path)):
            inst_str = str(inst)
            if inst_str.startswith("file://") or "/" in inst_str or "\\" in inst_str:
                clean_path = inst_str.replace("file://", "")
                p = Path(clean_path)
                if p.exists():
                    try:
                        ds = pydicom.dcmread(p)
                        file_size = p.stat().st_size
                        return ds, f"file://{p.resolve()}", file_size
                    except Exception as e:
                        logger.error("read_dicom_file_failed", path=str(p), error=str(e))
                        return None, inst_str, 0

            # SOP Instance UID fallback - resolve from storage root
            storage_root = self.settings.STORAGE_ROOT_URI.rstrip("/")
            candidate_path = Path(f"{storage_root.replace('file://', '')}/{inst_str}.dcm")
            if candidate_path.exists():
                try:
                    ds = pydicom.dcmread(candidate_path)
                    file_size = candidate_path.stat().st_size
                    return ds, f"file://{candidate_path.resolve()}", file_size
                except Exception as e:
                    logger.error("read_dicom_sop_failed", sop_uid=inst_str, error=str(e))
                    return None, inst_str, 0

        if isinstance(inst, dict):
            storage_uri = inst.get("storage_uri")
            if storage_uri:
                clean_path = storage_uri.replace("file://", "")
                p = Path(clean_path)
                if p.exists():
                    try:
                        ds = pydicom.dcmread(p)
                        file_size = p.stat().st_size
                        return ds, storage_uri, file_size
                    except Exception:
                        pass

        return None, str(inst), 0

    def _handle_store(self, message: Dict[str, Any]) -> Dict[str, Any]:
        destination_aet = message.get("destination_aet")
        host = message.get("host")
        port = message.get("port")
        calling_aet = message.get("calling_aet") or getattr(self.settings, "DICOM_AE_TITLE", "GOSMART_GS")
        raw_instances = message.get("instances") or []
        test_id = message.get("test_id")
        acted_by = message.get("acted_by") or f"actor:worker_{self.worker_id}"

        # Resolve destination host/port from Neo4j if not explicitly provided
        if not host or not port:
            dest_cfg = graph_service.find_destination_ae_sync(destination_aet)
            if dest_cfg:
                host = host or dest_cfg.get("host")
                port = port or dest_cfg.get("port")

        if not host or not port:
            return {
                "success": False,
                "error": f"Could not resolve network address (host/port) for destination AE: {destination_aet}",
                "destination_aet": destination_aet,
                "completed": 0,
                "failed": len(raw_instances),
                "total": len(raw_instances),
            }

        # Resolve all datasets and metadata
        resolved_items: List[Tuple[Optional[Dataset], str, int]] = []
        valid_datasets: List[Dataset] = []
        for inst in raw_instances:
            ds, obj_ref, byte_size = self._resolve_dataset(inst)
            resolved_items.append((ds, obj_ref, byte_size))
            if ds is not None:
                valid_datasets.append(ds)

        total_count = len(resolved_items)
        if total_count == 0:
            return {
                "success": True,
                "total": 0,
                "completed": 0,
                "failed": 0,
                "warnings": 0,
                "destination_aet": destination_aet,
                "instances": [],
            }

        # Build optimized presentation contexts for valid datasets
        contexts = build_store_contexts(valid_datasets)

        # Initialize Application Entity
        ae = AE(ae_title=calling_aet)
        ae.maximum_pdu_size = self.settings.DICOM_MAX_PDU
        for cx in contexts:
            ae.add_requested_context(cx.abstract_syntax, cx.transfer_syntax)

        timeout = float(message.get("timeout") or getattr(self.settings, "DICOM_SCU_TIMEOUT", 60))
        ae.network_timeout = timeout
        ae.acse_timeout = timeout
        ae.dimse_timeout = timeout

        # Open single association for the entire transfer job
        logger.info(
            "store_scu_associating",
            destination_aet=destination_aet,
            host=host,
            port=port,
            calling_aet=calling_aet,
            instances_count=total_count,
            contexts_count=len(contexts),
        )
        assoc = ae.associate(host, int(port), ae_title=destination_aet, contexts=contexts)

        if not assoc.is_established:
            err_msg = f"Failed to establish DICOM association with {destination_aet}@{host}:{port}"
            logger.warning("store_scu_association_failed", destination_aet=destination_aet, host=host, port=port)

            # Record failure TransferStatus for each instance
            for ds, obj_ref, byte_size in resolved_items:
                sop_uid = str(getattr(ds, "SOPInstanceUID", "")) if ds else None
                status_node = TransferStatusNode(
                    sender=calling_aet,
                    recepient=destination_aet,
                    objectRef=obj_ref,
                    transferTimeMs=0,
                    transferBytes=byte_size,
                    statusCode="0xA801",
                    statusMessage=err_msg,
                    sop_instance_uid=sop_uid,
                    direction="OUTBOUND",
                    service_type="C-STORE",
                    test_id=test_id,
                    acted_by=acted_by,
                )
                try:
                    graph_service.save_transfer_status_sync(status_node)
                except Exception as ex:
                    logger.warning("save_transfer_status_failed", error=str(ex))

            return {
                "success": False,
                "error": err_msg,
                "status_code": "0xA801",
                "destination_aet": destination_aet,
                "completed": 0,
                "failed": total_count,
                "total": total_count,
            }

        completed = 0
        failed = 0
        warnings = 0
        instance_results: List[Dict[str, Any]] = []

        try:
            for ds, obj_ref, byte_size in resolved_items:
                if ds is None:
                    failed += 1
                    status_node = TransferStatusNode(
                        sender=calling_aet,
                        recepient=destination_aet,
                        objectRef=obj_ref,
                        transferTimeMs=0,
                        transferBytes=0,
                        statusCode="0xC000",
                        statusMessage="Failed to load or parse DICOM dataset",
                        sop_instance_uid=None,
                        direction="OUTBOUND",
                        service_type="C-STORE",
                        test_id=test_id,
                        acted_by=acted_by,
                    )
                    graph_service.save_transfer_status_sync(status_node)
                    instance_results.append(
                        {
                            "objectRef": obj_ref,
                            "success": False,
                            "status_code": "0xC000",
                            "error": "Dataset unreadable",
                        }
                    )
                    continue

                sop_uid = str(getattr(ds, "SOPInstanceUID", ""))
                sop_class_uid = str(getattr(ds, "SOPClassUID", ""))
                ts_uid = str(getattr(getattr(ds, "file_meta", None), "TransferSyntaxUID", ExplicitVRLittleEndian))

                start_time = time.perf_counter()
                try:
                    status_ds = assoc.send_c_store(ds)
                    transfer_time_ms = max(1, int((time.perf_counter() - start_time) * 1000))
                    status_int = status_ds.Status if status_ds else 0xC000
                    status_hex = f"0x{status_int:04X}"
                except Exception as e:
                    transfer_time_ms = max(1, int((time.perf_counter() - start_time) * 1000))
                    status_hex = "0xC000"
                    status_ds = None
                    logger.error("c_store_send_exception", sop_uid=sop_uid, error=str(e))

                is_success = status_ds is not None and status_ds.Status == 0x0000
                is_warning = status_ds is not None and (status_ds.Status & 0xF000 == 0xB000)

                if is_success:
                    completed += 1
                    msg_text = "Transferred successfully"
                elif is_warning:
                    warnings += 1
                    msg_text = f"Transfer warning (status {status_hex})"
                else:
                    failed += 1
                    msg_text = f"Transfer failed with status {status_hex}"

                # Persist standalone TransferStatusNode to Neo4j
                status_node = TransferStatusNode(
                    sender=calling_aet,
                    recepient=destination_aet,
                    objectRef=obj_ref,
                    transferTimeMs=transfer_time_ms,
                    transferBytes=byte_size,
                    statusCode=status_hex,
                    statusMessage=msg_text,
                    sop_instance_uid=sop_uid,
                    direction="OUTBOUND",
                    service_type="C-STORE",
                    test_id=test_id,
                    acted_by=acted_by,
                )
                try:
                    graph_service.save_transfer_status_sync(status_node)
                except Exception as ex:
                    logger.warning("save_transfer_status_failed", error=str(ex))

                instance_results.append(
                    {
                        "sop_instance_uid": sop_uid,
                        "sop_class_uid": sop_class_uid,
                        "transfer_syntax_uid": ts_uid,
                        "status_code": status_hex,
                        "success": is_success or is_warning,
                        "transfer_time_ms": transfer_time_ms,
                        "transfer_bytes": byte_size,
                    }
                )

        finally:
            assoc.release()

        return {
            "success": failed == 0,
            "total": total_count,
            "completed": completed,
            "failed": failed,
            "warnings": warnings,
            "destination_aet": destination_aet,
            "destination_host": host,
            "destination_port": port,
            "instances": instance_results,
        }


class DicomStoreScuActor(pykka.ThreadingActor):
    """Coordinator and pool router for outbound DICOM Store SCU operations."""

    def __init__(self, pool_size: Optional[int] = None) -> None:
        super().__init__()
        self.settings = get_settings()
        self.pool_size = pool_size or getattr(self.settings, "DICOM_SCU_MAX_WORKERS", 4)
        self.workers: List[pykka.ActorRef] = []
        self._worker_index = 0
        self._total_transfers = 0
        self._completed_transfers = 0
        self._failed_transfers = 0

    def on_start(self) -> None:
        """Spawn worker pool on coordinator startup."""
        for i in range(self.pool_size):
            worker = DicomStoreWorkerActor.start(worker_id=i + 1)
            self.workers.append(worker)
        logger.info("dicom_store_scu_actor_started", pool_size=self.pool_size)

    def on_stop(self) -> None:
        """Stop all workers when coordinator stops."""
        for w in self.workers:
            try:
                w.stop()
            except Exception:
                pass
        self.workers.clear()
        logger.info("dicom_store_scu_actor_stopped")

    def on_receive(self, message: Dict[str, Any]) -> Any:
        msg_type = message.get("type", "").upper()

        if msg_type == "PING":
            return {
                "status": "PONG",
                "pool_size": len(self.workers),
                "workers": [w.is_alive() for w in self.workers],
            }

        if msg_type == "STATUS":
            return {
                "pool_size": len(self.workers),
                "total_transfers": self._total_transfers,
                "completed_transfers": self._completed_transfers,
                "failed_transfers": self._failed_transfers,
            }

        if msg_type == "STORE":
            if not self.workers:
                return {"success": False, "error": "No available Store SCU workers"}

            # Round-robin dispatch to worker
            worker = self.workers[self._worker_index % len(self.workers)]
            self._worker_index += 1

            self._total_transfers += 1
            timeout = float(message.get("timeout") or getattr(self.settings, "DICOM_SCU_TIMEOUT", 60))

            res = worker.ask(message, timeout=timeout)
            if res.get("success"):
                self._completed_transfers += res.get("completed", 0)
            else:
                self._failed_transfers += res.get("failed", 0)
            return res

        return {"error": f"Unknown message type: {msg_type}"}


_store_scu_actor_ref: Optional[pykka.ActorRef] = None
_store_actor_lock = threading.Lock()


def get_store_scu_actor() -> pykka.ActorRef:
    """Get or start the singleton DicomStoreScuActor instance."""
    global _store_scu_actor_ref
    with _store_actor_lock:
        if _store_scu_actor_ref is None or not _store_scu_actor_ref.is_alive():
            _store_scu_actor_ref = DicomStoreScuActor.start()
        return _store_scu_actor_ref


def stop_store_scu_actor() -> None:
    """Stop the singleton DicomStoreScuActor and its worker pool."""
    global _store_scu_actor_ref
    with _store_actor_lock:
        if _store_scu_actor_ref is not None:
            try:
                _store_scu_actor_ref.stop()
            except Exception:
                pass
            _store_scu_actor_ref = None
