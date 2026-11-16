import email
import io
import json
import uuid
from email import policy
from pathlib import Path
from typing import Any, Dict, List, Optional

import pydicom
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse

from gosmart_graphite.actor.audit_actor import record_audit
from gosmart_graphite.actor.dicom_actor import get_dicom_actor
from gosmart_graphite.core.auth_dependency import get_current_group_context, get_current_user
from gosmart_graphite.core.logging import get_logger
from gosmart_graphite.model.audit import AuditAction
from gosmart_graphite.service.dicom_service import dicom_service
from gosmart_graphite.service.dicomweb_service import dicomweb_service
from gosmart_graphite.service.group_service import group_service

logger = get_logger("gosmart_gs.api.dicomweb")

router = APIRouter(
    prefix="/dicomweb",
    tags=["DICOMweb"],
    dependencies=[Depends(get_current_user), Depends(get_current_group_context)],
)


def _get_actor_context(request: Request) -> tuple[str, str, Optional[str]]:
    """Extract actor_id, actor, and test_id from request."""
    user_id = request.headers.get("X-User-ID") or "anonymous"
    client_host = request.client.host if request.client else "unknown"
    actor_id = f"user:{user_id}"
    actor = f"{user_id}@{client_host}" if user_id != "anonymous" else f"client:{client_host}"
    test_id = request.headers.get("X-Test-ID")
    return actor_id, actor, test_id


async def _resolve_accessible_group_ids(
    current_user: Dict[str, Any],
    group_context: Optional[Dict[str, Any]],
) -> Optional[List[str]]:
    """Resolve effective accessible group IDs based on user roles and group context.

    Returns None for platform admins with no explicit group header (unrestricted scope),
    or a list of Group IDs the user is permitted to view.
    """
    system_roles = [str(r).upper() for r in current_user.get("system_roles", [])]
    if "PLATFORM_ADMIN" in system_roles or "ADMIN" in system_roles:
        if group_context and group_context.get("id"):
            closure = await group_service.get_group_hierarchy_closure(group_context["id"])
            return [g["id"] for g in closure] if closure else [group_context["id"]]
        return None  # Global cross-organization access

    if group_context and group_context.get("id"):
        closure = await group_service.get_group_hierarchy_closure(group_context["id"])
        return [g["id"] for g in closure] if closure else [group_context["id"]]

    effective_groups = await group_service.get_effective_accessible_groups(current_user["id"])
    return [g["id"] for g in effective_groups] if effective_groups else []


def _extract_dicom_payloads(content_type: str, body: bytes) -> List[bytes]:
    """Extract individual DICOM Part-10 byte payloads from raw binary or multipart/related request."""
    if "multipart" in content_type.lower():
        msg = email.message_from_bytes(
            b"Content-Type: " + content_type.encode("utf-8") + b"\r\n\r\n" + body,
            policy=policy.default,
        )
        payloads: List[bytes] = []
        if msg.is_multipart():
            for part in msg.iter_parts():
                payload = part.get_payload(decode=True)
                if payload and len(payload) > 128:
                    payloads.append(payload)
        return payloads
    return [body]


def _build_multipart_dicom_response(files: List[Path]) -> Response:
    """Format multiple DICOM Part-10 files into a standard PS 3.18 multipart/related response."""
    boundary = "DICOM_MULTIPART_BOUNDARY"
    parts = []
    for f in files:
        content = f.read_bytes()
        parts.append(
            b"--" + boundary.encode() + b"\r\n"
            b"Content-Type: application/dicom\r\n"
            b"Content-Length: " + str(len(content)).encode() + b"\r\n\r\n" + content + b"\r\n"
        )
    body = b"".join(parts) + b"--" + boundary.encode() + b"--\r\n"
    return Response(
        content=body,
        media_type=f'multipart/related; type="application/dicom"; boundary={boundary}',
        status_code=200,
    )


# -----------------------------------------------------------------------------
# QIDO-RS (Query based on ID for DICOM Objects)
# -----------------------------------------------------------------------------


