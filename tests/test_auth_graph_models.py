from gosmart_graphite.model.auth import (
    AuthenticatorNode,
    BelongsToRelation,
    GroupNode,
    HasAuthenticatorRelation,
    HasIdentityRelation,
    IdentityNode,
    MemberOfRelation,
    PerformedAtRelation,
    ScopedToRelation,
    SubGroupOfRelation,
    UserNode,
    UsesAuthenticatorRelation,
)


def test_user_node_deterministic_id_and_normalization():
    user1 = UserNode(
        display_name="Dr. Jane Doe",
        primary_email="  Jane.Doe@Hospital.ORG ",
        system_roles=["USER", "PLATFORM_ADMIN"],
    )
    assert user1.primary_email == "jane.doe@hospital.org"
    assert user1.is_active is True
    assert user1.is_shadow is False
    assert len(user1.id) == 36  # UUID format

    # Same email produces same deterministic ID
    user2 = UserNode(
        display_name="Different Name",
        primary_email="jane.doe@hospital.org",
    )
    assert user1.id == user2.id


def test_identity_node_deterministic_id():
    identity1 = IdentityNode(
        provider="GOOGLE",
        external_sub="google_sub_987654321",
        identifier="jane.doe@hospital.org",
        email="jane.doe@hospital.org",
        email_verified=True,
    )
    assert identity1.provider == "google"
    assert len(identity1.id) == 36

    identity2 = IdentityNode(
        provider="google",
        external_sub="google_sub_987654321",
        identifier="jane.doe@hospital.org",
    )
    assert identity1.id == identity2.id


def test_authenticator_node_deterministic_id():
    auth_node = AuthenticatorNode(
        authenticator_type="password",
        secret_hash="$argon2id$v=19$m=65536,t=3,p=4$somehashvalue...",
        name="Primary Password",
    )
    assert auth_node.__label__ == "Authenticator"
    assert len(auth_node.id) == 36
    assert auth_node.scopes == ["*"]


def test_group_node_deterministic_id_and_code():
    grp = GroupNode(
        name="Half Moon Bay Clinic",
        code=" hmb_img_01 ",
        group_type="FACILITY",
    )
    assert grp.code == "HMB_IMG_01"
    assert grp.is_active is True
    assert len(grp.id) == 36

    grp2 = GroupNode(
        name="Another Facility Name",
        code="HMB_IMG_01",
    )
    assert grp.id == grp2.id


def test_member_of_relation_properties_and_negation():
    member_rel = MemberOfRelation(
        start_node_id="usr_001",
        end_node_id="grp_001",
        roles=["Admin", "Radiologist"],
        status="ACTIVE",
        is_primary=True,
    )
    assert member_rel.__type__ == "MEMBER_OF"
    assert member_rel.status == "ACTIVE"
    assert member_rel.roles == ["Admin", "Radiologist"]
    assert member_rel.is_primary is True
    assert len(member_rel.id) == 36

    assert member_rel.to_negated_type() == "NOT_MEMBER_OF"
    assert member_rel.to_positive_type() == "MEMBER_OF"
    assert member_rel.is_negated() is False


def test_hierarchical_relations_types():
    sub_grp = SubGroupOfRelation(start_node_id="facility_1", end_node_id="hospital_1")
    assert sub_grp.__type__ == "SUB_GROUP_OF"

    has_id = HasIdentityRelation(start_node_id="usr_1", end_node_id="id_1")
    assert has_id.__type__ == "HAS_IDENTITY"

    uses_auth = UsesAuthenticatorRelation(start_node_id="id_1", end_node_id="auth_1")
    assert uses_auth.__type__ == "USES_AUTHENTICATOR"

    has_auth = HasAuthenticatorRelation(start_node_id="usr_1", end_node_id="auth_api_1")
    assert has_auth.__type__ == "HAS_AUTHENTICATOR"

    belongs_to = BelongsToRelation(start_node_id="pat_1", end_node_id="grp_1")
    assert belongs_to.__type__ == "BELONGS_TO"

    performed_at = PerformedAtRelation(start_node_id="study_1", end_node_id="grp_1")
    assert performed_at.__type__ == "PERFORMED_AT"

    scoped_to = ScopedToRelation(start_node_id="cfg_1", end_node_id="grp_1")
    assert scoped_to.__type__ == "SCOPED_TO"
