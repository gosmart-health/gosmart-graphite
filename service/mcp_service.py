import json
from typing import Any, Dict, List, Optional

from mcp.server.mcpserver import MCPServer

from core.config import get_settings
from core.logging import get_logger
from db.neo4j import neo4j_client
from service.dicom_service import dicom_service
from service.graph_service import graph_service
from service.pynetdicom_service import pynetdicom_service

logger = get_logger("gosmart_gs.mcp")

mcp = MCPServer("gosmart-dicom-graph-server")


@mcp.tool()
async def health_check() -> Dict[str, Any]:
    """Check system health status (Neo4j, Pykka Actor, Storage URI)."""
    from actor.dicom_actor import get_dicom_actor

    settings = get_settings()
    neo4j_healthy = await neo4j_client.verify_connectivity()

    actor_ref = get_dicom_actor()
    actor_healthy = actor_ref.is_alive()
    actor_info = {}
    if actor_healthy:
        try:
            actor_info = actor_ref.ask({"type": "PING"}, timeout=2.0)
        except Exception as e:
            actor_info = {"error": str(e)}

    status_data = {
        "overall_status": "HEALTHY" if neo4j_healthy and actor_healthy else "DEGRADED",
        "neo4j": {
            "healthy": neo4j_healthy,
            "uri": settings.DB_CONNECTION_URI,
        },
        "pykka_actor": {
            "healthy": actor_healthy,
            "details": actor_info,
        },
        "storage_root_uri": settings.STORAGE_ROOT_URI,
    }
    logger.info("mcp_health_check_executed", health=status_data)
    return status_data


@mcp.tool()
async def generate_mock_dicom(
    patient_id: str = "MOCK-001",
    patient_name: str = "DOE^JANE",
    modality: str = "CT",
    study_description: str = "Head CT Scan",
) -> Dict[str, Any]:
    """Generate a realistic mock DICOM file, store it locally, and index it into Neo4j."""
    ds = dicom_service.create_mock_dicom(
        patient_id=patient_id,
        patient_name=patient_name,
        modality=modality,
        study_description=study_description,
    )
    hierarchy = dicom_service.parse_dataset(ds)
    storage_uri = dicom_service.save_raw_dicom(ds, hierarchy.instance.sop_instance_uid)
    hierarchy.instance.storage_uri = storage_uri

    graph_res = None
    try:
        graph_res = await graph_service.save_hierarchy(hierarchy)
    except Exception as e:
        logger.warning("mcp_neo4j_save_skipped", error=str(e))

    res = {
        "success": True,
        "patient_id": hierarchy.patient.patient_id,
        "study_instance_uid": hierarchy.study.study_instance_uid,
        "series_instance_uid": hierarchy.series.series_instance_uid,
        "sop_instance_uid": hierarchy.instance.sop_instance_uid,
        "storage_uri": storage_uri,
        "graph_record": graph_res,
    }
    logger.info("mcp_mock_generated", patient_id=patient_id, modality=modality, storage_uri=storage_uri)
    return res


@mcp.tool()
async def ingest_dicom_file(file_path: str, use_actor: bool = True) -> Dict[str, Any]:
    """Parse and ingest a local DICOM file into the knowledge graph and storage root."""
    if use_actor:
        from actor.dicom_actor import get_dicom_actor

        actor = get_dicom_actor()
        res = actor.ask({"type": "INGEST_FILE", "file_path": file_path})
        logger.info("mcp_file_ingested_via_actor", file_path=file_path, result=res)
        return res

    hierarchy = dicom_service.parse_file(file_path)
    res = None
    try:
        res = await graph_service.save_hierarchy(hierarchy)
    except Exception as e:
        logger.warning("mcp_neo4j_save_skipped", error=str(e))

    result = {
        "success": True,
        "patient_id": hierarchy.patient.patient_id,
        "study_instance_uid": hierarchy.study.study_instance_uid,
        "series_instance_uid": hierarchy.series.series_instance_uid,
        "sop_instance_uid": hierarchy.instance.sop_instance_uid,
        "graph_result": res,
    }
    logger.info("mcp_file_ingested", file_path=file_path, patient_id=hierarchy.patient.patient_id)
    return result


@mcp.tool()
async def list_patients(limit: int = 50) -> List[Dict[str, Any]]:
    """Query list of patients and their study counts from Neo4j."""
    patients = await graph_service.list_patients(limit=limit)
    logger.info("mcp_patients_queried", count=len(patients), limit=limit)
    return patients


@mcp.tool()
async def get_patient_graph(patient_id: str) -> Dict[str, Any]:
    """Retrieve full DICOM graph hierarchy (Patient -> Studies -> Series -> Instances) for a patient."""
    graph_data = await graph_service.get_patient_graph(patient_id=patient_id)
    logger.info("mcp_patient_graph_queried", patient_id=patient_id, found=bool(graph_data))
    return graph_data


@mcp.tool()
async def execute_cypher(query: str, parameters_json: Optional[str] = None) -> List[Dict[str, Any]]:
    """Execute a custom Cypher query against the Neo4j DICOM graph database."""
    params = json.loads(parameters_json) if parameters_json else {}
    records = await graph_service.execute_custom_query(query, params)
    logger.info("mcp_cypher_executed", query=query, record_count=len(records))
    return records


@mcp.tool()
def echo_scu(
    host: str = "127.0.0.1",
    port: int = 11112,
    calling_aet: Optional[str] = None,
    called_aet: Optional[str] = None,
) -> Dict[str, Any]:
    """Test network connectivity to a DICOM SCP using C-ECHO (pynetdicom)."""
    res = pynetdicom_service.echo_scu(
        host=host,
        port=port,
        calling_aet=calling_aet,
        called_aet=called_aet,
    )
    logger.info("mcp_echo_scu_executed", host=host, port=port, result=res)
    return res
