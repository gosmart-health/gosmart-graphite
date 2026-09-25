from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class GraphNode(BaseModel):
    id: Any
    labels: List[str]
    properties: Dict[str, Any] = Field(default_factory=dict)


class GraphRelationship(BaseModel):
    id: Any
    type: str
    start_node_id: Any
    end_node_id: Any
    properties: Dict[str, Any] = Field(default_factory=dict)


class GraphQueryResult(BaseModel):
    nodes: List[GraphNode] = Field(default_factory=list)
    relationships: List[GraphRelationship] = Field(default_factory=list)
    raw_records: List[Dict[str, Any]] = Field(default_factory=list)
    count: int = 0


class CypherQueryRequest(BaseModel):
    query: str = Field(..., description="Cypher query to execute")
    parameters: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Parameters for the Cypher query")
