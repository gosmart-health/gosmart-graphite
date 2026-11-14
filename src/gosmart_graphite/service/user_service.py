import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from gosmart_graphite.actor.audit_actor import record_audit
from gosmart_graphite.core.config import get_settings
from gosmart_graphite.core.logging import get_logger
from gosmart_graphite.core.security import generate_api_key, hash_api_key, hash_password, verify_password
from gosmart_graphite.db.neo4j import Neo4jClient, neo4j_client
from gosmart_graphite.model.audit import AuditAction
from gosmart_graphite.model.auth import (
    AuthenticatorNode,
    HasAuthenticatorRelation,
    HasIdentityRelation,
    IdentityNode,
    UserNode,
    UsesAuthenticatorRelation,
)
from gosmart_graphite.service.group_service import group_service

logger = get_logger("gosmart_gs.user_service")


def _clean_neo4j_value(val: Any) -> Any:
    """Normalize Neo4j specific types to standard Python types."""
    if hasattr(val, "to_native"):
        return val.to_native()
    if isinstance(val, dict):
        return {k: _clean_neo4j_value(v) for k, v in val.items()}
    if isinstance(val, list):
        return [_clean_neo4j_value(v) for v in val]
    return val


class UserService:
    """Service for managing Users, Identities, Authenticators, and Credentials in Neo4j."""

    def __init__(self, client: Optional[Neo4jClient] = None) -> None:
        self.client = client or neo4j_client

    async def create_local_user(
        self,
        username: str,
        email: str,
        password: str,
        display_name: Optional[str] = None,
        system_roles: Optional[List[str]] = None,
        group_id: Optional[str] = None,
        group_roles: Optional[List[str]] = None,
        test_id: Optional[str] = None,
        acted_by: str = "system",
    ) -> Dict[str, Any]:
        """Create a local user with password authenticator and optional initial group affiliation."""
        normalized_email = email.strip().lower()
        normalized_username = username.strip()
        user_name = display_name or normalized_username
        roles = system_roles or ["USER"]

        user_node = UserNode(
            display_name=user_name,
            primary_email=normalized_email,
            is_active=True,
            is_shadow=False,
            system_roles=roles,
            test_id=test_id,
            ActedBy=acted_by,
        )

        identity_node = IdentityNode(
            provider="local",
            external_sub=normalized_username,
            identifier=normalized_username,
            email=normalized_email,
            email_verified=True,
            test_id=test_id,
            ActedBy=acted_by,
        )

        pwd_hash = hash_password(password)
        auth_node = AuthenticatorNode(
            authenticator_type="password",
            secret_hash=pwd_hash,
            name="Primary Password",
            test_id=test_id,
            ActedBy=acted_by,
        )

        rel_has_id = HasIdentityRelation(
            start_node_id=user_node.id,
            end_node_id=identity_node.id,
            test_id=test_id,
            ActedBy=acted_by,
        )

        rel_uses_auth = UsesAuthenticatorRelation(
            start_node_id=identity_node.id,
            end_node_id=auth_node.id,
            test_id=test_id,
            ActedBy=acted_by,
        )

        query = """
        MERGE (u:User {id: $user.id})
        ON CREATE SET u.display_name = $user.display_name,
                      u.primary_email = $user.primary_email,
                      u.is_active = $user.is_active,
                      u.is_shadow = $user.is_shadow,
                      u.system_roles = $user.system_roles,
                      u.test_id = $user.test_id,
                      u.Action = 'CREATE',
                      u.ActedAt = datetime({timezone: 'UTC'}),
                      u.ActedBy = $user.ActedBy,
                      u.CreatedAt = datetime({timezone: 'UTC'})
        ON MATCH SET  u.display_name = $user.display_name,
                      u.is_active = $user.is_active,
                      u.is_shadow = $user.is_shadow,
                      u.system_roles = $user.system_roles,
                      u.test_id = coalesce($user.test_id, u.test_id),
                      u.Action = 'UPDATE',
                      u.ActedAt = datetime({timezone: 'UTC'}),
                      u.ActedBy = $user.ActedBy,
                      u.UpdatedAt = datetime({timezone: 'UTC'})

        MERGE (i:Identity {id: $identity.id})
        ON CREATE SET i.provider = $identity.provider,
                      i.external_sub = $identity.external_sub,
                      i.identifier = $identity.identifier,
                      i.email = $identity.email,
                      i.email_verified = $identity.email_verified,
                      i.test_id = $identity.test_id,
                      i.Action = 'CREATE',
                      i.ActedAt = datetime({timezone: 'UTC'}),
                      i.ActedBy = $identity.ActedBy,
                      i.CreatedAt = datetime({timezone: 'UTC'})

        MERGE (a:Authenticator {id: $auth.id})
        ON CREATE SET a.authenticator_type = $auth.authenticator_type,
                      a.secret_hash = $auth.secret_hash,
                      a.name = $auth.name,
                      a.scopes = $auth.scopes,
                      a.test_id = $auth.test_id,
                      a.Action = 'CREATE',
                      a.ActedAt = datetime({timezone: 'UTC'}),
                      a.ActedBy = $auth.ActedBy,
                      a.CreatedAt = datetime({timezone: 'UTC'})

        MERGE (u)-[r_hi:HAS_IDENTITY {id: $rel_hi.id}]->(i)
        ON CREATE SET r_hi.test_id = $rel_hi.test_id,
                      r_hi.Action = 'CREATE',
                      r_hi.ActedAt = datetime({timezone: 'UTC'}),
                      r_hi.ActedBy = $rel_hi.ActedBy,
                      r_hi.CreatedAt = datetime({timezone: 'UTC'})

        MERGE (i)-[r_ua:USES_AUTHENTICATOR {id: $rel_ua.id}]->(a)
        ON CREATE SET r_ua.test_id = $rel_ua.test_id,
                      r_ua.Action = 'CREATE',
                      r_ua.ActedAt = datetime({timezone: 'UTC'}),
                      r_ua.ActedBy = $rel_ua.ActedBy,
                      r_ua.CreatedAt = datetime({timezone: 'UTC'})

        RETURN properties(u) AS user
        """
        params = {
            "user": {
                "id": user_node.id,
                "display_name": user_node.display_name,
                "primary_email": user_node.primary_email,
                "is_active": user_node.is_active,
                "is_shadow": user_node.is_shadow,
                "system_roles": user_node.system_roles,
                "test_id": user_node.test_id,
                "ActedBy": user_node.ActedBy,
            },
            "identity": {
                "id": identity_node.id,
                "provider": identity_node.provider,
                "external_sub": identity_node.external_sub,
                "identifier": identity_node.identifier,
                "email": identity_node.email,
                "email_verified": identity_node.email_verified,
                "test_id": identity_node.test_id,
                "ActedBy": identity_node.ActedBy,
            },
            "auth": {
                "id": auth_node.id,
                "authenticator_type": auth_node.authenticator_type,
                "secret_hash": auth_node.secret_hash,
                "name": auth_node.name,
                "scopes": auth_node.scopes,
                "test_id": auth_node.test_id,
                "ActedBy": auth_node.ActedBy,
            },
            "rel_hi": {
                "id": rel_has_id.id,
                "test_id": rel_has_id.test_id,
                "ActedBy": rel_has_id.ActedBy,
            },
            "rel_ua": {
                "id": rel_uses_auth.id,
                "test_id": rel_uses_auth.test_id,
                "ActedBy": rel_uses_auth.ActedBy,
            },
        }

        records = await self.client.execute_query(query, params)
        result_user = _clean_neo4j_value(records[0].get("user", records[0])) if records else {}

        if group_id and result_user:
            await group_service.add_user_to_group(
                user_id=user_node.id,
                group_id=group_id,
                roles=group_roles or ["Viewer"],
                is_primary=True,
                test_id=test_id,
                acted_by=acted_by,
            )

        return result_user

    async def find_or_create_shadow_user(
        self,
        provider: str,
        external_sub: str,
        email: str,
        display_name: Optional[str] = None,
        default_group_id: Optional[str] = None,
        default_group_roles: Optional[List[str]] = None,
        profile_metadata: Optional[Dict[str, Any]] = None,
        test_id: Optional[str] = None,
        acted_by: str = "system",
    ) -> Dict[str, Any]:
        """Find an existing user by OAuth identity, link to existing email user, or JIT provision a shadow user."""
        norm_provider = provider.strip().lower()
        norm_sub = external_sub.strip()
        norm_email = email.strip().lower()
        user_name = display_name or norm_email.split("@")[0]
        meta_json = json.dumps(profile_metadata or {})

        # 1. Check if identity already exists and is linked to a user
        find_query = """
        MATCH (u:User)-[:HAS_IDENTITY]->(i:Identity {provider: $provider, external_sub: $sub})
        WHERE (u.Action IS NULL OR u.Action <> 'DELETE')
        RETURN properties(u) AS user, properties(i) AS identity
        LIMIT 1
        """
        records = await self.client.execute_query(find_query, {"provider": norm_provider, "sub": norm_sub})
        if records:
            return _clean_neo4j_value(records[0].get("user", records[0]))

        # 2. Check if a User with the same verified primary_email already exists
        user_node = UserNode(
            display_name=user_name,
            primary_email=norm_email,
            is_active=True,
            is_shadow=True,
            system_roles=["USER"],
            test_id=test_id,
            ActedBy=acted_by,
        )

        identity_node = IdentityNode(
            provider=norm_provider,
            external_sub=norm_sub,
            identifier=norm_email,
            email=norm_email,
            email_verified=True,
            profile_metadata=meta_json,
            test_id=test_id,
            ActedBy=acted_by,
        )

        rel_has_id = HasIdentityRelation(
            start_node_id=user_node.id,
            end_node_id=identity_node.id,
            test_id=test_id,
            ActedBy=acted_by,
        )

        jit_query = """
        MERGE (u:User {id: $user.id})
        ON CREATE SET u.display_name = $user.display_name,
                      u.primary_email = $user.primary_email,
                      u.is_active = true,
                      u.is_shadow = true,
                      u.system_roles = ['USER'],
                      u.test_id = $user.test_id,
                      u.Action = 'CREATE',
                      u.ActedAt = datetime({timezone: 'UTC'}),
                      u.ActedBy = $acted_by,
                      u.CreatedAt = datetime({timezone: 'UTC'})
        ON MATCH SET  u.display_name = coalesce(u.display_name, $user.display_name),
                      u.test_id = coalesce($user.test_id, u.test_id),
                      u.UpdatedAt = datetime({timezone: 'UTC'})

        MERGE (i:Identity {id: $identity.id})
        ON CREATE SET i.provider = $identity.provider,
                      i.external_sub = $identity.external_sub,
                      i.identifier = $identity.identifier,
                      i.email = $identity.email,
                      i.email_verified = true,
                      i.profile_metadata = $identity.profile_metadata,
                      i.test_id = $identity.test_id,
                      i.Action = 'CREATE',
                      i.ActedAt = datetime({timezone: 'UTC'}),
                      i.ActedBy = $acted_by,
                      i.CreatedAt = datetime({timezone: 'UTC'})

        MERGE (u)-[r:HAS_IDENTITY {id: $rel.id}]->(i)
        ON CREATE SET r.test_id = $rel.test_id,
                      r.Action = 'CREATE',
                      r.ActedAt = datetime({timezone: 'UTC'}),
                      r.ActedBy = $acted_by,
                      r.CreatedAt = datetime({timezone: 'UTC'})

        RETURN properties(u) AS user
        """
        params = {
            "user": {
                "id": user_node.id,
                "display_name": user_node.display_name,
                "primary_email": user_node.primary_email,
                "test_id": test_id,
            },
            "identity": {
                "id": identity_node.id,
                "provider": identity_node.provider,
                "external_sub": identity_node.external_sub,
                "identifier": identity_node.identifier,
                "email": identity_node.email,
                "profile_metadata": identity_node.profile_metadata,
                "test_id": test_id,
            },
            "rel": {
                "id": rel_has_id.id,
                "test_id": test_id,
            },
            "acted_by": acted_by,
        }
        res_records = await self.client.execute_query(jit_query, params)
        res_user = _clean_neo4j_value(res_records[0].get("user", res_records[0])) if res_records else {}

        if default_group_id and res_user:
            await group_service.add_user_to_group(
                user_id=user_node.id,
                group_id=default_group_id,
                roles=default_group_roles or ["Viewer"],
                is_primary=True,
                test_id=test_id,
                acted_by=acted_by,
            )

        return res_user

    async def authenticate_local(
        self,
        identifier: str,
        password: str,
    ) -> Optional[Dict[str, Any]]:
        """Authenticate user by local username or email and password."""
        norm_identifier = identifier.strip()
        norm_email = identifier.strip().lower()

        query = """
        MATCH (u:User)-[:HAS_IDENTITY]->(i:Identity {provider: 'local'})
              -[:USES_AUTHENTICATOR]->(a:Authenticator {authenticator_type: 'password'})
        WHERE (i.identifier = $identifier OR u.primary_email = $email)
          AND u.is_active = true
          AND (u.Action IS NULL OR u.Action <> 'DELETE')
        RETURN properties(u) AS user, properties(a) AS authenticator, a.id AS auth_id
        LIMIT 1
        """
        records = await self.client.execute_query(query, {"identifier": norm_identifier, "email": norm_email})
        if not records:
            return None

        record = records[0]
        user_data = _clean_neo4j_value(record.get("user"))
        auth_data = _clean_neo4j_value(record.get("authenticator"))

        if not auth_data or not auth_data.get("secret_hash"):
            return None

        if not verify_password(password, auth_data["secret_hash"]):
            return None

        # Update last_used_at timestamp on the authenticator
        auth_id = record.get("auth_id")
        if auth_id:
            update_query = """
            MATCH (a:Authenticator {id: $auth_id})
            SET a.last_used_at = datetime({timezone: 'UTC'})
            """
            await self.client.execute_query(update_query, {"auth_id": auth_id})

        return user_data

    async def create_api_key(
        self,
        user_id: str,
        name: str,
        scopes: Optional[List[str]] = None,
        expires_at: Optional[datetime] = None,
        test_id: Optional[str] = None,
        acted_by: str = "system",
    ) -> Dict[str, Any]:
        """Generate and attach a secure API Key Authenticator directly to a User."""
        raw_key, prefix, key_hash = generate_api_key()
        scopes_list = scopes or ["*"]

        auth_node = AuthenticatorNode(
            authenticator_type="api_key",
            secret_hash=key_hash,
            prefix=prefix,
            name=name,
            scopes=scopes_list,
            expires_at=expires_at,
            test_id=test_id,
            ActedBy=acted_by,
        )

        rel = HasAuthenticatorRelation(
            start_node_id=user_id,
            end_node_id=auth_node.id,
            test_id=test_id,
            ActedBy=acted_by,
        )

        query = """
        MATCH (u:User {id: $user_id})
        MERGE (a:Authenticator {id: $auth.id})
        ON CREATE SET a.authenticator_type = 'api_key',
                      a.secret_hash = $auth.secret_hash,
                      a.prefix = $auth.prefix,
                      a.name = $auth.name,
                      a.scopes = $auth.scopes,
                      a.expires_at = $auth.expires_at,
                      a.test_id = $auth.test_id,
                      a.Action = 'CREATE',
                      a.ActedAt = datetime({timezone: 'UTC'}),
                      a.ActedBy = $acted_by,
                      a.CreatedAt = datetime({timezone: 'UTC'})

        MERGE (u)-[r:HAS_AUTHENTICATOR {id: $rel_id}]->(a)
        ON CREATE SET r.test_id = $rel_test_id,
                      r.Action = 'CREATE',
                      r.ActedAt = datetime({timezone: 'UTC'}),
                      r.ActedBy = $acted_by,
                      r.CreatedAt = datetime({timezone: 'UTC'})

        RETURN properties(a) AS authenticator
        """
        params = {
            "user_id": user_id,
            "auth": {
                "id": auth_node.id,
                "secret_hash": auth_node.secret_hash,
                "prefix": auth_node.prefix,
                "name": auth_node.name,
                "scopes": auth_node.scopes,
                "expires_at": auth_node.expires_at.isoformat() if auth_node.expires_at else None,
                "test_id": test_id,
            },
            "rel_id": rel.id,
            "rel_test_id": test_id,
            "acted_by": acted_by,
        }
        await self.client.execute_query(query, params)

        return {
            "raw_api_key": raw_key,
            "prefix": prefix,
            "authenticator_id": auth_node.id,
            "name": name,
            "scopes": scopes_list,
            "expires_at": expires_at.isoformat() if expires_at else None,
        }

    async def authenticate_api_key(self, raw_api_key: str) -> Optional[Dict[str, Any]]:
        """Authenticate user by incoming API key."""
        key_hash = hash_api_key(raw_api_key)

        query = """
        MATCH (u:User)-[:HAS_AUTHENTICATOR]->(a:Authenticator {authenticator_type: 'api_key', secret_hash: $key_hash})
        WHERE u.is_active = true
          AND (u.Action IS NULL OR u.Action <> 'DELETE')
          AND (a.expires_at IS NULL OR datetime(a.expires_at) > datetime({timezone: 'UTC'}))
        SET a.last_used_at = datetime({timezone: 'UTC'})
        RETURN properties(u) AS user, properties(a) AS authenticator
        LIMIT 1
        """
        records = await self.client.execute_query(query, {"key_hash": key_hash})
        if not records:
            return None

        record = records[0]
        user_data = _clean_neo4j_value(record.get("user"))
        auth_data = _clean_neo4j_value(record.get("authenticator"))
        user_data["api_key_scopes"] = auth_data.get("scopes", ["*"])
        user_data["api_key_name"] = auth_data.get("name")
        return user_data

    async def attach_password_to_user(
        self,
        user_id: str,
        password: str,
        username: Optional[str] = None,
        test_id: Optional[str] = None,
        acted_by: str = "system",
    ) -> Dict[str, Any]:
        """Attach a local password authenticator to an existing (e.g. shadow) user, promoting account."""
        user = await self.get_user_by_id(user_id)
        if not user:
            raise ValueError(f"User {user_id} not found")

        login_identifier = username or user.get("primary_email", "").split("@")[0]
        norm_email = user.get("primary_email", "")

        identity_node = IdentityNode(
            provider="local",
            external_sub=login_identifier,
            identifier=login_identifier,
            email=norm_email,
            email_verified=True,
            test_id=test_id,
            ActedBy=acted_by,
        )

        pwd_hash = hash_password(password)
        auth_node = AuthenticatorNode(
            authenticator_type="password",
            secret_hash=pwd_hash,
            name="Local Password",
            test_id=test_id,
            ActedBy=acted_by,
        )

        rel_has_id = HasIdentityRelation(
            start_node_id=user_id,
            end_node_id=identity_node.id,
            test_id=test_id,
            ActedBy=acted_by,
        )

        rel_uses_auth = UsesAuthenticatorRelation(
            start_node_id=identity_node.id,
            end_node_id=auth_node.id,
            test_id=test_id,
            ActedBy=acted_by,
        )

        query = """
        MATCH (u:User {id: $user_id})
        SET u.is_shadow = false,
            u.UpdatedAt = datetime({timezone: 'UTC'})

        MERGE (i:Identity {id: $identity.id})
        ON CREATE SET i.provider = 'local',
                      i.external_sub = $identity.external_sub,
                      i.identifier = $identity.identifier,
                      i.email = $identity.email,
                      i.email_verified = true,
                      i.test_id = $identity.test_id,
                      i.Action = 'CREATE',
                      i.ActedAt = datetime({timezone: 'UTC'}),
                      i.ActedBy = $acted_by,
                      i.CreatedAt = datetime({timezone: 'UTC'})

        MERGE (a:Authenticator {id: $auth.id})
        ON CREATE SET a.authenticator_type = 'password',
                      a.secret_hash = $auth.secret_hash,
                      a.name = $auth.name,
                      a.scopes = ['*'],
                      a.test_id = $auth.test_id,
                      a.Action = 'CREATE',
                      a.ActedAt = datetime({timezone: 'UTC'}),
                      a.ActedBy = $acted_by,
                      a.CreatedAt = datetime({timezone: 'UTC'})
        ON MATCH SET  a.secret_hash = $auth.secret_hash,
                      a.UpdatedAt = datetime({timezone: 'UTC'})

        MERGE (u)-[r_hi:HAS_IDENTITY {id: $rel_hi_id}]->(i)
        MERGE (i)-[r_ua:USES_AUTHENTICATOR {id: $rel_ua_id}]->(a)

        RETURN properties(u) AS user
        """
        params = {
            "user_id": user_id,
            "identity": {
                "id": identity_node.id,
                "external_sub": identity_node.external_sub,
                "identifier": identity_node.identifier,
                "email": identity_node.email,
                "test_id": test_id,
            },
            "auth": {
                "id": auth_node.id,
                "secret_hash": auth_node.secret_hash,
                "name": auth_node.name,
                "test_id": test_id,
            },
            "rel_hi_id": rel_has_id.id,
            "rel_ua_id": rel_uses_auth.id,
            "acted_by": acted_by,
        }
        records = await self.client.execute_query(query, params)
        return _clean_neo4j_value(records[0].get("user", records[0])) if records else {}

    async def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Fetch user by ID."""
        query = """
        MATCH (u:User {id: $user_id})
        WHERE (u.Action IS NULL OR u.Action <> 'DELETE')
        RETURN properties(u) AS user
        LIMIT 1
        """
        records = await self.client.execute_query(query, {"user_id": user_id})
        if records:
            return _clean_neo4j_value(records[0].get("user", records[0]))
        return None

    async def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Fetch user by primary email."""
        norm_email = email.strip().lower()
        query = """
        MATCH (u:User {primary_email: $email})
        WHERE (u.Action IS NULL OR u.Action <> 'DELETE')
        RETURN properties(u) AS user
        LIMIT 1
        """
        records = await self.client.execute_query(query, {"email": norm_email})
        if records:
            return _clean_neo4j_value(records[0].get("user", records[0]))
        return None

    async def list_users(
        self,
        include_inactive: bool = False,
        include_tests: bool = True,
        test_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """List users."""
        query = """
        MATCH (u:User)
        WHERE (u.Action IS NULL OR u.Action <> 'DELETE')
          AND ($include_inactive = true OR u.is_active = true)
          AND ($include_tests = true OR u.test_id IS NULL OR u.test_id = '')
          AND ($test_id IS NULL OR u.test_id = $test_id)
        RETURN properties(u) AS user
        ORDER BY u.display_name ASC
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
        return [_clean_neo4j_value(r.get("user", r)) for r in records]

    async def list_platform_admins(
        self,
        include_inactive: bool = False,
        test_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List all users possessing the global PLATFORM_ADMIN system role."""
        query = """
        MATCH (u:User)
        WHERE (u.Action IS NULL OR u.Action <> 'DELETE')
          AND 'PLATFORM_ADMIN' IN u.system_roles
          AND ($include_inactive = true OR u.is_active = true)
          AND ($test_id IS NULL OR u.test_id = $test_id)
        RETURN properties(u) AS user
        ORDER BY u.display_name ASC
        """
        records = await self.client.execute_query(
            query,
            {
                "include_inactive": include_inactive,
                "test_id": test_id,
            },
        )
        return [_clean_neo4j_value(r.get("user", r)) for r in records]

    async def reset_admin_password(
        self,
        identifier: str,
        new_password: str,
        acted_by: str = "cli:admin",
        test_id: Optional[str] = None,
    ) -> bool:
        """Reset the local password for a user identified by email, username, or User ID.

        Updates existing password Authenticator or creates a new one if missing.
        """
        norm_ident = identifier.strip()
        norm_email = norm_ident.lower()
        pwd_hash = hash_password(new_password)

        find_query = """
        MATCH (u:User)
        WHERE (u.Action IS NULL OR u.Action <> 'DELETE')
          AND (toLower(u.primary_email) = $email OR u.id = $ident)
        OPTIONAL MATCH (u)-[:HAS_IDENTITY]->(i:Identity)-[:USES_AUTHENTICATOR]->(a:Authenticator)
        WHERE a.authenticator_type = 'password'
        RETURN properties(u) AS user, properties(i) AS identity, properties(a) AS auth,
               a.id AS auth_id, i.id AS identity_id, u.id AS user_id
        LIMIT 1
        """
        records = await self.client.execute_query(find_query, {"email": norm_email, "ident": norm_ident})
        if not records:
            logger.warning("reset_password_user_not_found", identifier=identifier)
            return False

        rec = records[0]
        user_id = rec.get("user_id")
        auth_id = rec.get("auth_id")
        identity_id = rec.get("identity_id")

        if auth_id:
            update_query = """
            MATCH (a:Authenticator {id: $auth_id})
            SET a.secret_hash = $secret_hash,
                a.Action = 'UPDATE',
                a.ActedAt = datetime({timezone: 'UTC'}),
                a.ActedBy = $acted_by,
                a.UpdatedAt = datetime({timezone: 'UTC'})
            """
            await self.client.execute_query(
                update_query,
                {"auth_id": auth_id, "secret_hash": pwd_hash, "acted_by": acted_by},
            )
        else:
            auth_node = AuthenticatorNode(
                authenticator_type="password",
                secret_hash=pwd_hash,
                name="Primary Password",
                test_id=test_id,
                ActedBy=acted_by,
            )
            if identity_id:
                rel_id = f"rel_uses_{uuid.uuid4().hex[:8]}"
                create_auth_query = """
                MATCH (i:Identity {id: $identity_id})
                CREATE (a:Authenticator {
                    id: $auth.id,
                    authenticator_type: 'password',
                    secret_hash: $auth.secret_hash,
                    name: $auth.name,
                    test_id: $auth.test_id,
                    Action: 'CREATE',
                    ActedAt: datetime({timezone: 'UTC'}),
                    ActedBy: $auth.ActedBy,
                    CreatedAt: datetime({timezone: 'UTC'})
                })
                CREATE (i)-[:USES_AUTHENTICATOR {
                    id: $rel_id,
                    test_id: $auth.test_id,
                    Action: 'CREATE',
                    ActedAt: datetime({timezone: 'UTC'}),
                    ActedBy: $auth.ActedBy,
                    CreatedAt: datetime({timezone: 'UTC'})
                }]->(a)
                """
                await self.client.execute_query(
                    create_auth_query,
                    {
                        "identity_id": identity_id,
                        "rel_id": rel_id,
                        "auth": {
                            "id": auth_node.id,
                            "secret_hash": auth_node.secret_hash,
                            "name": auth_node.name,
                            "test_id": test_id,
                            "ActedBy": acted_by,
                        },
                    },
                )
            else:
                rel_id = f"rel_has_{uuid.uuid4().hex[:8]}"
                create_direct_query = """
                MATCH (u:User {id: $user_id})
                CREATE (a:Authenticator {
                    id: $auth.id,
                    authenticator_type: 'password',
                    secret_hash: $auth.secret_hash,
                    name: $auth.name,
                    test_id: $auth.test_id,
                    Action: 'CREATE',
                    ActedAt: datetime({timezone: 'UTC'}),
                    ActedBy: $auth.ActedBy,
                    CreatedAt: datetime({timezone: 'UTC'})
                })
                CREATE (u)-[:HAS_AUTHENTICATOR {
                    id: $rel_id,
                    test_id: $auth.test_id,
                    Action: 'CREATE',
                    ActedAt: datetime({timezone: 'UTC'}),
                    ActedBy: $auth.ActedBy,
                    CreatedAt: datetime({timezone: 'UTC'})
                }]->(a)
                """
                await self.client.execute_query(
                    create_direct_query,
                    {
                        "user_id": user_id,
                        "rel_id": rel_id,
                        "auth": {
                            "id": auth_node.id,
                            "secret_hash": auth_node.secret_hash,
                            "name": auth_node.name,
                            "test_id": test_id,
                            "ActedBy": acted_by,
                        },
                    },
                )

        record_audit(
            action=AuditAction.USER_PASSWORD_RESET,
            actor_id=acted_by,
            actor=acted_by,
            target_id=user_id,
            target=identifier,
            status_code=200,
            status_message="Password reset successfully",
            severity="WARNING",
            test_id=test_id,
        )
        logger.info("user_password_reset_success", identifier=identifier, acted_by=acted_by)
        return True

    async def bootstrap_admin_user(
        self,
        test_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Idempotently bootstrap the initial Platform Admin account upon system startup."""
        settings = get_settings()
        admin_email = settings.INITIAL_ADMIN_EMAIL
        admin_pass_secret = settings.INITIAL_ADMIN_PASSWORD
        force_reset = settings.FORCE_ADMIN_PASSWORD_RESET

        if not admin_email:
            logger.debug("admin_bootstrap_skipped_no_email")
            return None

        normalized_email = admin_email.strip().lower()
        existing_user = await self.get_user_by_email(normalized_email)
        existing_admins = await self.list_platform_admins(test_id=test_id)

        # 1. If admin user with this email already exists
        if existing_user:
            # Ensure user has PLATFORM_ADMIN role
            roles = existing_user.get("system_roles", [])
            if "PLATFORM_ADMIN" not in roles:
                roles = list(set(roles + ["PLATFORM_ADMIN"]))
                await self.client.execute_query(
                    "MATCH (u:User {id: $uid}) SET u.system_roles = $roles",
                    {"uid": existing_user["id"], "roles": roles},
                )
                existing_user["system_roles"] = roles

            # If force reset flag is active and password is provided, reset it
            if force_reset and admin_pass_secret:
                raw_pass = admin_pass_secret.get_secret_value()
                await self.reset_admin_password(
                    identifier=normalized_email,
                    new_password=raw_pass,
                    acted_by="system:force_reset_bootstrap",
                    test_id=test_id,
                )
                logger.warning(
                    "admin_password_force_reset_applied",
                    email=normalized_email,
                    notice="FORCE_ADMIN_PASSWORD_RESET flag was set and applied.",
                )
            else:
                logger.info(
                    "admin_bootstrap_verified_existing",
                    email=normalized_email,
                    user_id=existing_user["id"],
                )
            return existing_user

        # 2. If other platform admins already exist in system and no initial password is provided
        if existing_admins and not admin_pass_secret:
            logger.info(
                "admin_bootstrap_skipped_admins_present",
                admin_count=len(existing_admins),
            )
            return existing_admins[0]

        # 3. Create initial superuser if password provided
        if not admin_pass_secret:
            logger.warning(
                "no_initial_admin_password_configured",
                email=normalized_email,
                message="INITIAL_ADMIN_PASSWORD is not set in environment. Initial admin creation skipped.",
            )
            return None

        raw_pass = admin_pass_secret.get_secret_value()
        admin_username = settings.INITIAL_ADMIN_USERNAME
        admin_display_name = settings.INITIAL_ADMIN_DISPLAY_NAME

        # Ensure Root Catch-All group exists
        catch_all_group = await group_service.get_or_create_catch_all_group(
            test_id=test_id,
            acted_by="system:bootstrap",
        )

        # Create local admin user
        created_user = await self.create_local_user(
            username=admin_username,
            email=normalized_email,
            password=raw_pass,
            display_name=admin_display_name,
            system_roles=["USER", "PLATFORM_ADMIN"],
            group_id=catch_all_group.get("id"),
            group_roles=["Admin"],
            test_id=test_id,
            acted_by="system:bootstrap",
        )

        record_audit(
            action=AuditAction.ADMIN_BOOTSTRAPPED,
            actor_id="system:bootstrap",
            actor="system:bootstrap",
            target_id=created_user["id"],
            target=normalized_email,
            status_code=201,
            status_message="Initial Platform Admin account bootstrapped",
            severity="INFO",
            test_id=test_id,
        )

        logger.info(
            "admin_account_bootstrapped_successfully",
            email=normalized_email,
            user_id=created_user["id"],
            username=admin_username,
            group_id=catch_all_group.get("id"),
        )
        return created_user


user_service = UserService()
