"""Service for managing dynamic, group-scoped LLM configurations in Neo4j."""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, LiteralString, Optional

from gosmart_graphite.actor.audit_actor import record_audit
from gosmart_graphite.core.auth_dependency import get_active_group_id
from gosmart_graphite.core.logging import get_logger
from gosmart_graphite.core.security import (
    classify_llm_api_key,
    encrypt_llm_api_key,
    mask_llm_api_key,
)
from gosmart_graphite.db.neo4j import Neo4jClient, neo4j_client
from gosmart_graphite.model.audit import AuditAction
from gosmart_graphite.model.auditable import (
    generate_deterministic_id,
    to_negated_relation_type,
    to_positive_relation_type,
)
from gosmart_graphite.model.llm_config import (
    HasLlmConfigRelation,
    LLM_Config,
    LLMConfigCreateRequest,
    LLMConfigUpdateRequest,
)
from gosmart_graphite.service.system_service import normalize_test_id

logger = get_logger("gosmart_gs.llm_config")

DEFAULT_LLM_GROUP_NAME = "GoSmart Health"


def _clean_neo4j_value(val: Any) -> Any:
    """Normalize Neo4j specific types (e.g. neo4j.time.DateTime) to standard Python types."""
    if hasattr(val, "to_native"):
        return val.to_native()
    if isinstance(val, dict):
        return {k: _clean_neo4j_value(v) for k, v in val.items()}
    if isinstance(val, list):
        return [_clean_neo4j_value(v) for v in val]
    return val


def _parse_extra_config_value(raw_val: Any) -> Optional[Dict[str, Any]]:
    if raw_val is None or raw_val == "":
        return None
    if isinstance(raw_val, dict):
        return raw_val
    if isinstance(raw_val, str):
        try:
            parsed = json.loads(raw_val)
            return parsed if isinstance(parsed, dict) else None
        except Exception as why:
            logger.error(f"Failed to parse extra config value: {why}")
            return None
    return None


def _dict_to_llm_config_model(data: Dict[str, Any]) -> LLM_Config:
    """Convert raw Neo4j node properties to an LLM_Config domain model."""
    clean = _clean_neo4j_value(data)
    if "extra_config" in clean:
        clean["extra_config"] = _parse_extra_config_value(clean["extra_config"])
    return LLM_Config(**clean)


