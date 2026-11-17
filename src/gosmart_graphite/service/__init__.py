from gosmart_graphite.service.dicom_service import DicomService, dicom_service
from gosmart_graphite.service.embedding_service import (
    DEFAULT_EMBEDDING_TAG,
    EmbeddingService,
    SimilarityMetric,
    compute_cosine_scores,
    compute_dot_product_scores,
    compute_euclidean_scores,
    compute_manhattan_scores,
    compute_similarity_scores,
    normalize_vector,
    vector_service,
)
from gosmart_graphite.service.fhir_config_service import FHIRConfigService, fhir_config_service
from gosmart_graphite.service.fhir_service import (
    EncounterContext,
    EncRelevantResources,
    FHIRBatchFetcher,
    FhirService,
)
from gosmart_graphite.service.graph_service import GraphService, graph_service
from gosmart_graphite.service.group_service import (
    DEFAULT_MY_GROUP_MAX_DEPTH,
    LOST_AND_FOUND_GROUP_CODE,
    LOST_AND_FOUND_GROUP_NAME,
    LOST_AND_FOUND_GROUP_TYPE,
    UPWARD_GROUP_RELATION_TYPES,
    GroupService,
    group_or_lost_and_found,
    group_service,
    my_group,
    my_group_sync,
)
from gosmart_graphite.service.llm_config_service import LLMConfigService, llm_config_service
from gosmart_graphite.service.media_service import MediaService, media_service
from gosmart_graphite.service.pynetdicom_service import PyNetDicomService, pynetdicom_service
from gosmart_graphite.service.user_service import UserService, user_service

VectorService = EmbeddingService
embedding_service = vector_service

__all__ = [
    "DEFAULT_EMBEDDING_TAG",
    "DEFAULT_MY_GROUP_MAX_DEPTH",
    "LOST_AND_FOUND_GROUP_CODE",
    "LOST_AND_FOUND_GROUP_NAME",
    "LOST_AND_FOUND_GROUP_TYPE",
    "DicomService",
    "dicom_service",
    "EmbeddingService",
    "embedding_service",
    "EncRelevantResources",
    "EncounterContext",
    "FHIRBatchFetcher",
    "FHIRConfigService",
    "fhir_config_service",
    "FhirService",
    "GraphService",
    "graph_service",
    "LLMConfigService",
    "llm_config_service",
    "MediaService",
    "media_service",
    "PyNetDicomService",
    "pynetdicom_service",
    "GroupService",
    "group_or_lost_and_found",
    "group_service",
    "my_group",
    "my_group_sync",
    "UPWARD_GROUP_RELATION_TYPES",
    "UserService",
    "user_service",
    "SimilarityMetric",
    "VectorService",
    "compute_cosine_scores",
    "compute_dot_product_scores",
    "compute_euclidean_scores",
    "compute_manhattan_scores",
    "compute_similarity_scores",
    "normalize_vector",
    "vector_service",
]
