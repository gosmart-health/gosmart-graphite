from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.config import get_settings
from core.logging import get_logger
from db.neo4j import Neo4jClient, neo4j_client
from model.graph import (
    DICOMConfigurationNode,
    HasDicomConfigurationRelation,
    SystemNode,
    generate_deterministic_id,
    to_negated_relation_type,
    to_positive_relation_type,
)
from model.system import (
    DICOMConfigurationCreateRequest,
    DICOMConfigurationUpdateRequest,
    SystemUpdateRequest,
)

logger = get_logger("gosmart_gs.system")


def _clean_neo4j_value(val: Any) -> Any:
    """Normalize Neo4j specific types (e.g. neo4j.time.DateTime) to standard Python types."""
    if hasattr(val, "to_native"):
        return val.to_native()
    if isinstance(val, dict):
        return {k: _clean_neo4j_value(v) for k, v in val.items()}
    if isinstance(val, list):
        return [_clean_neo4j_value(v) for v in val]
    return val


def _normalize_test_id(test_id: Optional[str]) -> Optional[str]:
    """Normalize test_id so empty strings or whitespace are safely converted to None."""
    if test_id and isinstance(test_id, str):
        stripped = test_id.strip()
        return stripped if stripped else None
    return None


def _dict_to_dicom_config(data: Dict[str, Any]) -> Dict[str, Any]:
    """Map raw Neo4j node properties to a standardized DICOMConfigurationResponse dictionary."""
    clean = _clean_neo4j_value(data)
    roles = clean.get("roles", [])
    if isinstance(roles, str):
        roles = [roles]
    dicom_services = clean.get("dicom_services", ["*"])
    if isinstance(dicom_services, str):
        dicom_services = [dicom_services]
    transfer_syntaxes = clean.get("transfer_syntaxes", ["*"])
    if isinstance(transfer_syntaxes, str):
        transfer_syntaxes = [transfer_syntaxes]

    return {
        "id": clean.get("id", ""),
        "ae_title": clean.get("ae_title", "*"),
        "roles": roles,
        "host": clean.get("host", "0.0.0.0"),
        "port": clean.get("port", 11112),
        "is_promiscuous": clean.get("is_promiscuous", clean.get("ae_title") == "*"),
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
        "description": clean.get("description"),
        "test_id": clean.get("test_id"),
        "action": clean.get("Action", "CREATE"),
        "created_at": clean.get("CreatedAt"),
        "updated_at": clean.get("UpdatedAt"),
    }