def _dict_to_llm_config_response(
    data: Dict[str, Any],
    groups: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Map raw Neo4j LLM_Config properties into a sanitized LLMConfigResponse dictionary."""
    clean = _clean_neo4j_value(data)
    raw_api_key = clean.get("api_key") or ""
    extra_cfg = _parse_extra_config_value(clean.get("extra_config"))

    group_summaries: List[Dict[str, Any]] = []
    group_ids: List[str] = []
    for grp in groups or []:
        if not grp:
            continue
        clean_grp = _clean_neo4j_value(grp)
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
        "tag": clean.get("tag", ""),
        "provider": clean.get("provider", ""),
        "model": clean.get("model", ""),
        "url": clean.get("url") or "",
        "dimensions": clean.get("dimensions"),
        "dimensions_truncated": clean.get("dimensions_truncated"),
        "api_key": mask_llm_api_key(raw_api_key),
        "has_api_key": bool(str(raw_api_key).strip()),
        "api_key_source": classify_llm_api_key(raw_api_key),
        "location": clean.get("location") or "us-central1",
        "project_id": clean.get("project_id"),
        "extra_config": extra_cfg,
        "description": clean.get("description"),
        "group_ids": group_ids,
        "groups": group_summaries,
        "test_id": clean.get("test_id"),
        "action": clean.get("Action", "CREATE"),
        "created_at": clean.get("CreatedAt"),
        "updated_at": clean.get("UpdatedAt"),
    }


def _node_props_payload(cfg_node: LLM_Config, encrypted_api_key: str) -> Dict[str, Any]:
    """Build Neo4j-safe scalar property dictionary excluding audit timestamps managed in Cypher."""
    extra_str = json.dumps(cfg_node.extra_config, sort_keys=True) if cfg_node.extra_config is not None else None
    return {
        "id": cfg_node.id,
        "tag": cfg_node.tag,
        "provider": cfg_node.provider,
        "model": cfg_node.model,
        "url": cfg_node.url,
        "dimensions": cfg_node.dimensions,
        "dimensions_truncated": cfg_node.dimensions_truncated,
        "api_key": encrypted_api_key,
        "location": cfg_node.location,
        "project_id": cfg_node.project_id,
        "extra_config": extra_str,
        "description": cfg_node.description,
        "test_id": cfg_node.test_id,
        "ActedBy": cfg_node.ActedBy,
    }


class LLMConfigService:
    """Service managing Neo4j-persisted, purpose-tagged, group-scoped LLM configurations."""

    _UPSERT_INSPECT_CYPHER: LiteralString = """
    MATCH (tg:Group {id: $target_group_id})
    OPTIONAL MATCH (tg)-[tgr:HAS_LLM_CONFIG]->(grp_existing:LLM_Config {tag: $tag})
    WHERE (tgr.Action IS NULL OR tgr.Action <> 'DELETE')
      AND (grp_existing.Action IS NULL OR grp_existing.Action <> 'DELETE')
      AND (
        (
          ($test_id IS NULL OR $test_id = '')
          AND (grp_existing.test_id IS NULL OR trim(toString(grp_existing.test_id)) = '')
        )
        OR ($test_id IS NOT NULL AND $test_id <> '' AND grp_existing.test_id = $test_id)
      )
    WITH tg, grp_existing
    OPTIONAL MATCH (pg:Group)-[pr:HAS_LLM_CONFIG]->(grp_existing)
    WHERE grp_existing IS NOT NULL
      AND (pr.Action IS NULL OR pr.Action <> 'DELETE')
      AND (pg.Action IS NULL OR pg.Action <> 'DELETE')
    WITH grp_existing, collect(DISTINCT pg.id) AS grp_existing_group_ids
    OPTIONAL MATCH (scoped:LLM_Config {id: $scoped_cfg_id})
    OPTIONAL MATCH (spg:Group)-[spr:HAS_LLM_CONFIG]->(scoped)
    WHERE scoped IS NOT NULL
      AND (spr.Action IS NULL OR spr.Action <> 'DELETE')
      AND (spg.Action IS NULL OR spg.Action <> 'DELETE')
    RETURN properties(grp_existing) AS grp_existing,
           grp_existing_group_ids,
           properties(scoped) AS scoped_existing,
           collect(DISTINCT spg.id) AS scoped_group_ids
    LIMIT 1
    """

    _GROUP_TAG_CONFLICT_CYPHER: LiteralString = """
    MATCH (g:Group {id: $group_id})-[r:HAS_LLM_CONFIG]->(other:LLM_Config {tag: $tag})
    WHERE other.id <> $config_id
      AND (r.Action IS NULL OR r.Action <> 'DELETE')
      AND (other.Action IS NULL OR other.Action <> 'DELETE')
      AND (
        (($test_id IS NULL OR $test_id = '') AND (other.test_id IS NULL OR trim(toString(other.test_id)) = ''))
        OR ($test_id IS NOT NULL AND $test_id <> '' AND other.test_id = $test_id)
      )
    RETURN other.id AS conflict_id
    LIMIT 1
    """

    def __init__(self, client: Optional[Neo4jClient] = None) -> None:
        self.client = client or neo4j_client

    # ------------------------------------------------------------------
    # Group Resolution Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_resolve_group_query(identifier: Optional[str]) -> tuple[LiteralString, Optional[str]]:
        clean_ident = identifier.strip() if isinstance(identifier, str) and identifier.strip() else None
        if clean_ident is None:
            query = """
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

        query = """
        MATCH (g:Group)
        WHERE (g.id = $identifier OR g.name = $identifier OR toUpper(g.code) = toUpper($identifier))
          AND (g.Action IS NULL OR g.Action <> 'DELETE')
          AND coalesce(g.is_active, true) = true
          AND (
            (($test_id IS NULL OR $test_id = '') AND (g.test_id IS NULL OR trim(toString(g.test_id)) = ''))
            OR ($test_id IS NOT NULL AND $test_id <> '' AND g.test_id = $test_id)
          )
        RETURN properties(g) AS group
        """
        return query, clean_ident

    @staticmethod
    def _validate_resolved_group_records(
        records: List[Dict[str, Any]],
        clean_ident: Optional[str],
    ) -> Dict[str, Any]:
        target_label = clean_ident or DEFAULT_LLM_GROUP_NAME
        if not records:
            if clean_ident is None:
                raise RuntimeError(
                    f"Default group '{DEFAULT_LLM_GROUP_NAME}' not found in Neo4j. "
                    "Ensure system bootstrap has run or specify --assign <name or id>."
                )
            raise RuntimeError(f"Target group '{target_label}' not found in Neo4j.")
        if len(records) > 1:
            raise RuntimeError(
                f"Ambiguous group match for '{target_label}': found {len(records)} active Group nodes. "
                "Please specify a unique Group ID."
            )
        return _clean_neo4j_value(records[0].get("group", records[0]))

    async def resolve_target_group(
        self,
        identifier: Optional[str] = None,
        test_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Resolve a single active Group by id/name/code, or default 'GoSmart Health'.

        Raises RuntimeError when 0 or > 1 matching active groups exist.
        """
        norm_test_id = normalize_test_id(test_id)
        query, clean_ident = self._build_resolve_group_query(identifier)
        records = await self.client.execute_query(
            query,
            {
                "identifier": clean_ident,
                "default_name": DEFAULT_LLM_GROUP_NAME,
                "test_id": norm_test_id,
            },
        )
        return self._validate_resolved_group_records(records, clean_ident)

    def resolve_target_group_sync(
        self,
        identifier: Optional[str] = None,
        test_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Synchronously resolve a single active Group by id/name/code, or default 'GoSmart Health'."""
        norm_test_id = normalize_test_id(test_id)
        query, clean_ident = self._build_resolve_group_query(identifier)
        driver = self.client.get_sync_driver()
        with driver.session(database=self.client.settings.DB_DATABASE) as session:
            result = session.run(
                query,
                identifier=clean_ident,
                default_name=DEFAULT_LLM_GROUP_NAME,
                test_id=norm_test_id,
            )
            records = [r.data() for r in result]
        return self._validate_resolved_group_records(records, clean_ident)

    # ------------------------------------------------------------------
    # Upsert (with Group-Scoped Tag Uniqueness, Overwrite Cloning & History)
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_upsert_request(
        config_data: LLMConfigCreateRequest | LLM_Config | Dict[str, Any],
        assign_group: Optional[str],
        test_id: Optional[str],
    ) -> tuple[LLMConfigCreateRequest, Optional[str], Optional[str]]:
        if isinstance(config_data, dict):
            req = LLMConfigCreateRequest(**config_data)
        elif isinstance(config_data, LLM_Config):
            req = LLMConfigCreateRequest(
                tag=config_data.tag,
                provider=config_data.provider,
                model=config_data.model,
                url=config_data.url,
                dimensions=config_data.dimensions,
                dimensions_truncated=config_data.dimensions_truncated,
                api_key=config_data.api_key,
                location=config_data.location,
                project_id=config_data.project_id,
                extra_config=config_data.extra_config,
                description=config_data.description,
                test_id=config_data.test_id,
            )
        else:
            req = config_data

        effective_assign = assign_group if assign_group is not None else req.assign_group
        norm_test_id = normalize_test_id(test_id if test_id is not None else req.test_id)
        return req, effective_assign, norm_test_id

    @classmethod
    def _prepare_upsert_payload(
        cls,
        config_data: LLMConfigCreateRequest | LLM_Config | Dict[str, Any],
        assign_group: Optional[str],
        acted_by: str,
        test_id: Optional[str],
        group_id: Optional[str] = None,
    ) -> tuple[LLM_Config, Dict[str, Any], Optional[str], Optional[str]]:
        req, effective_assign, norm_test_id = cls._parse_upsert_request(
            config_data=config_data,
            assign_group=assign_group,
            test_id=test_id,
        )
        scoped_id = (
            LLM_Config.compute_group_scoped_id(tag=req.tag, group_id=group_id, test_id=norm_test_id) if group_id else ""
        )
        cfg_node = LLM_Config(
            id=scoped_id,
            tag=req.tag,
            provider=req.provider,
            model=req.model,
            url=req.url,
            dimensions=req.dimensions,
            dimensions_truncated=req.dimensions_truncated,
            api_key=req.api_key,
            location=req.location,
            project_id=req.project_id,
            extra_config=req.extra_config,
            description=req.description,
            test_id=norm_test_id,
            ActedBy=acted_by,
        )
        encrypted_key = encrypt_llm_api_key(cfg_node.api_key)
        props = _node_props_payload(cfg_node, encrypted_key)
        return cfg_node, props, effective_assign, norm_test_id

    @staticmethod
    def _plan_upsert_mutation(
        check_rec: Optional[Dict[str, Any]],
        target_group_id: str,
        scoped_cfg_id: str,
        props: Dict[str, Any],
    ) -> tuple[str, Dict[str, Any], bool, List[str], Optional[Dict[str, Any]], Optional[str]]:
        """Determine target node ID, archive flag, prior groups, and shared-config dissociation."""
        grp_existing = check_rec.get("grp_existing") if check_rec else None
        grp_existing_group_ids = (
            [gid for gid in (check_rec.get("grp_existing_group_ids") or []) if gid] if check_rec else []
        )
        scoped_existing = check_rec.get("scoped_existing") if check_rec else None
        scoped_group_ids = [gid for gid in (check_rec.get("scoped_group_ids") or []) if gid] if check_rec else []

        dissociate_shared_cfg_id: Optional[str] = None
        if grp_existing:
            other_groups = [gid for gid in grp_existing_group_ids if gid != target_group_id]
            if grp_existing.get("id") == scoped_cfg_id or not other_groups:
                effective_cfg_id = grp_existing["id"]
                effective_props = {**props, "id": effective_cfg_id}
                was_active = True
                prev_group_ids = grp_existing_group_ids
                existing_props = grp_existing
            else:
                dissociate_shared_cfg_id = grp_existing["id"]
                effective_cfg_id = scoped_cfg_id
                effective_props = props
                was_active = bool(scoped_existing and scoped_existing.get("Action") != "DELETE")
                prev_group_ids = scoped_group_ids if was_active else []
                existing_props = scoped_existing
        else:
            effective_cfg_id = scoped_cfg_id
            effective_props = props
            was_active = bool(scoped_existing and scoped_existing.get("Action") != "DELETE")
            prev_group_ids = scoped_group_ids if was_active else []
            existing_props = scoped_existing

        return (
            effective_cfg_id,
            effective_props,
            was_active,
            prev_group_ids,
            existing_props,
            dissociate_shared_cfg_id,
        )

    async def upsert_llm_config(
        self,
        config_data: LLMConfigCreateRequest | LLM_Config | Dict[str, Any],
        assign_group: Optional[str] = None,
        acted_by: str = "system",
        test_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create or overwrite a group-scoped LLM_Config and assign it via (:Group)-[:HAS_LLM_CONFIG]->(:LLM_Config).

        Tags are unique within an associated Group (not globally), allowing multiple sponsoring Groups
        to maintain independent configurations (e.g., separate API keys) for the same purpose tag.

        When overwriting an existing active LLM_Config for the target group/tag, clones the previous
        node as an archived node with Action='DELETE'.

        The links previously associated groups to the
        archived clone via NOT_HAS_LLM_CONFIG before updating the canonical node in place.
        """
        req, effective_assign, norm_test_id = self._parse_upsert_request(
            config_data=config_data,
            assign_group=assign_group,
            test_id=test_id,
        )
        target_group = await self.resolve_target_group(identifier=effective_assign, test_id=norm_test_id)
        target_group_id = target_group["id"]

        cfg_node, props, _, _ = self._prepare_upsert_payload(
            config_data=req,
            assign_group=effective_assign,
            acted_by=acted_by,
            test_id=norm_test_id,
            group_id=target_group_id,
        )
        neg_type = to_negated_relation_type("HAS_LLM_CONFIG")

        async def _upsert_tx(tx: Any) -> tuple[Dict[str, Any], List[Dict[str, Any]], bool, str]:
            check_res = await tx.run(
                self._UPSERT_INSPECT_CYPHER,
                target_group_id=target_group_id,
                tag=cfg_node.tag,
                scoped_cfg_id=cfg_node.id,
                test_id=norm_test_id,
            )
            check_rec = await check_res.single()
            (
                effective_cfg_id2,
                effective_props,
                was_active2,
                prev_group_ids,
                existing_props,
                dissociate_shared_cfg_id,
            ) = self._plan_upsert_mutation(
                check_rec=check_rec,
                target_group_id=target_group_id,
                scoped_cfg_id=cfg_node.id,
                props=props,
            )

            if dissociate_shared_cfg_id:
                dissoc_rel_id = generate_deterministic_id(
                    {"start_node_id": target_group_id, "type": neg_type, "end_node_id": dissociate_shared_cfg_id}
                )
                await tx.run(
                    f"""
                    MATCH (tg:Group {{id: $target_group_id}})-[r:HAS_LLM_CONFIG]->
                          (shared:LLM_Config {{id: $shared_cfg_id}})
                    WITH tg, shared, r, properties(r) AS old_props
                    CREATE (tg)-[r_not:{neg_type}]->(shared)
                    SET r_not = old_props,
                        r_not.id = $neg_rel_id,
                        r_not.Action = 'DELETE',
                        r_not.ActedAt = datetime({{timezone: 'UTC'}}),
                        r_not.ActedBy = $acted_by
                    DELETE r
                    """,
                    target_group_id=target_group_id,
                    shared_cfg_id=dissociate_shared_cfg_id,
                    neg_rel_id=dissoc_rel_id,
                    acted_by=acted_by,
                )

            if was_active2:
                archive_id = f"{effective_cfg_id2}__archived__{uuid.uuid4().hex[:12]}"
                neg_links = [
                    {
                        "group_id": gid,
                        "neg_rel_id": generate_deterministic_id(
                            {"start_node_id": gid, "type": neg_type, "end_node_id": archive_id}
                        ),
                    }
                    for gid in prev_group_ids
                ]
                await tx.run(
                    f"""
                    MATCH (existing:LLM_Config {{id: $cfg_id}})
                    CREATE (arch:LLM_Config)
                    SET arch = existing {{ .*, id: $archive_id }},
                        arch.Action = 'DELETE',
                        arch.ActedAt = datetime({{timezone: 'UTC'}}),
                        arch.ActedBy = $acted_by
                    WITH arch
                    UNWIND $neg_links AS link
                    MATCH (pg:Group {{id: link.group_id}})
                    CREATE (pg)-[r_not:{neg_type}]->(arch)
                    SET r_not.id = link.neg_rel_id,
                        r_not.Action = 'DELETE',
                        r_not.ActedAt = datetime({{timezone: 'UTC'}}),
                        r_not.ActedBy = $acted_by,
                        r_not.CreatedAt = datetime({{timezone: 'UTC'}}),
                        r_not.test_id = $test_id
                    """,
                    cfg_id=effective_cfg_id2,
                    archive_id=archive_id,
                    acted_by=acted_by,
                    neg_links=neg_links,
                    test_id=existing_props.get("test_id") if existing_props else norm_test_id,
                )

            rel = HasLlmConfigRelation(
                start_node_id=target_group_id,
                end_node_id=effective_cfg_id2,
                test_id=norm_test_id,
                ActedBy=acted_by,
            )
            upsert_res = await tx.run(
                f"""
                MATCH (tg:Group {{id: $target_group_id}})
                MERGE (l:LLM_Config {{id: $props.id}})
                ON CREATE SET l += $props,
                              l.Action = 'CREATE',
                              l.ActedAt = datetime({{timezone: 'UTC'}}),
                              l.CreatedAt = datetime({{timezone: 'UTC'}})
                ON MATCH SET  l += $props,
                              l.Action = CASE WHEN $was_active THEN 'UPDATE' ELSE 'RESTORE' END,
                              l.ActedAt = datetime({{timezone: 'UTC'}}),
                              l.UpdatedAt = datetime({{timezone: 'UTC'}})
                WITH tg, l
                OPTIONAL MATCH (tg)-[stale_not:{neg_type}]->(l)
                DELETE stale_not
                WITH tg, l
                MERGE (tg)-[r:HAS_LLM_CONFIG {{id: $rel_id}}]->(l)
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
                WITH l
                MATCH (g:Group)-[gr:HAS_LLM_CONFIG]->(l)
                WHERE (gr.Action IS NULL OR gr.Action <> 'DELETE')
                  AND (g.Action IS NULL OR g.Action <> 'DELETE')
                RETURN properties(l) AS config, collect(DISTINCT properties(g)) AS groups
                """,
                target_group_id=target_group_id,
                props=effective_props,
                was_active=was_active2,
                rel_id=rel.id,
                test_id=norm_test_id,
                acted_by=acted_by,
            )
            rec = await upsert_res.single()
            if not rec:
                return {}, [], was_active2, effective_cfg_id2
            return rec.get("config", {}), rec.get("groups", []), was_active2, effective_cfg_id2

        saved_props, groups, was_active, effective_cfg_id = await self.client.execute_write_tx(_upsert_tx)
        # noinspection DuplicatedCode
        resp = _dict_to_llm_config_response(saved_props, groups)

        record_audit(
            action=AuditAction.LLM_CONFIGURATION_CHANGED if was_active else AuditAction.LLM_CONFIGURATION_CREATED,
            actor_id=f"user:{acted_by}",
            actor=acted_by,
            target_id=f"llm_config:{effective_cfg_id}",
            target=f"LLM_Config tag={cfg_node.tag} ({cfg_node.provider}/{cfg_node.model})",
            status_code=200,
            status_message=f"LLM_Config '{cfg_node.tag}' {'updated' if was_active else 'created'}",
            severity="INFO",
            result="LLM configuration persisted in Neo4j",
            details={
                "tag": cfg_node.tag,
                "provider": cfg_node.provider,
                "model": cfg_node.model,
                "assigned_group_id": target_group_id,
                "overwritten": was_active,
            },
            test_id=norm_test_id,
        )
        return resp

    def upsert_llm_config_sync(
        self,
        config_data: LLMConfigCreateRequest | LLM_Config | Dict[str, Any],
        assign_group: Optional[str] = None,
        acted_by: str = "system",
        test_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Synchronously create or overwrite a group-scoped LLM_Config and assign it to a Group."""
        req, effective_assign, norm_test_id = self._parse_upsert_request(
            config_data=config_data,
            assign_group=assign_group,
            test_id=test_id,
        )
        target_group = self.resolve_target_group_sync(identifier=effective_assign, test_id=norm_test_id)
        target_group_id = target_group["id"]

        cfg_node, props, _, _ = self._prepare_upsert_payload(
            config_data=req,
            assign_group=effective_assign,
            acted_by=acted_by,
            test_id=norm_test_id,
            group_id=target_group_id,
        )
        neg_type = to_negated_relation_type("HAS_LLM_CONFIG")

        def _upsert_tx(tx: Any) -> tuple[Dict[str, Any], List[Dict[str, Any]], bool, str]:
            check_res = tx.run(
                self._UPSERT_INSPECT_CYPHER,
                target_group_id=target_group_id,
                tag=cfg_node.tag,
                scoped_cfg_id=cfg_node.id,
                test_id=norm_test_id,
            )
            check_rec = check_res.single()
            (
                effective_cfg_id3,
                effective_props,
                was_active3,
                prev_group_ids,
                existing_props,
                dissociate_shared_cfg_id,
            ) = self._plan_upsert_mutation(
                check_rec=check_rec,
                target_group_id=target_group_id,
                scoped_cfg_id=cfg_node.id,
                props=props,
            )

            if dissociate_shared_cfg_id:
                dissoc_rel_id = generate_deterministic_id(
                    {"start_node_id": target_group_id, "type": neg_type, "end_node_id": dissociate_shared_cfg_id}
                )
                tx.run(
                    f"""
                    MATCH (tg:Group {{id: $target_group_id}})-[r:HAS_LLM_CONFIG]->
                          (shared:LLM_Config {{id: $shared_cfg_id}})
                    WITH tg, shared, r, properties(r) AS old_props
                    CREATE (tg)-[r_not:{neg_type}]->(shared)
                    SET r_not = old_props,
                        r_not.id = $neg_rel_id,
                        r_not.Action = 'DELETE',
                        r_not.ActedAt = datetime({{timezone: 'UTC'}}),
                        r_not.ActedBy = $acted_by
                    DELETE r
                    """,
                    target_group_id=target_group_id,
                    shared_cfg_id=dissociate_shared_cfg_id,
                    neg_rel_id=dissoc_rel_id,
                    acted_by=acted_by,
                )

            if was_active3:
                archive_id = f"{effective_cfg_id3}__archived__{uuid.uuid4().hex[:12]}"
                neg_links = [
                    {
                        "group_id": gid,
                        "neg_rel_id": generate_deterministic_id(
                            {"start_node_id": gid, "type": neg_type, "end_node_id": archive_id}
                        ),
                    }
                    for gid in prev_group_ids
                ]
                tx.run(
                    f"""
                    MATCH (existing:LLM_Config {{id: $cfg_id}})
                    CREATE (arch:LLM_Config)
                    SET arch = existing {{ .*, id: $archive_id }},
                        arch.Action = 'DELETE',
                        arch.ActedAt = datetime({{timezone: 'UTC'}}),
                        arch.ActedBy = $acted_by
                    WITH arch
                    UNWIND $neg_links AS link
                    MATCH (pg:Group {{id: link.group_id}})
                    CREATE (pg)-[r_not:{neg_type}]->(arch)
                    SET r_not.id = link.neg_rel_id,
                        r_not.Action = 'DELETE',
                        r_not.ActedAt = datetime({{timezone: 'UTC'}}),
                        r_not.ActedBy = $acted_by,
                        r_not.CreatedAt = datetime({{timezone: 'UTC'}}),
                        r_not.test_id = $test_id
                    """,
                    cfg_id=effective_cfg_id3,
                    archive_id=archive_id,
                    acted_by=acted_by,
                    neg_links=neg_links,
                    test_id=existing_props.get("test_id") if existing_props else norm_test_id,
                )

            rel = HasLlmConfigRelation(
                start_node_id=target_group_id,
                end_node_id=effective_cfg_id3,
                test_id=norm_test_id,
                ActedBy=acted_by,
            )
            upsert_res = tx.run(
                f"""
                MATCH (tg:Group {{id: $target_group_id}})
                MERGE (l:LLM_Config {{id: $props.id}})
                ON CREATE SET l += $props,
                              l.Action = 'CREATE',
                              l.ActedAt = datetime({{timezone: 'UTC'}}),
                              l.CreatedAt = datetime({{timezone: 'UTC'}})
                ON MATCH SET  l += $props,
                              l.Action = CASE WHEN $was_active THEN 'UPDATE' ELSE 'RESTORE' END,
                              l.ActedAt = datetime({{timezone: 'UTC'}}),
                              l.UpdatedAt = datetime({{timezone: 'UTC'}})
                WITH tg, l
                OPTIONAL MATCH (tg)-[stale_not:{neg_type}]->(l)
                DELETE stale_not
                WITH tg, l
                MERGE (tg)-[r:HAS_LLM_CONFIG {{id: $rel_id}}]->(l)
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
                WITH l
                MATCH (g:Group)-[gr:HAS_LLM_CONFIG]->(l)
                WHERE (gr.Action IS NULL OR gr.Action <> 'DELETE')
                  AND (g.Action IS NULL OR g.Action <> 'DELETE')
                RETURN properties(l) AS config, collect(DISTINCT properties(g)) AS groups
                """,
                target_group_id=target_group_id,
                props=effective_props,
                was_active=was_active3,
                rel_id=rel.id,
                test_id=norm_test_id,
                acted_by=acted_by,
            )
            rec = upsert_res.single()
            if not rec:
                return {}, [], was_active3, effective_cfg_id3
            return rec.get("config", {}), rec.get("groups", []), was_active3, effective_cfg_id3

        saved_props, groups, was_active, effective_cfg_id = self.client.execute_write_tx_sync(_upsert_tx)
        # noinspection DuplicatedCode
        resp = _dict_to_llm_config_response(saved_props, groups)

        record_audit(
            action=AuditAction.LLM_CONFIGURATION_CHANGED if was_active else AuditAction.LLM_CONFIGURATION_CREATED,
            actor_id=f"user:{acted_by}",
            actor=acted_by,
            target_id=f"llm_config:{effective_cfg_id}",
            target=f"LLM_Config tag={cfg_node.tag} ({cfg_node.provider}/{cfg_node.model})",
            status_code=200,
            status_message=f"LLM_Config '{cfg_node.tag}' {'updated' if was_active else 'created'}",
            severity="INFO",
            result="LLM configuration persisted in Neo4j",
            details={
                "tag": cfg_node.tag,
                "provider": cfg_node.provider,
                "model": cfg_node.model,
                "assigned_group_id": target_group_id,
                "overwritten": was_active,
            },
            test_id=norm_test_id,
        )
        return resp

    # ------------------------------------------------------------------
    # Runtime Upward Group-Tree Resolution
    # ------------------------------------------------------------------

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
    MATCH (anc)-[r:HAS_LLM_CONFIG]->(l:LLM_Config {tag: $tag})
    WHERE (r.Action IS NULL OR r.Action <> 'DELETE')
      AND (l.Action IS NULL OR l.Action <> 'DELETE')
      AND (
        (($test_id IS NULL OR $test_id = '') AND (l.test_id IS NULL OR trim(toString(l.test_id)) = ''))
        OR ($test_id IS NOT NULL AND $test_id <> '' AND l.test_id = $test_id)
      )
    WITH l, anc, min(length(path)) AS depth
    ORDER BY depth ASC
    RETURN properties(l) AS config, anc.id AS group_id, depth
    """

    _DEFAULT_GROUP_RESOLVE_CYPHER: LiteralString = """
    MATCH (g:Group {name: $default_name})-[r:HAS_LLM_CONFIG]->(l:LLM_Config {tag: $tag})
    WHERE (g.Action IS NULL OR g.Action <> 'DELETE')
      AND coalesce(g.is_active, true) = true
      AND (r.Action IS NULL OR r.Action <> 'DELETE')
      AND (l.Action IS NULL OR l.Action <> 'DELETE')
      AND (
        (
          ($test_id IS NULL OR $test_id = '')
          AND (l.test_id IS NULL OR trim(toString(l.test_id)) = '')
          AND (g.test_id IS NULL OR trim(toString(g.test_id)) = '')
        )
        OR ($test_id IS NOT NULL AND $test_id <> '' AND l.test_id = $test_id AND g.test_id = $test_id)
      )
    RETURN properties(l) AS config, g.id AS group_id
    """

    @staticmethod
    def _pick_ancestor_record(
        records: List[Dict[str, Any]],
        tag: str,
        group_id: str,
    ) -> Optional[LLM_Config]:
        if not records:
            return None
        min_depth = records[0]["depth"]
        at_min = [r for r in records if r["depth"] == min_depth]
        distinct_ids = {r["config"].get("id") for r in at_min if r.get("config")}
        if len(distinct_ids) > 1:
            raise RuntimeError(
                f"Ambiguous LLM_Config for tag '{tag}' at depth {min_depth} starting from group '{group_id}'."
            )
        return _dict_to_llm_config_model(at_min[0]["config"])

    @staticmethod
    def _pick_default_record(records: List[Dict[str, Any]], tag: str) -> Optional[LLM_Config]:
        if not records:
            return None
        distinct_ids = {r["config"].get("id") for r in records if r.get("config")}
        if len(distinct_ids) > 1:
            raise RuntimeError(f"Ambiguous LLM_Config for tag '{tag}' on default group '{DEFAULT_LLM_GROUP_NAME}'.")
        return _dict_to_llm_config_model(records[0]["config"])

    async def resolve_config_for_group(
        self,
        tag: str,
        group_id: Optional[str] = None,
        test_id: Optional[str] = None,
    ) -> Optional[LLM_Config]:
        """Resolve an active LLM_Config by traversing upward from group_id, falling back to 'GoSmart Health'."""
        clean_tag = tag.strip()
        norm_test_id = normalize_test_id(test_id)
        effective_group_id = (
            group_id.strip() if isinstance(group_id, str) and group_id.strip() else get_active_group_id()
        )

        if effective_group_id:
            records = await self.client.execute_query(
                self._ANCESTOR_RESOLVE_CYPHER,
                {"group_id": effective_group_id, "tag": clean_tag, "test_id": norm_test_id},
            )
            picked = self._pick_ancestor_record(records, clean_tag, effective_group_id)
            if picked is not None:
                return picked

        default_records = await self.client.execute_query(
            self._DEFAULT_GROUP_RESOLVE_CYPHER,
            {"default_name": DEFAULT_LLM_GROUP_NAME, "tag": clean_tag, "test_id": norm_test_id},
        )
        return self._pick_default_record(default_records, clean_tag)

    def resolve_config_for_group_sync(
        self,
        tag: str,
        group_id: Optional[str] = None,
        test_id: Optional[str] = None,
    ) -> Optional[LLM_Config]:
        """Synchronously resolve an active LLM_Config upward from group_id, falling back to 'GoSmart Health'."""
        clean_tag = tag.strip()
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
                    tag=clean_tag,
                    test_id=norm_test_id,
                )
                picked = self._pick_ancestor_record([r.data() for r in res], clean_tag, effective_group_id)
                if picked is not None:
                    return picked

            def_res = session.run(
                self._DEFAULT_GROUP_RESOLVE_CYPHER,
                default_name=DEFAULT_LLM_GROUP_NAME,
                tag=clean_tag,
                test_id=norm_test_id,
            )
            return self._pick_default_record([r.data() for r in def_res], clean_tag)

    # ------------------------------------------------------------------
    # Read / Update / Assign / Dissociate / Delete / Restore
    # ------------------------------------------------------------------

    async def list_llm_configs(
        self,
        tag: Optional[str] = None,
        provider: Optional[str] = None,
        group_id: Optional[str] = None,
        include_tests: bool = False,
        test_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List active LLM_Config nodes with associated sponsoring groups (api_key masked)."""
        norm_test_id = normalize_test_id(test_id)
        clean_tag = tag.strip() if isinstance(tag, str) and tag.strip() else None
        clean_provider = provider.strip().lower() if isinstance(provider, str) and provider.strip() else None
        clean_group_id = group_id.strip() if isinstance(group_id, str) and group_id.strip() else None

        query = """
        MATCH (l:LLM_Config)
        WHERE (l.Action IS NULL OR l.Action <> 'DELETE')
          AND ($tag IS NULL OR l.tag = $tag)
          AND ($provider IS NULL OR toLower(l.provider) = $provider)
          AND ($include_tests = true OR l.test_id IS NULL OR trim(toString(l.test_id)) = '')
          AND ($test_id IS NULL OR l.test_id = $test_id)
        OPTIONAL MATCH (g:Group)-[r:HAS_LLM_CONFIG]->(l)
        WHERE (r.Action IS NULL OR r.Action <> 'DELETE')
          AND (g.Action IS NULL OR g.Action <> 'DELETE')
        WITH l, collect(DISTINCT properties(g)) AS groups
        WHERE ($group_id IS NULL OR any(grp IN groups WHERE grp.id = $group_id))
        RETURN properties(l) AS config, groups
        ORDER BY l.tag ASC, l.id ASC
        """
        records = await self.client.execute_query(
            query,
            {
                "tag": clean_tag,
                "provider": clean_provider,
                "group_id": clean_group_id,
                "include_tests": include_tests or bool(norm_test_id),
                "test_id": norm_test_id,
            },
        )
        return [_dict_to_llm_config_response(r.get("config", {}), r.get("groups", [])) for r in records]

    async def get_llm_config(
        self,
        config_id: Optional[str] = None,
        tag: Optional[str] = None,
        group_id: Optional[str] = None,
        test_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Retrieve a single active LLM_Config by ID or by (tag[, group_id]) (api_key masked)."""
        norm_test_id = normalize_test_id(test_id)
        clean_group = group_id.strip() if isinstance(group_id, str) and group_id.strip() else None

        if config_id:
            query = """
            MATCH (l:LLM_Config {id: $config_id})
            WHERE (l.Action IS NULL OR l.Action <> 'DELETE')
            OPTIONAL MATCH (g:Group)-[r:HAS_LLM_CONFIG]->(l)
            WHERE (r.Action IS NULL OR r.Action <> 'DELETE')
              AND (g.Action IS NULL OR g.Action <> 'DELETE')
            RETURN properties(l) AS config, collect(DISTINCT properties(g)) AS groups
            LIMIT 1
            """
            records = await self.client.execute_query(query, {"config_id": config_id})
            if not records or not records[0].get("config"):
                return None
            return _dict_to_llm_config_response(records[0]["config"], records[0].get("groups", []))

        if tag:
            clean_tag = tag.strip()
            if clean_group:
                query = """
                MATCH (target_g:Group)-[tr:HAS_LLM_CONFIG]->(l:LLM_Config {tag: $tag})
                WHERE (
                    target_g.id = $group_id
                    OR target_g.name = $group_id
                    OR toUpper(target_g.code) = toUpper($group_id)
                  )
                  AND (target_g.Action IS NULL OR target_g.Action <> 'DELETE')
                  AND coalesce(target_g.is_active, true) = true
                  AND (tr.Action IS NULL OR tr.Action <> 'DELETE')
                  AND (l.Action IS NULL OR l.Action <> 'DELETE')
                  AND (
                    (($test_id IS NULL OR $test_id = '') AND (l.test_id IS NULL OR trim(toString(l.test_id)) = ''))
                    OR ($test_id IS NOT NULL AND $test_id <> '' AND l.test_id = $test_id)
                  )
                WITH DISTINCT l
                OPTIONAL MATCH (g:Group)-[r:HAS_LLM_CONFIG]->(l)
                WHERE (r.Action IS NULL OR r.Action <> 'DELETE')
                  AND (g.Action IS NULL OR g.Action <> 'DELETE')
                RETURN properties(l) AS config, collect(DISTINCT properties(g)) AS groups
                """
                records = await self.client.execute_query(
                    query,
                    {"tag": clean_tag, "group_id": clean_group, "test_id": norm_test_id},
                )
                valid_records = [r for r in records if r.get("config")]
                if not valid_records:
                    return None
                if len(valid_records) > 1:
                    raise RuntimeError(
                        f"Ambiguous LLM_Config for tag '{clean_tag}' and group '{clean_group}': "
                        f"found {len(valid_records)} matching active configurations."
                    )
                return _dict_to_llm_config_response(
                    valid_records[0]["config"],
                    valid_records[0].get("groups", []),
                )

            query = """
            MATCH (l:LLM_Config {tag: $tag})
            WHERE (l.Action IS NULL OR l.Action <> 'DELETE')
              AND (
                (($test_id IS NULL OR $test_id = '') AND (l.test_id IS NULL OR trim(toString(l.test_id)) = ''))
                OR ($test_id IS NOT NULL AND $test_id <> '' AND l.test_id = $test_id)
              )
            OPTIONAL MATCH (g:Group)-[r:HAS_LLM_CONFIG]->(l)
            WHERE (r.Action IS NULL OR r.Action <> 'DELETE')
              AND (g.Action IS NULL OR g.Action <> 'DELETE')
            RETURN properties(l) AS config, collect(DISTINCT properties(g)) AS groups
            """
            records = await self.client.execute_query(query, {"tag": clean_tag, "test_id": norm_test_id})
            valid_records = [r for r in records if r.get("config")]
            if not valid_records:
                return None
            if len(valid_records) == 1:
                return _dict_to_llm_config_response(
                    valid_records[0]["config"],
                    valid_records[0].get("groups", []),
                )
            active_gid = get_active_group_id()
            if active_gid:
                matching_active = [
                    r
                    for r in valid_records
                    if any(grp and _clean_neo4j_value(grp).get("id") == active_gid for grp in (r.get("groups") or []))
                ]
                if len(matching_active) == 1:
                    return _dict_to_llm_config_response(
                        matching_active[0]["config"],
                        matching_active[0].get("groups", []),
                    )
            raise RuntimeError(
                f"Ambiguous LLM_Config for tag '{clean_tag}': found {len(valid_records)} active configurations "
                "across groups. Please specify config_id or group_id."
            )

        raise ValueError("Either config_id or tag must be provided.")

    async def update_llm_config(
        self,
        config_id: str,
        update_data: LLMConfigUpdateRequest,
        acted_by: str = "system",
    ) -> Optional[Dict[str, Any]]:
        """Update mutable properties of an active LLM_Config (cloning prior state with NOT_HAS_LLM_CONFIG)."""
        fetch_query = """
        MATCH (l:LLM_Config {id: $config_id})
        WHERE (l.Action IS NULL OR l.Action <> 'DELETE')
        OPTIONAL MATCH (g:Group)-[r:HAS_LLM_CONFIG]->(l)
        WHERE (r.Action IS NULL OR r.Action <> 'DELETE')
          AND (g.Action IS NULL OR g.Action <> 'DELETE')
        RETURN properties(l) AS config, collect(DISTINCT properties(g)) AS groups
        LIMIT 1
        """
        records = await self.client.execute_query(fetch_query, {"config_id": config_id})
        if not records or not records[0].get("config"):
            return None

        current_raw = _clean_neo4j_value(records[0]["config"])
        current_groups = records[0].get("groups", [])
        current_extra = _parse_extra_config_value(current_raw.get("extra_config"))

        merged_api_key = current_raw.get("api_key", "")
        if update_data.api_key is not None:
            merged_api_key = encrypt_llm_api_key(update_data.api_key)

        merged_node = LLM_Config(
            id=config_id,
            tag=current_raw["tag"],
            provider=update_data.provider if update_data.provider is not None else current_raw["provider"],
            model=update_data.model if update_data.model is not None else current_raw["model"],
            url=update_data.url if update_data.url is not None else (current_raw.get("url") or ""),
            dimensions=update_data.dimensions if update_data.dimensions is not None else current_raw.get("dimensions"),
            dimensions_truncated=(
                update_data.dimensions_truncated
                if update_data.dimensions_truncated is not None
                else current_raw.get("dimensions_truncated")
            ),
            api_key=merged_api_key,
            location=(
                update_data.location
                if update_data.location is not None
                else (current_raw.get("location") or "us-central1")
            ),
            project_id=update_data.project_id if update_data.project_id is not None else current_raw.get("project_id"),
            extra_config=update_data.extra_config if update_data.extra_config is not None else current_extra,
            description=(
                update_data.description if update_data.description is not None else current_raw.get("description")
            ),
            test_id=current_raw.get("test_id"),
            ActedBy=acted_by,
        )
        props = _node_props_payload(merged_node, merged_api_key)
        neg_type = to_negated_relation_type("HAS_LLM_CONFIG")

        extra_group_id: Optional[str] = None
        extra_rel_id: Optional[str] = None
        if update_data.assign_group is not None:
            grp = await self.resolve_target_group(
                identifier=update_data.assign_group,
                test_id=merged_node.test_id,
            )
            extra_group_id = grp["id"]
            if not extra_group_id:
                raise ValueError(f"Group {update_data.assign_group} not found")
            extra_rel_id = HasLlmConfigRelation(
                start_node_id=extra_group_id,
                end_node_id=config_id,
                test_id=merged_node.test_id,
                ActedBy=acted_by,
            ).id

        prev_group_ids = [g["id"] for g in current_groups if g and g.get("id")]
        archive_id = f"{config_id}__archived__{uuid.uuid4().hex[:12]}"
        neg_links = [
            {
                "group_id": gid,
                "neg_rel_id": generate_deterministic_id(
                    {"start_node_id": gid, "type": neg_type, "end_node_id": archive_id}
                ),
            }
            for gid in prev_group_ids
        ]

        async def _update_tx(tx: Any) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
            if extra_group_id:
                conflict_res = await tx.run(
                    self._GROUP_TAG_CONFLICT_CYPHER,
                    group_id=extra_group_id,
                    tag=merged_node.tag,
                    config_id=config_id,
                    test_id=normalize_test_id(merged_node.test_id),
                )
                conflict_rec = await conflict_res.single()
                if conflict_rec and conflict_rec.get("conflict_id"):
                    raise ValueError(
                        f"Group '{extra_group_id}' already has an active LLM_Config with tag "
                        f"'{merged_node.tag}' (id='{conflict_rec['conflict_id']}'). "
                        "Dissociate or update the existing configuration first."
                    )

            await tx.run(
                f"""
                MATCH (existing:LLM_Config {{id: $cfg_id}})
                CREATE (arch:LLM_Config)
                SET arch = existing {{ .*, id: $archive_id }},
                    arch.Action = 'DELETE',
                    arch.ActedAt = datetime({{timezone: 'UTC'}}),
                    arch.ActedBy = $acted_by
                WITH arch
                UNWIND $neg_links AS link
                MATCH (pg:Group {{id: link.group_id}})
                CREATE (pg)-[r_not:{neg_type}]->(arch)
                SET r_not.id = link.neg_rel_id,
                    r_not.Action = 'DELETE',
                    r_not.ActedAt = datetime({{timezone: 'UTC'}}),
                    r_not.ActedBy = $acted_by,
                    r_not.CreatedAt = datetime({{timezone: 'UTC'}}),
                    r_not.test_id = $test_id
                """,
                cfg_id=config_id,
                archive_id=archive_id,
                acted_by=acted_by,
                neg_links=neg_links,
                test_id=merged_node.test_id,
            )

            await tx.run(
                """
                MATCH (l:LLM_Config {id: $cfg_id})
                SET l += $props,
                    l.Action = 'UPDATE',
                    l.ActedAt = datetime({timezone: 'UTC'}),
                    l.UpdatedAt = datetime({timezone: 'UTC'})
                """,
                cfg_id=config_id,
                props=props,
            )

            if extra_group_id and extra_rel_id:
                await tx.run(
                    f"""
                    MATCH (tg:Group {{id: $group_id}}), (l:LLM_Config {{id: $cfg_id}})
                    OPTIONAL MATCH (tg)-[stale_not:{neg_type}]->(l)
                    DELETE stale_not
                    WITH tg, l
                    MERGE (tg)-[r:HAS_LLM_CONFIG {{id: $rel_id}}]->(l)
                    ON CREATE SET r.test_id = $test_id,
                                  r.Action = 'CREATE',
                                  r.ActedAt = datetime({{timezone: 'UTC'}}),
                                  r.ActedBy = $acted_by,
                                  r.CreatedAt = datetime({{timezone: 'UTC'}})
                    ON MATCH SET  r.Action = 'UPDATE',
                                  r.ActedAt = datetime({{timezone: 'UTC'}}),
                                  r.ActedBy = $acted_by,
                                  r.UpdatedAt = datetime({{timezone: 'UTC'}})
                    """,
                    group_id=extra_group_id,
                    cfg_id=config_id,
                    rel_id=extra_rel_id,
                    test_id=merged_node.test_id,
                    acted_by=acted_by,
                )

            res = await tx.run(
                """
                MATCH (l:LLM_Config {id: $cfg_id})
                OPTIONAL MATCH (g:Group)-[r:HAS_LLM_CONFIG]->(l)
                WHERE (r.Action IS NULL OR r.Action <> 'DELETE')
                  AND (g.Action IS NULL OR g.Action <> 'DELETE')
                RETURN properties(l) AS config, collect(DISTINCT properties(g)) AS groups
                """,
                cfg_id=config_id,
            )
            rec = await res.single()
            return (rec.get("config", {}), rec.get("groups", [])) if rec else ({}, [])

        updated_props, updated_groups = await self.client.execute_write_tx(_update_tx)
        record_audit(
            action=AuditAction.LLM_CONFIGURATION_CHANGED,
            actor_id=f"user:{acted_by}",
            actor=acted_by,
            target_id=f"llm_config:{config_id}",
            target=f"LLM_Config tag={merged_node.tag}",
            status_code=200,
            status_message=f"LLM_Config '{merged_node.tag}' updated",
            severity="INFO",
            result="LLM configuration updated",
            details={"tag": merged_node.tag, "provider": merged_node.provider, "model": merged_node.model},
            test_id=merged_node.test_id,
        )
        return _dict_to_llm_config_response(updated_props, updated_groups)

    async def assign_to_group(
        self,
        config_id: str,
        group_identifier: str,
        acted_by: str = "system",
        test_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Assign an existing active LLM_Config to an active Group via HAS_LLM_CONFIG."""
        cfg = await self.get_llm_config(config_id=config_id)
        if not cfg:
            return None

        eff_test_id = normalize_test_id(test_id if test_id is not None else cfg.get("test_id"))
        cfg_test_id = normalize_test_id(cfg.get("test_id"))
        target_group = await self.resolve_target_group(identifier=group_identifier, test_id=eff_test_id)
        group_id = target_group["id"]
        rel = HasLlmConfigRelation(
            start_node_id=group_id,
            end_node_id=config_id,
            test_id=eff_test_id,
            ActedBy=acted_by,
        )
        neg_type = to_negated_relation_type("HAS_LLM_CONFIG")

        async def _assign_tx(tx: Any) -> Optional[tuple[Dict[str, Any], List[Dict[str, Any]]]]:
            conflict_res = await tx.run(
                self._GROUP_TAG_CONFLICT_CYPHER,
                group_id=group_id,
                tag=cfg["tag"] if cfg else "???",
                config_id=config_id,
                test_id=cfg_test_id,
            )
            conflict_rec = await conflict_res.single()
            if conflict_rec and conflict_rec.get("conflict_id"):
                raise ValueError(
                    f"Group '{group_id}' already has an active LLM_Config with tag '{cfg['tag']}' "
                    f"(id='{conflict_rec['conflict_id']}'). Dissociate or update the existing configuration first."
                )

            await tx.run(
                f"""
                MATCH (g:Group {{id: $group_id}}), (l:LLM_Config {{id: $config_id}})
                OPTIONAL MATCH (g)-[stale_not:{neg_type}]->(l)
                DELETE stale_not
                WITH g, l
                MERGE (g)-[r:HAS_LLM_CONFIG {{id: $rel_id}}]->(l)
                ON CREATE SET r.test_id = $test_id,
                              r.Action = 'CREATE',
                              r.ActedAt = datetime({{timezone: 'UTC'}}),
                              r.ActedBy = $acted_by,
                              r.CreatedAt = datetime({{timezone: 'UTC'}})
                ON MATCH SET  r.Action = 'UPDATE',
                              r.ActedAt = datetime({{timezone: 'UTC'}}),
                              r.ActedBy = $acted_by,
                              r.UpdatedAt = datetime({{timezone: 'UTC'}})
                """,
                group_id=group_id,
                config_id=config_id,
                rel_id=rel.id,
                test_id=eff_test_id,
                acted_by=acted_by,
            )
            res = await tx.run(
                """
                MATCH (l:LLM_Config {id: $config_id})
                OPTIONAL MATCH (g:Group)-[r:HAS_LLM_CONFIG]->(l)
                WHERE (r.Action IS NULL OR r.Action <> 'DELETE')
                  AND (g.Action IS NULL OR g.Action <> 'DELETE')
                RETURN properties(l) AS config, collect(DISTINCT properties(g)) AS groups
                """,
                config_id=config_id,
            )
            rec = await res.single()
            if not rec or not rec.get("config"):
                return None
            return rec["config"], rec.get("groups", [])

        tx_res = await self.client.execute_write_tx(_assign_tx)
        if not tx_res:
            return None
        record_audit(
            action=AuditAction.LLM_CONFIGURATION_ASSIGNED,
            actor_id=f"user:{acted_by}",
            actor=acted_by,
            target_id=f"llm_config:{config_id}",
            target=f"Group {group_id}",
            status_code=200,
            status_message=f"LLM_Config {config_id} assigned to Group {group_id}",
            severity="INFO",
            result="HAS_LLM_CONFIG relationship created",
            details={"config_id": config_id, "group_id": group_id},
            test_id=eff_test_id,
        )
        return _dict_to_llm_config_response(tx_res[0], tx_res[1])

    async def dissociate_from_group(
        self,
        config_id: str,
        group_id: str,
        acted_by: str = "system",
    ) -> bool:
        """Soft-dissociate a Group from an LLM_Config by swapping HAS_LLM_CONFIG to NOT_HAS_LLM_CONFIG."""
        neg_type = to_negated_relation_type("HAS_LLM_CONFIG")
        new_rel_id = generate_deterministic_id({"start_node_id": group_id, "type": neg_type, "end_node_id": config_id})

        query = f"""
        MATCH (g:Group {{id: $group_id}})-[r:HAS_LLM_CONFIG]->(l:LLM_Config {{id: $config_id}})
        WITH g, l, r, properties(r) AS old_props
        CREATE (g)-[r_not:{neg_type}]->(l)
        SET r_not = old_props,
            r_not.id = $new_rel_id,
            r_not.Action = 'DELETE',
            r_not.ActedAt = datetime({{timezone: 'UTC'}}),
            r_not.ActedBy = $acted_by
        DELETE r
        RETURN count(r_not) AS cnt, l.test_id AS test_id
        """

        async def _swap_tx(tx: Any) -> tuple[bool, Optional[str]]:
            res = await tx.run(
                query,
                group_id=group_id,
                config_id=config_id,
                new_rel_id=new_rel_id,
                acted_by=acted_by,
            )
            rec = await res.single()
            if not rec or rec.get("cnt", 0) == 0:
                return False, None
            return True, rec.get("test_id")

        success, cfg_test_id = await self.client.execute_write_tx(_swap_tx)
        if success:
            record_audit(
                action=AuditAction.LLM_CONFIGURATION_DISSOCIATED,
                actor_id=f"user:{acted_by}",
                actor=acted_by,
                target_id=f"llm_config:{config_id}",
                target=f"Group {group_id}",
                status_code=200,
                status_message=f"LLM_Config {config_id} dissociated from Group {group_id}",
                severity="INFO",
                result="NOT_HAS_LLM_CONFIG relationship created",
                details={"config_id": config_id, "group_id": group_id},
                test_id=cfg_test_id,
            )
        return success

    async def delete_llm_config(
        self,
        config_id: str,
        acted_by: str = "system",
        hard_delete: bool = False,
    ) -> bool:
        """Soft-delete (or hard-delete) an LLM_Config and negate HAS_LLM_CONFIG relationships."""
        if hard_delete:
            recs = await self.client.execute_query(
                """
                MATCH (l:LLM_Config {id: $config_id})
                DETACH DELETE l
                RETURN count(l) AS cnt
                """,
                {"config_id": config_id},
            )
            return bool(recs and recs[0].get("cnt", 0) > 0)

        neg_type = to_negated_relation_type("HAS_LLM_CONFIG")

        async def _del_tx(tx: Any) -> tuple[bool, Optional[str]]:
            find_res = await tx.run(
                """
                MATCH (l:LLM_Config {id: $config_id})
                WHERE (l.Action IS NULL OR l.Action <> 'DELETE')
                OPTIONAL MATCH (g:Group)-[r:HAS_LLM_CONFIG]->(l)
                RETURN l.test_id AS test_id, collect(DISTINCT g.id) AS group_ids
                """,
                config_id=config_id,
            )
            rec = await find_res.single()
            if not rec:
                return False, None

            group_ids = [gid for gid in (rec.get("group_ids") or []) if gid]
            cfg_test_id4 = rec.get("test_id")

            for gid in group_ids:
                new_rel_id = generate_deterministic_id(
                    {"start_node_id": gid, "type": neg_type, "end_node_id": config_id}
                )
                await tx.run(
                    f"""
                    MATCH (g:Group {{id: $gid}})-[r:HAS_LLM_CONFIG]->(l:LLM_Config {{id: $config_id}})
                    WITH g, l, r, properties(r) AS old_props
                    CREATE (g)-[r_not:{neg_type}]->(l)
                    SET r_not = old_props,
                        r_not.id = $new_rel_id,
                        r_not.Action = 'DELETE',
                        r_not.ActedAt = datetime({{timezone: 'UTC'}}),
                        r_not.ActedBy = $acted_by
                    DELETE r
                    """,
                    gid=gid,
                    config_id=config_id,
                    new_rel_id=new_rel_id,
                    acted_by=acted_by,
                )

            await tx.run(
                """
                MATCH (l:LLM_Config {id: $config_id})
                SET l.Action = 'DELETE',
                    l.ActedAt = datetime({timezone: 'UTC'}),
                    l.ActedBy = $acted_by
                """,
                config_id=config_id,
                acted_by=acted_by,
            )
            return True, cfg_test_id4

        success, cfg_test_id = await self.client.execute_write_tx(_del_tx)
        if success:
            record_audit(
                action=AuditAction.LLM_CONFIGURATION_REMOVED,
                actor_id=f"user:{acted_by}",
                actor=acted_by,
                target_id=f"llm_config:{config_id}",
                target=f"LLM_Config {config_id}",
                status_code=200,
                status_message=f"LLM_Config {config_id} soft-deleted",
                severity="INFO",
                result="LLM_Config marked Action='DELETE'",
                details={"config_id": config_id},
                test_id=cfg_test_id,
            )
        return success

    async def restore_llm_config(
        self,
        config_id: str,
        acted_by: str = "system",
    ) -> Optional[Dict[str, Any]]:
        """Restore a soft-deleted LLM_Config (whose deterministic id was NOT archived) and its group links."""
        if "__archived__" in config_id:
            return None

        neg_type = to_negated_relation_type("HAS_LLM_CONFIG")
        pos_type = to_positive_relation_type("HAS_LLM_CONFIG")

        async def _restore_tx(tx: Any) -> Optional[tuple[Dict[str, Any], List[Dict[str, Any]]]]:
            find_res = await tx.run(
                f"""
                MATCH (l:LLM_Config {{id: $config_id}})
                WHERE l.Action = 'DELETE'
                OPTIONAL MATCH (g:Group)-[r:{neg_type}]->(l)
                RETURN l.id AS id, l.tag AS tag, l.test_id AS test_id, collect(DISTINCT g.id) AS group_ids
                """,
                config_id=config_id,
            )
            rec = await find_res.single()
            if not rec or not rec.get("id"):
                return None

            cfg_tag = rec.get("tag")
            cfg_test_id = normalize_test_id(rec.get("test_id"))
            group_ids = [gid for gid in (rec.get("group_ids") or []) if gid]

            if cfg_tag:
                for gid in group_ids:
                    conflict_res = await tx.run(
                        self._GROUP_TAG_CONFLICT_CYPHER,
                        group_id=gid,
                        tag=cfg_tag,
                        config_id=config_id,
                        test_id=cfg_test_id,
                    )
                    conflict_rec = await conflict_res.single()
                    if conflict_rec and conflict_rec.get("conflict_id"):
                        raise ValueError(
                            f"Cannot restore LLM_Config '{config_id}': Group '{gid}' already has an active "
                            f"LLM_Config with tag '{cfg_tag}' (id='{conflict_rec['conflict_id']}')."
                        )

            for gid in group_ids:
                new_rel_id = generate_deterministic_id(
                    {"start_node_id": gid, "type": pos_type, "end_node_id": config_id}
                )
                await tx.run(
                    f"""
                    MATCH (g:Group {{id: $gid}})-[r:{neg_type}]->(l:LLM_Config {{id: $config_id}})
                    WITH g, l, r, properties(r) AS old_props
                    CREATE (g)-[r_pos:{pos_type}]->(l)
                    SET r_pos = old_props,
                        r_pos.id = $new_rel_id,
                        r_pos.Action = 'RESTORE',
                        r_pos.ActedAt = datetime({{timezone: 'UTC'}}),
                        r_pos.ActedBy = $acted_by
                    DELETE r
                    """,
                    gid=gid,
                    config_id=config_id,
                    new_rel_id=new_rel_id,
                    acted_by=acted_by,
                )

            res = await tx.run(
                f"""
                MATCH (l:LLM_Config {{id: $config_id}})
                SET l.Action = 'RESTORE',
                    l.ActedAt = datetime({{timezone: 'UTC'}}),
                    l.ActedBy = $acted_by
                WITH l
                OPTIONAL MATCH (g:Group)-[r:{pos_type}]->(l)
                WHERE (r.Action IS NULL OR r.Action <> 'DELETE')
                  AND (g.Action IS NULL OR g.Action <> 'DELETE')
                RETURN properties(l) AS config, collect(DISTINCT properties(g)) AS groups
                """,
                config_id=config_id,
                acted_by=acted_by,
            )
            r2 = await res.single()
            if not r2:
                return None
            return r2.get("config", {}), r2.get("groups", [])

        tx_res = await self.client.execute_write_tx(_restore_tx)
        if not tx_res:
            return None
        restored_props, groups = tx_res
        record_audit(
            action=AuditAction.LLM_CONFIGURATION_RESTORED,
            actor_id=f"user:{acted_by}",
            actor=acted_by,
            target_id=f"llm_config:{config_id}",
            target=f"LLM_Config {config_id}",
            status_code=200,
            status_message=f"LLM_Config {config_id} restored",
            severity="INFO",
            result="LLM_Config restored",
            details={"config_id": config_id},
            test_id=restored_props.get("test_id"),
        )
        return _dict_to_llm_config_response(restored_props, groups)


llm_config_service = LLMConfigService()
