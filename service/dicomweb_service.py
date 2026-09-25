from pathlib import Path
from typing import Any, Dict, List, Optional

import pydicom
from pydicom.dataset import Dataset
from pydicom.values import MultiValue, PersonName, Sequence

from core.config import get_settings
from core.logging import get_logger

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


class DicomWebService:
    """Service implementing DICOMweb PS 3.18 operations."""

    def __init__(self):
        self.settings = get_settings()

    def get_storage_path(self) -> Path:
        raw_uri = self.settings.STORAGE_ROOT_URI
        if raw_uri.startswith("file://"):
            return Path(raw_uri.replace("file://", ""))
        return Path(raw_uri)

    def search_studies(self, patient_id: Optional[str] = None, study_uid: Optional[str] = None) -> List[Dict[str, Any]]:
        """QIDO-RS: Search for studies in storage."""
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

    def get_instance_file(self, study_uid: str, series_uid: str, sop_uid: str) -> Optional[Path]:
        """WADO-RS: Retrieve instance Part-10 file."""
        storage_path = self.get_storage_path()
        dcm_file = storage_path / f"{sop_uid}.dcm"
        if dcm_file.exists():
            return dcm_file

        for candidate in storage_path.glob("*.dcm"):
            try:
                ds = pydicom.dcmread(str(candidate), stop_before_pixels=True)
                if (
                    getattr(ds, "SOPInstanceUID", None) == sop_uid
                    and getattr(ds, "StudyInstanceUID", None) == study_uid
                    and getattr(ds, "SeriesInstanceUID", None) == series_uid
                ):
                    return candidate
            except Exception:
                continue
        return None


dicomweb_service = DicomWebService()
