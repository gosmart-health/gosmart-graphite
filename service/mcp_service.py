import json
from typing import Any, Dict, List, Optional

from mcp.server.mcpserver import MCPServer

from core.config import get_settings
from core.logging import get_logger
from db.neo4j import neo4j_client
from model.system import (
    DICOMConfigurationCreateRequest,
    DICOMConfigurationUpdateRequest,
    SystemUpdateRequest,
)
from service.dicom_service import dicom_service
from service.graph_service import graph_service
from service.pynetdicom_service import pynetdicom_service
from service.system_service import system_service

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
async def list_patients(
    limit: int = 50,
    include_tests: bool = False,
    test_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Query list of patients and their study counts from Neo4j."""
    patients = await graph_service.list_patients(limit=limit, include_tests=include_tests, test_id=test_id)
    logger.info("mcp_patients_queried", count=len(patients), limit=limit, include_tests=include_tests)
    return patients


@mcp.tool()
async def get_patient_graph(
    patient_id: str,
    include_tests: bool = False,
) -> Dict[str, Any]:
    """Retrieve full DICOM graph hierarchy (Patient -> Studies -> Series -> Instances) for a patient."""
    graph_data = await graph_service.get_patient_graph(patient_id=patient_id, include_tests=include_tests)
    logger.info("mcp_patient_graph_queried", patient_id=patient_id, found=bool(graph_data), include_tests=include_tests)
    return graph_data


@mcp.tool()
async def soft_delete_relationship(
    start_node_id: str,
    end_node_id: str,
    rel_type: str,
    acted_by: str = "mcp_agent",
) -> Dict[str, Any]:
    """Atomically soft-delete a relationship in Neo4j, cloning properties to NOT_<rel_type> under a transaction."""
    res = await graph_service.soft_delete_relationship(
        start_node_id=start_node_id,
        end_node_id=end_node_id,
        rel_type=rel_type,
        acted_by=acted_by,
    )
    return {"success": bool(res), "relationship": res}


@mcp.tool()
async def restore_relationship(
    start_node_id: str,
    end_node_id: str,
    rel_type: str,
    acted_by: str = "mcp_agent",
) -> Dict[str, Any]:
    """Atomically restore a soft-deleted relationship in Neo4j back to <rel_type> under a write transaction."""
    res = await graph_service.restore_relationship(
        start_node_id=start_node_id,
        end_node_id=end_node_id,
        rel_type=rel_type,
        acted_by=acted_by,
    )
    return {"success": bool(res), "relationship": res}


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


# -----------------------------------------------------------------------------
# System & Configuration MCP Tools
# -----------------------------------------------------------------------------


@mcp.tool()
async def get_system_info(
    include_configs: bool = True,
    test_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Retrieve root System node properties, ownership info, and linked DICOM configurations."""
    sys_info = await system_service.get_system(include_configs=include_configs, test_id=test_id)
    logger.info("mcp_get_system_info_executed", found=bool(sys_info))
    return sys_info


@mcp.tool()
async def update_system_info(
    name: Optional[str] = None,
    owner: Optional[str] = None,
    address: Optional[str] = None,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    serial_number: Optional[str] = None,
    acted_by: str = "mcp_agent",
    test_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Update mutable properties of the root System node (installer configuration)."""
    req = SystemUpdateRequest(
        name=name,
        owner=owner,
        address=address,
        email=email,
        phone=phone,
        serial_number=serial_number,
    )
    updated = await system_service.update_system(data=req, acted_by=acted_by, test_id=test_id)
    logger.info("mcp_update_system_info_executed", updated=bool(updated), acted_by=acted_by)
    return updated


@mcp.tool()
async def bootstrap_system_defaults(acted_by: str = "mcp_agent") -> Dict[str, Any]:
    """Ensure the root System node and default promiscuous DICOM configuration are initialized."""
    res = await system_service.bootstrap_system(acted_by=acted_by)
    logger.info("mcp_bootstrap_system_executed", success=res.get("success", False))
    return res


@mcp.tool()
async def list_dicom_configurations(
    role: Optional[str] = None,
    is_promiscuous: Optional[bool] = None,
    include_tests: bool = False,
    test_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """List active DICOM configurations linked to the System (supports role and promiscuous filtering)."""
    configs = await system_service.list_configurations(
        role=role,
        is_promiscuous=is_promiscuous,
        include_tests=include_tests,
        test_id=test_id,
    )
    logger.info("mcp_list_dicom_configurations_executed", count=len(configs))
    return configs


@mcp.tool()
async def get_dicom_configuration(config_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve single DICOM Configuration by its deterministic ID."""
    cfg = await system_service.get_configuration(config_id=config_id)
    logger.info("mcp_get_dicom_configuration_executed", config_id=config_id, found=bool(cfg))
    return cfg


@mcp.tool()
async def create_dicom_configuration(
    ae_title: str = "*",
    roles_json: Optional[str] = None,
    host: str = "0.0.0.0",
    port: int = 11112,
    tls_enabled: bool = False,
    dicom_services_json: Optional[str] = None,
    transfer_syntaxes_json: Optional[str] = None,
    description: Optional[str] = None,
    test_id: Optional[str] = None,
    acted_by: str = "mcp_agent",
) -> Dict[str, Any]:
    """Create a new DICOM Configuration (* for promiscuous mode) linked to the System."""
    roles = json.loads(roles_json) if roles_json else ["SCP"]
    dicom_services = json.loads(dicom_services_json) if dicom_services_json else ["*"]
    transfer_syntaxes = json.loads(transfer_syntaxes_json) if transfer_syntaxes_json else ["*"]

    req = DICOMConfigurationCreateRequest(
        ae_title=ae_title,
        roles=roles,
        host=host,
        port=port,
        tls_enabled=tls_enabled,
        dicom_services=dicom_services,
        transfer_syntaxes=transfer_syntaxes,
        description=description,
        test_id=test_id,
    )
    cfg = await system_service.create_configuration(config_data=req, acted_by=acted_by)
    logger.info("mcp_create_dicom_configuration_executed", config_id=cfg.get("id"), ae_title=ae_title)
    return cfg


@mcp.tool()
async def update_dicom_configuration(
    config_id: str,
    ae_title: Optional[str] = None,
    roles_json: Optional[str] = None,
    host: Optional[str] = None,
    port: Optional[int] = None,
    tls_enabled: Optional[bool] = None,
    description: Optional[str] = None,
    acted_by: str = "mcp_agent",
) -> Optional[Dict[str, Any]]:
    """Update mutable properties of an existing DICOM Configuration."""
    roles = json.loads(roles_json) if roles_json else None
    req = DICOMConfigurationUpdateRequest(
        ae_title=ae_title,
        roles=roles,
        host=host,
        port=port,
        tls_enabled=tls_enabled,
        description=description,
    )
    updated = await system_service.update_configuration(config_id=config_id, update_data=req, acted_by=acted_by)
    logger.info("mcp_update_dicom_configuration_executed", config_id=config_id, updated=bool(updated))
    return updated


@mcp.tool()
async def delete_dicom_configuration(
    config_id: str,
    hard: bool = False,
    acted_by: str = "mcp_agent",
) -> Dict[str, Any]:
    """Soft-delete (or hard-delete if specified) a DICOM Configuration."""
    deleted = await system_service.delete_configuration(config_id=config_id, acted_by=acted_by, hard_delete=hard)
    logger.info("mcp_delete_dicom_configuration_executed", config_id=config_id, success=deleted)
    return {
        "success": deleted,
        "config_id": config_id,
        "hard_deleted": hard,
    }


@mcp.tool()
async def restore_dicom_configuration(
    config_id: str,
    acted_by: str = "mcp_agent",
) -> Dict[str, Any]:
    """Restore a previously soft-deleted DICOM Configuration."""
    restored = await system_service.restore_configuration(config_id=config_id, acted_by=acted_by)
    logger.info("mcp_restore_dicom_configuration_executed", config_id=config_id, restored=bool(restored))
    return {
        "success": bool(restored),
        "configuration": restored,
    }
