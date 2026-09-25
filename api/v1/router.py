from fastapi import APIRouter
from api.v1.endpoints.dicom import router as dicom_router
from api.v1.endpoints.graph import router as graph_router
from api.v1.endpoints.health import router as health_router

api_v1_router = APIRouter()
api_v1_router.include_router(health_router)
api_v1_router.include_router(dicom_router)
api_v1_router.include_router(graph_router)
