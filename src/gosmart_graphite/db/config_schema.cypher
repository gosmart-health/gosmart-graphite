// =============================================================================
// GoSmart DICOM-FHIR Graph Server — Configuration Schema (DICOM, FHIR, LLM)
// =============================================================================

// -----------------------------------------------------------------------------
// 1. Configuration Node Uniqueness Constraints
// -----------------------------------------------------------------------------
CREATE CONSTRAINT dicom_config_sys_id_unique IF NOT EXISTS FOR (c:DICOMConfiguration) REQUIRE c.id IS UNIQUE;
CREATE CONSTRAINT fhir_config_sys_id_unique IF NOT EXISTS FOR (f:FHIRConfiguration) REQUIRE f.id IS UNIQUE;
CREATE CONSTRAINT llm_config_sys_id_unique IF NOT EXISTS FOR (l:LLM_Config) REQUIRE l.id IS UNIQUE;

// -----------------------------------------------------------------------------
// 2. Configuration Property Indexes
// -----------------------------------------------------------------------------
CREATE INDEX llm_config_tag_idx IF NOT EXISTS FOR (l:LLM_Config) ON (l.tag);

// -----------------------------------------------------------------------------
// 3. Configuration Relationship Indexes
// -----------------------------------------------------------------------------
CREATE INDEX has_llm_config_id_idx IF NOT EXISTS FOR ()-[r:HAS_LLM_CONFIG]-() ON (r.id);
CREATE INDEX not_has_llm_config_id_idx IF NOT EXISTS FOR ()-[r:NOT_HAS_LLM_CONFIG]-() ON (r.id);
CREATE INDEX has_fhir_config_id_idx IF NOT EXISTS FOR ()-[r:HAS_FHIR_CONFIG]-() ON (r.id);
CREATE INDEX not_has_fhir_config_id_idx IF NOT EXISTS FOR ()-[r:NOT_HAS_FHIR_CONFIG]-() ON (r.id);
CREATE INDEX has_dicom_config_id_idx IF NOT EXISTS FOR ()-[r:HAS_DICOM_CONFIGURATION]-() ON (r.id);
CREATE INDEX not_has_dicom_config_id_idx IF NOT EXISTS FOR ()-[r:NOT_HAS_DICOM_CONFIGURATION]-() ON (r.id);
CREATE INDEX serves_id_idx IF NOT EXISTS FOR ()-[r:SERVES]-() ON (r.id);
CREATE INDEX not_serves_id_idx IF NOT EXISTS FOR ()-[r:NOT_SERVES]-() ON (r.id);
