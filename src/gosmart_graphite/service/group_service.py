from typing import Any, Dict, List, Optional

from gosmart_graphite.actor.audit_actor import record_audit
from gosmart_graphite.core.logging import get_logger
from gosmart_graphite.db.neo4j import Neo4jClient, neo4j_client
from gosmart_graphite.model.audit import AuditAction
from gosmart_graphite.model.auth import (
    GroupNode,
    MemberOfRelation,
    PerformedAtRelation,
    ServesRelation,
    SubGroupOfRelation,
)
from gosmart_graphite.model.graph import generate_deterministic_id, to_negated_relation_type

logger = get_logger("gosmart_gs.group_service")


def _clean_neo4j_value(val: Any) -> Any:
    """Normalize Neo4j specific types to standard Python types."""
    if hasattr(val, "to_native"):
        return val.to_native()
    if isinstance(val, dict):
        return {k: _clean_neo4j_value(v) for k, v in val.items()}
    if isinstance(val, list):
        return [_clean_neo4j_value(v) for v in val]
    return val


class GroupService:
    """Service for managing organizational groups, facility hierarchies, and user affiliations in Neo4j."""

    def __init__(self, client: Optional[Neo4jClient] = None) -> None:
        self.client = client or neo4j_client

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

    async def nest_group(
        self,
        child_group_id: str,
        parent_group_id: str,
        test_id: Optional[str] = None,
        acted_by: str = "system",
    ) -> Dict[str, Any]:
        """Establish hierarchical relationship: (Child Group)-[:SUB_GROUP_OF]->(Parent Group)."""
        rel = SubGroupOfRelation(
            start_node_id=child_group_id,
            end_node_id=parent_group_id,
            test_id=test_id,
            ActedBy=acted_by,
        )

        query = """
        MATCH (child:Group {id: $child_id})
        MATCH (parent:Group {id: $parent_id})
        MERGE (child)-[r:SUB_GROUP_OF {id: $rel_id}]->(parent)
        ON CREATE SET r.test_id = $test_id,
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

    async def get_group_hierarchy_closure(
        self,
        group_id: str,
    ) -> List[Dict[str, Any]]:
        """Resolve downward hierarchy closure for a specific group (itself + child subgroups)."""
        query = """
        MATCH (root_grp:Group {id: $group_id})
        WHERE (root_grp.Action IS NULL OR root_grp.Action <> 'DELETE')
          AND root_grp.is_active = true
        OPTIONAL MATCH (sub_grp:Group)-[:SUB_GROUP_OF*1..]->(root_grp)
        WHERE (sub_grp.Action IS NULL OR sub_grp.Action <> 'DELETE')
          AND sub_grp.is_active = true
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

    async def get_effective_accessible_groups(
        self,
        user_id: str,
    ) -> List[Dict[str, Any]]:
        """Resolve effective group scope for a user by evaluating downward hierarchy from active memberships."""
        query = """
        MATCH (u:User {id: $user_id})-[r:MEMBER_OF {status: 'ACTIVE'}]->(root_grp:Group)
        WHERE (root_grp.Action IS NULL OR root_grp.Action <> 'DELETE')
          AND root_grp.is_active = true
        OPTIONAL MATCH (sub_grp:Group)-[:SUB_GROUP_OF*1..]->(root_grp)
        WHERE (sub_grp.Action IS NULL OR sub_grp.Action <> 'DELETE')
          AND sub_grp.is_active = true
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

    async def get_or_create_catch_all_group(
        self,
        test_id: Optional[str] = None,
        acted_by: str = "system",
    ) -> Dict[str, Any]:
        """Ensure the resilient 'GoSmart Health' catch-all root organization exists."""
        return await self.create_group(
            name="GoSmart Health",
            code="GOSMART_HEALTH",
            group_type="HEALTH_SYSTEM",
            description="Catch-all root organization for unassociated and lost-and-found studies",
            test_id=test_id,
            acted_by=acted_by,
        )

    def get_or_create_catch_all_group_sync(
        self,
        test_id: Optional[str] = None,
        acted_by: str = "system",
    ) -> Dict[str, Any]:
        """Synchronously ensure the resilient 'GoSmart Health' catch-all root organization exists."""
        node = GroupNode(
            name="GoSmart Health",
            code="GOSMART_HEALTH",
            group_type="HEALTH_SYSTEM",
            description="Catch-all root organization for unassociated and lost-and-found studies",
            test_id=test_id,
            ActedBy=acted_by,
        )
        query = """
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
            "description": node.description,
            "test_id": node.test_id,
            "acted_by": acted_by,
        }
        try:
            driver = self.client.get_sync_driver()
            with driver.session(database=self.client.settings.DB_DATABASE) as session:
                res = session.run(query, **params)
                rec = res.single()
                return _clean_neo4j_value(rec["group"]) if rec else {}
        except Exception as e:
            logger.warning("get_or_create_catch_all_group_sync_error", error=str(e))
            return {"id": node.id, "name": node.name, "code": node.code}

    async def associate_dicom_config_to_group(
        self,
        config_id: str,
        group_id: str,
        test_id: Optional[str] = None,
        acted_by: str = "system",
    ) -> Dict[str, Any]:
        """Associate a DICOMConfiguration to a Group via SERVES relationship."""
        rel = ServesRelation(
            start_node_id=config_id,
            end_node_id=group_id,
            test_id=test_id,
            ActedBy=acted_by,
        )

        query = """
        MATCH (c:DICOMConfiguration {id: $config_id})
        MATCH (g:Group {id: $group_id})
        MERGE (c)-[r:SERVES {id: $rel_id}]->(g)
        ON CREATE SET r.test_id = $test_id,
                      r.Action = 'CREATE',
                      r.ActedAt = datetime({timezone: 'UTC'}),
                      r.ActedBy = $acted_by,
                      r.CreatedAt = datetime({timezone: 'UTC'})
        ON MATCH SET  r.test_id = coalesce($test_id, r.test_id),
                      r.Action = 'UPDATE',
                      r.ActedAt = datetime({timezone: 'UTC'}),
                      r.ActedBy = $acted_by,
                      r.UpdatedAt = datetime({timezone: 'UTC'})
        RETURN properties(r) AS rel, properties(c) AS config, properties(g) AS group
        """
        params = {
            "config_id": config_id,
            "group_id": group_id,
            "rel_id": rel.id,
            "test_id": test_id,
            "acted_by": acted_by,
        }
        records = await self.client.execute_query(query, params)
        res = _clean_neo4j_value(records[0]) if records else {}

        record_audit(
            action=AuditAction.DICOM_CONFIGURATION_SERVES_GROUP,
            actor_id=f"user:{acted_by}",
            actor=acted_by,
            target_id=f"config:{config_id}",
            target=f"Group {group_id}",
            status_code=200,
            status_message=f"DICOM Configuration {config_id} now serves Group {group_id}",
            severity="INFO",
            result="SERVES relationship established",
            details={"config_id": config_id, "group_id": group_id},
            test_id=test_id,
        )
        return res

    async def dissociate_dicom_config_from_group(
        self,
        config_id: str,
        group_id: str,
        acted_by: str = "system",
    ) -> Optional[Dict[str, Any]]:
        """Dissociate a DICOMConfiguration from a Group by swapping SERVES to NOT_SERVES."""
        neg_type = to_negated_relation_type("SERVES")
        new_rel_id = generate_deterministic_id({"start_node_id": config_id, "type": neg_type, "end_node_id": group_id})

        query = f"""
        MATCH (c:DICOMConfiguration {{id: $config_id}})-[r:SERVES]->(g:Group {{id: $group_id}})
        WITH c, g, r, properties(r) AS old_props
        CREATE (c)-[r_not:{neg_type}]->(g)
        SET r_not = old_props,
            r_not.id = $new_rel_id,
            r_not.Action = 'DELETE',
            r_not.ActedAt = datetime({{timezone: 'UTC'}}),
            r_not.ActedBy = $acted_by
        DELETE r
        RETURN properties(r_not) AS r_not
        """

        async def _swap_tx(tx: Any) -> Optional[Dict[str, Any]]:
            res = await tx.run(
                query,
                config_id=config_id,
                group_id=group_id,
                new_rel_id=new_rel_id,
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
                result="SERVES relationship disabled to NOT_SERVES",
                details={"config_id": config_id, "group_id": group_id},
            )
        return result

    async def get_group_for_ae_title(self, ae_title: str) -> Optional[Dict[str, Any]]:
        """Look up the active Group served by the given remote or local AE Title."""
        norm_aet = ae_title.strip()
        query = """
        MATCH (c:DICOMConfiguration)-[r:SERVES]->(g:Group)
        WHERE (c.ae_title = $ae_title OR (c.is_promiscuous = true AND c.ae_title = '*'))
          AND (c.Action IS NULL OR c.Action <> 'DELETE')
          AND (r.Action IS NULL OR r.Action <> 'DELETE')
          AND (g.Action IS NULL OR g.Action <> 'DELETE')
          AND g.is_active = true
        RETURN properties(g) AS group, c.ae_title AS matched_aet
        ORDER BY CASE WHEN c.ae_title = $ae_title THEN 0 ELSE 1 END
        LIMIT 1
        """
        records = await self.client.execute_query(query, {"ae_title": norm_aet})
        if records:
            return _clean_neo4j_value(records[0].get("group"))
        return None

    def get_group_for_ae_title_sync(self, ae_title: str) -> Optional[Dict[str, Any]]:
        """Synchronously look up the active Group served by the given AE Title."""
        norm_aet = ae_title.strip()
        query = """
        MATCH (c:DICOMConfiguration)-[r:SERVES]->(g:Group)
        WHERE (c.ae_title = $ae_title OR (c.is_promiscuous = true AND c.ae_title = '*'))
          AND (c.Action IS NULL OR c.Action <> 'DELETE')
          AND (r.Action IS NULL OR r.Action <> 'DELETE')
          AND (g.Action IS NULL OR g.Action <> 'DELETE')
          AND g.is_active = true
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

    async def get_effective_accessible_groups_for_ae(self, ae_title: str) -> List[Dict[str, Any]]:
        """Resolve effective group scope for a caller AE title (group itself + downward closure)."""
        norm_aet = ae_title.strip()
        query = """
        MATCH (c:DICOMConfiguration {ae_title: $ae_title})-[r:SERVES]->(root_grp:Group)
        WHERE (c.Action IS NULL OR c.Action <> 'DELETE')
          AND (r.Action IS NULL OR r.Action <> 'DELETE')
          AND (root_grp.Action IS NULL OR root_grp.Action <> 'DELETE')
          AND root_grp.is_active = true
        OPTIONAL MATCH (sub_grp:Group)-[:SUB_GROUP_OF*1..]->(root_grp)
        WHERE (sub_grp.Action IS NULL OR sub_grp.Action <> 'DELETE')
          AND sub_grp.is_active = true
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
        MATCH (c:DICOMConfiguration {ae_title: $ae_title})-[r:SERVES]->(root_grp:Group)
        WHERE (c.Action IS NULL OR c.Action <> 'DELETE')
          AND (r.Action IS NULL OR r.Action <> 'DELETE')
          AND (root_grp.Action IS NULL OR root_grp.Action <> 'DELETE')
          AND root_grp.is_active = true
        OPTIONAL MATCH (sub_grp:Group)-[:SUB_GROUP_OF*1..]->(root_grp)
        WHERE (sub_grp.Action IS NULL OR sub_grp.Action <> 'DELETE')
          AND sub_grp.is_active = true
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


group_service = GroupService()
