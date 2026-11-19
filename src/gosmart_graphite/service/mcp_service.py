import json
from contextvars import ContextVar
from typing import Any, Dict, List, Optional

from mcp.server.mcpserver import MCPServer
from starlette.datastructures import Headers, QueryParams
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from gosmart_graphite.core.auth_dependency import reset_active_group_id, set_active_group_id
from gosmart_graphite.core.config import get_settings
from gosmart_graphite.core.logging import get_logger
from gosmart_graphite.core.security import decode_token
from gosmart_graphite.db.neo4j import neo4j_client
from gosmart_graphite.model.llm_config import LLMConfigCreateRequest
from gosmart_graphite.model.system import (
    DICOMConfigurationCreateRequest,
    DICOMConfigurationUpdateRequest,
    SystemUpdateRequest,
)
from gosmart_graphite.service.dicom_service import dicom_service
from gosmart_graphite.service.graph_service import graph_service
from gosmart_graphite.service.llm_config_service import llm_config_service
from gosmart_graphite.service.pynetdicom_service import pynetdicom_service
from gosmart_graphite.service.system_service import system_service
from gosmart_graphite.service.user_service import user_service

logger = get_logger("gosmart_gs.mcp")

mcp = MCPServer("gosmart-graphite")

# Context variables to hold authenticated user and active group across MCP tool invocations
_current_mcp_user: ContextVar[Optional[Dict[str, Any]]] = ContextVar("_current_mcp_user", default=None)
_current_mcp_group: ContextVar[Optional[str]] = ContextVar("_current_mcp_group", default=None)


def get_current_mcp_user() -> Optional[Dict[str, Any]]:
    """Retrieve the currently authenticated MCP user from context."""
    return _current_mcp_user.get()


def get_current_mcp_group() -> Optional[str]:
    """Retrieve the active Group ID bound to the current MCP request context, if any."""
    return _current_mcp_group.get()


def get_current_mcp_acted_by(fallback: str = "mcp_agent") -> str:
    """Resolve acted_by audit string based on the active MCP user."""
    user = _current_mcp_user.get()
    if user:
        email = user.get("primary_email") or user.get("id")
        return f"user:{email}"
    return fallback


def check_mcp_admin_role() -> None:
    """Check if the currently authenticated MCP user has Admin privileges."""
    user = _current_mcp_user.get()
    if user:
        roles = user.get("system_roles", [])
        if not any(r in roles for r in ["PLATFORM_ADMIN", "Admin", "admin"]):
            raise PermissionError("Permission denied: Operation requires PLATFORM_ADMIN or Admin role")


@mcp.tool()
async def health_check() -> Dict[str, Any]:
    """Check system health status (Neo4j, Pykka Actor, Storage URI)."""
    from gosmart_graphite.actor.dicom_actor import get_dicom_actor

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
        from gosmart_graphite.actor.dicom_actor import get_dicom_actor

        actor = get_dicom_actor()
        res = actor.ask({"type": "INGEST_FILE", "file_path": file_path}, block=True)
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
    check_mcp_admin_role()
    effective_acted_by = acted_by if acted_by != "mcp_agent" else get_current_mcp_acted_by(fallback="mcp_agent")
    req = SystemUpdateRequest(
        name=name,
        owner=owner,
        address=address,
        email=email,
        phone=phone,
        serial_number=serial_number,
    )
    updated = await system_service.update_system(data=req, acted_by=effective_acted_by, test_id=test_id)
    logger.info("mcp_update_system_info_executed", updated=bool(updated), acted_by=effective_acted_by)
    return updated


