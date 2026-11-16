import io
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from gosmart_graphite.actor.dicom_actor import get_dicom_actor
from gosmart_graphite.core.auth_dependency import get_current_group_context, get_current_user
from gosmart_graphite.model.dicom import DicomIngestResponse
from gosmart_graphite.service.dicom_service import dicom_service
from gosmart_graphite.service.graph_service import graph_service
from gosmart_graphite.service.pynetdicom_service import pynetdicom_service

router = APIRouter(
    prefix="/dicom",
    tags=["DICOM"],
    dependencies=[Depends(get_current_user), Depends(get_current_group_context)],
)


class EchoRequest(BaseModel):
    host: str = "127.0.0.1"
    port: int = 11112
    calling_aet: Optional[str] = None
    called_aet: Optional[str] = None


class MockDicomRequest(BaseModel):
    patient_id: str = "MOCK-001"
    patient_name: str = "DOE^JANE"
    modality: str = "CT"
    study_description: str = "Chest CT High-Resolution"
    async_actor: bool = False
    test_id: Optional[str] = None


@router.post("/upload", response_model=DicomIngestResponse)
async def upload_dicom_file(
    file: UploadFile = File(...),
    async_actor: bool = Form(default=False),
    test_id: Optional[str] = Form(default=None),
) -> DicomIngestResponse:
    """Upload and process a DICOM (.dcm) file directly."""
    try:
        content = await file.read()
        buffer = io.BytesIO(content)
        hierarchy = dicom_service.parse_file(buffer)
        hierarchy.test_id = test_id
        hierarchy.acted_by = "api"

        # Save to storage root
        storage_uri = dicom_service.save_raw_dicom(content, hierarchy.instance.sop_instance_uid)
        hierarchy.instance.storage_uri = storage_uri

        if async_actor:
            actor = get_dicom_actor()
            actor.tell(
                {
                    "type": "INGEST_FILE",
                    "file_path": storage_uri.replace("file://", ""),
                    "test_id": test_id,
                    "acted_by": "api_actor",
                }
            )
            return DicomIngestResponse(
                success=True,
                message="Dispatched DICOM file to Pykka background actor for ingestion",
                patient_id=hierarchy.patient.patient_id,
                study_instance_uid=hierarchy.study.study_instance_uid,
                series_instance_uid=hierarchy.series.series_instance_uid,
                sop_instance_uid=hierarchy.instance.sop_instance_uid,
                storage_uri=storage_uri,
                dispatched_to_actor=True,
                test_id=test_id,
            )

        # Synchronous processing
        await graph_service.save_hierarchy(hierarchy)
        return DicomIngestResponse(
            success=True,
            message="DICOM file processed and ingested into Neo4j graph",
            patient_id=hierarchy.patient.patient_id,
            study_instance_uid=hierarchy.study.study_instance_uid,
            series_instance_uid=hierarchy.series.series_instance_uid,
            sop_instance_uid=hierarchy.instance.sop_instance_uid,
            storage_uri=storage_uri,
            dispatched_to_actor=False,
            test_id=test_id,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to process DICOM file: {str(e)}")


@router.post("/mock", response_model=DicomIngestResponse)
async def generate_and_ingest_mock(req: MockDicomRequest) -> DicomIngestResponse:
    """Generate a realistic mock DICOM instance and ingest it into the Neo4j graph."""
    if req.async_actor:
        actor = get_dicom_actor()
        res = actor.ask(
            {
                "type": "INGEST_MOCK",
                "patient_id": req.patient_id,
                "modality": req.modality,
                "test_id": req.test_id,
                "acted_by": "api_actor",
            }
        )
        return DicomIngestResponse(
            success=res.get("success", False),
            message="Mock DICOM generated and processed by Pykka actor",
            patient_id=res.get("patient_id"),
            study_instance_uid=res.get("study_instance_uid"),
            sop_instance_uid=res.get("sop_instance_uid"),
            storage_uri=res.get("storage_uri"),
            dispatched_to_actor=True,
            test_id=req.test_id,
        )

    # Synchronous processing
    ds = dicom_service.create_mock_dicom(
        patient_id=req.patient_id,
        patient_name=req.patient_name,
        modality=req.modality,
        study_description=req.study_description,
    )
    hierarchy = dicom_service.parse_dataset(ds)
    hierarchy.test_id = req.test_id
    hierarchy.acted_by = "api"
    storage_uri = dicom_service.save_raw_dicom(ds, hierarchy.instance.sop_instance_uid)
    hierarchy.instance.storage_uri = storage_uri

    await graph_service.save_hierarchy(hierarchy)
    return DicomIngestResponse(
        success=True,
        message="Mock DICOM generated and ingested into Neo4j graph",
        patient_id=hierarchy.patient.patient_id,
        study_instance_uid=hierarchy.study.study_instance_uid,
        series_instance_uid=hierarchy.series.series_instance_uid,
        sop_instance_uid=hierarchy.instance.sop_instance_uid,
        storage_uri=storage_uri,
        dispatched_to_actor=False,
        test_id=req.test_id,
    )


@router.post("/echo")
async def dicom_c_echo(req: EchoRequest):
    """Test DICOM network connectivity via C-ECHO (pynetdicom)."""
    result = pynetdicom_service.echo_scu(
        host=req.host,
        port=req.port,
        calling_aet=req.calling_aet,
        called_aet=req.called_aet,
    )
    return result
