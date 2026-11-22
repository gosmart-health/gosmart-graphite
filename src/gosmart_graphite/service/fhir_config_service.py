"""Service for managing group-scoped FHIR configurations in Neo4j."""

from __future__ import annotations

from typing import Any, Dict, List, LiteralString, Optional

from gosmart_graphite.actor.audit_actor import record_audit
from gosmart_graphite.core.auth_dependency import get_active_group_id
from gosmart_graphite.core.logging import get_logger
from gosmart_graphite.db.neo4j import Neo4jClient, neo4j_client
from gosmart_graphite.model.audit import AuditAction
from gosmart_graphite.model.auditable import to_negated_relation_type
from gosmart_graphite.model.fhir_config import (
    FHIRConfigurationCreateRequest,
    HasFhirConfigRelation,
)
from gosmart_graphite.model.graph import FHIRConfigurationNode
from gosmart_graphite.service.system_service import clean_neo4j_value, normalize_test_id

logger = get_logger("gosmart_gs.fhir_config")

DEFAULT_FHIR_GROUP_NAME = "GoSmart Health"


def _dict_to_fhir_config_model(data: Dict[str, Any]) -> FHIRConfigurationNode:
    """Convert raw Neo4j node properties to a FHIRConfigurationNode domain model."""
    clean = clean_neo4j_value(data)
    return FHIRConfigurationNode(**clean)


