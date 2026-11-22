from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from gosmart_graphite.core.config import get_settings
from gosmart_graphite.core.logging import get_logger
from gosmart_graphite.db.neo4j import Neo4jClient, neo4j_client
from gosmart_graphite.model.auditable import (
    generate_deterministic_id,
    to_negated_relation_type,
    to_positive_relation_type,
)
from gosmart_graphite.model.dicom_config import (
    DICOMConfigurationCreateRequest,
    DICOMConfigurationNode,
    DICOMConfigurationUpdateRequest,
    HasDicomConfigurationRelation,
)
from gosmart_graphite.model.graph import SystemNode
from gosmart_graphite.model.system import SystemUpdateRequest
from gosmart_graphite.service.group_service import group_service

logger = get_logger("gosmart_gs.system")


def clean_neo4j_value(val: Any) -> Any:
    """Normalize Neo4j specific types (e.g. neo4j.time.DateTime) to standard Python types."""
    if hasattr(val, "to_native"):
        return val.to_native()
    if isinstance(val, dict):
        return {k: clean_neo4j_value(v) for k, v in val.items()}
    if isinstance(val, list):
        return [clean_neo4j_value(v) for v in val]
    return val


def normalize_test_id(test_id: Optional[str]) -> Optional[str]:
    """Normalize test_id so empty strings or whitespace are safely converted to None."""
    if test_id and isinstance(test_id, str):
        stripped = test_id.strip()
        return stripped if stripped else None
    return None


