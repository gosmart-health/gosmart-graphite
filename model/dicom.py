from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class PatientModel(BaseModel):
    patient_id: str = Field(..., description="DICOM Patient ID (0010,0020)")
    patient_name: Optional[str] = Field(None, description="Patient Name (0010,0010)")
    patient_birth_date: Optional[str] = Field(None, description="Patient Birth Date (0010,0030)")
    patient_sex: Optional[str] = Field(None, description="Patient Sex (0010,0040)")


class StudyModel(BaseModel):
    study_instance_uid: str = Field(..., description="Study Instance UID (0020,000D)")
    study_date: Optional[str] = Field(None, description="Study Date (0008,0020)")
    study_time: Optional[str] = Field(None, description="Study Time (0008,0030)")
    study_description: Optional[str] = Field(None, description="Study Description (0008,1030)")
    accession_number: Optional[str] = Field(None, description="Accession Number (0008,0050)")


class SeriesModel(BaseModel):
    series_instance_uid: str = Field(..., description="Series Instance UID (0020,000E)")
    series_number: Optional[int] = Field(None, description="Series Number (0020,0011)")
    modality: str = Field("OT", description="Modality (0008,0060), e.g. CT, MR, US")
    series_description: Optional[str] = Field(None, description="Series Description (0008,103E)")
    body_part_examined: Optional[str] = Field(None, description="Body Part Examined (0018,0015)")


class InstanceModel(BaseModel):
    sop_instance_uid: str = Field(..., description="SOP Instance UID (0008,0018)")
    sop_class_uid: Optional[str] = Field(None, description="SOP Class UID (0008,0016)")
    instance_number: Optional[int] = Field(None, description="Instance Number (0020,0013)")
    transfer_syntax_uid: Optional[str] = Field(None, description="Transfer Syntax UID (0002,0010)")
    storage_uri: Optional[str] = Field(None, description="URI where the raw DICOM file is persisted")
    file_size_bytes: Optional[int] = Field(None, description="File size in bytes")


class DicomHierarchy(BaseModel):
    """Composite DICOM hierarchy representing Patient -> Study -> Series -> Instance."""
    patient: PatientModel
    study: StudyModel
    series: SeriesModel
    instance: InstanceModel
    extra_metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Additional DICOM tags")


class DicomIngestRequest(BaseModel):
    file_path: Optional[str] = Field(None, description="Local or URI path to a DICOM file")
    async_processing: bool = Field(default=True, description="Whether to dispatch to Pykka background actor")


class DicomIngestResponse(BaseModel):
    success: bool
    message: str
    patient_id: Optional[str] = None
    study_instance_uid: Optional[str] = None
    series_instance_uid: Optional[str] = None
    sop_instance_uid: Optional[str] = None
    storage_uri: Optional[str] = None
    dispatched_to_actor: bool = False
