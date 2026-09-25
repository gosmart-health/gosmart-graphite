from typing import Dict, Optional
from pydantic import BaseModel, Field


class ServiceHealth(BaseModel):
    name: str
    status: str = Field(description="HEALTHY, DEGRADED, or UNHEALTHY")
    details: Optional[Dict[str, str]] = None


class HealthResponse(BaseModel):
    status: str = Field(description="System overall status: HEALTHY, DEGRADED, or UNHEALTHY")
    version: str
    services: Dict[str, ServiceHealth]
    storage_root_uri: str
    db_connection_uri: str