@mcp.tool()
async def bootstrap_system_defaults(acted_by: str = "mcp_agent") -> Dict[str, Any]:
    """Ensure the root System node and default promiscuous DICOM configuration are initialized."""
    check_mcp_admin_role()
    effective_acted_by = acted_by if acted_by != "mcp_agent" else get_current_mcp_acted_by(fallback="mcp_agent")
    res = await system_service.bootstrap_system(acted_by=effective_acted_by)
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
        is_local: bool = False,
        tls_enabled: bool = False,
        dicom_services_json: Optional[str] = None,
        transfer_syntaxes_json: Optional[str] = None,
        storage_root_uri: Optional[str] = None,
        group_id: Optional[str] = None,
        description: Optional[str] = None,
        test_id: Optional[str] = None,
        acted_by: str = "mcp_agent",
) -> Dict[str, Any]:
    """Create a new DICOM Configuration attached to a sponsoring Group (or default 'GoSmart Health')."""
    check_mcp_admin_role()
    effective_acted_by = acted_by if acted_by != "mcp_agent" else get_current_mcp_acted_by(fallback="mcp_agent")
    roles = json.loads(roles_json) if roles_json else ["SCP"]
    dicom_services = json.loads(dicom_services_json) if dicom_services_json else ["*"]
    transfer_syntaxes = json.loads(transfer_syntaxes_json) if transfer_syntaxes_json else ["*"]

    req = DICOMConfigurationCreateRequest(
        ae_title=ae_title,
        roles=roles,
        host=host,
        port=port,
        is_local=is_local,
        tls_enabled=tls_enabled,
        dicom_services=dicom_services,
        transfer_syntaxes=transfer_syntaxes,
        storage_root_uri=storage_root_uri,
        group_id=group_id,
        description=description,
        test_id=test_id,
    )
    cfg = await system_service.create_configuration(
        config_data=req, acted_by=effective_acted_by, assign_group=group_id
    )
    logger.info("mcp_create_dicom_configuration_executed", config_id=cfg.get("id"), ae_title=ae_title, group_id=group_id)
    return cfg


@mcp.tool()
async def update_dicom_configuration(
        config_id: str,
        ae_title: Optional[str] = None,
        roles_json: Optional[str] = None,
        host: Optional[str] = None,
        port: Optional[int] = None,
        is_local: Optional[bool] = None,
        tls_enabled: Optional[bool] = None,
        storage_root_uri: Optional[str] = None,
        description: Optional[str] = None,
        acted_by: str = "mcp_agent",
) -> Optional[Dict[str, Any]]:
    """Update mutable properties of an existing DICOM Configuration."""
    check_mcp_admin_role()
    effective_acted_by = acted_by if acted_by != "mcp_agent" else get_current_mcp_acted_by(fallback="mcp_agent")
    roles = json.loads(roles_json) if roles_json else None
    req = DICOMConfigurationUpdateRequest(
        ae_title=ae_title,
        roles=roles,
        host=host,
        port=port,
        is_local=is_local,
        tls_enabled=tls_enabled,
        storage_root_uri=storage_root_uri,
        description=description,
    )
    updated = await system_service.update_configuration(
        config_id=config_id, update_data=req, acted_by=effective_acted_by
    )
    logger.info("mcp_update_dicom_configuration_executed", config_id=config_id, updated=bool(updated))
    return updated


@mcp.tool()
async def delete_dicom_configuration(
        config_id: str,
        hard: bool = False,
        acted_by: str = "mcp_agent",
) -> Dict[str, Any]:
    """Soft-delete (or hard-delete if specified) a DICOM Configuration."""
    check_mcp_admin_role()
    effective_acted_by = acted_by if acted_by != "mcp_agent" else get_current_mcp_acted_by(fallback="mcp_agent")
    deleted = await system_service.delete_configuration(
        config_id=config_id, acted_by=effective_acted_by, hard_delete=hard
    )
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
    check_mcp_admin_role()
    effective_acted_by = acted_by if acted_by != "mcp_agent" else get_current_mcp_acted_by(fallback="mcp_agent")
    restored = await system_service.restore_configuration(config_id=config_id, acted_by=effective_acted_by)
    logger.info("mcp_restore_dicom_configuration_executed", config_id=config_id, restored=bool(restored))
    return {
        "success": bool(restored),
        "configuration": restored,
    }


# -----------------------------------------------------------------------------
# Dynamic LLM Configuration MCP Tools
# -----------------------------------------------------------------------------


