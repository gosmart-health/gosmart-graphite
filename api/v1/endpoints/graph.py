from fastapi import APIRouter, HTTPException, Query

from model.graph import CypherQueryRequest
from service.graph_service import graph_service

router = APIRouter(prefix="/graph", tags=["Graph (Neo4j)"])


@router.get("/patients")
async def list_patients(limit: int = Query(default=50, ge=1, le=500)):
    """List patients indexed in the graph with study counts."""
    try:
        patients = await graph_service.list_patients(limit=limit)
        return {"count": len(patients), "patients": patients}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Neo4j query error: {str(e)}")


@router.get("/patients/{patient_id}")
async def get_patient_graph(patient_id: str):
    """Retrieve complete DICOM graph hierarchy (Studies, Series, Instances) for a patient."""
    try:
        data = await graph_service.get_patient_graph(patient_id)
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
