from typing import Any, Dict, List, LiteralString, Optional, Tuple, Union, cast

from gosmart_graphite.actor.audit_actor import record_audit
from gosmart_graphite.core.logging import get_logger
from gosmart_graphite.db.neo4j import Neo4jClient, neo4j_client
from gosmart_graphite.model.audit import AuditAction
from gosmart_graphite.model.auditable import generate_deterministic_id, to_negated_relation_type
from gosmart_graphite.model.auth import (
    BelongsToRelation,
    Group,
    GroupNode,
    HasAuthenticatorRelation,
    HasIdentityRelation,
    HasLostAndFoundRelation,
    MemberOfRelation,
    PerformedAtRelation,
    ScopedToRelation,
    ServesRelation,
    UsesAuthenticatorRelation,
)
from gosmart_graphite.model.dicom_config import HasDicomConfigurationRelation
from gosmart_graphite.model.embedding import EmbedsRelation
from gosmart_graphite.model.fhir.relations import FHIR_RELATION_CLASSES
from gosmart_graphite.model.fhir_config import HasFhirConfigRelation
from gosmart_graphite.model.graph import (
    HasInstanceRelation,
    HasSeriesRelation,
    HasStudyRelation,
    SystemNode,
)
from gosmart_graphite.model.llm_config import HasLlmConfigRelation

logger = get_logger("gosmart_gs.group_service")

DEFAULT_MY_GROUP_MAX_DEPTH: int = 8
DEFAULT_GROUP_NAME: str = "GoSmart Health"
DEFAULT_GROUP_CODE: str = "GOSMART_HEALTH"
DEFAULT_GROUP_TYPE: str = "HEALTH_SYSTEM"
DEFAULT_GROUP_DESCRIPTION: str = "Catch-all root organization for unassociated and lost-and-found studies"

LOST_AND_FOUND_GROUP_NAME: str = "LostAndFound"
LOST_AND_FOUND_GROUP_CODE: str = "LOST_AND_FOUND"
LOST_AND_FOUND_GROUP_TYPE: str = "SYSTEM"
LOST_AND_FOUND_GROUP_DESCRIPTION: str = (
    "Operational recovery group associated with the root System node for unassociated or misconfigured nodes"
)

UPWARD_GROUP_RELATION_TYPES: Tuple[str, ...] = tuple(
    dict.fromkeys(
        (
            # Group hierarchy & direct tenancy / auth affiliations
            MemberOfRelation.__type__,
            HasIdentityRelation.__type__,
            UsesAuthenticatorRelation.__type__,
            HasAuthenticatorRelation.__type__,
            BelongsToRelation.__type__,
            PerformedAtRelation.__type__,
            ServesRelation.__type__,
            ScopedToRelation.__type__,
            # Configuration sponsorship
            HasDicomConfigurationRelation.__type__,
            HasLlmConfigRelation.__type__,
            HasFhirConfigRelation.__type__,
            # DICOM hierarchy
            HasStudyRelation.__type__,
            HasSeriesRelation.__type__,
            HasInstanceRelation.__type__,
            # Cross-domain & clinical FHIR relationships
            *(rel_cls.__type__ for rel_cls in FHIR_RELATION_CLASSES),
            # Decoupled vector embeddings
            EmbedsRelation.__type__,
        )
    )
)


def _clean_neo4j_value(val: Any) -> Any:
    """Normalize Neo4j specific types to standard Python types."""
    if hasattr(val, "to_native"):
        return val.to_native()
    if isinstance(val, dict):
        return {k: _clean_neo4j_value(v) for k, v in val.items()}
    if isinstance(val, list):
        return [_clean_neo4j_value(v) for v in val]
    return val


def _dict_to_group_node(data: Optional[Dict[str, Any]]) -> Optional[GroupNode]:
    """Convert a cleaned Neo4j Group property dictionary into a validated GroupNode."""
    if not data:
        return None
    clean = _clean_neo4j_value(data)
    if not isinstance(clean, dict) or not clean.get("name") or not clean.get("code"):
        return None
    return GroupNode(**clean)


def _build_default_lost_and_found_group(
    test_id: Optional[str] = None,
    acted_by: str = "system",
) -> GroupNode:
    """Construct the deterministic LostAndFound GroupNode."""
    return GroupNode(
        name=LOST_AND_FOUND_GROUP_NAME,
        code=LOST_AND_FOUND_GROUP_CODE,
        group_type=LOST_AND_FOUND_GROUP_TYPE,
        is_active=True,
        description=LOST_AND_FOUND_GROUP_DESCRIPTION,
        test_id=test_id,
        ActedBy=acted_by,
    )


def _validate_max_depth(max_depth: int) -> int:
    """Validate that max_depth is a non-negative integer."""
    if isinstance(max_depth, bool) or not isinstance(max_depth, int) or max_depth < 0:
        raise ValueError(f"max_depth must be a non-negative integer, got {max_depth!r}")
    return max_depth


def _build_my_group_query(max_depth: int = DEFAULT_MY_GROUP_MAX_DEPTH) -> LiteralString:
    """Build Cypher query traversing curated upward/group-affiliation relationships up to max_depth."""
    depth = _validate_max_depth(max_depth)
    rel_pattern = "|".join(UPWARD_GROUP_RELATION_TYPES)
    query = f"""
    MATCH p = (start {{id: $node_id}})-[:{rel_pattern}*0..{depth}]-(g:Group)
    WHERE (start.Action IS NULL OR start.Action <> 'DELETE')
      AND (g.Action IS NULL OR g.Action <> 'DELETE')
      AND coalesce(g.is_active, true) = true
      AND ALL(
        n IN nodes(p)
        WHERE (n.Action IS NULL OR n.Action <> 'DELETE')
          AND NOT 'System' IN labels(n)
      )
      AND ALL(
        r IN relationships(p)
        WHERE (r.Action IS NULL OR r.Action <> 'DELETE')
          AND NOT type(r) STARTS WITH 'NOT_'
          AND (type(r) <> 'MEMBER_OF' OR coalesce(r.status, 'ACTIVE') = 'ACTIVE')
      )
    WITH g, p, relationships(p) AS rels
    RETURN properties(g) AS group, length(p) AS depth
    ORDER BY depth ASC,
             CASE
               WHEN any(r IN rels WHERE type(r) = 'MEMBER_OF' AND coalesce(r.is_primary, false) = true)
               THEN 0
               ELSE 1
             END ASC,
             g.code ASC,
             g.id ASC
    LIMIT 1
    """
    return cast(LiteralString, query)