def _dict_to_dicom_config(
    data: Dict[str, Any],
    groups: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Map raw Neo4j node properties to a standardized DICOMConfigurationResponse dictionary."""
    clean = clean_neo4j_value(data)
    roles = clean.get("roles", [])
    if isinstance(roles, str):
        roles = [roles]
    dicom_services = clean.get("dicom_services", ["*"])
    if isinstance(dicom_services, str):
        dicom_services = [dicom_services]
    transfer_syntaxes = clean.get("transfer_syntaxes", ["*"])
    if isinstance(transfer_syntaxes, str):
        transfer_syntaxes = [transfer_syntaxes]

    group_summaries: List[Dict[str, Any]] = []
    group_ids: List[str] = []
    for grp in groups or []:
        if not grp:
            continue
        clean_grp = clean_neo4j_value(grp)
        gid = clean_grp.get("id")
        if gid and gid not in group_ids:
            group_ids.append(gid)
            group_summaries.append(
                {
                    "id": gid,
                    "name": clean_grp.get("name", ""),
                    "code": clean_grp.get("code", ""),
                    "group_type": clean_grp.get("group_type"),
                }
            )

    primary_group_id = clean.get("group_id") or (group_ids[0] if group_ids else None)
    if primary_group_id and primary_group_id not in group_ids:
        group_ids.append(primary_group_id)

    ae_title = clean.get("ae_title", "*")
    storage_root_uri = clean.get("storage_root_uri") or f"/tmp/gosmart_graphite/{ae_title}"

    return {
        "id": clean.get("id", ""),
        "ae_title": ae_title,
        "roles": roles,
        "host": clean.get("host", "0.0.0.0"),
        "port": clean.get("port", 11112),
        "is_local": clean.get("is_local", False),
        "is_promiscuous": clean.get("is_promiscuous", ae_title == "*"),
        "tls_enabled": clean.get("tls_enabled", False),
        "tls_cert_path": clean.get("tls_cert_path"),
        "tls_key_path": clean.get("tls_key_path"),
        "tls_ca_path": clean.get("tls_ca_path"),
        "tls_ciphers": clean.get("tls_ciphers"),
        "dicom_services": dicom_services,
        "transfer_syntaxes": transfer_syntaxes,
        "dicomweb_enabled": clean.get("dicomweb_enabled", True),
        "dicomweb_url_prefix": clean.get("dicomweb_url_prefix", "/dicomweb"),
        "dicomweb_qido_enabled": clean.get("dicomweb_qido_enabled", True),
        "dicomweb_wado_enabled": clean.get("dicomweb_wado_enabled", True),
        "dicomweb_stow_enabled": clean.get("dicomweb_stow_enabled", True),
        "storage_root_uri": storage_root_uri,
        "description": clean.get("description"),
        "group_id": primary_group_id,
        "group_ids": group_ids,
        "groups": group_summaries,
        "test_id": clean.get("test_id"),
        "action": clean.get("Action", "CREATE"),
        "created_at": clean.get("CreatedAt"),
        "updated_at": clean.get("UpdatedAt"),
    }


def _dict_to_system_response(data: Dict[str, Any], configs: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Map raw Neo4j node properties to a standardized SystemResponse dictionary."""
    clean = clean_neo4j_value(data)
    installed_at = clean.get("installed_at")
    if isinstance(installed_at, str):
        installed_at = datetime.fromisoformat(installed_at)
    elif installed_at is None:
        installed_at = datetime.now(timezone.utc)

    config_list = configs or []
    return {
        "id": clean.get("id", ""),
        "name": clean.get("name", "GoSmart Health VNA"),
        "owner": clean.get("owner", "GoSmart Health"),
        "address": clean.get("address", "Half Moon Bay, California"),
        "email": clean.get("email", "admin@gosmart.health"),
        "phone": clean.get("phone", "+1 (650) 535-0097"),
        "serial_number": clean.get("serial_number", "GSH-VNA-0001"),
        "installed_at": installed_at,
        "test_id": clean.get("test_id"),
        "action": clean.get("Action", "CREATE"),
        "created_at": clean.get("CreatedAt"),
        "updated_at": clean.get("UpdatedAt"),
        "configurations_count": len(config_list),
        "configurations": config_list,
    }


DEFAULT_TEST_CLEANUP_LABELS: List[str] = [
    "System",
    "DICOMConfiguration",
    "Instance",
    "Patient",
    "Series",
    "Study",
]


class SystemService:
    """Service handling lifecycle, graph persistence, and CRUD operations for System & DICOM configurations."""

    def __init__(self, client: Optional[Neo4jClient] = None) -> None:
        self.client = client or neo4j_client

    async def bootstrap_system(self, acted_by: str = "system", test_id: Optional[str] = None) -> Dict[str, Any]:
        """Ensure the immutable root System node and default promiscuous DICOM entry exist."""
        test_id = normalize_test_id(test_id)
        settings = get_settings()

        # 1. Create or ensure root System Node
        sys_node = SystemNode(
            name="GoSmart Health VNA",
            owner="GoSmart Health",
            address="Half Moon Bay, California",
            email="admin@gosmart.health",
            phone="+1 (650) 535-0097",
            serial_number="GSH-VNA-0001",
            test_id=test_id,
            ActedBy=acted_by,
        )

        merge_system_cypher = """
        MERGE (s:System {id: $id})
        ON CREATE SET s.name = $name,
                      s.owner = $owner,
                      s.address = $address,
                      s.email = $email,
                      s.phone = $phone,
                      s.serial_number = $serial_number,
                      s.installed_at = datetime({timezone: 'UTC'}),
                      s.test_id = $test_id,
                      s.Action = 'CREATE',
                      s.ActedAt = datetime({timezone: 'UTC'}),
                      s.ActedBy = $acted_by,
                      s.CreatedAt = datetime({timezone: 'UTC'})
        ON MATCH SET  s.UpdatedAt = coalesce(s.UpdatedAt, datetime({timezone: 'UTC'}))
        RETURN properties(s) AS s
        """

        sys_params = {
            "id": sys_node.id,
            "name": sys_node.name,
            "owner": sys_node.owner,
            "address": sys_node.address,
            "email": sys_node.email,
            "phone": sys_node.phone,
            "serial_number": sys_node.serial_number,
            "test_id": test_id,
            "acted_by": acted_by,
        }

        records = await self.client.execute_query(merge_system_cypher, sys_params)
        raw_system = records[0].get("s", {}) if records else {}

        # 2. Check if primary promiscuous DICOM configuration exists
        check_promiscuous_cypher = """
        MATCH (s:System {id: $sys_id})-[:HAS_DICOM_CONFIGURATION]->(c:DICOMConfiguration {ae_title: '*'})
        WHERE (c.Action IS NULL OR c.Action <> 'DELETE')
        RETURN properties(c) AS c
        LIMIT 1
        """
        promiscuous_records = await self.client.execute_query(check_promiscuous_cypher, {"sys_id": sys_node.id})

        # 3. Ensure resilient Catch-All Group "GoSmart Health" and operational recovery "LostAndFound" Group exist
        catch_all_grp = await group_service.get_or_create_catch_all_group(test_id=test_id, acted_by=acted_by)
        lost_and_found_grp = await group_service.ensure_lost_and_found_group(test_id=test_id, acted_by=acted_by)

        promiscuous_config: Dict[str, Any] = {}
        if promiscuous_records:
            promiscuous_config = _dict_to_dicom_config(promiscuous_records[0].get("c", {}))
            logger.info("promiscuous_dicom_config_exists", config_id=promiscuous_config.get("id"))
        else:
            # Create default promiscuous DICOM entry attached to System
            cfg_node = DICOMConfigurationNode(
                ae_title="*",
                roles=["SCP"],
                host="0.0.0.0",
                port=settings.DICOM_SCP_PORT,
                is_local=True,
                is_promiscuous=True,
                dicom_services=["*"],
                transfer_syntaxes=["*"],
                dicomweb_enabled=True,
                dicomweb_url_prefix="/dicomweb",
                dicomweb_qido_enabled=True,
                dicomweb_wado_enabled=True,
                dicomweb_stow_enabled=True,
                description=f"Default Promiscuous DICOM SCP (matches config {settings.DICOM_AE_TITLE})",
                test_id=test_id,
                ActedBy=acted_by,
            )

            rel = HasDicomConfigurationRelation(
                start_node_id=sys_node.id,
                end_node_id=cfg_node.id,
                test_id=test_id,
                ActedBy=acted_by,
            )

            merge_config_cypher = """
            MATCH (s:System {id: $sys_id})
            MERGE (c:DICOMConfiguration {id: $cfg.id})
            ON CREATE SET c.ae_title = $cfg.ae_title,
                          c.roles = $cfg.roles,
                          c.host = $cfg.host,
                          c.port = $cfg.port,
                          c.is_local = $cfg.is_local,
                          c.is_promiscuous = $cfg.is_promiscuous,
                          c.tls_enabled = $cfg.tls_enabled,
                          c.tls_cert_path = $cfg.tls_cert_path,
                          c.tls_key_path = $cfg.tls_key_path,
                          c.tls_ca_path = $cfg.tls_ca_path,
                          c.tls_ciphers = $cfg.tls_ciphers,
                          c.dicom_services = $cfg.dicom_services,
                          c.transfer_syntaxes = $cfg.transfer_syntaxes,
                          c.dicomweb_enabled = $cfg.dicomweb_enabled,
                          c.dicomweb_url_prefix = $cfg.dicomweb_url_prefix,
                          c.dicomweb_qido_enabled = $cfg.dicomweb_qido_enabled,
                          c.dicomweb_wado_enabled = $cfg.dicomweb_wado_enabled,
                          c.dicomweb_stow_enabled = $cfg.dicomweb_stow_enabled,
                          c.storage_root_uri = $cfg.storage_root_uri,
                          c.description = $cfg.description,
                          c.test_id = $cfg.test_id,
                          c.Action = 'CREATE',
                          c.ActedAt = datetime({timezone: 'UTC'}),
                          c.ActedBy = $cfg.ActedBy,
                          c.CreatedAt = datetime({timezone: 'UTC'})
            MERGE (s)-[r:HAS_DICOM_CONFIGURATION {id: $rel.id}]->(c)
            ON CREATE SET r.Action = 'CREATE',
                          r.ActedAt = datetime({timezone: 'UTC'}),
                          r.ActedBy = $rel.ActedBy,
                          r.CreatedAt = datetime({timezone: 'UTC'}),
                          r.test_id = $rel.test_id
            RETURN properties(c) AS c
            """

            cfg_dict = cfg_node.model_dump()
            rel_dict = rel.model_dump()
            created_records = await self.client.execute_query(
                merge_config_cypher,
                {
                    "sys_id": sys_node.id,
                    "cfg": cfg_dict,
                    "rel": rel_dict,
                },
            )
            if created_records:
                promiscuous_config = _dict_to_dicom_config(created_records[0].get("c", {}))
                logger.info("promiscuous_dicom_config_created", config_id=promiscuous_config.get("id"))

        # Fetch all configurations for system response
        configs = await self.list_configurations(system_id=sys_node.id, include_tests=bool(test_id), test_id=test_id)
        sys_resp = _dict_to_system_response(raw_system, configs)

        try:
            from gosmart_graphite.service.pynetdicom_service import pynetdicom_service

            pynetdicom_service.sync_from_configurations(configs)
        except Exception as scp_err:
            logger.warning("scp_bootstrap_sync_failed", error=str(scp_err))

        return {
            "success": True,
            "message": "System and promiscuous DICOM configuration verified/bootstrapped successfully",
            "system": sys_resp,
            "promiscuous_config": promiscuous_config,
            "catch_all_group": catch_all_grp,
            "lost_and_found_group": lost_and_found_grp.model_dump(),
        }

    async def get_system(
        self,
        include_configs: bool = True,
        test_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Retrieve root System node and linked active configurations."""
        test_id = normalize_test_id(test_id)
        query = """
        MATCH (s:System)
        WHERE (($test_id IS NULL OR $test_id = '') AND (s.test_id IS NULL OR s.test_id = ''))
           OR ($test_id IS NOT NULL AND $test_id <> '' AND s.test_id = $test_id)
        RETURN properties(s) AS s
        LIMIT 1
        """
        records = await self.client.execute_query(query, {"test_id": test_id})
        if not records:
            return None

        raw_sys = records[0].get("s", {})
        sys_id = raw_sys.get("id", "")

        configs = []
        if include_configs and sys_id:
            configs = await self.list_configurations(system_id=sys_id, include_tests=bool(test_id), test_id=test_id)

        return _dict_to_system_response(raw_sys, configs)

    async def update_system(
        self,
        data: SystemUpdateRequest,
        acted_by: str = "system",
        test_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update mutable fields of the root System node."""
        test_id = normalize_test_id(test_id)
        query = """
        MATCH (s:System)
        WHERE (($test_id IS NULL OR $test_id = '') AND (s.test_id IS NULL OR s.test_id = ''))
           OR ($test_id IS NOT NULL AND $test_id <> '' AND s.test_id = $test_id)
        SET s.name = coalesce($name, s.name),
            s.owner = coalesce($owner, s.owner),
            s.address = coalesce($address, s.address),
            s.email = coalesce($email, s.email),
            s.phone = coalesce($phone, s.phone),
            s.serial_number = coalesce($serial_number, s.serial_number),
            s.installed_at = CASE WHEN $installed_at IS NOT NULL THEN datetime($installed_at) ELSE s.installed_at END,
            s.Action = 'UPDATE',
            s.ActedAt = datetime({timezone: 'UTC'}),
            s.ActedBy = $acted_by,
            s.UpdatedAt = datetime({timezone: 'UTC'})
        RETURN properties(s) AS s
        """
        params = {
            "name": data.name,
            "owner": data.owner,
            "address": data.address,
            "email": data.email,
            "phone": data.phone,
            "serial_number": data.serial_number,
            "installed_at": data.installed_at.isoformat() if data.installed_at else None,
            "acted_by": acted_by,
            "test_id": test_id,
        }
        records = await self.client.execute_query(query, params)
        if not records:
            return None

        raw_sys = records[0].get("s", {})
        sys_id = raw_sys.get("id", "")
        configs = await self.list_configurations(system_id=sys_id, include_tests=bool(test_id), test_id=test_id)
        return _dict_to_system_response(raw_sys, configs)

    async def list_configurations(
        self,
        system_id: Optional[str] = None,
        role: Optional[str] = None,
        is_promiscuous: Optional[bool] = None,
        is_local: Optional[bool] = None,
        group_id: Optional[str] = None,
        include_tests: bool = False,
        test_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List active DICOM configurations attached to Groups or System matching filters."""
        test_id = normalize_test_id(test_id)
        clean_group_id = group_id.strip() if isinstance(group_id, str) and group_id.strip() else None
        query = """
        MATCH (c:DICOMConfiguration)
        WHERE (c.Action IS NULL OR c.Action <> 'DELETE')
          AND ($include_tests = true OR c.test_id IS NULL OR trim(toString(c.test_id)) = '')
          AND ($test_id IS NULL OR $test_id = '' OR c.test_id = $test_id)
          AND ($role IS NULL OR $role IN c.roles)
          AND ($is_promiscuous IS NULL OR c.is_promiscuous = $is_promiscuous)
          AND ($is_local IS NULL OR c.is_local = $is_local)
        OPTIONAL MATCH (s:System)-[r_sys:HAS_DICOM_CONFIGURATION]->(c)
        WHERE (r_sys.Action IS NULL OR r_sys.Action <> 'DELETE')
        OPTIONAL MATCH (c)-[r_grp:HAS_DICOM_CONFIGURATION|SERVES]-(g:Group)
        WHERE (r_grp.Action IS NULL OR r_grp.Action <> 'DELETE')
          AND (g.Action IS NULL OR g.Action <> 'DELETE')
        WITH c, collect(DISTINCT s.id) AS sys_ids, collect(DISTINCT properties(g)) AS groups
        WHERE ($system_id IS NULL OR $system_id IN sys_ids OR size(groups) > 0)
          AND (
            $group_id IS NULL
            OR any(
              grp IN groups
              WHERE grp.id = $group_id OR grp.name = $group_id OR toUpper(grp.code) = toUpper($group_id)
            )
          )
        RETURN properties(c) AS c, groups
        ORDER BY c.ae_title ASC
        """
        params = {
            "system_id": system_id,
            "role": role,
            "is_promiscuous": is_promiscuous,
            "is_local": is_local,
            "group_id": clean_group_id,
            "include_tests": include_tests or bool(test_id),
            "test_id": test_id,
        }
        records = await self.client.execute_query(query, params)
        res_list = []
        for r in records:
            c_dict = r.get("c", {})
            groups = r.get("groups") or []
            res_list.append(_dict_to_dicom_config(c_dict, groups=groups))
        return res_list

    async def get_configuration(self, config_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve single active DICOMConfiguration by deterministic ID."""
        query = """
        MATCH (c:DICOMConfiguration {id: $config_id})
        WHERE (c.Action IS NULL OR c.Action <> 'DELETE')
        OPTIONAL MATCH (c)-[r_grp:HAS_DICOM_CONFIGURATION|SERVES]-(g:Group)
        WHERE (r_grp.Action IS NULL OR r_grp.Action <> 'DELETE')
          AND (g.Action IS NULL OR g.Action <> 'DELETE')
        RETURN properties(c) AS c, collect(DISTINCT properties(g)) AS groups
        LIMIT 1
        """
        records = await self.client.execute_query(query, {"config_id": config_id})
        if not records:
            return None
        raw_c = records[0].get("c", {})
        groups = records[0].get("groups") or []
        return _dict_to_dicom_config(raw_c, groups=groups)

    async def create_configuration(
        self,
        config_data: DICOMConfigurationCreateRequest,
        assign_group: Optional[str] = None,
        acted_by: str = "system",
        system_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create or update a DICOMConfiguration node and attach it directly to its target Group."""
        norm_test_id = normalize_test_id(config_data.test_id)
        cfg_node = DICOMConfigurationNode(
            ae_title=config_data.ae_title,
            roles=config_data.roles,
            host=config_data.host,
            port=config_data.port,
            is_local=config_data.is_local,
            tls_enabled=config_data.tls_enabled,
            tls_cert_path=config_data.tls_cert_path,
            tls_key_path=config_data.tls_key_path,
            tls_ca_path=config_data.tls_ca_path,
            tls_ciphers=config_data.tls_ciphers,
            dicom_services=config_data.dicom_services,
            transfer_syntaxes=config_data.transfer_syntaxes,
            dicomweb_enabled=config_data.dicomweb_enabled,
            dicomweb_url_prefix=config_data.dicomweb_url_prefix,
            dicomweb_qido_enabled=config_data.dicomweb_qido_enabled,
            dicomweb_wado_enabled=config_data.dicomweb_wado_enabled,
            dicomweb_stow_enabled=config_data.dicomweb_stow_enabled,
            storage_root_uri=config_data.storage_root_uri,
            description=config_data.description,
            test_id=norm_test_id,
            ActedBy=acted_by,
        )

        # Resolve target Group by ID, name, or code (defaulting to 'GoSmart Health')
        effective_group_ident = assign_group if assign_group is not None else config_data.group_id
        if effective_group_ident is not None and isinstance(effective_group_ident, str):
            effective_group_ident = effective_group_ident.strip() or None

        if effective_group_ident is None:
            try:
                target_group = await group_service.resolve_target_group(identifier=None, test_id=norm_test_id)
            except RuntimeError:
                target_group = await group_service.get_or_create_catch_all_group(
                    test_id=norm_test_id,
                    acted_by=acted_by,
                )
        else:
            target_group = await group_service.resolve_target_group(
                identifier=effective_group_ident,
                test_id=norm_test_id,
            )

        target_group_id = target_group["id"]

        query = """
        MERGE (c:DICOMConfiguration {id: $cfg.id})
        ON CREATE SET c.ae_title = $cfg.ae_title,
                      c.roles = $cfg.roles,
                      c.host = $cfg.host,
                      c.port = $cfg.port,
                      c.is_local = $cfg.is_local,
                      c.is_promiscuous = $cfg.is_promiscuous,
                      c.tls_enabled = $cfg.tls_enabled,
                      c.tls_cert_path = $cfg.tls_cert_path,
                      c.tls_key_path = $cfg.tls_key_path,
                      c.tls_ca_path = $cfg.tls_ca_path,
                      c.tls_ciphers = $cfg.tls_ciphers,
                      c.dicom_services = $cfg.dicom_services,
                      c.transfer_syntaxes = $cfg.transfer_syntaxes,
                      c.dicomweb_enabled = $cfg.dicomweb_enabled,
                      c.dicomweb_url_prefix = $cfg.dicomweb_url_prefix,
                      c.dicomweb_qido_enabled = $cfg.dicomweb_qido_enabled,
                      c.dicomweb_wado_enabled = $cfg.dicomweb_wado_enabled,
                      c.dicomweb_stow_enabled = $cfg.dicomweb_stow_enabled,
                      c.storage_root_uri = $cfg.storage_root_uri,
                      c.description = $cfg.description,
                      c.test_id = $cfg.test_id,
                      c.Action = 'CREATE',
                      c.ActedAt = datetime({timezone: 'UTC'}),
                      c.ActedBy = $cfg.ActedBy,
                      c.CreatedAt = datetime({timezone: 'UTC'})
        ON MATCH SET  c.ae_title = $cfg.ae_title,
                      c.roles = $cfg.roles,
                      c.host = $cfg.host,
                      c.port = $cfg.port,
                      c.is_local = $cfg.is_local,
                      c.is_promiscuous = $cfg.is_promiscuous,
                      c.tls_enabled = $cfg.tls_enabled,
                      c.tls_cert_path = $cfg.tls_cert_path,
                      c.tls_key_path = $cfg.tls_key_path,
                      c.tls_ca_path = $cfg.tls_ca_path,
                      c.tls_ciphers = $cfg.tls_ciphers,
                      c.dicom_services = $cfg.dicom_services,
                      c.transfer_syntaxes = $cfg.transfer_syntaxes,
                      c.dicomweb_enabled = $cfg.dicomweb_enabled,
                      c.dicomweb_url_prefix = $cfg.dicomweb_url_prefix,
                      c.dicomweb_qido_enabled = $cfg.dicomweb_qido_enabled,
                      c.dicomweb_wado_enabled = $cfg.dicomweb_wado_enabled,
                      c.dicomweb_stow_enabled = $cfg.dicomweb_stow_enabled,
                      c.storage_root_uri = $cfg.storage_root_uri,
                      c.description = $cfg.description,
                      c.test_id = $cfg.test_id,
                      c.Action = CASE WHEN c.Action = 'DELETE' THEN 'RESTORE' ELSE 'UPDATE' END,
                      c.ActedAt = datetime({timezone: 'UTC'}),
                      c.ActedBy = $cfg.ActedBy,
                      c.UpdatedAt = datetime({timezone: 'UTC'})
        WITH c
        OPTIONAL MATCH (:System)-[stale_sys_rel:HAS_DICOM_CONFIGURATION]->(c)
        DELETE stale_sys_rel
        RETURN properties(c) AS c
        """

        records = await self.client.execute_query(
            query,
            {
                "cfg": cfg_node.model_dump(),
            },
        )
        raw_c = records[0].get("c", {}) if records else {}

        await group_service.associate_dicom_config_to_group(
            config_id=cfg_node.id,
            group_id=target_group_id,
            test_id=norm_test_id,
            acted_by=acted_by,
        )
        raw_c["group_id"] = target_group_id

        cfg_resp = _dict_to_dicom_config(raw_c, groups=[target_group])
        try:
            from gosmart_graphite.service.pynetdicom_service import pynetdicom_service

            pynetdicom_service.on_configuration_added(cfg_resp)
        except Exception as scp_err:
            logger.warning("scp_on_configuration_added_failed", error=str(scp_err))
        return cfg_resp

    async def update_configuration(
        self,
        config_id: str,
        update_data: DICOMConfigurationUpdateRequest,
        acted_by: str = "system",
    ) -> Optional[Dict[str, Any]]:
        """Update mutable fields of an existing DICOMConfiguration and its Group association."""
        query = """
        MATCH (c:DICOMConfiguration {id: $config_id})
        WHERE (c.Action IS NULL OR c.Action <> 'DELETE')
        SET c.ae_title = coalesce($ae_title, c.ae_title),
            c.roles = coalesce($roles, c.roles),
            c.host = coalesce($host, c.host),
            c.port = coalesce($port, c.port),
            c.is_local = coalesce($is_local, c.is_local),
            c.is_promiscuous = CASE WHEN $ae_title IS NOT NULL THEN ($ae_title = '*') ELSE c.is_promiscuous END,
            c.tls_enabled = coalesce($tls_enabled, c.tls_enabled),
            c.tls_cert_path = coalesce($tls_cert_path, c.tls_cert_path),
            c.tls_key_path = coalesce($tls_key_path, c.tls_key_path),
            c.tls_ca_path = coalesce($tls_ca_path, c.tls_ca_path),
            c.tls_ciphers = coalesce($tls_ciphers, c.tls_ciphers),
            c.dicom_services = coalesce($dicom_services, c.dicom_services),
            c.transfer_syntaxes = coalesce($transfer_syntaxes, c.transfer_syntaxes),
            c.dicomweb_enabled = coalesce($dicomweb_enabled, c.dicomweb_enabled),
            c.dicomweb_url_prefix = coalesce($dicomweb_url_prefix, c.dicomweb_url_prefix),
            c.dicomweb_qido_enabled = coalesce($dicomweb_qido_enabled, c.dicomweb_qido_enabled),
            c.dicomweb_wado_enabled = coalesce($dicomweb_wado_enabled, c.dicomweb_wado_enabled),
            c.dicomweb_stow_enabled = coalesce($dicomweb_stow_enabled, c.dicomweb_stow_enabled),
            c.storage_root_uri = coalesce($storage_root_uri, c.storage_root_uri),
            c.description = coalesce($description, c.description),
            c.Action = 'UPDATE',
            c.ActedAt = datetime({timezone: 'UTC'}),
            c.ActedBy = $acted_by,
            c.UpdatedAt = datetime({timezone: 'UTC'})
        RETURN properties(c) AS c
        """
        params = {
            "config_id": config_id,
            "ae_title": update_data.ae_title,
            "roles": update_data.roles,
            "host": update_data.host,
            "port": update_data.port,
            "is_local": update_data.is_local,
            "tls_enabled": update_data.tls_enabled,
            "tls_cert_path": update_data.tls_cert_path,
            "tls_key_path": update_data.tls_key_path,
            "tls_ca_path": update_data.tls_ca_path,
            "tls_ciphers": update_data.tls_ciphers,
            "dicom_services": update_data.dicom_services,
            "transfer_syntaxes": update_data.transfer_syntaxes,
            "dicomweb_enabled": update_data.dicomweb_enabled,
            "dicomweb_url_prefix": update_data.dicomweb_url_prefix,
            "dicomweb_qido_enabled": update_data.dicomweb_qido_enabled,
            "dicomweb_wado_enabled": update_data.dicomweb_wado_enabled,
            "dicomweb_stow_enabled": update_data.dicomweb_stow_enabled,
            "storage_root_uri": update_data.storage_root_uri,
            "description": update_data.description,
            "acted_by": acted_by,
        }
        records = await self.client.execute_query(query, params)
        if not records:
            return None
        raw_c = records[0].get("c", {})

        # Handle group association update if provided
        groups: List[Dict[str, Any]] = []
        if update_data.group_id is not None:
            assoc_res = await group_service.associate_dicom_config_to_group(
                config_id=config_id,
                group_id=update_data.group_id,
                test_id=raw_c.get("test_id"),
                acted_by=acted_by,
            )
            grp = assoc_res.get("group") or {}
            resolved_gid = grp.get("id") or update_data.group_id
            raw_c["group_id"] = resolved_gid
            if grp:
                groups = [grp]
        else:
            existing = await self.get_configuration(config_id)
            if existing:
                if existing.get("group_id"):
                    raw_c["group_id"] = existing["group_id"]
                if existing.get("groups"):
                    groups = existing["groups"]

        updated_cfg = _dict_to_dicom_config(raw_c, groups=groups)
        try:
            from gosmart_graphite.service.pynetdicom_service import pynetdicom_service

            pynetdicom_service.on_configuration_changed(config_id, updated_cfg)
        except Exception as scp_err:
            logger.warning("scp_on_configuration_changed_failed", error=str(scp_err))
        return updated_cfg

    async def delete_configuration(
        self,
        config_id: str,
        acted_by: str = "system",
        hard_delete: bool = False,
    ) -> bool:
        """Soft-delete (or hard-delete if requested) a DICOMConfiguration and negate relationships."""
        if hard_delete:
            del_cypher = """
            MATCH (c:DICOMConfiguration {id: $config_id})
            DETACH DELETE c
            RETURN count(c) AS cnt
            """
            recs = await self.client.execute_query(del_cypher, {"config_id": config_id})
            deleted = bool(recs and recs[0].get("cnt", 0) > 0)
            if deleted:
                try:
                    from gosmart_graphite.service.pynetdicom_service import pynetdicom_service

                    pynetdicom_service.on_configuration_removed(config_id)
                except Exception as scp_err:
                    logger.warning("scp_on_configuration_removed_failed", error=str(scp_err))
            return deleted

        find_query = """
        MATCH (c:DICOMConfiguration {id: $config_id})
        WHERE (c.Action IS NULL OR c.Action <> 'DELETE')
        OPTIONAL MATCH (owner)-[r_has:HAS_DICOM_CONFIGURATION]->(c)
        WHERE (owner:Group OR owner:System)
        OPTIONAL MATCH (c)-[r_serves:SERVES]->(g:Group)
        RETURN c.id AS cid,
               collect(DISTINCT owner.id) AS owner_ids,
               collect(DISTINCT g.id) AS served_group_ids
        LIMIT 1
        """
        recs = await self.client.execute_query(find_query, {"config_id": config_id})
        if not recs or not recs[0].get("cid"):
            return False

        owner_ids = [oid for oid in (recs[0].get("owner_ids") or []) if oid]
        served_group_ids = [gid for gid in (recs[0].get("served_group_ids") or []) if gid]

        neg_has = to_negated_relation_type("HAS_DICOM_CONFIGURATION")
        neg_serves = to_negated_relation_type("SERVES")

        has_swaps = [
            {
                "owner_id": oid,
                "new_rel_id": generate_deterministic_id(
                    {"start_node_id": oid, "type": neg_has, "end_node_id": config_id}
                ),
            }
            for oid in owner_ids
        ]
        serves_swaps = [
            {
                "group_id": gid,
                "new_rel_id": generate_deterministic_id(
                    {"start_node_id": config_id, "type": neg_serves, "end_node_id": gid}
                ),
            }
            for gid in served_group_ids
        ]

        async def _swap_tx(tx: Any) -> bool:
            res = await tx.run(
                """
                MATCH (c:DICOMConfiguration {id: $config_id})
                SET c.Action = 'DELETE',
                    c.ActedAt = datetime({timezone: 'UTC'}),
                    c.ActedBy = $acted_by
                RETURN count(c) AS cnt
                """,
                config_id=config_id,
                acted_by=acted_by,
            )
            rec = await res.single()
            if not rec or rec.get("cnt", 0) == 0:
                return False

            for item in has_swaps:
                await tx.run(
                    f"""
                    MATCH (owner {{id: $owner_id}})-[r:HAS_DICOM_CONFIGURATION]->
                          (c:DICOMConfiguration {{id: $config_id}})
                    WITH owner, c, r, properties(r) AS old_props
                    CREATE (owner)-[r_not:{neg_has}]->(c)
                    SET r_not = old_props,
                        r_not.id = $new_rel_id,
                        r_not.Action = 'DELETE',
                        r_not.ActedAt = datetime({{timezone: 'UTC'}}),
                        r_not.ActedBy = $acted_by
                    DELETE r
                    """,
                    owner_id=item["owner_id"],
                    config_id=config_id,
                    new_rel_id=item["new_rel_id"],
                    acted_by=acted_by,
                )

            for item in serves_swaps:
                await tx.run(
                    f"""
                    MATCH (c:DICOMConfiguration {{id: $config_id}})-[r:SERVES]->(g:Group {{id: $group_id}})
                    WITH c, g, r, properties(r) AS old_props
                    CREATE (c)-[r_not:{neg_serves}]->(g)
                    SET r_not = old_props,
                        r_not.id = $new_rel_id,
                        r_not.Action = 'DELETE',
                        r_not.ActedAt = datetime({{timezone: 'UTC'}}),
                        r_not.ActedBy = $acted_by
                    DELETE r
                    """,
                    config_id=config_id,
                    group_id=item["group_id"],
                    new_rel_id=item["new_rel_id"],
                    acted_by=acted_by,
                )
            return True

        success = await self.client.execute_write_tx(_swap_tx)
        if success:
            try:
                from gosmart_graphite.service.pynetdicom_service import pynetdicom_service

                pynetdicom_service.on_configuration_removed(config_id)
            except Exception as scp_err:
                logger.warning("scp_on_configuration_removed_failed", error=str(scp_err))
        return success

    async def restore_configuration(
        self,
        config_id: str,
        acted_by: str = "system",
    ) -> Optional[Dict[str, Any]]:
        """Restore a soft-deleted configuration by swapping relationships back and setting Action='RESTORE'."""
        neg_has = to_negated_relation_type("HAS_DICOM_CONFIGURATION")
        pos_has = to_positive_relation_type("HAS_DICOM_CONFIGURATION")
        neg_serves = to_negated_relation_type("SERVES")
        pos_serves = to_positive_relation_type("SERVES")

        find_rel_query = f"""
        MATCH (c:DICOMConfiguration {{id: $config_id}})
        WHERE c.Action = 'DELETE'
        OPTIONAL MATCH (owner)-[r_has:{neg_has}]->(c)
        WHERE (owner:Group OR owner:System)
        OPTIONAL MATCH (c)-[r_serves:{neg_serves}]->(g:Group)
        RETURN c.id AS cid,
               collect(DISTINCT owner.id) AS owner_ids,
               collect(DISTINCT g.id) AS served_group_ids
        LIMIT 1
        """
        rel_recs = await self.client.execute_query(find_rel_query, {"config_id": config_id})
        if not rel_recs or not rel_recs[0].get("cid"):
            return None

        owner_ids = [oid for oid in (rel_recs[0].get("owner_ids") or []) if oid]
        served_group_ids = [gid for gid in (rel_recs[0].get("served_group_ids") or []) if gid]

        has_swaps = [
            {
                "owner_id": oid,
                "new_rel_id": generate_deterministic_id(
                    {"start_node_id": oid, "type": pos_has, "end_node_id": config_id}
                ),
            }
            for oid in owner_ids
        ]
        serves_swaps = [
            {
                "group_id": gid,
                "new_rel_id": generate_deterministic_id(
                    {"start_node_id": config_id, "type": pos_serves, "end_node_id": gid}
                ),
            }
            for gid in served_group_ids
        ]

        async def _restore_tx(tx: Any) -> Optional[Dict[str, Any]]:
            for item in has_swaps:
                await tx.run(
                    f"""
                    MATCH (owner {{id: $owner_id}})-[r:{neg_has}]->(c:DICOMConfiguration {{id: $config_id}})
                    WITH owner, c, r, properties(r) AS old_props
                    CREATE (owner)-[r_pos:{pos_has}]->(c)
                    SET r_pos = old_props,
                        r_pos.id = $new_rel_id,
                        r_pos.Action = 'RESTORE',
                        r_pos.ActedAt = datetime({{timezone: 'UTC'}}),
                        r_pos.ActedBy = $acted_by
                    DELETE r
                    """,
                    owner_id=item["owner_id"],
                    config_id=config_id,
                    new_rel_id=item["new_rel_id"],
                    acted_by=acted_by,
                )

            for item in serves_swaps:
                await tx.run(
                    f"""
                    MATCH (c:DICOMConfiguration {{id: $config_id}})-[r:{neg_serves}]->(g:Group {{id: $group_id}})
                    WITH c, g, r, properties(r) AS old_props
                    CREATE (c)-[r_pos:{pos_serves}]->(g)
                    SET r_pos = old_props,
                        r_pos.id = $new_rel_id,
                        r_pos.Action = 'RESTORE',
                        r_pos.ActedAt = datetime({{timezone: 'UTC'}}),
                        r_pos.ActedBy = $acted_by
                    DELETE r
                    """,
                    config_id=config_id,
                    group_id=item["group_id"],
                    new_rel_id=item["new_rel_id"],
                    acted_by=acted_by,
                )

            res = await tx.run(
                """
                MATCH (c:DICOMConfiguration {id: $config_id})
                SET c.Action = 'RESTORE',
                    c.ActedAt = datetime({timezone: 'UTC'}),
                    c.ActedBy = $acted_by,
                    c.UpdatedAt = datetime({timezone: 'UTC'})
                WITH c
                OPTIONAL MATCH (c)-[r_grp:HAS_DICOM_CONFIGURATION|SERVES]-(g:Group)
                WHERE (r_grp.Action IS NULL OR r_grp.Action <> 'DELETE')
                  AND (g.Action IS NULL OR g.Action <> 'DELETE')
                RETURN properties(c) AS c, collect(DISTINCT properties(g)) AS groups
                """,
                config_id=config_id,
                acted_by=acted_by,
            )
            rec = await res.single()
            if not rec:
                return None
            return _dict_to_dicom_config(rec.get("c", {}), groups=rec.get("groups") or [])

        restored_cfg = await self.client.execute_write_tx(_restore_tx)
        if restored_cfg:
            try:
                from gosmart_graphite.service.pynetdicom_service import pynetdicom_service

                pynetdicom_service.on_configuration_added(restored_cfg)
            except Exception as scp_err:
                logger.warning("scp_on_configuration_restored_failed", error=str(scp_err))
        return restored_cfg

    async def cleanup_test_data(self, test_id: str) -> int:
        """Detach and delete all System and Configuration nodes tagged with test_id."""
        clean_test_id = normalize_test_id(test_id)
        if not clean_test_id:
            return 0
        try:
            from gosmart_graphite.service.pynetdicom_service import pynetdicom_service

            for inst in list(pynetdicom_service._instances.values()):
                if normalize_test_id(inst.all_configs.get("test_id")) == clean_test_id:
                    pynetdicom_service.on_configuration_removed(inst.config_id)
        except Exception:
            pass
        query = """
        MATCH (n)
        WHERE (n:System OR n:DICOMConfiguration) AND n.test_id = $test_id
        DETACH DELETE n
        RETURN count(n) AS cnt
        """
        records = await self.client.execute_query(query, {"test_id": clean_test_id})
        deleted = records[0].get("cnt", 0) if records else 0
        logger.info("system_test_data_cleaned", test_id=clean_test_id, deleted_count=deleted)
        return deleted

    async def cleanup_all_test_data(
        self,
        test_id: Optional[str] = None,
        labels: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Detach and delete test data across specified node labels where test_id is non-null/non-empty."""
        import re

        clean_test_id = normalize_test_id(test_id)
        raw_labels = labels if labels is not None else DEFAULT_TEST_CLEANUP_LABELS
        target_labels = [lbl.strip() for lbl in raw_labels if lbl and lbl.strip()]

        valid_identifier = re.compile(r"^[A-Za-z0-9_]+$")
        target_labels = [lbl for lbl in target_labels if valid_identifier.match(lbl)]
        if not target_labels:
            target_labels = list(DEFAULT_TEST_CLEANUP_LABELS)

        # 1. Drain and stop any running DICOM SCP instances matching test configs
        try:
            from gosmart_graphite.service.pynetdicom_service import pynetdicom_service

            for inst in list(pynetdicom_service._instances.values()):
                inst_test_id = normalize_test_id(inst.all_configs.get("test_id"))
                if inst_test_id and (clean_test_id is None or inst_test_id == clean_test_id):
                    pynetdicom_service.on_configuration_removed(inst.config_id)
        except Exception as e:
            logger.warning("cleanup_test_scp_drain_failed", error=str(e))

        # 2. Query breakdown of matching nodes by label before deletion
        count_query = """
        UNWIND $target_labels AS target_label
        CALL (target_label) {
            MATCH (n)
            WHERE target_label IN labels(n)
              AND n.test_id IS NOT NULL
              AND trim(toString(n.test_id)) <> ''
              AND ($test_id IS NULL OR n.test_id = $test_id)
            RETURN count(n) AS cnt
        }
        RETURN target_label AS label, cnt
        """
        count_recs = await self.client.execute_query(
            count_query,
            {"target_labels": target_labels, "test_id": clean_test_id},
        )
        deleted_by_label: Dict[str, int] = {r["label"]: r["cnt"] for r in count_recs if r.get("cnt", 0) > 0}
        total_to_delete = sum(deleted_by_label.values())

        # 3. Detach and delete matching nodes
        if total_to_delete > 0:
            delete_query = """
            MATCH (n)
            WHERE any(lbl IN labels(n) WHERE lbl IN $target_labels)
              AND n.test_id IS NOT NULL
              AND trim(toString(n.test_id)) <> ''
              AND ($test_id IS NULL OR n.test_id = $test_id)
            DETACH DELETE n
            """
            await self.client.execute_query(
                delete_query,
                {"target_labels": target_labels, "test_id": clean_test_id},
            )

        logger.info(
            "test_cleanup_completed",
            test_id=clean_test_id,
            total_deleted=total_to_delete,
            deleted_by_label=deleted_by_label,
            labels_cleaned=target_labels,
        )

        return {
            "success": True,
            "message": f"Test data cleanup completed successfully. Deleted {total_to_delete} test nodes.",
            "total_deleted": total_to_delete,
            "deleted_by_label": deleted_by_label,
            "labels_cleaned": target_labels,
            "test_id": clean_test_id,
        }


system_service = SystemService()
