from pathlib import Path
from typing import Any, Dict, List, Optional

import pydicom
from pydicom.dataset import Dataset
from pydicom.values import MultiValue, PersonName, Sequence

from actor.audit_actor import record_audit
from core.config import get_settings
from core.logging import get_logger
from model.audit import AuditAction
from service.graph_service import graph_service
from service.media_service import media_service

logger = get_logger("gosmart_gs.dicomweb")


def dataset_to_dicom_json(ds: Dataset) -> Dict[str, Any]:
    """Convert pydicom Dataset to PS 3.18 DICOM JSON model."""
    result: Dict[str, Any] = {}
    for elem in ds:
        if elem.tag.is_private:
            continue
        tag_key = f"{elem.tag.group:04X}{elem.tag.element:04X}"
        vr = elem.VR
        val = elem.value

        entry: Dict[str, Any] = {"vr": vr}
        if val is None or val == "":
            pass
        elif isinstance(val, (str, int, float)):
            entry["Value"] = [val]
        elif isinstance(val, (list, tuple)):
            entry["Value"] = list(val)
        elif isinstance(val, MultiValue):
            entry["Value"] = [str(v) for v in val]
        elif isinstance(val, PersonName):
            entry["Value"] = [{"Alphabetic": str(val)}]
        elif isinstance(val, Sequence):
            entry["Value"] = [dataset_to_dicom_json(sub_ds) for sub_ds in val]
        else:
            entry["Value"] = [str(val)]
        result[tag_key] = entry
    return result


