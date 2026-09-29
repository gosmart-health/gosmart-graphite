from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from pydantic import BaseModel, Field

from model.graph import (
    HasInstanceRelation,
    HasSeriesRelation,
    HasStudyRelation,
    InstanceNode,
    PatientNode,
    SeriesNode,
    StudyNode,
)


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
    source: Optional[str] = Field(None, description="Ingestion stream source: dimse:<ip>, stow:<ip>, file:<path>")
    source_identity: Optional[str] = Field(
        None, description="Identity of stream sender: ae:<AE_title> or user:<user_id>"
    )
    destination: Optional[str] = Field(
        None, description="Local destination endpoint: dimse:<ip>:<port>, stow:<host>:<port>, file:<uri>"
    )
    destination_identity: Optional[str] = Field(
        None, description="Identity of receiving entity: ae:<called_aet> or service:<name>"
    )


class DicomHierarchy(BaseModel):
    """Composite DICOM hierarchy representing Patient -> Study -> Series -> Instance."""

    patient: PatientModel
    study: StudyModel
    series: SeriesModel
    instance: InstanceModel
    extra_metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Additional DICOM tags")
    test_id: Optional[str] = Field(
        default=None,
        description="Optional test identifier to mark all derived graph entities as ephemeral test data",
    )
    acted_by: str = Field(
        default="system",
        description="Identifier of the user or agent responsible for the ingestion action",
    )
    expires_at: Optional[datetime] = Field(
        default=None,
        description="Optional expiration timestamp for the hierarchy entities",
    )

    def to_nodes(self) -> Tuple[PatientNode, StudyNode, SeriesNode, InstanceNode]:
        """Convert the hierarchy to concrete AuditableNode instances with deterministic IDs."""
        p_node = PatientNode(
            patient_id=self.patient.patient_id,
            patient_name=self.patient.patient_name,
            patient_birth_date=self.patient.patient_birth_date,
            patient_sex=self.patient.patient_sex,
            test_id=self.test_id,
            ActedBy=self.acted_by,
            ExpiresAt=self.expires_at,
        )
        s_node = StudyNode(
            study_instance_uid=self.study.study_instance_uid,
            study_date=self.study.study_date,
            study_time=self.study.study_time,
            study_description=self.study.study_description,
            accession_number=self.study.accession_number,
            test_id=self.test_id,
            ActedBy=self.acted_by,
            ExpiresAt=self.expires_at,
        )
        se_node = SeriesNode(
            series_instance_uid=self.series.series_instance_uid,
            series_number=self.series.series_number,
            modality=self.series.modality,
            series_description=self.series.series_description,
            body_part_examined=self.series.body_part_examined,
            test_id=self.test_id,
            ActedBy=self.acted_by,
            ExpiresAt=self.expires_at,
        )
        i_node = InstanceNode(
            sop_instance_uid=self.instance.sop_instance_uid,
            sop_class_uid=self.instance.sop_class_uid,
            instance_number=self.instance.instance_number,
            transfer_syntax_uid=self.instance.transfer_syntax_uid,
            storage_uri=self.instance.storage_uri,
            file_size_bytes=self.instance.file_size_bytes,
            Source=self.instance.source,
            SourceIdentity=self.instance.source_identity,
            Destination=self.instance.destination,
            DestinationIdentity=self.instance.destination_identity,
            test_id=self.test_id,
            ActedBy=self.acted_by,
            ExpiresAt=self.expires_at,
        )
        return p_node, s_node, se_node, i_node

    def to_relations(
        self,
        patient_node: Optional[PatientNode] = None,
        study_node: Optional[StudyNode] = None,
        series_node: Optional[SeriesNode] = None,
        instance_node: Optional[InstanceNode] = None,
    ) -> Tuple[HasStudyRelation, HasSeriesRelation, HasInstanceRelation]:
        """Convert the hierarchy to concrete AuditableRelation instances with deterministic IDs."""
        if not (patient_node and study_node and series_node and instance_node):
            patient_node, study_node, series_node, instance_node = self.to_nodes()

        r_study = HasStudyRelation(
            start_node_id=patient_node.id,
            end_node_id=study_node.id,
            test_id=self.test_id,
            ActedBy=self.acted_by,
            ExpiresAt=self.expires_at,
        )
        r_series = HasSeriesRelation(
            start_node_id=study_node.id,
            end_node_id=series_node.id,
            test_id=self.test_id,
            ActedBy=self.acted_by,
            ExpiresAt=self.expires_at,
        )
        r_instance = HasInstanceRelation(
            start_node_id=series_node.id,
            end_node_id=instance_node.id,
            test_id=self.test_id,
            ActedBy=self.acted_by,
            ExpiresAt=self.expires_at,
        )
        return r_study, r_series, r_instance


class DicomIngestRequest(BaseModel):
    file_path: Optional[str] = Field(None, description="Local or URI path to a DICOM file")
    async_processing: bool = Field(default=True, description="Whether to dispatch to Pykka background actor")
    test_id: Optional[str] = Field(None, description="Optional test session identifier for test isolation")
    acted_by: Optional[str] = Field("system", description="Identifier of the actor or user")


class DicomIngestResponse(BaseModel):
    success: bool
    message: str
    patient_id: Optional[str] = None
    study_instance_uid: Optional[str] = None
    series_instance_uid: Optional[str] = None
    sop_instance_uid: Optional[str] = None
    storage_uri: Optional[str] = None
    dispatched_to_actor: bool = False
    test_id: Optional[str] = None
