from datetime import datetime
from typing import Any, ClassVar, Dict, List, Optional

from pydantic import Field, field_validator

from gosmart_graphite.model.auditable import AuditableNode, AuditableRelation


class UserNode(AuditableNode):
    """Graph node representing an authenticated principal (human user or service account)."""

    __label__: ClassVar[str] = "User"

    display_name: str = Field(..., description="Full display name (e.g. 'Dr. Jane Doe')")
    primary_email: str = Field(..., description="Canonical primary email address")
    is_active: bool = Field(default=True, description="Whether the user account is active")
    is_shadow: bool = Field(
        default=False,
        description="Whether this is a shadow account provisioned automatically via OAuth without local password",
    )
    system_roles: List[str] = Field(
        default_factory=lambda: ["USER"],
        description="Global system roles (e.g. ['USER'], ['PLATFORM_ADMIN'])",
    )
    avatar_url: Optional[str] = Field(default=None, description="URL or avatar image endpoint")

    @field_validator("primary_email", mode="before")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        return v.strip().lower() if isinstance(v, str) else v

    def get_identity_subset(self) -> Dict[str, Any]:
        subset: Dict[str, Any] = {
            "entity": self.__label__,
            "primary_email": self.primary_email.lower().strip(),
        }
        if self.test_id:
            subset["test_id"] = self.test_id
        return subset


class IdentityNode(AuditableNode):
    """Graph node representing an external or local identity provider claim."""

    __label__: ClassVar[str] = "Identity"

    provider: str = Field(..., description="Identity provider name: 'google', 'local', 'github', 'saml_sso'")
    external_sub: str = Field(..., description="Unique provider subject/user identifier (e.g. Google sub)")
    identifier: str = Field(..., description="User login identifier or username")
    email: Optional[str] = Field(default=None, description="Provider-reported email address")
    email_verified: bool = Field(default=False, description="Whether the provider has verified the email")
    profile_metadata: Optional[str] = Field(
        default=None,
        description="JSON serialized extra claims from provider (avatar, given_name, etc.)",
    )

    @field_validator("provider", mode="before")
    @classmethod
    def normalize_provider(cls, v: str) -> str:
        return v.strip().lower() if isinstance(v, str) else v

    def get_identity_subset(self) -> Dict[str, Any]:
        subset: Dict[str, Any] = {
            "entity": self.__label__,
            "provider": self.provider.lower().strip(),
            "external_sub": self.external_sub.strip(),
        }
        if self.test_id:
            subset["test_id"] = self.test_id
        return subset


class AuthenticatorNode(AuditableNode):
    """Graph node representing a credential or verification factor."""

    __label__: ClassVar[str] = "Authenticator"

    authenticator_type: str = Field(
        ...,
        description="Type of authenticator: 'password', 'api_key', 'passkey', 'totp'",
    )
    secret_hash: str = Field(..., description="Argon2 hash for password or SHA-256 hash for API key")
    prefix: Optional[str] = Field(default=None, description="Key prefix (e.g. 'gs_live_') for fast lookup")
    name: Optional[str] = Field(default=None, description="Human-readable label (e.g. 'MacBook Pro CLI', 'MCP Agent')")
    scopes: List[str] = Field(default_factory=lambda: ["*"], description="Authorized permission scopes")
    expires_at: Optional[datetime] = Field(default=None, description="Credential expiration timestamp")
    last_used_at: Optional[datetime] = Field(default=None, description="Timestamp of last successful authentication")

    def get_identity_subset(self) -> Dict[str, Any]:
        subset: Dict[str, Any] = {
            "entity": self.__label__,
            "authenticator_type": self.authenticator_type,
            "secret_hash": self.secret_hash[:32],
        }
        if self.test_id:
            subset["test_id"] = self.test_id
        return subset


class GroupNode(AuditableNode):
    """Graph node representing an organization, hospital system, facility, department, or care team."""

    __label__: ClassVar[str] = "Group"

    name: str = Field(..., description="Organization or facility name (e.g. 'Half Moon Bay Clinic')")
    code: str = Field(..., description="Unique alphanumeric code/slug (e.g. 'HMB_IMG_01')")
    group_type: str = Field(
        default="FACILITY",
        description="Group type: HEALTH_SYSTEM | HOSPITAL | FACILITY | DEPARTMENT | CARE_TEAM",
    )
    is_active: bool = Field(default=True, description="Whether the group is active")
    description: Optional[str] = Field(default=None, description="Detailed description")

    @field_validator("code", mode="before")
    @classmethod
    def normalize_code(cls, v: str) -> str:
        return v.strip().upper() if isinstance(v, str) else v

    def get_identity_subset(self) -> Dict[str, Any]:
        return {
            "entity": self.__label__,
            "code": self.code.upper().strip(),
        }


Group = GroupNode


# Graph Relationships for Auth, Groups, and Tenancy


class HasIdentityRelation(AuditableRelation):
    """Relationship connecting User -> Identity."""

    __type__: ClassVar[str] = "HAS_IDENTITY"


class UsesAuthenticatorRelation(AuditableRelation):
    """Relationship connecting Identity -> Authenticator."""

    __type__: ClassVar[str] = "USES_AUTHENTICATOR"


class HasAuthenticatorRelation(AuditableRelation):
    """Relationship connecting User -> Authenticator (direct API keys, Passkeys, TOTP)."""

    __type__: ClassVar[str] = "HAS_AUTHENTICATOR"


class MemberOfRelation(AuditableRelation):
    """Relationship connecting User -> Group with group-specific roles, permissions, and lifecycle status."""

    __type__: ClassVar[str] = "MEMBER_OF"

    roles: List[str] = Field(
        default_factory=lambda: ["Viewer"],
        description="Group-specific roles (e.g. ['Admin'], ['Viewer'], ['Editor'], ['Radiologist'], ['Nurse'])",
    )
    permissions: List[str] = Field(
        default_factory=list,
        description="Fine-grained granular permissions within this group",
    )
    status: str = Field(
        default="ACTIVE",
        description="Affiliation lifecycle status: 'ACTIVE' | 'INACTIVE' | 'SUSPENDED' | 'PENDING'",
    )
    is_primary: bool = Field(default=False, description="Whether this is the user's primary/default group affiliation")


class BelongsToRelation(AuditableRelation):
    """Relationship connecting Patient/Encounter -> Group, Child Group -> Parent Group, or Root Group -> System."""

    __type__: ClassVar[str] = "BELONGS_TO"


class PerformedAtRelation(AuditableRelation):
    """Relationship connecting Study -> Group."""

    __type__: ClassVar[str] = "PERFORMED_AT"


class ScopedToRelation(AuditableRelation):
    """Relationship connecting DICOMConfiguration -> Group."""

    __type__: ClassVar[str] = "SCOPED_TO"


class ServesRelation(AuditableRelation):
    """Relationship connecting DICOMConfiguration -> Group with 'SERVES' relation type."""

    __type__: ClassVar[str] = "SERVES"


class HasLostAndFoundRelation(AuditableRelation):
    """Relationship connecting System -> LostAndFound Group for operational recovery of unassociated nodes."""

    __type__: ClassVar[str] = "HAS_LOST_AND_FOUND"
