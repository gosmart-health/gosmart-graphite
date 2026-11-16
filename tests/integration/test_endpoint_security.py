import uuid

import pytest
from fastapi.testclient import TestClient

from gosmart_graphite.core.security import create_access_token
from gosmart_graphite.db.neo4j import neo4j_client
from gosmart_graphite.main import app
from gosmart_graphite.service.graph_service import graph_service
from gosmart_graphite.service.group_service import group_service
from gosmart_graphite.service.user_service import user_service


@pytest.fixture
def raw_client():
    """TestClient without dependency overrides to verify authentic HTTP AuthN gates."""
    app.dependency_overrides.clear()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_unauthenticated_requests_rejected(raw_client):
    # Protected endpoints must reject unauthenticated requests with 401
    res_system = raw_client.get("/api/v1/system")
    assert res_system.status_code == 401
    assert "WWW-Authenticate" in res_system.headers

    res_dicomweb = raw_client.get("/dicomweb/studies")
    assert res_dicomweb.status_code == 401

    res_dicom = raw_client.get("/api/v1/configurations/dicom")
    assert res_dicom.status_code == 401

    res_graph = raw_client.get("/api/v1/graph/patients")
    assert res_graph.status_code == 401


def test_public_endpoints_accessible_without_auth(raw_client):
    # Public endpoints must succeed without credentials
    res_root = raw_client.get("/")
    assert res_root.status_code == 200

    res_health = raw_client.get("/api/v1/health")
    assert res_health.status_code == 200


@pytest.mark.anyio
async def test_authenticated_requests_with_jwt_and_api_key(raw_client):
    test_id = f"test_ep_sec_{uuid.uuid4().hex[:8]}"

    await graph_service.cleanup_test_data(test_id)
    await graph_service.init_schema_constraints()

    try:
        # Create user and group
        group = await group_service.create_group(
            name="Coastline Imaging",
            code=f"CLI_{uuid.uuid4().hex[:4]}",
            test_id=test_id,
        )

        email = f"doctor_{uuid.uuid4().hex[:4]}@coastline.org"
        user = await user_service.create_local_user(
            username=f"doc_{uuid.uuid4().hex[:4]}",
            email=email,
            password="SecurePassword2026!",
            display_name="Dr. Coastline",
            system_roles=["USER", "PHYSICIAN"],
            group_id=group["id"],
            group_roles=["Radiologist"],
            test_id=test_id,
        )

        # 1. Bearer JWT Access Token
        jwt_token = create_access_token({"sub": user["id"], "email": email})
        auth_headers = {"Authorization": f"Bearer {jwt_token}"}

        res_graph = raw_client.get("/api/v1/graph/patients", headers=auth_headers)
        assert res_graph.status_code == 200

        res_dicomweb = raw_client.get("/dicomweb/studies", headers=auth_headers)
        assert res_dicomweb.status_code == 200

        # 2. Invalid JWT token returns 401
        res_invalid_jwt = raw_client.get(
            "/api/v1/graph/patients",
            headers={"Authorization": "Bearer invalid.jwt.token"},
        )
        assert res_invalid_jwt.status_code == 401

        # 3. API Key Authentication (via X-API-Key header)
        api_key_data = await user_service.create_api_key(
            user_id=user["id"],
            name="Automated Ingest Key",
            scopes=["mcp:full", "dicom:full"],
            test_id=test_id,
        )
        raw_api_key = api_key_data["raw_api_key"]

        res_api_key = raw_client.get(
            "/api/v1/graph/patients",
            headers={"X-API-Key": raw_api_key},
        )
        assert res_api_key.status_code == 200

        # 4. Multi-Group Context Header (X-Group-ID)
        # Valid group affiliation:
        res_valid_grp = raw_client.get(
            "/api/v1/graph/patients",
            headers={**auth_headers, "X-Group-ID": group["id"]},
        )
        assert res_valid_grp.status_code == 200

        # Non-affiliated unauthorized group:
        unauthorized_group = await group_service.create_group(
            name="Unrelated Hospital",
            code=f"UNR_{uuid.uuid4().hex[:4]}",
            test_id=test_id,
        )
        res_unauthorized_grp = raw_client.get(
            "/api/v1/graph/patients",
            headers={**auth_headers, "X-Group-ID": unauthorized_group["id"]},
        )
        assert res_unauthorized_grp.status_code == 403

    finally:
        await neo4j_client.close()
