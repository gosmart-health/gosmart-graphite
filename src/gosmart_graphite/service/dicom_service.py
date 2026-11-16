import io
import logging
import uuid
from pathlib import Path
from typing import Optional, Union

import pydicom
from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, SecondaryCaptureImageStorage, generate_uid

from gosmart_graphite.core.config import Settings, get_settings
from gosmart_graphite.model.dicom import DicomHierarchy, InstanceModel, PatientModel, SeriesModel, StudyModel

logger = logging.getLogger(__name__)


class DicomService:
    """Handles DICOM dataset parsing, extraction, and local storage persistence."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()

    def get_storage_path(self) -> Path:
        """Resolve the local filesystem path from STORAGE_ROOT_URI."""
        uri = self.settings.STORAGE_ROOT_URI
        if uri.startswith("file://"):
            local_path = Path(uri.replace("file://", ""))
        else:
            local_path = Path(uri)
        local_path.mkdir(parents=True, exist_ok=True)
        return local_path

    def parse_dataset(
        self,
        ds: Dataset,
        storage_uri: Optional[str] = None,
        file_size: Optional[int] = None,
        source: Optional[str] = None,
        source_identity: Optional[str] = None,
        destination: Optional[str] = None,
        destination_identity: Optional[str] = None,
    ) -> DicomHierarchy:
        """Extract structured Pydantic hierarchy from a pydicom Dataset."""
        # Patient Level
        patient_id = str(getattr(ds, "PatientID", "UNKNOWN_PATIENT"))
        patient_name = str(getattr(ds, "PatientName", "")) if hasattr(ds, "PatientName") else None
        patient_birth_date = str(getattr(ds, "PatientBirthDate", "")) if hasattr(ds, "PatientBirthDate") else None
        patient_sex = str(getattr(ds, "PatientSex", "")) if hasattr(ds, "PatientSex") else None

        patient = PatientModel(
            patient_id=patient_id,
            patient_name=patient_name,
            patient_birth_date=patient_birth_date,
            patient_sex=patient_sex,
        )

        # Study Level
        study_instance_uid = str(getattr(ds, "StudyInstanceUID", generate_uid()))
        study_date = str(getattr(ds, "StudyDate", "")) if hasattr(ds, "StudyDate") else None
        study_time = str(getattr(ds, "StudyTime", "")) if hasattr(ds, "StudyTime") else None
        study_description = str(getattr(ds, "StudyDescription", "")) if hasattr(ds, "StudyDescription") else None
        accession_number = str(getattr(ds, "AccessionNumber", "")) if hasattr(ds, "AccessionNumber") else None

        study = StudyModel(
            study_instance_uid=study_instance_uid,
            study_date=study_date,
            study_time=study_time,
            study_description=study_description,
            accession_number=accession_number,
        )

        # Series Level
        series_instance_uid = str(getattr(ds, "SeriesInstanceUID", generate_uid()))
        modality = str(getattr(ds, "Modality", "OT"))
        series_number = None
        if hasattr(ds, "SeriesNumber") and ds.SeriesNumber is not None:
            try:
                series_number = int(ds.SeriesNumber)
            except ValueError, TypeError:
                pass
        series_description = str(getattr(ds, "SeriesDescription", "")) if hasattr(ds, "SeriesDescription") else None
        body_part_examined = str(getattr(ds, "BodyPartExamined", "")) if hasattr(ds, "BodyPartExamined") else None

        series = SeriesModel(
            series_instance_uid=series_instance_uid,
            series_number=series_number,
            modality=modality,
            series_description=series_description,
            body_part_examined=body_part_examined,
        )

        # Instance Level
        sop_instance_uid = str(getattr(ds, "SOPInstanceUID", generate_uid()))
        sop_class_uid = str(getattr(ds, "SOPClassUID", "")) if hasattr(ds, "SOPClassUID") else None
        instance_number = None
        if hasattr(ds, "InstanceNumber") and ds.InstanceNumber is not None:
            try:
                instance_number = int(ds.InstanceNumber)
            except ValueError, TypeError:
                pass
        transfer_syntax = getattr(ds.file_meta, "TransferSyntaxUID", None) if hasattr(ds, "file_meta") else None

        instance = InstanceModel(
            sop_instance_uid=sop_instance_uid,
            sop_class_uid=sop_class_uid,
            instance_number=instance_number,
            transfer_syntax_uid=str(transfer_syntax) if transfer_syntax else None,
            storage_uri=storage_uri,
            file_size_bytes=file_size,
            source=source,
            source_identity=source_identity,
            destination=destination,
            destination_identity=destination_identity,
        )

        return DicomHierarchy(
            patient=patient,
            study=study,
            series=series,
            instance=instance,
        )

    def parse_file(
        self,
        file_path_or_buffer: Union[str, Path, io.BytesIO],
        source: Optional[str] = None,
        source_identity: Optional[str] = None,
        destination: Optional[str] = None,
        destination_identity: Optional[str] = None,
    ) -> DicomHierarchy:
        """Read and parse a DICOM dataset from file or binary buffer."""
        file_size = None
        if isinstance(file_path_or_buffer, (str, Path)):
            p = Path(file_path_or_buffer)
            if p.exists():
                file_size = p.stat().st_size
            ds = pydicom.dcmread(str(p), force=True)
            resolved_path = str(p.resolve())
            storage_uri = f"file://{resolved_path}"
            if not source:
                source = f"file:{resolved_path}"
            if not source_identity:
                source_identity = "user:local"
            if not destination:
                destination = f"file:{resolved_path}"
            if not destination_identity:
                destination_identity = "service:local_storage"
        else:
            content = file_path_or_buffer.getvalue()
            file_size = len(content)
            ds = pydicom.dcmread(file_path_or_buffer, force=True)
            storage_uri = None

        return self.parse_dataset(
            ds,
            storage_uri=storage_uri,
            file_size=file_size,
            source=source,
            source_identity=source_identity,
            destination=destination,
            destination_identity=destination_identity,
        )

    def save_raw_dicom(self, content_or_dataset: Union[bytes, Dataset], sop_instance_uid: str) -> str:
        """Persist a raw DICOM object into the configured storage URI."""
        from gosmart_graphite.service.media_service import media_service

        root_uri = self.settings.STORAGE_ROOT_URI.rstrip("/")
        target_uri = f"{root_uri}/{sop_instance_uid}.dcm"

        if isinstance(content_or_dataset, Dataset):
            return media_service.write_dicom_sync(target_uri, content_or_dataset)
        else:
            return media_service.write_sync(target_uri, content_or_dataset)

    def create_mock_dicom(
        self,
        patient_id: str = "P1001",
        patient_name: str = "DOE^JOHN",
        modality: str = "CT",
        study_description: str = "Brain CT with Contrast",
    ) -> FileDataset:
        """Generate a valid in-memory DICOM FileDataset for testing and exploration."""
        file_meta = FileMetaDataset()
        file_meta.MediaStorageSOPClassUID = SecondaryCaptureImageStorage
        file_meta.MediaStorageSOPInstanceUID = generate_uid()
        file_meta.TransferSyntaxUID = ExplicitVRLittleEndian

        ds = FileDataset("mock.dcm", {}, file_meta=file_meta, preamble=b"\0" * 128)

        ds.PatientID = patient_id
        ds.PatientName = patient_name
        ds.PatientBirthDate = "19800101"
        ds.PatientSex = "M"

        ds.StudyInstanceUID = generate_uid()
        ds.StudyDate = "20260925"
        ds.StudyTime = "094500"
        ds.StudyDescription = study_description
        ds.AccessionNumber = f"ACC-{uuid.uuid4().hex[:8].upper()}"

        ds.SeriesInstanceUID = generate_uid()
        ds.SeriesNumber = 1
        ds.Modality = modality
        ds.SeriesDescription = f"{modality} Axial 5mm"
        ds.BodyPartExamined = "HEAD"

        ds.SOPInstanceUID = file_meta.MediaStorageSOPInstanceUID
        ds.SOPClassUID = file_meta.MediaStorageSOPClassUID
        ds.InstanceNumber = 1

        return ds


dicom_service = DicomService()