def _dict_to_system_response(data: Dict[str, Any], configs: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Map raw Neo4j node properties to a standardized SystemResponse dictionary."""
    clean = _clean_neo4j_value(data)
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
        test_id = _normalize_test_id(test_id)
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

        promiscuous_config: Dict[str, Any] = {}
        if promiscuous_records:
            promiscuous_config = _dict_to_dicom_config(promiscuous_records[0].get("c", {}))
            logger.info("promiscuous_dicom_config_exists", config_id=promiscuous_config.get("id"))
        else:
            # Create default promiscuous DICOM entry
            cfg_node = DICOMConfigurationNode(
                ae_title="*",
                roles=["SCP"],
                host="0.0.0.0",
                port=settings.DICOM_SCP_PORT,
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
            from service.pynetdicom_service import pynetdicom_service

            pynetdicom_service.sync_from_configurations(configs)
        except Exception as scp_err:
            logger.warning("scp_bootstrap_sync_failed", error=str(scp_err))

        return {
            "success": True,
            "message": "System and promiscuous DICOM configuration verified/bootstrapped successfully",
            "system": sys_resp,
            "promiscuous_config": promiscuous_config,
        }

    async def get_system(
        self,
        include_configs: bool = True,
        test_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Retrieve root System node and linked active configurations."""
        test_id = _normalize_test_id(test_id)
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
        test_id = _normalize_test_id(test_id)
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
        include_tests: bool = False,
        test_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List active DICOM configurations linked to System or matching filters."""
        test_id = _normalize_test_id(test_id)
        query = """
        MATCH (s:System)-[:HAS_DICOM_CONFIGURATION]->(c:DICOMConfiguration)
        WHERE (c.Action IS NULL OR c.Action <> 'DELETE')
          AND ($system_id IS NULL OR s.id = $system_id)
          AND ($include_tests = true OR c.test_id IS NULL OR c.test_id = '')
          AND ($test_id IS NULL OR $test_id = '' OR c.test_id = $test_id)
          AND ($role IS NULL OR $role IN c.roles)
          AND ($is_promiscuous IS NULL OR c.is_promiscuous = $is_promiscuous)
        RETURN properties(c) AS c
        ORDER BY c.ae_title ASC
        """
        params = {
            "system_id": system_id,
            "role": role,
            "is_promiscuous": is_promiscuous,
            "include_tests": include_tests,
            "test_id": test_id,
        }
        records = await self.client.execute_query(query, params)
        return [_dict_to_dicom_config(r.get("c", {})) for r in records]

    async def get_configuration(self, config_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve single active DICOMConfiguration by deterministic ID."""
        query = """
        MATCH (c:DICOMConfiguration {id: $config_id})
        WHERE (c.Action IS NULL OR c.Action <> 'DELETE')
        RETURN properties(c) AS c
        LIMIT 1
        """
        records = await self.client.execute_query(query, {"config_id": config_id})
        if not records:
            return None
        return _dict_to_dicom_config(records[0].get("c", {}))

    async def create_configuration(
        self,
        config_data: DICOMConfigurationCreateRequest,
        acted_by: str = "system",
        system_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a new DICOMConfiguration node and link to the root System."""
        norm_test_id = _normalize_test_id(config_data.test_id)
        cfg_node = DICOMConfigurationNode(
            ae_title=config_data.ae_title,
            roles=config_data.roles,
            host=config_data.host,
            port=config_data.port,
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
            description=config_data.description,
            test_id=norm_test_id,
            ActedBy=acted_by,
        )

        # Resolve system node
        if not system_id:
            sys_query = """
            MATCH (s:System)
            WHERE (($test_id IS NULL OR $test_id = '') AND (s.test_id IS NULL OR s.test_id = ''))
               OR ($test_id IS NOT NULL AND $test_id <> '' AND s.test_id = $test_id)
            RETURN s.id AS id
            LIMIT 1
            """
            sys_rec = await self.client.execute_query(sys_query, {"test_id": norm_test_id})
            if sys_rec:
                system_id = sys_rec[0].get("id")
            else:
                # Bootstrap system if missing
                boot = await self.bootstrap_system(acted_by=acted_by, test_id=norm_test_id)
                system_id = boot["system"]["id"]

        rel = HasDicomConfigurationRelation(
            start_node_id=system_id,
            end_node_id=cfg_node.id,
            test_id=norm_test_id,
            ActedBy=acted_by,
        )

        query = """
        MATCH (s:System {id: $system_id})
        MERGE (c:DICOMConfiguration {id: $cfg.id})
        ON CREATE SET c.ae_title = $cfg.ae_title,
                      c.roles = $cfg.roles,
                      c.host = $cfg.host,
                      c.port = $cfg.port,
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

        records = await self.client.execute_query(
            query,
            {
                "system_id": system_id,
                "cfg": cfg_node.model_dump(),
                "rel": rel.model_dump(),
            },
        )
        raw_c = records[0].get("c", {}) if records else {}
        cfg_resp = _dict_to_dicom_config(raw_c)
        try:
            from service.pynetdicom_service import pynetdicom_service

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
        """Update mutable fields of an existing DICOMConfiguration."""
        query = """
        MATCH (c:DICOMConfiguration {id: $config_id})
        WHERE (c.Action IS NULL OR c.Action <> 'DELETE')
        SET c.ae_title = coalesce($ae_title, c.ae_title),
            c.roles = coalesce($roles, c.roles),
            c.host = coalesce($host, c.host),
            c.port = coalesce($port, c.port),
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
            "description": update_data.description,
            "acted_by": acted_by,
        }
        records = await self.client.execute_query(query, params)
        if not records:
            return None
        updated_cfg = _dict_to_dicom_config(records[0].get("c", {}))
        try:
            from service.pynetdicom_service import pynetdicom_service

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
        """Soft-delete (or hard-delete if requested) a DICOMConfiguration and negate relationship."""
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
                    from service.pynetdicom_service import pynetdicom_service

                    pynetdicom_service.on_configuration_removed(config_id)
                except Exception as scp_err:
                    logger.warning("scp_on_configuration_removed_failed", error=str(scp_err))
            return deleted

        # Soft delete: swap relation to NOT_HAS_DICOM_CONFIGURATION and set Action='DELETE'
        find_rel_query = """
        MATCH (s:System)-[r:HAS_DICOM_CONFIGURATION]->(c:DICOMConfiguration {id: $config_id})
        RETURN s.id AS sys_id
        LIMIT 1
        """
        rel_recs = await self.client.execute_query(find_rel_query, {"config_id": config_id})
        if not rel_recs:
            return False

        sys_id = rel_recs[0].get("sys_id")
        neg_type = to_negated_relation_type("HAS_DICOM_CONFIGURATION")
        new_rel_id = generate_deterministic_id({"start_node_id": sys_id, "type": neg_type, "end_node_id": config_id})

        swap_query = f"""
        MATCH (s:System {{id: $sys_id}})-[r:HAS_DICOM_CONFIGURATION]->(c:DICOMConfiguration {{id: $config_id}})
        WITH s, c, r, properties(r) AS old_props
        CREATE (s)-[r_not:{neg_type}]->(c)
        SET r_not = old_props,
            r_not.id = $new_rel_id,
            r_not.Action = 'DELETE',
            r_not.ActedAt = datetime({{timezone: 'UTC'}}),
            r_not.ActedBy = $acted_by
        DELETE r
        SET c.Action = 'DELETE',
            c.ActedAt = datetime({{timezone: 'UTC'}}),
            c.ActedBy = $acted_by
        RETURN count(c) AS cnt
        """

        async def _swap_tx(tx: Any) -> bool:
            res = await tx.run(
                swap_query,
                sys_id=sys_id,
                config_id=config_id,
                new_rel_id=new_rel_id,
                acted_by=acted_by,
            )
            rec = await res.single()
            return bool(rec and rec.get("cnt", 0) > 0)

        success = await self.client.execute_write_tx(_swap_tx)
        if success:
            try:
                from service.pynetdicom_service import pynetdicom_service

                pynetdicom_service.on_configuration_removed(config_id)
            except Exception as scp_err:
                logger.warning("scp_on_configuration_removed_failed", error=str(scp_err))
        return success

    async def restore_configuration(
        self,
        config_id: str,
        acted_by: str = "system",
    ) -> Optional[Dict[str, Any]]:
        """Restore a soft-deleted configuration by swapping relationship back and setting Action='RESTORE'."""
        neg_type = to_negated_relation_type("HAS_DICOM_CONFIGURATION")
        pos_type = to_positive_relation_type("HAS_DICOM_CONFIGURATION")

        find_rel_query = f"""
        MATCH (s:System)-[r:{neg_type}]->(c:DICOMConfiguration {{id: $config_id}})
        RETURN s.id AS sys_id
        LIMIT 1
        """
        rel_recs = await self.client.execute_query(find_rel_query, {"config_id": config_id})
        if not rel_recs:
            return None

        sys_id = rel_recs[0].get("sys_id")
        new_rel_id = generate_deterministic_id({"start_node_id": sys_id, "type": pos_type, "end_node_id": config_id})

        restore_query = f"""
        MATCH (s:System {{id: $sys_id}})-[r:{neg_type}]->(c:DICOMConfiguration {{id: $config_id}})
        WITH s, c, r, properties(r) AS old_props
        CREATE (s)-[r_pos:{pos_type}]->(c)
        SET r_pos = old_props,
            r_pos.id = $new_rel_id,
            r_pos.Action = 'RESTORE',
            r_pos.ActedAt = datetime({{timezone: 'UTC'}}),
            r_pos.ActedBy = $acted_by
        DELETE r
        SET c.Action = 'RESTORE',
            c.ActedAt = datetime({{timezone: 'UTC'}}),
            c.ActedBy = $acted_by
        RETURN properties(c) AS c
        """

        async def _restore_tx(tx: Any) -> Optional[Dict[str, Any]]:
            res = await tx.run(
                restore_query,
                sys_id=sys_id,
                config_id=config_id,
                new_rel_id=new_rel_id,
                acted_by=acted_by,
            )
            rec = await res.single()
            if not rec:
                return None
            return _dict_to_dicom_config(rec.get("c", {}))

        restored_cfg = await self.client.execute_write_tx(_restore_tx)
        if restored_cfg:
            try:
                from service.pynetdicom_service import pynetdicom_service

                pynetdicom_service.on_configuration_added(restored_cfg)
            except Exception as scp_err:
                logger.warning("scp_on_configuration_restored_failed", error=str(scp_err))
        return restored_cfg

    async def cleanup_test_data(self, test_id: str) -> int:
        """Detach and delete all System and Configuration nodes tagged with test_id."""
        clean_test_id = _normalize_test_id(test_id)
        if not clean_test_id:
            return 0
        try:
            from service.pynetdicom_service import pynetdicom_service

            for inst in list(pynetdicom_service._instances.values()):
                if _normalize_test_id(inst.config.get("test_id")) == clean_test_id:
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

        clean_test_id = _normalize_test_id(test_id)
        raw_labels = labels if labels is not None else DEFAULT_TEST_CLEANUP_LABELS
        target_labels = [lbl.strip() for lbl in raw_labels if lbl and lbl.strip()]

        valid_identifier = re.compile(r"^[A-Za-z0-9_]+$")
        target_labels = [lbl for lbl in target_labels if valid_identifier.match(lbl)]
        if not target_labels:
            target_labels = list(DEFAULT_TEST_CLEANUP_LABELS)

        # 1. Drain and stop any running DICOM SCP instances matching test configs
        try:
            from service.pynetdicom_service import pynetdicom_service

            for inst in list(pynetdicom_service._instances.values()):
                inst_test_id = _normalize_test_id(inst.config.get("test_id"))
                if inst_test_id and (clean_test_id is None or inst_test_id == clean_test_id):
                    pynetdicom_service.on_configuration_removed(inst.config_id)
        except Exception as e:
            logger.warning("cleanup_test_scp_drain_failed", error=str(e))

        # 2. Query breakdown of matching nodes by label before deletion
        count_query = """
        UNWIND $target_labels AS target_label
        CALL {
            WITH target_label
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