@router.get("/studies", response_class=Response)
async def qido_search_studies(
    request: Request,
    PatientID: Optional[str] = Query(None, alias="PatientID"),
    PatientName: Optional[str] = Query(None, alias="PatientName"),
    StudyInstanceUID: Optional[str] = Query(None, alias="StudyInstanceUID"),
    StudyDate: Optional[str] = Query(None, alias="StudyDate"),
    AccessionNumber: Optional[str] = Query(None, alias="AccessionNumber"),
    limit: int = Query(100, ge=1, le=1000),
    current_user: Dict[str, Any] = Depends(get_current_user),
    group_context: Optional[Dict[str, Any]] = Depends(get_current_group_context),
):
    """QIDO-RS: Search for studies matching query parameters directly from the graph."""
    actor_id, actor, test_id = _get_actor_context(request)
    accessible_group_ids = await _resolve_accessible_group_ids(current_user, group_context)
    studies = dicomweb_service.search_studies(
        patient_id=PatientID,
        patient_name=PatientName,
        study_uid=StudyInstanceUID,
        study_date=StudyDate,
        accession_number=AccessionNumber,
        limit=limit,
        accessible_group_ids=accessible_group_ids,
        actor_id=actor_id,
        actor=actor,
        test_id=test_id,
    )
    return Response(
        content=json.dumps(studies),
        media_type="application/dicom+json",
        status_code=200,
    )


@router.get("/studies/{study_uid}/series", response_class=Response)
@router.get("/series", response_class=Response)
async def qido_search_series(
    request: Request,
    study_uid: Optional[str] = None,
    SeriesInstanceUID: Optional[str] = Query(None, alias="SeriesInstanceUID"),
    Modality: Optional[str] = Query(None, alias="Modality"),
    limit: int = Query(100, ge=1, le=1000),
    current_user: Dict[str, Any] = Depends(get_current_user),
    group_context: Optional[Dict[str, Any]] = Depends(get_current_group_context),
):
    """QIDO-RS: Search for series matching query parameters directly from the graph."""
    actor_id, actor, test_id = _get_actor_context(request)
    accessible_group_ids = await _resolve_accessible_group_ids(current_user, group_context)
    series = dicomweb_service.search_series(
        study_uid=study_uid,
        series_uid=SeriesInstanceUID,
        modality=Modality,
        limit=limit,
        accessible_group_ids=accessible_group_ids,
        actor_id=actor_id,
        actor=actor,
        test_id=test_id,
    )
    return Response(
        content=json.dumps(series),
        media_type="application/dicom+json",
        status_code=200,
    )


@router.get("/studies/{study_uid}/series/{series_uid}/instances", response_class=Response)
@router.get("/instances", response_class=Response)
async def qido_search_instances(
    request: Request,
    study_uid: Optional[str] = None,
    series_uid: Optional[str] = None,
    SOPInstanceUID: Optional[str] = Query(None, alias="SOPInstanceUID"),
    limit: int = Query(100, ge=1, le=1000),
    current_user: Dict[str, Any] = Depends(get_current_user),
    group_context: Optional[Dict[str, Any]] = Depends(get_current_group_context),
):
    """QIDO-RS: Search for instances directly from the graph."""
    actor_id, actor, test_id = _get_actor_context(request)
    accessible_group_ids = await _resolve_accessible_group_ids(current_user, group_context)
    instances = dicomweb_service.search_instances(
        study_uid=study_uid,
        series_uid=series_uid,
        sop_uid=SOPInstanceUID,
        limit=limit,
        accessible_group_ids=accessible_group_ids,
        actor_id=actor_id,
        actor=actor,
        test_id=test_id,
    )
    return Response(
        content=json.dumps(instances),
        media_type="application/dicom+json",
        status_code=200,
    )


# -----------------------------------------------------------------------------
# WADO-RS (Web Access to DICOM Objects by RS)
# -----------------------------------------------------------------------------


@router.get("/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}")
async def wado_get_instance(
    request: Request,
    study_uid: str,
    series_uid: str,
    sop_uid: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
    group_context: Optional[Dict[str, Any]] = Depends(get_current_group_context),
):
    """WADO-RS: Retrieve single DICOM SOP Instance."""
    actor_id, actor, test_id = _get_actor_context(request)
    accessible_group_ids = await _resolve_accessible_group_ids(current_user, group_context)
    file_path = dicomweb_service.get_instance_file(
        study_uid,
        series_uid,
        sop_uid,
        accessible_group_ids=accessible_group_ids,
        actor_id=actor_id,
        actor=actor,
        test_id=test_id,
    )
    if not file_path or not file_path.exists():
        raise HTTPException(status_code=404, detail="Instance not found")

    return FileResponse(
        path=str(file_path),
        media_type="application/dicom",
        filename=f"{sop_uid}.dcm",
    )