def study_record_to_dicom_json(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Convert Neo4j Study graph record to PS 3.18 DICOM JSON model."""
    result: Dict[str, Any] = {}

    def _set_str(tag: str, vr: str, val: Any) -> None:
        if val is not None and str(val).strip() != "":
            result[tag] = {"vr": vr, "Value": [str(val)]}

    def _set_int(tag: str, vr: str, val: Any) -> None:
        if val is not None:
            try:
                result[tag] = {"vr": vr, "Value": [int(val)]}
            except ValueError, TypeError:
                pass

    if rec.get("patient_name"):
        result["00100010"] = {"vr": "PN", "Value": [{"Alphabetic": str(rec["patient_name"])}]}
    _set_str("00100020", "LO", rec.get("patient_id"))
    _set_str("00100030", "DA", rec.get("patient_birth_date"))
    _set_str("00100040", "CS", rec.get("patient_sex"))

    _set_str("0020000D", "UI", rec.get("study_instance_uid"))
    _set_str("00080020", "DA", rec.get("study_date"))
    _set_str("00080030", "TM", rec.get("study_time"))
    _set_str("00081030", "LO", rec.get("study_description"))
    _set_str("00080050", "SH", rec.get("accession_number"))

    modalities = [m for m in rec.get("modalities", []) if m]
    if modalities:
        result["00080061"] = {"vr": "CS", "Value": modalities}

    # Counts computed from live graph traversal
    _set_int("00201206", "IS", rec.get("num_series", 0))
    _set_int("00201208", "IS", rec.get("num_instances", 0))

    return result


def series_record_to_dicom_json(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Convert Neo4j Series graph record to PS 3.18 DICOM JSON model."""
    result: Dict[str, Any] = {}

    def _set_str(tag: str, vr: str, val: Any) -> None:
        if val is not None and str(val).strip() != "":
            result[tag] = {"vr": vr, "Value": [str(val)]}

    def _set_int(tag: str, vr: str, val: Any) -> None:
        if val is not None:
            try:
                result[tag] = {"vr": vr, "Value": [int(val)]}
            except ValueError, TypeError:
                pass

    _set_str("0020000D", "UI", rec.get("study_instance_uid"))
    _set_str("0020000E", "UI", rec.get("series_instance_uid"))
    _set_str("00080060", "CS", rec.get("modality"))
    _set_str("0008103E", "LO", rec.get("series_description"))
    _set_str("00180015", "CS", rec.get("body_part_examined"))
    _set_int("00200011", "IS", rec.get("series_number"))
    _set_int("00201209", "IS", rec.get("num_instances", 0))

    return result


def instance_record_to_dicom_json(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Convert Neo4j Instance graph record to PS 3.18 DICOM JSON model."""
    result: Dict[str, Any] = {}

    def _set_str(tag: str, vr: str, val: Any) -> None:
        if val is not None and str(val).strip() != "":
            result[tag] = {"vr": vr, "Value": [str(val)]}

    def _set_int(tag: str, vr: str, val: Any) -> None:
        if val is not None:
            try:
                result[tag] = {"vr": vr, "Value": [int(val)]}
            except ValueError, TypeError:
                pass

    _set_str("0020000D", "UI", rec.get("study_instance_uid"))
    _set_str("0020000E", "UI", rec.get("series_instance_uid"))
    _set_str("00080018", "UI", rec.get("sop_instance_uid"))
    _set_str("00080016", "UI", rec.get("sop_class_uid"))
    _set_int("00200013", "IS", rec.get("instance_number"))

    return result


class DicomWebService:
    """Service implementing DICOMweb PS 3.18 operations powered by Neo4j Knowledge Graph."""

    def __init__(self):
        self.settings = get_settings()

    def get_storage_path(self) -> Path:
        raw_uri = self.settings.STORAGE_ROOT_URI
        if raw_uri.startswith("file://"):
            return Path(raw_uri.replace("file://", ""))
        return Path(raw_uri)

    def search_studies(
        self,
        patient_id: Optional[str] = None,
        patient_name: Optional[str] = None,
        study_uid: Optional[str] = None,
        study_date: Optional[str] = None,
        accession_number: Optional[str] = None,
        include_tests: bool = True,
        test_id: Optional[str] = None,
        accessible_group_ids: Optional[List[str]] = None,
        limit: int = 100,
        actor_id: str = "service:dicomweb",
        actor: str = "dicomweb",
    ) -> List[Dict[str, Any]]:
        """QIDO-RS: Search for studies directly from Neo4j Knowledge Graph properties."""
        results: List[Dict[str, Any]] = []
        try:
            records = graph_service.search_studies_qido_sync(
                patient_id=patient_id,
                patient_name=patient_name,
                study_uid=study_uid,
                study_date=study_date,
                accession_number=accession_number,
                include_tests=include_tests,
                test_id=test_id,
                accessible_group_ids=accessible_group_ids,
                limit=limit,
            )
            if records:
                results = [study_record_to_dicom_json(r) for r in records]
            else:
                results = self._search_studies_filesystem(patient_id=patient_id, study_uid=study_uid)
        except Exception as e:
            logger.warning("qido_graph_search_fallback", error=str(e))
            results = self._search_studies_filesystem(patient_id=patient_id, study_uid=study_uid)

        # Audit event recording
        target_id = f"study:{study_uid}" if study_uid else (f"patient:{patient_id}" if patient_id else "query:studies")
        record_audit(
            action=AuditAction.DICOMWEB_STUDIES_QUERIED.value,
            status_code=200,
            status_message="OK",
            severity="INFO",
            actor_id=actor_id,
            actor=actor,
            target_id=target_id,
            target=study_uid or patient_id or "All Studies",
            result=f"Found {len(results)} studies matching QIDO query",
            details={
                "patient_id": patient_id,
                "patient_name": patient_name,
                "study_uid": study_uid,
                "study_date": study_date,
                "accession_number": accession_number,
                "count": len(results),
                "limit": limit,
            },
            test_id=test_id,
        )

        return results

    def search_series(
        self,
        study_uid: Optional[str] = None,
        series_uid: Optional[str] = None,
        modality: Optional[str] = None,
        include_tests: bool = True,
        test_id: Optional[str] = None,
        accessible_group_ids: Optional[List[str]] = None,
        limit: int = 100,
        actor_id: str = "service:dicomweb",
        actor: str = "dicomweb",
    ) -> List[Dict[str, Any]]:
        """QIDO-RS: Search for series directly from Neo4j Knowledge Graph properties."""
        results: List[Dict[str, Any]] = []
        try:
            records = graph_service.search_series_qido_sync(
                study_uid=study_uid,
                series_uid=series_uid,
                modality=modality,
                include_tests=include_tests,
                test_id=test_id,
                accessible_group_ids=accessible_group_ids,
                limit=limit,
            )
            results = [series_record_to_dicom_json(r) for r in records]
        except Exception as e:
            logger.warning("qido_series_search_error", error=str(e))
            results = []

        # Audit event recording
        record_audit(
            action=AuditAction.DICOMWEB_SERIES_QUERIED.value,
            status_code=200,
            status_message="OK",
            severity="INFO",
            actor_id=actor_id,
            actor=actor,
            target_id=f"series:{series_uid}" if series_uid else (f"study:{study_uid}" if study_uid else "query:series"),
            target=series_uid or study_uid or "All Series",
            result=f"Found {len(results)} series matching QIDO query",
            details={
                "study_uid": study_uid,
                "series_uid": series_uid,
                "modality": modality,
                "count": len(results),
                "limit": limit,
            },
            test_id=test_id,
        )

        return results

    def search_instances(
        self,
        study_uid: Optional[str] = None,
        series_uid: Optional[str] = None,
        sop_uid: Optional[str] = None,
        include_tests: bool = True,
        test_id: Optional[str] = None,
        accessible_group_ids: Optional[List[str]] = None,
        limit: int = 100,
        actor_id: str = "service:dicomweb",
        actor: str = "dicomweb",
    ) -> List[Dict[str, Any]]:
        """QIDO-RS: Search for instances directly from Neo4j Knowledge Graph properties."""
        results: List[Dict[str, Any]] = []
        try:
            records = graph_service.search_instances_qido_sync(
                study_uid=study_uid,
                series_uid=series_uid,
                sop_uid=sop_uid,
                include_tests=include_tests,
                test_id=test_id,
                accessible_group_ids=accessible_group_ids,
                limit=limit,
            )
            results = [instance_record_to_dicom_json(r) for r in records]
        except Exception as e:
            logger.warning("qido_instances_search_error", error=str(e))
            results = []

        # Audit event recording
        if sop_uid:
            target_id = f"sop_instance:{sop_uid}"
        elif series_uid:
            target_id = f"series:{series_uid}"
        elif study_uid:
            target_id = f"study:{study_uid}"
        else:
            target_id = "query:instances"

        record_audit(
            action=AuditAction.DICOMWEB_INSTANCES_QUERIED.value,
            status_code=200,
            status_message="OK",
            severity="INFO",
            actor_id=actor_id,
            actor=actor,
            target_id=target_id,
            target=sop_uid or series_uid or study_uid or "All Instances",
            result=f"Found {len(results)} instances matching QIDO query",
            details={
                "study_uid": study_uid,
                "series_uid": series_uid,
                "sop_uid": sop_uid,
                "count": len(results),
                "limit": limit,
            },
            test_id=test_id,
        )

        return results

    def _search_studies_filesystem(
        self, patient_id: Optional[str] = None, study_uid: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Fallback: Search studies via local filesystem scanning."""
        storage_path = self.get_storage_path()
        results: List[Dict[str, Any]] = []
        if not storage_path.exists():
            return results

        seen_studies = set()
        for dcm_file in storage_path.glob("*.dcm"):
            try:
                ds = pydicom.dcmread(str(dcm_file), stop_before_pixels=True)
                cur_study_uid = getattr(ds, "StudyInstanceUID", None)
                cur_patient_id = getattr(ds, "PatientID", None)

                if patient_id and cur_patient_id != patient_id:
                    continue
                if study_uid and cur_study_uid != study_uid:
                    continue

                if cur_study_uid and cur_study_uid not in seen_studies:
                    seen_studies.add(cur_study_uid)
                    results.append(dataset_to_dicom_json(ds))
            except Exception as e:
                logger.warning("dicom_read_error", file=str(dcm_file), error=str(e))
        return results

    def get_instance_file(
        self,
        study_uid: Optional[str] = None,
        series_uid: Optional[str] = None,
        sop_uid: str = "",
        accessible_group_ids: Optional[List[str]] = None,
        actor_id: str = "service:dicomweb",
        actor: str = "dicomweb",
        test_id: Optional[str] = None,
    ) -> Optional[Path]:
        """WADO-RS: Retrieve instance Part-10 file by querying Neo4j storage_uri first."""
        target_path: Optional[Path] = None
        storage_uri = graph_service.get_instance_storage_uri_sync(sop_uid, accessible_group_ids=accessible_group_ids)
        if storage_uri:
            parsed = media_service.parse_uri(storage_uri)
            p = Path(parsed.raw_path)
            if p.exists():
                target_path = p

        # Fallback: check configured local storage path directly (only when unconstrained/admin)
        if not target_path and accessible_group_ids is None:
            storage_path = self.get_storage_path()
            dcm_file = storage_path / f"{sop_uid}.dcm"
            if dcm_file.exists():
                target_path = dcm_file
            else:
                for candidate in storage_path.glob("*.dcm"):
                    try:
                        ds = pydicom.dcmread(str(candidate), stop_before_pixels=True)
                        if getattr(ds, "SOPInstanceUID", None) == sop_uid:
                            if study_uid and getattr(ds, "StudyInstanceUID", None) != study_uid:
                                continue
                            if series_uid and getattr(ds, "SeriesInstanceUID", None) != series_uid:
                                continue
                            target_path = candidate
                            break
                    except Exception:
                        continue

        # Audit event recording
        if target_path and target_path.exists():
            record_audit(
                action=AuditAction.DICOMWEB_INSTANCE_RETRIEVED.value,
                status_code=200,
                status_message="OK",
                severity="INFO",
                actor_id=actor_id,
                actor=actor,
                target_id=f"sop_instance:{sop_uid}",
                target=sop_uid,
                result=f"Instance retrieved from {target_path}",
                details={
                    "study_uid": study_uid,
                    "series_uid": series_uid,
                    "sop_uid": sop_uid,
                    "file_path": str(target_path),
                },
                test_id=test_id,
            )
        else:
            record_audit(
                action=AuditAction.DICOMWEB_INSTANCE_RETRIEVED.value,
                status_code=404,
                status_message="Not Found",
                severity="WARNING",
                actor_id=actor_id,
                actor=actor,
                target_id=f"sop_instance:{sop_uid}",
                target=sop_uid,
                result="Instance file not found",
                details={"study_uid": study_uid, "series_uid": series_uid, "sop_uid": sop_uid},
                test_id=test_id,
            )

        return target_path

    def get_series_files(
        self,
        study_uid: str,
        series_uid: str,
        accessible_group_ids: Optional[List[str]] = None,
        actor_id: str = "service:dicomweb",
        actor: str = "dicomweb",
        test_id: Optional[str] = None,
    ) -> List[Path]:
        """WADO-RS: Retrieve all instance Part-10 files for a series."""
        uris = graph_service.get_series_storage_uris_sync(
            study_uid, series_uid, accessible_group_ids=accessible_group_ids
        )
        files: List[Path] = []
        for item in uris:
            uri = item.get("storage_uri")
            if uri:
                parsed = media_service.parse_uri(uri)
                p = Path(parsed.raw_path)
                if p.exists():
                    files.append(p)
        if not files and accessible_group_ids is None:
            # Fallback to filesystem scan
            storage_path = self.get_storage_path()
            for candidate in storage_path.glob("*.dcm"):
                try:
                    ds = pydicom.dcmread(str(candidate), stop_before_pixels=True)
                    if (
                        getattr(ds, "StudyInstanceUID", None) == study_uid
                        and getattr(ds, "SeriesInstanceUID", None) == series_uid
                    ):
                        files.append(candidate)
                except Exception:
                    continue

        # Audit event recording
        record_audit(
            action=AuditAction.DICOMWEB_SERIES_RETRIEVED.value,
            status_code=200 if files else 404,
            status_message="OK" if files else "Not Found",
            severity="INFO" if files else "WARNING",
            actor_id=actor_id,
            actor=actor,
            target_id=f"series:{series_uid}",
            target=series_uid,
            result=f"Retrieved {len(files)} files for series" if files else "Series not found or contains no files",
            details={"study_uid": study_uid, "series_uid": series_uid, "count": len(files)},
            test_id=test_id,
        )

        return files

    def get_study_files(
        self,
        study_uid: str,
        accessible_group_ids: Optional[List[str]] = None,
        actor_id: str = "service:dicomweb",
        actor: str = "dicomweb",
        test_id: Optional[str] = None,
    ) -> List[Path]:
        """WADO-RS: Retrieve all instance Part-10 files for a study."""
        uris = graph_service.get_study_storage_uris_sync(study_uid, accessible_group_ids=accessible_group_ids)
        files: List[Path] = []
        for item in uris:
            uri = item.get("storage_uri")
            if uri:
                parsed = media_service.parse_uri(uri)
                p = Path(parsed.raw_path)
                if p.exists():
                    files.append(p)
        if not files and accessible_group_ids is None:
            # Fallback to filesystem scan
            storage_path = self.get_storage_path()
            for candidate in storage_path.glob("*.dcm"):
                try:
                    ds = pydicom.dcmread(str(candidate), stop_before_pixels=True)
                    if getattr(ds, "StudyInstanceUID", None) == study_uid:
                        files.append(candidate)
                except Exception:
                    continue

        # Audit event recording
        record_audit(
            action=AuditAction.DICOMWEB_STUDY_RETRIEVED.value,
            status_code=200 if files else 404,
            status_message="OK" if files else "Not Found",
            severity="INFO" if files else "WARNING",
            actor_id=actor_id,
            actor=actor,
            target_id=f"study:{study_uid}",
            target=study_uid,
            result=f"Retrieved {len(files)} files for study" if files else "Study not found or contains no files",
            details={"study_uid": study_uid, "count": len(files)},
            test_id=test_id,
        )

        return files


dicomweb_service = DicomWebService()