def _dict_to_fhir_config_response(
    data: Dict[str, Any],
    groups: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Map raw Neo4j FHIRConfiguration properties into a standardized response dictionary."""
    clean = clean_neo4j_value(data)

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

    return {
        "id": clean.get("id", ""),
        "url": clean.get("url", ""),
        "accession_system": clean.get("accession_system", ""),
        "patient_id_system": clean.get("patient_id_system", ""),
        "poll_sec": clean.get("poll_sec", 120),
        "group_ids": group_ids,
        "groups": group_summaries,
        "test_id": clean.get("test_id"),
        "action": clean.get("Action", "CREATE"),
        "created_at": clean.get("CreatedAt"),
        "updated_at": clean.get("UpdatedAt"),
    }


def _node_props_payload(cfg_node: FHIRConfigurationNode) -> Dict[str, Any]:
    """Build Neo4j-safe scalar property dictionary excluding audit timestamps managed in Cypher."""
    return {
        "id": cfg_node.id,
        "url": cfg_node.url,
        "accession_system": cfg_node.accession_system,
        "patient_id_system": cfg_node.patient_id_system,
        "poll_sec": cfg_node.poll_sec,
        "test_id": cfg_node.test_id,
        "ActedBy": cfg_node.ActedBy,
    }


class FHIRConfigService:
    """Service managing Neo4j-persisted, group-scoped FHIR configurations."""

    _ANCESTOR_RESOLVE_CYPHER: LiteralString = """
    MATCH path = (start_g:Group {id: $group_id})-[:BELONGS_TO*0..]->(anc:Group)
    WHERE (start_g.Action IS NULL OR start_g.Action <> 'DELETE')
      AND coalesce(start_g.is_active, true) = true
      AND all(
        n IN nodes(path)
        WHERE 'Group' IN labels(n)
          AND (n.Action IS NULL OR n.Action <> 'DELETE')
          AND coalesce(n.is_active, true) = true
      )
      AND all(rel IN relationships(path) WHERE rel.Action IS NULL OR rel.Action <> 'DELETE')
    MATCH (anc)-[r:HAS_FHIR_CONFIG]->(f:FHIRConfiguration)
    WHERE (r.Action IS NULL OR r.Action <> 'DELETE')
      AND (f.Action IS NULL OR f.Action <> 'DELETE')
      AND (
        (($test_id IS NULL OR $test_id = '') AND (f.test_id IS NULL OR trim(toString(f.test_id)) = ''))
        OR ($test_id IS NOT NULL AND $test_id <> '' AND f.test_id = $test_id)
      )
    WITH f, anc, min(length(path)) AS depth
    ORDER BY depth ASC
    RETURN properties(f) AS config, anc.id AS group_id, depth
    """

    _DEFAULT_GROUP_RESOLVE_CYPHER: LiteralString = """
    MATCH (g:Group {name: $default_name})-[r:HAS_FHIR_CONFIG]->(f:FHIRConfiguration)
    WHERE (g.Action IS NULL OR g.Action <> 'DELETE')
      AND coalesce(g.is_active, true) = true
      AND (r.Action IS NULL OR r.Action <> 'DELETE')
      AND (f.Action IS NULL OR f.Action <> 'DELETE')
      AND (
        (
          ($test_id IS NULL OR $test_id = '')
          AND (f.test_id IS NULL OR trim(toString(f.test_id)) = '')
          AND (g.test_id IS NULL OR trim(toString(g.test_id)) = '')
        )
        OR ($test_id IS NOT NULL AND $test_id <> '' AND f.test_id = $test_id AND g.test_id = $test_id)
      )
    RETURN properties(f) AS config, g.id AS group_id
    """

    def __init__(self, client: Optional[Neo4jClient] = None) -> None:
        self.client = client or neo4j_client

    @staticmethod
    def _build_resolve_group_query(group_id: Optional[str]) -> tuple[LiteralString, Optional[str]]:
        clean_gid = group_id.strip() if isinstance(group_id, str) and group_id.strip() else None
        if clean_gid is None:
            query: LiteralString = """
            MATCH (g:Group)
            WHERE g.name = $default_name
              AND (g.Action IS NULL OR g.Action <> 'DELETE')
              AND coalesce(g.is_active, true) = true
              AND (
                (($test_id IS NULL OR $test_id = '') AND (g.test_id IS NULL OR trim(toString(g.test_id)) = ''))
                OR ($test_id IS NOT NULL AND $test_id <> '' AND g.test_id = $test_id)
              )
            RETURN properties(g) AS group
            """
            return query, None

        query_by_ident: LiteralString = """
        MATCH (g:Group)
        WHERE (g.id = $group_id OR g.name = $group_id OR toUpper(g.code) = toUpper($group_id))
          AND (g.Action IS NULL OR g.Action <> 'DELETE')
          AND coalesce(g.is_active, true) = true
          AND (
            (($test_id IS NULL OR $test_id = '') AND (g.test_id IS NULL OR trim(toString(g.test_id)) = ''))
            OR ($test_id IS NOT NULL AND $test_id <> '' AND g.test_id = $test_id)
          )
        RETURN properties(g) AS group
        """
        return query_by_ident, clean_gid

    @staticmethod
    def _validate_resolved_group_records(
        records: List[Dict[str, Any]],
        clean_gid: Optional[str],
    ) -> Dict[str, Any]:
        target_label = clean_gid or DEFAULT_FHIR_GROUP_NAME
        if not records:
            if clean_gid is None:
                raise RuntimeError(
                    f"Default group '{DEFAULT_FHIR_GROUP_NAME}' not found in Neo4j. "
                    "Ensure system bootstrap has run or specify --assign <group_id>."
                )
            raise RuntimeError(f"Target group '{target_label}' not found in Neo4j.")
        if len(records) > 1:
            raise RuntimeError(
                f"Ambiguous group match for '{target_label}': found {len(records)} active Group nodes. "
                "Please specify a unique Group ID."
            )
        return clean_neo4j_value(records[0].get("group", records[0]))

    async def resolve_target_group(
        self,
        group_id: Optional[str] = None,
        test_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Resolve a single active Group by id/name/code, or default 'GoSmart Health'.

        Raises RuntimeError when 0 or > 1 matching active groups exist.
        """
        norm_test_id = normalize_test_id(test_id)
        query, clean_gid = self._build_resolve_group_query(group_id)
        records = await self.client.execute_query(
            query,
            {
                "group_id": clean_gid,
                "default_name": DEFAULT_FHIR_GROUP_NAME,
                "test_id": norm_test_id,
            },
        )
        return self._validate_resolved_group_records(records, clean_gid)

    def resolve_target_group_sync(
        self,
        group_id: Optional[str] = None,
        test_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Synchronously resolve a single active Group by id/name/code, or default 'GoSmart Health'."""
        norm_test_id = normalize_test_id(test_id)
        query, clean_gid = self._build_resolve_group_query(group_id)
        driver = self.client.get_sync_driver()
        with driver.session(database=self.client.settings.DB_DATABASE) as session:
            result = session.run(
                query,
                group_id=clean_gid,
                default_name=DEFAULT_FHIR_GROUP_NAME,
                test_id=norm_test_id,
            )
            records = [r.data() for r in result]
        return self._validate_resolved_group_records(records, clean_gid)

    @staticmethod
    def _pick_ancestor_record(
        records: List[Dict[str, Any]],
        group_id: str,
    ) -> Optional[FHIRConfigurationNode]:
        if not records:
            return None
        min_depth = records[0]["depth"]
        at_min = [r for r in records if r["depth"] == min_depth]
        distinct_ids = {r["config"].get("id") for r in at_min if r.get("config")}
        if len(distinct_ids) > 1:
            raise RuntimeError(f"Ambiguous FHIRConfiguration at depth {min_depth} starting from group '{group_id}'.")
        return _dict_to_fhir_config_model(at_min[0]["config"])

    @staticmethod
    def _pick_default_record(records: List[Dict[str, Any]]) -> Optional[FHIRConfigurationNode]:
        if not records:
            return None
        distinct_ids = {r["config"].get("id") for r in records if r.get("config")}
        if len(distinct_ids) > 1:
            raise RuntimeError(f"Ambiguous FHIRConfiguration on default group '{DEFAULT_FHIR_GROUP_NAME}'.")
        return _dict_to_fhir_config_model(records[0]["config"])

    async def resolve_config_for_group(
        self,
        group_id: Optional[str] = None,
        test_id: Optional[str] = None,
    ) -> Optional[FHIRConfigurationNode]:
        """Resolve an active FHIRConfiguration by traversing upward from group_id, falling back to 'GoSmart Health'."""
        norm_test_id = normalize_test_id(test_id)
        effective_group_id = (
            group_id.strip() if isinstance(group_id, str) and group_id.strip() else get_active_group_id()
        )

        if effective_group_id:
            records = await self.client.execute_query(
                self._ANCESTOR_RESOLVE_CYPHER,
                {"group_id": effective_group_id, "test_id": norm_test_id},
            )
            picked = self._pick_ancestor_record(records, effective_group_id)
            if picked is not None:
                return picked

        default_records = await self.client.execute_query(
            self._DEFAULT_GROUP_RESOLVE_CYPHER,
            {"default_name": DEFAULT_FHIR_GROUP_NAME, "test_id": norm_test_id},
        )
        return self._pick_default_record(default_records)

    def resolve_config_for_group_sync(
        self,
        group_id: Optional[str] = None,
        test_id: Optional[str] = None,
    ) -> Optional[FHIRConfigurationNode]:
        """Synchronously resolve an active FHIRConfiguration upward from group_id, falling back to 'GoSmart Health'."""
        norm_test_id = normalize_test_id(test_id)
        effective_group_id = (
            group_id.strip() if isinstance(group_id, str) and group_id.strip() else get_active_group_id()
        )

        driver = self.client.get_sync_driver()
        with driver.session(database=self.client.settings.DB_DATABASE) as session:
            if effective_group_id:
                res = session.run(
                    self._ANCESTOR_RESOLVE_CYPHER,
                    group_id=effective_group_id,
                    test_id=norm_test_id,
                )
                picked = self._pick_ancestor_record([r.data() for r in res], effective_group_id)
                if picked is not None:
                    return picked

            def_res = session.run(
                self._DEFAULT_GROUP_RESOLVE_CYPHER,
                default_name=DEFAULT_FHIR_GROUP_NAME,
                test_id=norm_test_id,
            )
            return self._pick_default_record([r.data() for r in def_res])

    @staticmethod
    def _parse_create_request(
        config_data: FHIRConfigurationCreateRequest | FHIRConfigurationNode | Dict[str, Any],
        assign_group_id: Optional[str],
        test_id: Optional[str],
    ) -> tuple[FHIRConfigurationCreateRequest, Optional[str], Optional[str]]:
        if isinstance(config_data, dict):
            req = FHIRConfigurationCreateRequest(**config_data)
        elif isinstance(config_data, FHIRConfigurationNode):
            req = FHIRConfigurationCreateRequest(
                url=config_data.url,
                accession_system=config_data.accession_system,
                patient_id_system=config_data.patient_id_system,
                poll_sec=config_data.poll_sec,
                test_id=config_data.test_id,
            )
        else:
            req = config_data

        if assign_group_id is not None:
            clean_assign = assign_group_id.strip() if isinstance(assign_group_id, str) else assign_group_id
            effective_group_id = clean_assign or None
        else:
            effective_group_id = req.group_id

        norm_test_id = normalize_test_id(test_id if test_id is not None else req.test_id)
        return req, effective_group_id, norm_test_id

    async def create_fhir_config(
        self,
        config_data: FHIRConfigurationCreateRequest | FHIRConfigurationNode | Dict[str, Any],
        assign_group_id: Optional[str] = None,
        acted_by: str = "system",
        test_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a FHIRConfiguration node and assign it via (:Group)-[:HAS_FHIR_CONFIG]->(:FHIRConfiguration)."""
        req, effective_group_id, norm_test_id = self._parse_create_request(
            config_data=config_data,
            assign_group_id=assign_group_id,
            test_id=test_id,
        )
        target_group = await self.resolve_target_group(group_id=effective_group_id, test_id=norm_test_id)
        target_group_id = target_group["id"]

        cfg_node = FHIRConfigurationNode(
            accession_system=req.accession_system,
            patient_id_system=req.patient_id_system,
            url=req.url,
            poll_sec=req.poll_sec,
            test_id=norm_test_id,
            ActedBy=acted_by,
        )
        props = _node_props_payload(cfg_node)
        rel = HasFhirConfigRelation(
            start_node_id=target_group_id,
            end_node_id=cfg_node.id,
            test_id=norm_test_id,
            ActedBy=acted_by,
        )
        neg_type = to_negated_relation_type("HAS_FHIR_CONFIG")

        async def _create_tx(tx: Any) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
            res = await tx.run(
                f"""
                MATCH (tg:Group {{id: $target_group_id}})
                MERGE (f:FHIRConfiguration {{id: $props.id}})
                ON CREATE SET f += $props,
                              f.Action = 'CREATE',
                              f.ActedAt = datetime({{timezone: 'UTC'}}),
                              f.CreatedAt = datetime({{timezone: 'UTC'}})
                ON MATCH SET  f += $props,
                              f.Action = CASE WHEN f.Action = 'DELETE' THEN 'RESTORE' ELSE 'UPDATE' END,
                              f.ActedAt = datetime({{timezone: 'UTC'}}),
                              f.UpdatedAt = datetime({{timezone: 'UTC'}})
                WITH tg, f
                OPTIONAL MATCH (tg)-[stale_not:{neg_type}]->(f)
                DELETE stale_not
                WITH tg, f
                MERGE (tg)-[r:HAS_FHIR_CONFIG {{id: $rel_id}}]->(f)
                ON CREATE SET r.test_id = $test_id,
                              r.Action = 'CREATE',
                              r.ActedAt = datetime({{timezone: 'UTC'}}),
                              r.ActedBy = $acted_by,
                              r.CreatedAt = datetime({{timezone: 'UTC'}})
                ON MATCH SET  r.test_id = coalesce($test_id, r.test_id),
                              r.Action = 'UPDATE',
                              r.ActedAt = datetime({{timezone: 'UTC'}}),
                              r.ActedBy = $acted_by,
                              r.UpdatedAt = datetime({{timezone: 'UTC'}})
                WITH f
                OPTIONAL MATCH (g:Group)-[gr:HAS_FHIR_CONFIG]->(f)
                WHERE (gr.Action IS NULL OR gr.Action <> 'DELETE')
                  AND (g.Action IS NULL OR g.Action <> 'DELETE')
                RETURN properties(f) AS config, collect(DISTINCT properties(g)) AS groups
                """,
                target_group_id=target_group_id,
                props=props,
                rel_id=rel.id,
                test_id=norm_test_id,
                acted_by=acted_by,
            )
            rec = await res.single()
            if not rec:
                return {}, []
            return rec.get("config", {}), rec.get("groups", [])

        saved_props, groups = await self.client.execute_write_tx(_create_tx)
        resp = _dict_to_fhir_config_response(saved_props, groups)

        record_audit(
            action=AuditAction.FHIR_CONFIGURATION_CREATED,
            actor_id=f"user:{acted_by}",
            actor=acted_by,
            target_id=f"fhir_config:{cfg_node.id}",
            target=f"FHIRConfiguration url={cfg_node.url}",
            status_code=200,
            status_message=f"FHIRConfiguration '{cfg_node.url}' registered",
            severity="INFO",
            result="FHIR configuration persisted in Neo4j",
            details={
                "url": cfg_node.url,
                "accession_system": cfg_node.accession_system,
                "patient_id_system": cfg_node.patient_id_system,
                "poll_sec": cfg_node.poll_sec,
                "assigned_group_id": target_group_id,
            },
            test_id=norm_test_id,
        )
        return resp


fhir_config_service = FHIRConfigService()
