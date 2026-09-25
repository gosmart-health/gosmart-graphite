from fastapi import APIRouter, Depends
from actor.dicom_actor import get_dicom_actor
from core.config import Settings, get_settings
from db.neo4j import Neo4jClient, get_neo4j
from model.health import HealthResponse, ServiceHealth

router = APIRouter(prefix="/health", tags=["Health"])


@router.get("", response_model=HealthResponse)
async def check_health(
    settings: Settings = Depends(get_settings),
    neo4j: Neo4jClient = Depends(get_neo4j),
) -> HealthResponse:
    services = {}

    # Check Neo4j
    neo4j_healthy = await neo4j.verify_connectivity()
    services["neo4j"] = ServiceHealth(
        name="Neo4j Graph Database",
        status="HEALTHY" if neo4j_healthy else "UNHEALTHY",
        details={"connection_uri": settings.DB_CONNECTION_URI},
    )

    # Check Pykka Actor
    actor_ref = get_dicom_actor()
    actor_healthy = actor_ref.is_alive()
    actor_details = {}
    if actor_healthy:
        try:
            res = actor_ref.ask({"type": "PING"}, timeout=2.0)
            actor_details["processed_count"] = str(res.get("processed_count", 0))
        except Exception as e:
            actor_details["error"] = str(e)

    services["pykka_actor"] = ServiceHealth(
        name="Pykka DICOM Actor",
        status="HEALTHY" if actor_healthy else "UNHEALTHY",
        details=actor_details,
    )

    overall_status = "HEALTHY" if neo4j_healthy and actor_healthy else "DEGRADED"

    return HealthResponse(
        status=overall_status,
        version=settings.APP_VERSION,
        services=services,
        storage_root_uri=settings.STORAGE_ROOT_URI,
        db_connection_uri=settings.DB_CONNECTION_URI,
    )