@router.get("/studies/{study_uid}/series/{series_uid}")
async def wado_get_series(
    request: Request,
    study_uid: str,
    series_uid: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
    group_context: Optional[Dict[str, Any]] = Depends(get_current_group_context),
):
    """WADO-RS: Retrieve all DICOM SOP Instances for a Series as multipart/related."""
    actor_id, actor, test_id = _get_actor_context(request)
    accessible_group_ids = await _resolve_accessible_group_ids(current_user, group_context)
    files = dicomweb_service.get_series_files(
        study_uid,
        series_uid,
        accessible_group_ids=accessible_group_ids,
        actor_id=actor_id,
        actor=actor,
        test_id=test_id,
    )
    if not files:
        raise HTTPException(status_code=404, detail="Series not found or contains no instances")
    return _build_multipart_dicom_response(files)


@router.get("/studies/{study_uid}")
async def wado_get_study(
    request: Request,
    study_uid: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
    group_context: Optional[Dict[str, Any]] = Depends(get_current_group_context),
):
    """WADO-RS: Retrieve all DICOM SOP Instances for a Study as multipart/related."""
    actor_id, actor, test_id = _get_actor_context(request)
    accessible_group_ids = await _resolve_accessible_group_ids(current_user, group_context)
    files = dicomweb_service.get_study_files(
        study_uid,
        accessible_group_ids=accessible_group_ids,
        actor_id=actor_id,
        actor=actor,
        test_id=test_id,
    )
    if not files:
        raise HTTPException(status_code=404, detail="Study not found or contains no instances")
    return _build_multipart_dicom_response(files)


# -----------------------------------------------------------------------------
# STOW-RS (Store Over the Web by RS)
# -----------------------------------------------------------------------------


