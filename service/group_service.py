from typing import Any, Dict, List, Optional

from core.logging import get_logger
from db.neo4j import Neo4jClient, neo4j_client
from model.auth import GroupNode, MemberOfRelation, SubGroupOfRelation

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
        MERGE (g:Group {id: $id})
        ON CREATE SET g.name = $name,
                      g.code = $code,
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


group_service = GroupService()
