from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from core.auth_dependency import get_current_group_context, get_current_user
from model.graph import CypherQueryRequest
from service.graph_service import graph_service

router = APIRouter(
    prefix="/graph",
    tags=["Graph (Neo4j)"],
    dependencies=[Depends(get_current_user), Depends(get_current_group_context)],
)


class RelationshipActionRequest(BaseModel):
    start_node_id: str = Field(..., description="Deterministic ID of the source node")
    end_node_id: str = Field(..., description="Deterministic ID of the target node")
    rel_type: str = Field(..., description="Relationship type, e.g. HAS_STUDY")
    acted_by: str = Field(default="user", description="Identifier of the user or agent initiating the action")


class NodeActionRequest(BaseModel):
    acted_by: str = Field(default="user", description="Identifier of the user or agent initiating the action")


@router.get("/patients")
async def list_patients(
    limit: int = Query(default=50, ge=1, le=500),
    include_tests: bool = Query(default=False, description="Whether to include test data (test_id IS NOT NULL)"),
    test_id: Optional[str] = Query(default=None, description="Filter specifically by test_id"),
):
    """List patients indexed in the graph with study counts."""
    try:
        patients = await graph_service.list_patients(limit=limit, include_tests=include_tests, test_id=test_id)
        return {"count": len(patients), "patients": patients}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Neo4j query error: {str(e)}")


@router.get("/patients/{patient_id}")
async def get_patient_graph(
    patient_id: str,
    include_tests: bool = Query(default=False, description="Whether to include test data"),
):
    """Retrieve complete DICOM graph hierarchy (Studies, Series, Instances) for a patient."""
    try:
        data = await graph_service.get_patient_graph(patient_id, include_tests=include_tests)
        if not data:
            raise HTTPException(status_code=404, detail=f"Patient '{patient_id}' not found in graph")
        return data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Neo4j query error: {str(e)}")


@router.post("/query")
async def execute_custom_cypher(req: CypherQueryRequest):
    """Execute custom Cypher query against the Neo4j DICOM graph database."""
    try:
        records = await graph_service.execute_custom_query(req.query, req.parameters)
        return {"count": len(records), "records": records}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Cypher execution failed: {str(e)}")


@router.post("/relationships/soft-delete")
async def soft_delete_relationship(req: RelationshipActionRequest):
    """Atomically soft-delete a relationship by substituting NOT_<rel_type> under a write transaction."""
    try:
        result = await graph_service.soft_delete_relationship(
            start_node_id=req.start_node_id,
            end_node_id=req.end_node_id,
            rel_type=req.rel_type,
            acted_by=req.acted_by,
        )
        if not result:
            raise HTTPException(
                status_code=404,
                detail=f"Relationship '{req.rel_type}' between '{req.start_node_id}' and '{req.end_node_id}' not found",
            )
        return {"status": "SUCCESS", "action": "DELETE", "relationship": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Relationship soft-delete failed: {str(e)}")


@router.post("/relationships/restore")
async def restore_relationship(req: RelationshipActionRequest):
    """Atomically restore a soft-deleted relationship by substituting back to <rel_type> under a write transaction."""
    try:
        result = await graph_service.restore_relationship(
            start_node_id=req.start_node_id,
            end_node_id=req.end_node_id,
            rel_type=req.rel_type,
            acted_by=req.acted_by,
        )
        if not result:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Negated relationship for '{req.rel_type}' "
                    f"between '{req.start_node_id}' and '{req.end_node_id}' not found"
                ),
            )
        return {"status": "SUCCESS", "action": "RESTORE", "relationship": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Relationship restore failed: {str(e)}")


@router.post("/nodes/{node_id}/soft-delete")
async def soft_delete_node(node_id: str, req: Optional[NodeActionRequest] = None):
    """Soft-delete a node by updating Action = 'DELETE'."""
    acted_by = req.acted_by if req else "user"
    try:
        result = await graph_service.soft_delete_node(node_id, acted_by=acted_by)
        if not result:
            raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found")
        return {"status": "SUCCESS", "action": "DELETE", "node": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Node soft-delete failed: {str(e)}")


@router.post("/nodes/{node_id}/restore")
async def restore_node(node_id: str, req: Optional[NodeActionRequest] = None):
    """Restore a soft-deleted node by updating Action = 'RESTORE'."""
    acted_by = req.acted_by if req else "user"
    try:
        result = await graph_service.restore_node(node_id, acted_by=acted_by)
        if not result:
            raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found")
        return {"status": "SUCCESS", "action": "RESTORE", "node": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Node restore failed: {str(e)}")
