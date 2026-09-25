import io
import json
from typing import Optional

import pydicom
from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse

from actor.dicom_actor import get_dicom_actor
from core.logging import get_logger
from service.dicom_service import dicom_service
from service.dicomweb_service import dicomweb_service

logger = get_logger("gosmart_gs.api.dicomweb")

router = APIRouter(prefix="/dicomweb", tags=["DICOMweb"])


@router.get("/studies", response_class=Response)
async def qido_search_studies(
    PatientID: Optional[str] = Query(None, alias="PatientID"),
    StudyInstanceUID: Optional[str] = Query(None, alias="StudyInstanceUID"),
):
    """QIDO-RS: Search for studies matching query parameters."""
    studies = dicomweb_service.search_studies(patient_id=PatientID, study_uid=StudyInstanceUID)
    return Response(
        content=json.dumps(studies),
        media_type="application/dicom+json",
        status_code=200,
    )


@router.get("/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}")
async def wado_get_instance(study_uid: str, series_uid: str, sop_uid: str):
    """WADO-RS: Retrieve single DICOM SOP Instance."""
    file_path = dicomweb_service.get_instance_file(study_uid, series_uid, sop_uid)
    if not file_path or not file_path.exists():
        raise HTTPException(status_code=404, detail="Instance not found")

    return FileResponse(
        path=str(file_path),
        media_type="application/dicom",
        filename=f"{sop_uid}.dcm",
    )


@router.post("/studies", response_class=Response)
async def stow_store_instances(request: Request):
    """STOW-RS: Store DICOM instances into the archive."""
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Empty request body")

    try:
        ds = pydicom.dcmread(io.BytesIO(body), force=True)
    except Exception as e:
        logger.error("stow_parse_error", error=str(e))
        raise HTTPException(status_code=400, detail=f"Failed to parse DICOM payload: {e}")

    sop_uid = getattr(ds, "SOPInstanceUID", None)
    sop_class_uid = getattr(ds, "SOPClassUID", "1.2.840.10008.5.1.4.1.1.7")
    if not sop_uid:
        raise HTTPException(status_code=400, detail="Missing SOPInstanceUID in DICOM dataset")

    file_path = dicom_service.save_raw_dicom(ds, sop_uid)

    actor = get_dicom_actor()
    if actor.is_alive():
        actor.tell({"type": "INGEST_FILE", "file_path": str(file_path)})

    stow_response = {
        "00081199": {
            "vr": "SQ",
            "Value": [
                {
                    "00081150": {"vr": "UI", "Value": [sop_class_uid]},
                    "00081155": {"vr": "UI", "Value": [sop_uid]},
                }
            ],
        }
    }

    return Response(
        content=json.dumps(stow_response),
        media_type="application/dicom+json",
        status_code=200,
    )


@router.get("/wado")
async def wado_uri(
    requestType: str = Query("WADO"),
    studyUID: Optional[str] = Query(None),
    seriesUID: Optional[str] = Query(None),
    objectUID: Optional[str] = Query(None),
):
    """WADO-URI: Retrieve DICOM instance via legacy URL parameters."""
    if requestType != "WADO" or not objectUID or not studyUID or not seriesUID:
        raise HTTPException(status_code=400, detail="Invalid WADO-URI parameters")

    file_path = dicomweb_service.get_instance_file(studyUID, seriesUID, objectUID)
    if not file_path or not file_path.exists():
        raise HTTPException(status_code=404, detail="DICOM object not found")

    return FileResponse(
        path=str(file_path),
        media_type="application/dicom",
        filename=f"{objectUID}.dcm",
    )