@mcp.tool()
async def list_llm_configurations(
        tag: Optional[str] = None,
        provider: Optional[str] = None,
        group_id: Optional[str] = None,
        include_tests: bool = False,
        test_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """List active group-scoped LLM_Config nodes (with api_key redacted)."""
    configs = await llm_config_service.list_llm_configs(
        tag=tag,
        provider=provider,
        group_id=group_id,
        include_tests=include_tests,
        test_id=test_id,
    )
    logger.info("mcp_list_llm_configurations_executed", count=len(configs))
    return configs


@mcp.tool()
async def get_llm_configuration(
        config_id: Optional[str] = None,
        tag: Optional[str] = None,
        group_id: Optional[str] = None,
        test_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Retrieve a single active LLM_Config by ID or (tag[, group_id]) (api_key redacted)."""
    cfg = await llm_config_service.get_llm_config(
        config_id=config_id,
        tag=tag,
        group_id=group_id,
        test_id=test_id,
    )
    logger.info("mcp_get_llm_configuration_executed", config_id=config_id, tag=tag, group_id=group_id, found=bool(cfg))
    return cfg


@mcp.tool()
async def upsert_llm_configuration(
        tag: str,
        provider: str,
        model: str,
        url: str = "",
        dimensions: Optional[int] = None,
        dimensions_truncated: Optional[int] = None,
        api_key: str = "",
        location: str = "us-central1",
        project_id: Optional[str] = None,
        extra_config_json: Optional[str] = None,
        description: Optional[str] = None,
        assign_group: Optional[str] = None,
        test_id: Optional[str] = None,
        acted_by: str = "mcp_agent",
) -> Dict[str, Any]:
    """Create or overwrite a purpose-tagged LLM_Config node and assign it to a sponsoring Group."""
    check_mcp_admin_role()
    effective_acted_by = acted_by if acted_by != "mcp_agent" else get_current_mcp_acted_by(fallback="mcp_agent")
    extra_config = json.loads(extra_config_json) if extra_config_json else None
    req = LLMConfigCreateRequest(
        tag=tag,
        provider=provider,
        model=model,
        url=url,
        dimensions=dimensions,
        dimensions_truncated=dimensions_truncated,
        api_key=api_key,
        location=location,
        project_id=project_id,
        extra_config=extra_config,
        description=description,
        assign_group=assign_group,
        test_id=test_id,
    )
    saved = await llm_config_service.upsert_llm_config(
        config_data=req,
        assign_group=assign_group,
        acted_by=effective_acted_by,
        test_id=test_id,
    )
    logger.info("mcp_upsert_llm_configuration_executed", config_id=saved.get("id"), tag=tag)
    return saved


@mcp.tool()
async def assign_llm_configuration_to_group(
        config_id: str,
        group: str,
        test_id: Optional[str] = None,
        acted_by: str = "mcp_agent",
) -> Optional[Dict[str, Any]]:
    """Assign an existing active LLM_Config to a target sponsoring Group via HAS_LLM_CONFIG."""
    check_mcp_admin_role()
    effective_acted_by = acted_by if acted_by != "mcp_agent" else get_current_mcp_acted_by(fallback="mcp_agent")
    res = await llm_config_service.assign_to_group(
        config_id=config_id,
        group_identifier=group,
        acted_by=effective_acted_by,
        test_id=test_id,
    )
    logger.info("mcp_assign_llm_configuration_executed", config_id=config_id, group=group, assigned=bool(res))
    return res


@mcp.tool()
async def dissociate_llm_configuration_from_group(
        config_id: str,
        group_id: str,
        acted_by: str = "mcp_agent",
) -> Dict[str, Any]:
    """Soft-dissociate a Group from an LLM_Config (swapping HAS_LLM_CONFIG to NOT_HAS_LLM_CONFIG)."""
    check_mcp_admin_role()
    effective_acted_by = acted_by if acted_by != "mcp_agent" else get_current_mcp_acted_by(fallback="mcp_agent")
    success = await llm_config_service.dissociate_from_group(
        config_id=config_id,
        group_id=group_id,
        acted_by=effective_acted_by,
    )
    logger.info(
        "mcp_dissociate_llm_configuration_executed",
        config_id=config_id,
        group_id=group_id,
        success=success,
    )
    return {
        "success": success,
        "config_id": config_id,
        "group_id": group_id,
    }


@mcp.tool()
async def delete_llm_configuration(
        config_id: str,
        hard: bool = False,
        acted_by: str = "mcp_agent",
) -> Dict[str, Any]:
    """Soft-delete (or hard-delete if specified) an LLM_Config node."""
    check_mcp_admin_role()
    effective_acted_by = acted_by if acted_by != "mcp_agent" else get_current_mcp_acted_by(fallback="mcp_agent")
    deleted = await llm_config_service.delete_llm_config(
        config_id=config_id,
        acted_by=effective_acted_by,
        hard_delete=hard,
    )
    logger.info("mcp_delete_llm_configuration_executed", config_id=config_id, success=deleted)
    return {
        "success": deleted,
        "config_id": config_id,
        "hard_deleted": hard,
    }


@mcp.tool()
async def restore_llm_configuration(
        config_id: str,
        acted_by: str = "mcp_agent",
) -> Dict[str, Any]:
    """Restore a previously soft-deleted LLM_Config node."""
    check_mcp_admin_role()
    effective_acted_by = acted_by if acted_by != "mcp_agent" else get_current_mcp_acted_by(fallback="mcp_agent")
    restored = await llm_config_service.restore_llm_config(config_id=config_id, acted_by=effective_acted_by)
    logger.info("mcp_restore_llm_configuration_executed", config_id=config_id, restored=bool(restored))
    return {
        "success": bool(restored),
        "configuration": restored,
    }


@mcp.tool()
async def store_scu(
        destination_aet: str,
        instances_json: Optional[str] = None,
        study_instance_uid: Optional[str] = None,
        series_instance_uid: Optional[str] = None,
        sop_instance_uid: Optional[str] = None,
        patient_id: Optional[str] = None,
        host: Optional[str] = None,
        port: Optional[int] = None,
        calling_aet: Optional[str] = None,
        acted_by: str = "mcp_agent",
        timeout: float = 60.0,
) -> Dict[str, Any]:
    """Send DICOM instance(s) or query-matched instances to a remote AE Title using Store SCU Actor."""
    from gosmart_graphite.actor.store_scu_actor import get_store_scu_actor

    effective_acted_by = acted_by if acted_by != "mcp_agent" else get_current_mcp_acted_by(fallback="mcp_agent")
    instances: List[str] = json.loads(instances_json) if instances_json else []

    if not instances and (study_instance_uid or series_instance_uid or sop_instance_uid or patient_id):
        matched = graph_service.find_instances_for_retrieve_sync(
            patient_id=patient_id,
            study_uid=study_instance_uid,
            series_uid=series_instance_uid,
            sop_uid=sop_instance_uid,
            include_tests=True,
        )
        instances = [inst["storage_uri"] for inst in matched if inst.get("storage_uri")]

    actor = get_store_scu_actor()
    res = actor.ask(
        {
            "type": "STORE",
            "destination_aet": destination_aet,
            "host": host,
            "port": port,
            "calling_aet": calling_aet,
            "instances": instances,
            "acted_by": effective_acted_by,
        },
        block=True,
        timeout=timeout,
    )
    logger.info("mcp_store_scu_executed", destination_aet=destination_aet, result=res)
    return res


# -----------------------------------------------------------------------------
# MCP SSE Security Middleware & Application Factory
# -----------------------------------------------------------------------------


# In-memory registry of active authenticated MCP session IDs -> user dict
_active_mcp_sessions: Dict[str, Dict[str, Any]] = {}


class MCPAuthMiddleware:
    """ASGI Middleware enforcing Bearer JWT or API Key authentication on MCP SSE & Messages endpoints."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if scope.get("method") == "OPTIONS":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        query_params = QueryParams(scope.get("query_string", b""))

        token = None
        auth_header = headers.get("authorization")
        if auth_header and auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
        elif "token" in query_params:
            token = query_params["token"]

        api_key = headers.get("x-api-key") or query_params.get("api_key")

        user: Optional[Dict[str, Any]] = None

        if token:
            try:
                payload = decode_token(token)
                user_id = payload.get("sub")
                if user_id:
                    u = await user_service.get_user_by_id(user_id)
                    if u and u.get("is_active", True):
                        user = u
                    elif not u:
                        user = {
                            "id": user_id,
                            "primary_email": payload.get("email"),
                            "display_name": payload.get("display_name"),
                            "system_roles": payload.get("roles", []),
                            "is_active": True,
                        }
            except Exception as e:
                logger.warning("mcp_auth_token_failed", error=str(e))
        elif api_key:
            try:
                u = await user_service.authenticate_api_key(api_key)
                if u and u.get("is_active", True):
                    user = u
            except Exception as e:
                logger.warning("mcp_auth_api_key_failed", error=str(e))

        # Check if this is an SSE or StreamableHTTP request carrying a known session_id from an authenticated connection
        session_id = query_params.get("session_id") or headers.get("mcp-session-id")
        if not user and session_id and session_id in _active_mcp_sessions:
            user = _active_mcp_sessions[session_id]

        if not user:
            response = JSONResponse(
                {"detail": "Unauthorized: Authentication credentials required for MCP service"},
                status_code=401,
            )
            await response(scope, receive, send)
            return

        raw_group_id = headers.get("x-group-id") or query_params.get("group_id")
        clean_group_id = raw_group_id.strip() if isinstance(raw_group_id, str) and raw_group_id.strip() else None

        token_ctx = _current_mcp_user.set(user)
        group_ctx = _current_mcp_group.set(clean_group_id)
        active_group_ctx = set_active_group_id(clean_group_id)

        async def send_wrapper(message: Dict[str, Any]) -> None:
            # 1. Capture mcp-session-id from response headers for Streamable HTTP
            if message["type"] == "http.response.start":
                raw_headers = message.get("headers", [])
                for k, v in raw_headers:
                    if k.lower() == b"mcp-session-id":
                        try:
                            sid = v.decode("utf-8").strip()
                            if user:
                                _active_mcp_sessions[sid] = user
                        except Exception as why:
                            logger.warning(f"Failed to capture mcp-session-id: {why}")

            # 2. Capture session_id from SSE body declaration
            elif message["type"] == "http.response.body":
                body_bytes = message.get("body", b"")
                if b"session_id=" in body_bytes:
                    try:
                        body_text = body_bytes.decode("utf-8", errors="ignore")
                        for line in body_text.splitlines():
                            if "session_id=" in line:
                                extracted_sid = line.split("session_id=")[1].strip().split("&")[0].split()[0]
                                if user:
                                    _active_mcp_sessions[extracted_sid] = user
                    except Exception as why:
                        logger.warning(f"Failed to capture mcp-session-id: {why}")
                        pass
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            reset_active_group_id(active_group_ctx)
            _current_mcp_group.reset(group_ctx)
            _current_mcp_user.reset(token_ctx)


class DynamicMCPStreamableApp:
    """Dynamic ASGI wrapper around the MCP StreamableHTTP Starlette sub-app.

    Ensures that incoming requests are dispatched to the currently active session manager,
    which is refreshed on each application lifespan startup.
    """

    def __init__(self, mcp_server: MCPServer) -> None:
        self.mcp_server = mcp_server
        self._current_app: Optional[Any] = None

    def refresh(self, streamable_http_path: str = "/") -> Any:
        self._current_app = self.mcp_server.streamable_http_app(streamable_http_path=streamable_http_path)
        return self._current_app

    @property
    def current_app(self) -> Any:
        if self._current_app is None:
            self.refresh()
        return self._current_app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self.current_app(scope, receive, send)


dynamic_mcp_streamable_app = DynamicMCPStreamableApp(mcp)


def get_mcp_sse_app(*args: Any, **kwargs: Any) -> Any:
    """Build and wrap the MCP SSE Starlette app with MCPAuthMiddleware."""
    base_app = mcp.sse_app(*args, **kwargs)
    return MCPAuthMiddleware(base_app)


def get_mcp_streamable_http_app(*args: Any, **kwargs: Any) -> Any:
    """Build and wrap the MCP StreamableHTTP Starlette app with MCPAuthMiddleware."""
    if args or (kwargs and set(kwargs.keys()) - {"streamable_http_path"}):
        base_app = mcp.streamable_http_app(*args, **kwargs)
        return MCPAuthMiddleware(base_app)
    dynamic_mcp_streamable_app.refresh(kwargs.get("streamable_http_path", "/"))
    return MCPAuthMiddleware(dynamic_mcp_streamable_app)
