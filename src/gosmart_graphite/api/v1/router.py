from fastapi import APIRouter

from gosmart_graphite.api.v1.endpoints.auth import router as auth_router
from gosmart_graphite.api.v1.endpoints.dicom import router as dicom_router
from gosmart_graphite.api.v1.endpoints.graph import router as graph_router
from gosmart_graphite.api.v1.endpoints.health import router as health_router
from gosmart_graphite.api.v1.endpoints.llm import router as llm_router
from gosmart_graphite.api.v1.endpoints.system import router as system_router

api_v1_router = APIRouter()
api_v1_router.include_router(health_router)
api_v1_router.include_router(auth_router)
api_v1_router.include_router(system_router)
api_v1_router.include_router(llm_router)
api_v1_router.include_router(dicom_router)
api_v1_router.include_router(graph_router)