_ENSURE_LOST_AND_FOUND_CYPHER: LiteralString = """
MERGE (s:System {id: $sys_id})
ON CREATE SET s.name = $sys_name,
              s.owner = $sys_owner,
              s.address = $sys_address,
              s.email = $sys_email,
              s.phone = $sys_phone,
              s.serial_number = $sys_serial_number,
              s.installed_at = datetime({timezone: 'UTC'}),
              s.test_id = $test_id,
              s.Action = 'CREATE',
              s.ActedAt = datetime({timezone: 'UTC'}),
              s.ActedBy = $acted_by,
              s.CreatedAt = datetime({timezone: 'UTC'})
MERGE (g:Group {code: $code})
ON CREATE SET g.id = $id,
              g.name = $name,
              g.group_type = $group_type,
              g.is_active = true,
              g.description = $description,
              g.test_id = $test_id,
              g.Action = 'CREATE',
              g.ActedAt = datetime({timezone: 'UTC'}),
              g.ActedBy = $acted_by,
              g.CreatedAt = datetime({timezone: 'UTC'})
ON MATCH SET  g.name = $name,
              g.group_type = $group_type,
              g.is_active = true,
              g.description = $description,
              g.Action = 'UPDATE',
              g.ActedAt = datetime({timezone: 'UTC'}),
              g.ActedBy = $acted_by,
              g.UpdatedAt = datetime({timezone: 'UTC'})
MERGE (s)-[r:HAS_LOST_AND_FOUND {id: $rel_id}]->(g)
ON CREATE SET r.start_node_id = s.id,
              r.end_node_id = g.id,
              r.test_id = $test_id,
              r.Action = 'CREATE',
              r.ActedAt = datetime({timezone: 'UTC'}),
              r.ActedBy = $acted_by,
              r.CreatedAt = datetime({timezone: 'UTC'})
ON MATCH SET  r.Action = 'UPDATE',
              r.ActedAt = datetime({timezone: 'UTC'}),
              r.ActedBy = $acted_by,
              r.UpdatedAt = datetime({timezone: 'UTC'})
RETURN properties(g) AS group
"""


def _build_lost_and_found_params(
    test_id: Optional[str] = None,
    acted_by: str = "system",
) -> Tuple[GroupNode, Dict[str, Any]]:
    """Build parameters for persisting the System -> HAS_LOST_AND_FOUND -> LostAndFound Group in Neo4j."""
    sys_node = SystemNode(test_id=test_id, ActedBy=acted_by)
    grp_node = _build_default_lost_and_found_group(test_id=test_id, acted_by=acted_by)
    rel = HasLostAndFoundRelation(
        start_node_id=sys_node.id,
        end_node_id=grp_node.id,
        test_id=test_id,
        ActedBy=acted_by,
    )
    params: Dict[str, Any] = {
        "sys_id": sys_node.id,
        "sys_name": sys_node.name,
        "sys_owner": sys_node.owner,
        "sys_address": sys_node.address,
        "sys_email": sys_node.email,
        "sys_phone": sys_node.phone,
        "sys_serial_number": sys_node.serial_number,
        "id": grp_node.id,
        "name": grp_node.name,
        "code": grp_node.code,
        "group_type": grp_node.group_type,
        "description": grp_node.description,
        "rel_id": rel.id,
        "test_id": grp_node.test_id,
        "acted_by": acted_by,
    }
    return grp_node, params


_ENSURE_CATCH_ALL_GROUP_CYPHER: LiteralString = """
MERGE (s:System {id: $sys_id})
ON CREATE SET s.name = $sys_name,
              s.owner = $sys_owner,
              s.address = $sys_address,
              s.email = $sys_email,
              s.phone = $sys_phone,
              s.serial_number = $sys_serial_number,
              s.installed_at = datetime({timezone: 'UTC'}),
              s.test_id = $test_id,
              s.Action = 'CREATE',
              s.ActedAt = datetime({timezone: 'UTC'}),
              s.ActedBy = $acted_by,
              s.CreatedAt = datetime({timezone: 'UTC'})
MERGE (g:Group {code: $code})
ON CREATE SET g.id = $id,
              g.name = $name,
              g.group_type = $group_type,
              g.is_active = true,
              g.description = $description,
              g.test_id = $test_id,
              g.Action = 'CREATE',
              g.ActedAt = datetime({timezone: 'UTC'}),
              g.ActedBy = $acted_by,
              g.CreatedAt = datetime({timezone: 'UTC'})
ON MATCH SET  g.name = $name,
              g.group_type = $group_type,
              g.is_active = true,
              g.description = $description,
              g.Action = 'UPDATE',
              g.ActedAt = datetime({timezone: 'UTC'}),
              g.ActedBy = $acted_by,
              g.UpdatedAt = datetime({timezone: 'UTC'})
MERGE (g)-[r:BELONGS_TO {id: $rel_id}]->(s)
ON CREATE SET r.start_node_id = g.id,
              r.end_node_id = s.id,
              r.test_id = $test_id,
              r.Action = 'CREATE',
              r.ActedAt = datetime({timezone: 'UTC'}),
              r.ActedBy = $acted_by,
              r.CreatedAt = datetime({timezone: 'UTC'})
ON MATCH SET  r.Action = 'UPDATE',
              r.ActedAt = datetime({timezone: 'UTC'}),
              r.ActedBy = $acted_by,
              r.UpdatedAt = datetime({timezone: 'UTC'})
RETURN properties(g) AS group
"""


def _build_catch_all_group_params(
    test_id: Optional[str] = None,
    acted_by: str = "system",
) -> Tuple[GroupNode, Dict[str, Any]]:
    """Build parameters for persisting GoSmart Health Group -> BELONGS_TO -> System in Neo4j."""
    sys_node = SystemNode(test_id=test_id, ActedBy=acted_by)
    grp_node = GroupNode(
        name=DEFAULT_GROUP_NAME,
        code=DEFAULT_GROUP_CODE,
        group_type=DEFAULT_GROUP_TYPE,
        is_active=True,
        description=DEFAULT_GROUP_DESCRIPTION,
        test_id=test_id,
        ActedBy=acted_by,
    )
    rel = BelongsToRelation(
        start_node_id=grp_node.id,
        end_node_id=sys_node.id,
        test_id=test_id,
        ActedBy=acted_by,
    )
    params: Dict[str, Any] = {
        "sys_id": sys_node.id,
        "sys_name": sys_node.name,
        "sys_owner": sys_node.owner,
        "sys_address": sys_node.address,
        "sys_email": sys_node.email,
        "sys_phone": sys_node.phone,
        "sys_serial_number": sys_node.serial_number,
        "id": grp_node.id,
        "name": grp_node.name,
        "code": grp_node.code,
        "group_type": grp_node.group_type,
        "description": grp_node.description,
        "rel_id": rel.id,
        "test_id": grp_node.test_id,
        "acted_by": acted_by,
    }
    return grp_node, params