@router.post("/studies", response_class=Response)
async def stow_store_instances(
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_user),
    group_context: Optional[Dict[str, Any]] = Depends(get_current_group_context),
):
    """STOW-RS: Store DICOM instances into the archive supporting raw binary and multipart/related."""
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Empty request body")

    content_type = request.headers.get("content-type", "application/dicom")
    payloads = _extract_dicom_payloads(content_type, body)
    if not payloads:
        raise HTTPException(status_code=400, detail="No DICOM payloads found in request")

    client_host = request.client.host if request.client else "unknown"
    user_id = request.headers.get("X-User-ID") or current_user.get("id") or "anonymous"
    server_host = request.url.hostname or "127.0.0.1"
    server_port = request.url.port or 8000

    # Determine Group association for STOW-RS: active group context -> primary affiliation -> catch-all
    group_id = None
    if group_context and group_context.get("id"):
        group_id = group_context["id"]
    elif current_user.get("id"):
        affiliations = await group_service.get_user_affiliations(current_user["id"])
        primary = next((a for a in affiliations if a.get("is_primary")), None)
        if primary and primary.get("id"):
            group_id = primary["id"]
        else:
            catch_all = await group_service.get_or_create_catch_all_group()
            group_id = catch_all["id"] if catch_all else None

    source = f"stow:{client_host}"
    source_identity = f"user:{user_id}"
    destination = f"stow:{server_host}:{server_port}"
    destination_identity = "service:dicomweb"

    actor = get_dicom_actor()
    referenced_sop_items: List[Dict[str, Any]] = []
    failed_sop_items: List[Dict[str, Any]] = []

    for payload in payloads:
        sop_uid = None
        sop_class_uid = "1.2.840.10008.5.1.4.1.1.7"
        try:
            ds = pydicom.dcmread(io.BytesIO(payload), force=True)
            sop_uid = getattr(ds, "SOPInstanceUID", None)
            sop_class_uid = getattr(ds, "SOPClassUID", sop_class_uid)
        except Exception as parse_err:
            logger.warning("stow_parse_warning", error=str(parse_err))

        # First order of action: Persist raw object into blackbox storage unconditionally
        storage_uid = str(sop_uid) if sop_uid else f"unparsed_{uuid.uuid4().hex}"
        try:
            file_path = dicom_service.save_raw_dicom(payload, storage_uid)
        except Exception as save_err:
            logger.error("stow_blackbox_save_failed", sop_uid=storage_uid, error=str(save_err))
            if sop_uid:
                failed_sop_items.append(
                    {
                        "00081150": {"vr": "UI", "Value": [sop_class_uid]},
                        "00081155": {"vr": "UI", "Value": [sop_uid]},
                        "00081197": {"vr": "US", "Value": [272]},  # Out of Resources
                    }
                )
            continue

        if not sop_uid:
            continue

        # Ingestion pipeline: index into knowledge graph via actor
        if actor.is_alive():
            actor.tell(
                {
                    "type": "INGEST_FILE",
                    "file_path": str(file_path),
                    "source": source,
                    "source_identity": source_identity,
                    "destination": destination,
                    "destination_identity": destination_identity,
                    "acted_by": f"user:{user_id}",
                    "group_id": group_id,
                }
            )

        referenced_sop_items.append(
            {
                "00081150": {"vr": "UI", "Value": [sop_class_uid]},
                "00081155": {"vr": "UI", "Value": [sop_uid]},
            }
        )

    stow_response: Dict[str, Any] = {}
    if referenced_sop_items:
        stow_response["00081199"] = {"vr": "SQ", "Value": referenced_sop_items}
    if failed_sop_items:
        stow_response["00081198"] = {"vr": "SQ", "Value": failed_sop_items}

    status_code = 200 if referenced_sop_items else 400

    # Audit recording for STOW-RS batch
    record_audit(
        action=AuditAction.DICOMWEB_INSTANCES_STORED.value,
        status_code=status_code,
        status_message="OK" if status_code == 200 else "Failed",
        severity="INFO" if status_code == 200 else "ERROR",
        actor_id=source_identity,
        actor=f"{user_id}@{client_host}",
        target_id=f"payloads:{len(payloads)}",
        target=f"{len(referenced_sop_items)} stored, {len(failed_sop_items)} failed",
        result=(
            f"STOW-RS processed {len(payloads)} instances: "
            f"{len(referenced_sop_items)} succeeded, {len(failed_sop_items)} failed"
        ),
        details={
            "client_host": client_host,
            "total_payloads": len(payloads),
            "stored_count": len(referenced_sop_items),
            "failed_count": len(failed_sop_items),
            "group_id": group_id,
            "referenced_sop_uids": [
                item["00081155"]["Value"][0] for item in referenced_sop_items if "00081155" in item
            ],
        },
        test_id=request.headers.get("X-Test-ID"),
    )

    return Response(
        content=json.dumps(stow_response),
        media_type="application/dicom+json",
        status_code=status_code,
    )


# -----------------------------------------------------------------------------
# Legacy WADO-URI
# -----------------------------------------------------------------------------


@router.get("/wado")
async def wado_uri(
    request: Request,
    requestType: str = Query("WADO"),
    studyUID: Optional[str] = Query(None),
    seriesUID: Optional[str] = Query(None),
    objectUID: Optional[str] = Query(None),
    current_user: Dict[str, Any] = Depends(get_current_user),
    group_context: Optional[Dict[str, Any]] = Depends(get_current_group_context),
):
    """WADO-URI: Retrieve DICOM instance via legacy URL parameters."""
    if requestType != "WADO" or not objectUID or not studyUID or not seriesUID:
        raise HTTPException(status_code=400, detail="Invalid WADO-URI parameters")

    actor_id, actor, test_id = _get_actor_context(request)
    accessible_group_ids = await _resolve_accessible_group_ids(current_user, group_context)
    file_path = dicomweb_service.get_instance_file(
        studyUID,
        seriesUID,
        objectUID,
        accessible_group_ids=accessible_group_ids,
        actor_id=actor_id,
        actor=actor,
        test_id=test_id,
    )
    if not file_path or not file_path.exists():
        raise HTTPException(status_code=404, detail="DICOM object not found")

    return FileResponse(
        path=str(file_path),
        media_type="application/dicom",
        filename=f"{objectUID}.dcm",
    )