class GroupService:
    """Service for managing organizational groups, facility hierarchies, and user affiliations in Neo4j."""

    def __init__(self, client: Optional[Neo4jClient] = None) -> None:
        self.client = client or neo4j_client
        self._lost_and_found_group: GroupNode = _build_default_lost_and_found_group()

    @staticmethod
    def _build_resolve_group_query(identifier: Optional[str]) -> Tuple[LiteralString, Optional[str]]:
        clean_ident = identifier.strip() if isinstance(identifier, str) and identifier.strip() else None
        if clean_ident is None:
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
        WHERE (g.id = $identifier OR g.name = $identifier OR toUpper(g.code) = toUpper($identifier))
          AND (g.Action IS NULL OR g.Action <> 'DELETE')
          AND coalesce(g.is_active, true) = true
          AND (
            (($test_id IS NULL OR $test_id = '') AND (g.test_id IS NULL OR trim(toString(g.test_id)) = ''))
            OR ($test_id IS NOT NULL AND $test_id <> '' AND g.test_id = $test_id)
          )
        RETURN properties(g) AS group
        """
        return query_by_ident, clean_ident

    @staticmethod
    def _validate_resolved_group_records(
        records: List[Dict[str, Any]],
        clean_ident: Optional[str],
    ) -> Dict[str, Any]:
        target_label = clean_ident or DEFAULT_GROUP_NAME
        if not records:
            if clean_ident is None:
                raise RuntimeError(
                    f"Default group '{DEFAULT_GROUP_NAME}' not found in Neo4j. "
                    "Ensure system bootstrap has run or specify --assign <name or id>."
                )
            raise RuntimeError(f"Target group '{target_label}' not found in Neo4j.")
        if len(records) > 1:
            raise RuntimeError(
                f"Ambiguous group match for '{target_label}': found {len(records)} active Group nodes. "
                "Please specify a unique Group ID."
            )
        return _clean_neo4j_value(records[0].get("group", records[0]))

    async def add_user_to_group(
        self,
        user_id: str,
        group_id: str,
        roles: Optional[List[str]] = None,
        permissions: Optional[List[str]] = None,
        status: str = "ACTIVE",
        is_primary: bool = False,
        test_id: Optional[str] = None,
        acted_by: str = "system",
    ) -> Dict[str, Any]:
        """Affiliate a User with a Group via MEMBER_OF relationship."""
        roles_list = roles or ["Viewer"]
        perms_list = permissions or []
        rel = MemberOfRelation(
            start_node_id=user_id,
            end_node_id=group_id,
            roles=roles_list,
            permissions=perms_list,
            status=status,
            is_primary=is_primary,
            test_id=test_id,
            ActedBy=acted_by,
        )

        query = """
        MATCH (u:User {id: $user_id})
        MATCH (g:Group {id: $group_id})
        MERGE (u)-[r:MEMBER_OF {id: $rel_id}]->(g)
        ON CREATE SET r.roles = $roles,
                      r.permissions = $permissions,
                      r.status = $status,
                      r.is_primary = $is_primary,
                      r.test_id = $test_id,
                      r.Action = 'CREATE',
                      r.ActedAt = datetime({timezone: 'UTC'}),
                      r.ActedBy = $acted_by,
                      r.CreatedAt = datetime({timezone: 'UTC'})
        ON MATCH SET  r.roles = $roles,
                      r.permissions = $permissions,
                      r.status = $status,
                      r.is_primary = $is_primary,
                      r.test_id = coalesce($test_id, r.test_id),
                      r.Action = 'UPDATE',
                      r.ActedAt = datetime({timezone: 'UTC'}),
                      r.ActedBy = $acted_by,
                      r.UpdatedAt = datetime({timezone: 'UTC'})
        RETURN properties(r) AS member_of, properties(g) AS group
        """
        params = {
            "user_id": user_id,
            "group_id": group_id,
            "rel_id": rel.id,
            "roles": roles_list,
            "permissions": perms_list,
            "status": status,
            "is_primary": is_primary,
            "test_id": test_id,
            "acted_by": acted_by,
        }
        records = await self.client.execute_query(query, params)
        if records:
            return _clean_neo4j_value(records[0])
        return {}

    async def associate_dicom_config_to_group(
        self,
        config_id: str,
        group_id: str,
        test_id: Optional[str] = None,
        acted_by: str = "system",
    ) -> Dict[str, Any]:
        """Associate a DICOMConfiguration to a Group via HAS_DICOM_CONFIGURATION and SERVES relationships.

        Accepts group_id as an exact Group ID, Group name, or Group code.
        """
        norm_test_id = test_id.strip() if isinstance(test_id, str) and test_id.strip() else None
        target_group = await self.resolve_target_group(identifier=group_id, test_id=norm_test_id)
        resolved_group_id = target_group["id"]

        rel_serves = ServesRelation(
            start_node_id=config_id,
            end_node_id=resolved_group_id,
            test_id=norm_test_id,
            ActedBy=acted_by,
        )
        rel_has = HasDicomConfigurationRelation(
            start_node_id=resolved_group_id,
            end_node_id=config_id,
            test_id=norm_test_id,
            ActedBy=acted_by,
        )
        neg_serves = to_negated_relation_type("SERVES")
        neg_has = to_negated_relation_type("HAS_DICOM_CONFIGURATION")

        query = f"""
        MATCH (c:DICOMConfiguration {{id: $config_id}})
        MATCH (g:Group {{id: $group_id}})
        OPTIONAL MATCH (g)-[stale_not_has:{neg_has}]->(c)
        DELETE stale_not_has
        WITH c, g
        OPTIONAL MATCH (c)-[stale_not_serves:{neg_serves}]->(g)
        DELETE stale_not_serves
        WITH c, g
        MERGE (g)-[r_has:HAS_DICOM_CONFIGURATION {{id: $has_rel_id}}]->(c)
        ON CREATE SET r_has.test_id = $test_id,
                      r_has.Action = 'CREATE',
                      r_has.ActedAt = datetime({{timezone: 'UTC'}}),
                      r_has.ActedBy = $acted_by,
                      r_has.CreatedAt = datetime({{timezone: 'UTC'}})
        ON MATCH SET  r_has.test_id = coalesce($test_id, r_has.test_id),
                      r_has.Action = 'UPDATE',
                      r_has.ActedAt = datetime({{timezone: 'UTC'}}),
                      r_has.ActedBy = $acted_by,
                      r_has.UpdatedAt = datetime({{timezone: 'UTC'}})
        MERGE (c)-[r:SERVES {{id: $rel_id}}]->(g)
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
        RETURN properties(r) AS rel, properties(c) AS config, properties(g) AS group
        """
        params = {
            "config_id": config_id,
            "group_id": resolved_group_id,
            "rel_id": rel_serves.id,
            "has_rel_id": rel_has.id,
            "test_id": norm_test_id,
            "acted_by": acted_by,
        }
        records = await self.client.execute_query(query, params)
        res = _clean_neo4j_value(records[0]) if records else {}

        record_audit(
            action=AuditAction.DICOM_CONFIGURATION_SERVES_GROUP,
            actor_id=f"user:{acted_by}",
            actor=acted_by,
            target_id=f"config:{config_id}",
            target=f"Group {resolved_group_id}",
            status_code=200,
            status_message=f"DICOM Configuration {config_id} now serves Group {resolved_group_id}",
            severity="INFO",
            result="HAS_DICOM_CONFIGURATION and SERVES relationships established",
            details={"config_id": config_id, "group_id": resolved_group_id},
            test_id=norm_test_id,
        )
        return res

    async def associate_study_and_patient_to_group(
        self,
        patient_id: str,
        study_uid: str,
        group_id: str,
        test_id: Optional[str] = None,
        acted_by: str = "system",
    ) -> Dict[str, Any]:
        """Attach Patient (BELONGS_TO) and Study (PERFORMED_AT) to target Group."""
        query = """
        MATCH (p:Patient {patient_id: $patient_id})
        MATCH (s:Study {study_instance_uid: $study_uid})
        MATCH (g:Group {id: $group_id})
        MERGE (p)-[r_p:BELONGS_TO {start_node_id: p.id, end_node_id: g.id}]->(g)
        ON CREATE SET r_p.test_id = $test_id,
                      r_p.Action = 'CREATE',
                      r_p.ActedAt = datetime({timezone: 'UTC'}),
                      r_p.ActedBy = $acted_by,
                      r_p.CreatedAt = datetime({timezone: 'UTC'})
        MERGE (s)-[r_s:PERFORMED_AT {start_node_id: s.id, end_node_id: g.id}]->(g)
        ON CREATE SET r_s.test_id = $test_id,
                      r_s.Action = 'CREATE',
                      r_s.ActedAt = datetime({timezone: 'UTC'}),
                      r_s.ActedBy = $acted_by,
                      r_s.CreatedAt = datetime({timezone: 'UTC'})
        RETURN properties(p) AS patient, properties(s) AS study, properties(g) AS group
        """
        records = await self.client.execute_query(
            query,
            {
                "patient_id": patient_id,
                "study_uid": study_uid,
                "group_id": group_id,
                "test_id": test_id,
                "acted_by": acted_by,
            },
        )
        res = _clean_neo4j_value(records[0]) if records else {}
        record_audit(
            action=AuditAction.STUDY_ASSOCIATED_TO_GROUP,
            actor_id=f"user:{acted_by}",
            actor=acted_by,
            target_id=f"study:{study_uid}",
            target=f"Group {group_id}",
            status_code=200,
            status_message=f"Study {study_uid} attached to Group {group_id}",
            severity="INFO",
            result="Study PERFORMED_AT association created",
            details={"study_instance_uid": study_uid, "patient_id": patient_id, "group_id": group_id},
            test_id=test_id,
        )
        return res

    async def create_group(
        self,
        name: str,
        code: str,
        group_type: str = "FACILITY",
        is_active: bool = True,
        description: Optional[str] = None,
        test_id: Optional[str] = None,
        acted_by: str = "system",
    ) -> Dict[str, Any]:
        """Create or update an organizational group/facility node."""
        node = GroupNode(
            name=name,
            code=code,
            group_type=group_type,
            is_active=is_active,
            description=description,
            test_id=test_id,
            ActedBy=acted_by,
        )

        query = """
        MERGE (g:Group {code: $code})
        ON CREATE SET g.id = $id,
                      g.name = $name,
                      g.group_type = $group_type,
                      g.is_active = $is_active,
                      g.description = $description,
                      g.test_id = $test_id,
                      g.Action = 'CREATE',
                      g.ActedAt = datetime({timezone: 'UTC'}),
                      g.ActedBy = $acted_by,
                      g.CreatedAt = datetime({timezone: 'UTC'})
        ON MATCH SET  g.name = $name,
                      g.group_type = $group_type,
                      g.is_active = $is_active,
                      g.description = $description,
                      g.test_id = coalesce($test_id, g.test_id),
                      g.Action = 'UPDATE',
                      g.ActedAt = datetime({timezone: 'UTC'}),
                      g.ActedBy = $acted_by,
                      g.UpdatedAt = datetime({timezone: 'UTC'})
        RETURN properties(g) AS group
        """
        params = {
            "id": node.id,
            "name": node.name,
            "code": node.code,
            "group_type": node.group_type,
            "is_active": node.is_active,
            "description": node.description,
            "test_id": node.test_id,
            "acted_by": acted_by,
        }
        records = await self.client.execute_query(query, params)
        if records:
            return _clean_neo4j_value(records[0].get("group", records[0]))
        return {}

    async def dissociate_dicom_config_from_group(
        self,
        config_id: str,
        group_id: str,
        acted_by: str = "system",
    ) -> Optional[Dict[str, Any]]:
        """Dissociate a DICOMConfiguration from a Group by negating HAS_DICOM_CONFIGURATION and SERVES."""
        neg_serves = to_negated_relation_type("SERVES")
        neg_has = to_negated_relation_type("HAS_DICOM_CONFIGURATION")
        new_serves_rel_id = generate_deterministic_id(
            {"start_node_id": config_id, "type": neg_serves, "end_node_id": group_id}
        )
        new_has_rel_id = generate_deterministic_id(
            {"start_node_id": group_id, "type": neg_has, "end_node_id": config_id}
        )

        query = f"""
        MATCH (c:DICOMConfiguration {{id: $config_id}})
        MATCH (g:Group {{id: $group_id}})
        OPTIONAL MATCH (c)-[r_serves:SERVES]->(g)
        OPTIONAL MATCH (g)-[r_has:HAS_DICOM_CONFIGURATION]->(c)
        WITH c, g, r_serves, r_has
        WHERE r_serves IS NOT NULL OR r_has IS NOT NULL
        FOREACH (_ IN CASE WHEN r_serves IS NOT NULL THEN [1] ELSE [] END |
            CREATE (c)-[r_not_serves:{neg_serves}]->(g)
            SET r_not_serves = properties(r_serves),
                r_not_serves.id = $new_serves_rel_id,
                r_not_serves.Action = 'DELETE',
                r_not_serves.ActedAt = datetime({{timezone: 'UTC'}}),
                r_not_serves.ActedBy = $acted_by
            DELETE r_serves
        )
        FOREACH (_ IN CASE WHEN r_has IS NOT NULL THEN [1] ELSE [] END |
            CREATE (g)-[r_not_has:{neg_has}]->(c)
            SET r_not_has = properties(r_has),
                r_not_has.id = $new_has_rel_id,
                r_not_has.Action = 'DELETE',
                r_not_has.ActedAt = datetime({{timezone: 'UTC'}}),
                r_not_has.ActedBy = $acted_by
            DELETE r_has
        )
        WITH c, g
        OPTIONAL MATCH (c)-[r_not:{neg_serves}]->(g)
        RETURN properties(r_not) AS r_not
        """

        async def _swap_tx(tx: Any) -> Optional[Dict[str, Any]]:
            res = await tx.run(
                query,
                config_id=config_id,
                group_id=group_id,
                new_serves_rel_id=new_serves_rel_id,
                new_has_rel_id=new_has_rel_id,
                acted_by=acted_by,
            )
            rec = await res.single()
            if not rec:
                return None
            return _clean_neo4j_value(rec.data().get("r_not", rec.data()))

        result = await self.client.execute_write_tx(_swap_tx)
        if result:
            record_audit(
                action=AuditAction.DICOM_CONFIGURATION_DISSOCIATED,
                actor_id=f"user:{acted_by}",
                actor=acted_by,
                target_id=f"config:{config_id}",
                target=f"Group {group_id}",
                status_code=200,
                status_message=f"DICOM Configuration {config_id} dissociated from Group {group_id}",
                severity="INFO",
                result="HAS_DICOM_CONFIGURATION and SERVES relationships disabled",
                details={"config_id": config_id, "group_id": group_id},
            )
        return result

    async def ensure_lost_and_found_group(
        self,
        test_id: Optional[str] = None,
        acted_by: str = "system",
    ) -> GroupNode:
        """Ensure the 'LostAndFound' operational recovery Group and its System association exist in Neo4j."""
        default_node, params = _build_lost_and_found_params(test_id=test_id, acted_by=acted_by)
        try:
            records = await self.client.execute_query(_ENSURE_LOST_AND_FOUND_CYPHER, params)
            if records:
                persisted = _dict_to_group_node(records[0].get("group", records[0]))
                if persisted is not None:
                    if not test_id:
                        self._lost_and_found_group = persisted
                    return persisted
        except Exception as e:
            logger.warning("ensure_lost_and_found_group_error", error=str(e))
        if not test_id:
            self._lost_and_found_group = default_node
        return default_node

    def ensure_lost_and_found_group_sync(
        self,
        test_id: Optional[str] = None,
        acted_by: str = "system",
    ) -> GroupNode:
        """Synchronously ensure the 'LostAndFound' Group and its System association exist in Neo4j."""
        default_node, params = _build_lost_and_found_params(test_id=test_id, acted_by=acted_by)
        try:
            driver = self.client.get_sync_driver()
            with driver.session(database=self.client.settings.DB_DATABASE) as session:
                res = session.run(_ENSURE_LOST_AND_FOUND_CYPHER, **params)
                rec = res.single()
                if rec:
                    raw_data = rec.data() if hasattr(rec, "data") else dict(rec)
                    persisted = _dict_to_group_node(raw_data.get("group", raw_data))
                    if persisted is not None:
                        if not test_id:
                            self._lost_and_found_group = persisted
                        return persisted
        except Exception as e:
            logger.warning("ensure_lost_and_found_group_sync_error", error=str(e))
        if not test_id:
            self._lost_and_found_group = default_node
        return default_node

    async def get_effective_accessible_groups(
        self,
        user_id: str,
    ) -> List[Dict[str, Any]]:
        """Resolve effective group scope for a user by evaluating downward hierarchy from active memberships."""
        query = """
        MATCH (u:User {id: $user_id})-[r:MEMBER_OF {status: 'ACTIVE'}]->(root_grp:Group)
        WHERE (root_grp.Action IS NULL OR root_grp.Action <> 'DELETE')
          AND root_grp.is_active = true
        OPTIONAL MATCH sub_path = (sub_grp:Group)-[:BELONGS_TO*1..]->(root_grp)
        WHERE all(
            n IN nodes(sub_path)
            WHERE 'Group' IN labels(n)
              AND (n.Action IS NULL OR n.Action <> 'DELETE')
              AND n.is_active = true
          )
          AND all(rel IN relationships(sub_path) WHERE rel.Action IS NULL OR rel.Action <> 'DELETE')
        WITH collect(DISTINCT root_grp) + collect(DISTINCT sub_grp) AS accessible_groups
        UNWIND accessible_groups AS g
        RETURN DISTINCT g.id AS id,
                        g.name AS name,
                        g.code AS code,
                        g.group_type AS group_type,
                        g.is_active AS is_active
        ORDER BY g.name ASC
        """
        records = await self.client.execute_query(query, {"user_id": user_id})
        return [_clean_neo4j_value(r) for r in records]

    async def get_effective_accessible_groups_for_ae(self, ae_title: str) -> List[Dict[str, Any]]:
        """Resolve effective group scope for a caller AE title (group itself + downward closure)."""
        norm_aet = ae_title.strip()
        query = """
        MATCH (c:DICOMConfiguration {ae_title: $ae_title})-[r:HAS_DICOM_CONFIGURATION|SERVES]-(root_grp:Group)
        WHERE (c.Action IS NULL OR c.Action <> 'DELETE')
          AND (r.Action IS NULL OR r.Action <> 'DELETE')
          AND (root_grp.Action IS NULL OR root_grp.Action <> 'DELETE')
          AND coalesce(root_grp.is_active, true) = true
        OPTIONAL MATCH sub_path = (sub_grp:Group)-[:BELONGS_TO*1..]->(root_grp)
        WHERE all(
            n IN nodes(sub_path)
            WHERE 'Group' IN labels(n)
              AND (n.Action IS NULL OR n.Action <> 'DELETE')
              AND coalesce(n.is_active, true) = true
          )
          AND all(rel IN relationships(sub_path) WHERE rel.Action IS NULL OR rel.Action <> 'DELETE')
        WITH collect(DISTINCT root_grp) + collect(DISTINCT sub_grp) AS accessible_groups
        UNWIND accessible_groups AS g
        RETURN DISTINCT g.id AS id,
                        g.name AS name,
                        g.code AS code,
                        g.group_type AS group_type,
                        g.is_active AS is_active
        ORDER BY g.name ASC
        """
        records = await self.client.execute_query(query, {"ae_title": norm_aet})
        return [_clean_neo4j_value(r) for r in records]

    def get_effective_accessible_groups_for_ae_sync(self, ae_title: str) -> List[Dict[str, Any]]:
        """Synchronously resolve effective group scope for a caller AE title."""
        norm_aet = ae_title.strip()
        query = """
        MATCH (c:DICOMConfiguration {ae_title: $ae_title})-[r:HAS_DICOM_CONFIGURATION|SERVES]-(root_grp:Group)
        WHERE (c.Action IS NULL OR c.Action <> 'DELETE')
          AND (r.Action IS NULL OR r.Action <> 'DELETE')
          AND (root_grp.Action IS NULL OR root_grp.Action <> 'DELETE')
          AND coalesce(root_grp.is_active, true) = true
        OPTIONAL MATCH sub_path = (sub_grp:Group)-[:BELONGS_TO*1..]->(root_grp)
        WHERE all(
            n IN nodes(sub_path)
            WHERE 'Group' IN labels(n)
              AND (n.Action IS NULL OR n.Action <> 'DELETE')
              AND coalesce(n.is_active, true) = true
          )
          AND all(rel IN relationships(sub_path) WHERE rel.Action IS NULL OR rel.Action <> 'DELETE')
        WITH collect(DISTINCT root_grp) + collect(DISTINCT sub_grp) AS accessible_groups
        UNWIND accessible_groups AS g
        RETURN DISTINCT g.id AS id,
                        g.name AS name,
                        g.code AS code,
                        g.group_type AS group_type,
                        g.is_active AS is_active
        ORDER BY g.name ASC
        """
        try:
            driver = self.client.get_sync_driver()
            with driver.session(database=self.client.settings.DB_DATABASE) as session:
                res = session.run(query, ae_title=norm_aet)
                return [_clean_neo4j_value(r.data()) for r in res]
        except Exception as e:
            logger.warning("get_effective_accessible_groups_for_ae_sync_failed", ae_title=norm_aet, error=str(e))
            return []

    async def get_group_by_code(self, code: str) -> Optional[Dict[str, Any]]:
        """Fetch group node by code slug."""
        normalized_code = code.strip().upper()
        query = """
        MATCH (g:Group {code: $code})
        WHERE (g.Action IS NULL OR g.Action <> 'DELETE')
        RETURN properties(g) AS group
        LIMIT 1
        """
        records = await self.client.execute_query(query, {"code": normalized_code})
        if records:
            return _clean_neo4j_value(records[0].get("group", records[0]))
        return None

    async def get_group_by_id(self, group_id: str) -> Optional[Dict[str, Any]]:
        """Fetch group node by deterministic ID."""
        query = """
        MATCH (g:Group {id: $group_id})
        WHERE (g.Action IS NULL OR g.Action <> 'DELETE')
        RETURN properties(g) AS group
        LIMIT 1
        """
        records = await self.client.execute_query(query, {"group_id": group_id})
        if records:
            return _clean_neo4j_value(records[0].get("group", records[0]))
        return None

    async def get_group_for_ae_title(self, ae_title: str) -> Optional[Dict[str, Any]]:
        """Look up the active Group associated with the given remote or local AE Title."""
        norm_aet = ae_title.strip()
        query = """
        MATCH (c:DICOMConfiguration)-[r:HAS_DICOM_CONFIGURATION|SERVES]-(g:Group)
        WHERE (c.ae_title = $ae_title OR (c.is_promiscuous = true AND c.ae_title = '*'))
          AND (c.Action IS NULL OR c.Action <> 'DELETE')
          AND (r.Action IS NULL OR r.Action <> 'DELETE')
          AND (g.Action IS NULL OR g.Action <> 'DELETE')
          AND coalesce(g.is_active, true) = true
        RETURN properties(g) AS group, c.ae_title AS matched_aet
        ORDER BY CASE WHEN c.ae_title = $ae_title THEN 0 ELSE 1 END
        LIMIT 1
        """
        records = await self.client.execute_query(query, {"ae_title": norm_aet})
        if records:
            return _clean_neo4j_value(records[0].get("group"))
        return None

    def get_group_for_ae_title_sync(self, ae_title: str) -> Optional[Dict[str, Any]]:
        """Synchronously look up the active Group associated with the given AE Title."""
        norm_aet = ae_title.strip()
        query = """
        MATCH (c:DICOMConfiguration)-[r:HAS_DICOM_CONFIGURATION|SERVES]-(g:Group)
        WHERE (c.ae_title = $ae_title OR (c.is_promiscuous = true AND c.ae_title = '*'))
          AND (c.Action IS NULL OR c.Action <> 'DELETE')
          AND (r.Action IS NULL OR r.Action <> 'DELETE')
          AND (g.Action IS NULL OR g.Action <> 'DELETE')
          AND coalesce(g.is_active, true) = true
        RETURN properties(g) AS group, c.ae_title AS matched_aet
        ORDER BY CASE WHEN c.ae_title = $ae_title THEN 0 ELSE 1 END
        LIMIT 1
        """
        try:
            driver = self.client.get_sync_driver()
            with driver.session(database=self.client.settings.DB_DATABASE) as session:
                res = session.run(query, ae_title=norm_aet)
                rec = res.single()
                if rec:
                    return _clean_neo4j_value(rec["group"])
        except Exception as e:
            logger.warning("get_group_for_ae_title_sync_failed", ae_title=norm_aet, error=str(e))
        return None

    def get_storage_root_for_ae_title_sync(self, ae_title: str) -> Optional[str]:
        """Synchronously look up storage_root_uri from active DICOMConfiguration for an AE Title.

        Prefers an exact ae_title match. Falls back to a promiscuous '*' configuration only when
        its storage_root_uri is explicitly configured (i.e. does not contain '*').
        """
        norm_aet = (ae_title or "").strip()
        if not norm_aet:
            return None
        query = """
        MATCH (c:DICOMConfiguration)
        WHERE (c.ae_title = $ae_title OR (c.is_promiscuous = true AND c.ae_title = '*'))
          AND (c.Action IS NULL OR c.Action <> 'DELETE')
          AND c.storage_root_uri IS NOT NULL
          AND trim(c.storage_root_uri) <> ''
        RETURN c.storage_root_uri AS storage_root_uri, c.ae_title AS matched_aet
        ORDER BY CASE WHEN c.ae_title = $ae_title THEN 0 ELSE 1 END
        LIMIT 2
        """
        try:
            driver = self.client.get_sync_driver()
            with driver.session(database=self.client.settings.DB_DATABASE) as session:
                records = [r.data() for r in session.run(query, ae_title=norm_aet)]
                for rec in records:
                    uri = str(rec.get("storage_root_uri") or "").strip()
                    matched_aet = str(rec.get("matched_aet") or "").strip()
                    if not uri:
                        continue
                    if matched_aet == "*" and "*" in uri:
                        continue
                    return uri
        except Exception as e:
            logger.warning("get_storage_root_for_ae_title_sync_failed", ae_title=norm_aet, error=str(e))
        return None

    async def get_group_hierarchy_closure(
        self,
        group_id: str,
    ) -> List[Dict[str, Any]]:
        """Resolve downward hierarchy closure for a specific group (itself + child subgroups)."""
        query = """
        MATCH (root_grp:Group {id: $group_id})
        WHERE (root_grp.Action IS NULL OR root_grp.Action <> 'DELETE')
          AND root_grp.is_active = true
        OPTIONAL MATCH sub_path = (sub_grp:Group)-[:BELONGS_TO*1..]->(root_grp)
        WHERE all(
            n IN nodes(sub_path)
            WHERE 'Group' IN labels(n)
              AND (n.Action IS NULL OR n.Action <> 'DELETE')
              AND n.is_active = true
          )
          AND all(rel IN relationships(sub_path) WHERE rel.Action IS NULL OR rel.Action <> 'DELETE')
        WITH collect(DISTINCT root_grp) + collect(DISTINCT sub_grp) AS accessible_groups
        UNWIND accessible_groups AS g
        RETURN DISTINCT g.id AS id,
                        g.name AS name,
                        g.code AS code,
                        g.group_type AS group_type,
                        g.is_active AS is_active
        ORDER BY g.name ASC
        """
        records = await self.client.execute_query(query, {"group_id": group_id})
        return [_clean_neo4j_value(r) for r in records]

    async def get_or_create_catch_all_group(
        self,
        test_id: Optional[str] = None,
        acted_by: str = "system",
    ) -> Dict[str, Any]:
        """Ensure the resilient 'GoSmart Health' catch-all root organization exists and belongs to System."""
        _, params = _build_catch_all_group_params(test_id=test_id, acted_by=acted_by)
        records = await self.client.execute_query(_ENSURE_CATCH_ALL_GROUP_CYPHER, params)
        if records:
            return _clean_neo4j_value(records[0].get("group", records[0]))
        return {}

    def get_or_create_catch_all_group_sync(
        self,
        test_id: Optional[str] = None,
        acted_by: str = "system",
    ) -> Dict[str, Any]:
        """Synchronously ensure 'GoSmart Health' catch-all root organization exists and belongs to System."""
        node, params = _build_catch_all_group_params(test_id=test_id, acted_by=acted_by)
        try:
            driver = self.client.get_sync_driver()
            with driver.session(database=self.client.settings.DB_DATABASE) as session:
                res = session.run(_ENSURE_CATCH_ALL_GROUP_CYPHER, **params)
                rec = res.single()
                return _clean_neo4j_value(rec["group"]) if rec else {}
        except Exception as e:
            logger.warning("get_or_create_catch_all_group_sync_error", error=str(e))
            return {"id": node.id, "name": node.name, "code": node.code}

    async def get_user_affiliations(
        self,
        user_id: str,
        include_inactive: bool = False,
    ) -> List[Dict[str, Any]]:
        """Get all groups and roles associated with a user."""
        query = """
        MATCH (u:User {id: $user_id})-[r:MEMBER_OF]->(g:Group)
        WHERE (g.Action IS NULL OR g.Action <> 'DELETE')
          AND ($include_inactive = true OR (r.status = 'ACTIVE' AND g.is_active = true))
        RETURN g.id AS group_id,
               g.name AS group_name,
               g.code AS group_code,
               g.group_type AS group_type,
               r.roles AS roles,
               r.permissions AS permissions,
               r.status AS status,
               r.is_primary AS is_primary
        ORDER BY r.is_primary DESC, g.name ASC
        """
        records = await self.client.execute_query(query, {"user_id": user_id, "include_inactive": include_inactive})
        return [_clean_neo4j_value(r) for r in records]

    def group_or_lost_and_found(
        self,
        group: Optional[Union[Group, Dict[str, Any]]] = None,
    ) -> Group:
        """Return the provided Group if present, or fall back to the LostAndFound operational recovery Group."""
        if isinstance(group, GroupNode):
            return group
        if isinstance(group, dict):
            converted = _dict_to_group_node(group)
            if converted is not None:
                return converted
        return self._lost_and_found_group

    async def list_groups(
        self,
        include_inactive: bool = False,
        include_tests: bool = True,
        test_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """List groups with optional status and test_id filtering."""
        query = """
        MATCH (g:Group)
        WHERE (g.Action IS NULL OR g.Action <> 'DELETE')
          AND ($include_inactive = true OR g.is_active = true)
          AND ($include_tests = true OR g.test_id IS NULL OR g.test_id = '')
          AND ($test_id IS NULL OR g.test_id = $test_id)
        RETURN properties(g) AS group
        ORDER BY g.name ASC
        LIMIT $limit
        """
        records = await self.client.execute_query(
            query,
            {
                "include_inactive": include_inactive,
                "include_tests": include_tests,
                "test_id": test_id,
                "limit": limit,
            },
        )
        return [_clean_neo4j_value(r.get("group", r)) for r in records]

    @property
    def lost_and_found_group(self) -> GroupNode:
        """Return the cached LostAndFound operational recovery GroupNode."""
        return self._lost_and_found_group

    def _resolve_missing_group(
        self,
        node_id: str,
        max_depth: int,
        return_lost_and_found_group: bool,
        method: str = "my_group",
    ) -> Optional[GroupNode]:
        """Log the fallback decision and return LostAndFound or None when no Group is found."""
        if return_lost_and_found_group:
            fallback = self.lost_and_found_group
            logger.info(
                "my_group_not_found_returning_lost_and_found",
                method=method,
                node_id=node_id,
                max_depth=max_depth,
                return_lost_and_found_group=True,
                decision="return_lost_and_found_group",
                lost_and_found_group_id=fallback.id,
            )
            return fallback

        logger.info(
            "my_group_not_found_returning_none",
            method=method,
            node_id=node_id,
            max_depth=max_depth,
            return_lost_and_found_group=False,
            decision="return_none",
        )
        return None

    async def my_group(
        self,
        node_id: str,
        max_depth: int = DEFAULT_MY_GROUP_MAX_DEPTH,
        return_lost_and_found_group: bool = True,
    ) -> Optional[GroupNode]:
        """Traverse up from node_id (up to max_depth hops) to resolve the nearest active GroupNode."""
        depth = _validate_max_depth(max_depth)
        clean_id = node_id.strip() if isinstance(node_id, str) else ""
        if not clean_id:
            return None

        query = _build_my_group_query(max_depth=depth)
        records = await self.client.execute_query(query, {"node_id": clean_id})
        group_node = _dict_to_group_node(records[0].get("group", records[0])) if records else None
        if group_node is not None:
            return group_node
        return self._resolve_missing_group(
            node_id=clean_id,
            max_depth=depth,
            return_lost_and_found_group=return_lost_and_found_group,
            method="my_group",
        )

    def my_group_sync(
        self,
        node_id: str,
        max_depth: int = DEFAULT_MY_GROUP_MAX_DEPTH,
        return_lost_and_found_group: bool = True,
    ) -> Optional[GroupNode]:
        """Synchronously traverse up from node_id (up to max_depth hops) to resolve the nearest active GroupNode."""
        depth = _validate_max_depth(max_depth)
        clean_id = node_id.strip() if isinstance(node_id, str) else ""
        if not clean_id:
            return None

        query = _build_my_group_query(max_depth=depth)
        try:
            driver = self.client.get_sync_driver()
            with driver.session(database=self.client.settings.DB_DATABASE) as session:
                res = session.run(query, node_id=clean_id)
                rec = res.single()
                if rec:
                    raw_data = rec.data() if hasattr(rec, "data") else dict(rec)
                    group_node = _dict_to_group_node(raw_data.get("group", raw_data))
                    if group_node is not None:
                        return group_node
        except Exception as e:
            logger.warning("my_group_sync_failed", node_id=clean_id, error=str(e))

        return self._resolve_missing_group(
            node_id=clean_id,
            max_depth=depth,
            return_lost_and_found_group=return_lost_and_found_group,
            method="my_group_sync",
        )

    async def nest_group(
        self,
        child_group_id: str,
        parent_group_id: str,
        test_id: Optional[str] = None,
        acted_by: str = "system",
    ) -> Dict[str, Any]:
        """Establish hierarchical relationship: (Child Group)-[:BELONGS_TO]->(Parent Group)."""
        rel = BelongsToRelation(
            start_node_id=child_group_id,
            end_node_id=parent_group_id,
            test_id=test_id,
            ActedBy=acted_by,
        )

        query = """
        MATCH (child:Group {id: $child_id})
        MATCH (parent:Group {id: $parent_id})
        MERGE (child)-[r:BELONGS_TO {id: $rel_id}]->(parent)
        ON CREATE SET r.start_node_id = child.id,
                      r.end_node_id = parent.id,
                      r.test_id = $test_id,
                      r.Action = 'CREATE',
                      r.ActedAt = datetime({timezone: 'UTC'}),
                      r.ActedBy = $acted_by,
                      r.CreatedAt = datetime({timezone: 'UTC'})
        ON MATCH SET  r.test_id = coalesce($test_id, r.test_id),
                      r.Action = 'UPDATE',
                      r.ActedAt = datetime({timezone: 'UTC'}),
                      r.ActedBy = $acted_by,
                      r.UpdatedAt = datetime({timezone: 'UTC'})
        RETURN properties(r) AS rel, child.id AS child_id, parent.id AS parent_id
        """
        params = {
            "child_id": child_group_id,
            "parent_id": parent_group_id,
            "rel_id": rel.id,
            "test_id": test_id,
            "acted_by": acted_by,
        }
        records = await self.client.execute_query(query, params)
        if records:
            return _clean_neo4j_value(records[0])
        return {}

    async def reassociate_study_to_group(
        self,
        study_instance_uid: str,
        target_group_id: str,
        test_id: Optional[str] = None,
        acted_by: str = "system",
    ) -> Dict[str, Any]:
        """Triage tool: Re-associate study from old group / catch-all to target Group."""
        neg_type = to_negated_relation_type("PERFORMED_AT")

        # Swap existing PERFORMED_AT relationships
        find_query = """
        MATCH (s:Study {study_instance_uid: $study_uid})-[r:PERFORMED_AT]->(old_g:Group)
        RETURN s.id AS study_id, old_g.id AS old_group_id, r.id AS rel_id
        """
        existing_rels = await self.client.execute_query(find_query, {"study_uid": study_instance_uid})

        for row in existing_rels:
            old_gid = row.get("old_group_id")
            s_id = row.get("study_id")
            if old_gid and old_gid != target_group_id:
                new_neg_id = generate_deterministic_id(
                    {"start_node_id": s_id, "type": neg_type, "end_node_id": old_gid}
                )
                swap_q = f"""
                MATCH (s:Study {{id: $s_id}})-[r:PERFORMED_AT]->(g:Group {{id: $old_gid}})
                WITH s, g, r, properties(r) AS old_props
                CREATE (s)-[r_not:{neg_type}]->(g)
                SET r_not = old_props,
                    r_not.id = $new_neg_id,
                    r_not.Action = 'DELETE',
                    r_not.ActedAt = datetime({{timezone: 'UTC'}}),
                    r_not.ActedBy = $acted_by
                DELETE r
                """
                await self.client.execute_query(
                    swap_q,
                    {"s_id": s_id, "old_gid": old_gid, "new_neg_id": new_neg_id, "acted_by": acted_by},
                )

        # Create new PERFORMED_AT link to target group
        new_rel = PerformedAtRelation(
            start_node_id=generate_deterministic_id({"entity": "Study", "study_instance_uid": study_instance_uid}),
            end_node_id=target_group_id,
            test_id=test_id,
            ActedBy=acted_by,
        )

        link_query = """
        MATCH (s:Study {study_instance_uid: $study_uid})
        MATCH (g:Group {id: $target_group_id})
        MERGE (s)-[r:PERFORMED_AT {id: $rel_id}]->(g)
        ON CREATE SET r.test_id = $test_id,
                      r.Action = 'CREATE',
                      r.ActedAt = datetime({timezone: 'UTC'}),
                      r.ActedBy = $acted_by,
                      r.CreatedAt = datetime({timezone: 'UTC'})
        ON MATCH SET  r.test_id = coalesce($test_id, r.test_id),
                      r.Action = 'UPDATE',
                      r.ActedAt = datetime({timezone: 'UTC'}),
                      r.ActedBy = $acted_by
        WITH s, g
        OPTIONAL MATCH (p:Patient)-[:HAS_STUDY]->(s)
        FOREACH (_ IN CASE WHEN p IS NOT NULL THEN [1] ELSE [] END |
            MERGE (p)-[r_p:BELONGS_TO {start_node_id: p.id, end_node_id: g.id}]->(g)
            ON CREATE SET r_p.test_id = $test_id,
                          r_p.Action = 'CREATE',
                          r_p.ActedAt = datetime({timezone: 'UTC'}),
                          r_p.ActedBy = $acted_by,
                          r_p.CreatedAt = datetime({timezone: 'UTC'})
        )
        RETURN properties(s) AS study, properties(g) AS group
        """
        records = await self.client.execute_query(
            link_query,
            {
                "study_uid": study_instance_uid,
                "target_group_id": target_group_id,
                "rel_id": new_rel.id,
                "test_id": test_id,
                "acted_by": acted_by,
            },
        )
        res = _clean_neo4j_value(records[0]) if records else {}

        record_audit(
            action=AuditAction.STUDY_REASSOCIATED_TO_GROUP,
            actor_id=f"user:{acted_by}",
            actor=acted_by,
            target_id=f"study:{study_instance_uid}",
            target=f"Target Group {target_group_id}",
            status_code=200,
            status_message=f"Study {study_instance_uid} triaged/reassociated to Group {target_group_id}",
            severity="INFO",
            result="Study re-associated to target group",
            details={"study_instance_uid": study_instance_uid, "target_group_id": target_group_id},
            test_id=test_id,
        )
        return res

    async def resolve_target_group(
        self,
        identifier: Optional[str] = None,
        test_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Resolve a single active Group by id/name/code, or default 'GoSmart Health'.

        Raises RuntimeError when 0 or > 1 matching active groups exist.
        """
        norm_test_id = test_id.strip() if isinstance(test_id, str) and test_id.strip() else None
        query, clean_ident = self._build_resolve_group_query(identifier)
        records = await self.client.execute_query(
            query,
            {
                "identifier": clean_ident,
                "default_name": DEFAULT_GROUP_NAME,
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
        norm_test_id = test_id.strip() if isinstance(test_id, str) and test_id.strip() else None
        query, clean_ident = self._build_resolve_group_query(identifier)
        driver = self.client.get_sync_driver()
        with driver.session(database=self.client.settings.DB_DATABASE) as session:
            result = session.run(
                query,
                identifier=clean_ident,
                default_name=DEFAULT_GROUP_NAME,
                test_id=norm_test_id,
            )
            records = [r.data() for r in result]
        return self._validate_resolved_group_records(records, clean_ident)

    async def update_user_group_status(
        self,
        user_id: str,
        group_id: str,
        status: str,
        acted_by: str = "system",
    ) -> Optional[Dict[str, Any]]:
        """Update affiliation status (e.g. 'ACTIVE', 'INACTIVE', 'SUSPENDED') on MEMBER_OF relationship."""
        query = """
        MATCH (u:User {id: $user_id})-[r:MEMBER_OF]->(g:Group {id: $group_id})
        SET r.status = $status,
            r.Action = 'UPDATE',
            r.ActedAt = datetime({timezone: 'UTC'}),
            r.ActedBy = $acted_by,
            r.UpdatedAt = datetime({timezone: 'UTC'})
        RETURN properties(r) AS member_of
        """
        records = await self.client.execute_query(
            query,
            {"user_id": user_id, "group_id": group_id, "status": status, "acted_by": acted_by},
        )
        if records:
            return _clean_neo4j_value(records[0].get("member_of", records[0]))
        return None


group_service = GroupService()


async def my_group(
    node_id: str,
    max_depth: int = DEFAULT_MY_GROUP_MAX_DEPTH,
    return_lost_and_found_group: bool = True,
) -> Optional[GroupNode]:
    """Traverse up from node_id (up to max_depth hops) to resolve the nearest active GroupNode."""
    return await group_service.my_group(
        node_id=node_id,
        max_depth=max_depth,
        return_lost_and_found_group=return_lost_and_found_group,
    )


def my_group_sync(
    node_id: str,
    max_depth: int = DEFAULT_MY_GROUP_MAX_DEPTH,
    return_lost_and_found_group: bool = True,
) -> Optional[GroupNode]:
    """Synchronously traverse up from node_id (up to max_depth hops) to resolve the nearest active GroupNode."""
    return group_service.my_group_sync(
        node_id=node_id,
        max_depth=max_depth,
        return_lost_and_found_group=return_lost_and_found_group,
    )


def group_or_lost_and_found(
    group: Optional[Union[Group, Dict[str, Any]]] = None,
) -> Group:
    """Return the provided Group if present, or fall back to the LostAndFound operational recovery Group."""
    return group_service.group_or_lost_and_found(group)
