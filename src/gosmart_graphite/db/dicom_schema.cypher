// =============================================================================
// GoSmart DICOM-FHIR Graph Server — DICOM Hierarchy Schema Constraints
// =============================================================================
// Note: Cross-domain DICOM Study.accession_number index (study_accession_number_idx)
// is defined in fhir_schema.cypher.

// -----------------------------------------------------------------------------
// 1. DICOM Node Uniqueness Constraints (System UUIDv5 `id` & DICOM UIDs)
// -----------------------------------------------------------------------------
CREATE CONSTRAINT patient_sys_id_unique IF NOT EXISTS FOR (p:Patient) REQUIRE p.id IS UNIQUE;
CREATE CONSTRAINT patient_pid_unique IF NOT EXISTS FOR (p:Patient) REQUIRE p.patient_id IS UNIQUE;
CREATE CONSTRAINT study_sys_id_unique IF NOT EXISTS FOR (s:Study) REQUIRE s.id IS UNIQUE;
CREATE CONSTRAINT study_uid_unique IF NOT EXISTS FOR (s:Study) REQUIRE s.study_instance_uid IS UNIQUE;
CREATE CONSTRAINT series_sys_id_unique IF NOT EXISTS FOR (se:Series) REQUIRE se.id IS UNIQUE;
CREATE CONSTRAINT series_uid_unique IF NOT EXISTS FOR (se:Series) REQUIRE se.series_instance_uid IS UNIQUE;
CREATE CONSTRAINT instance_sys_id_unique IF NOT EXISTS FOR (i:Instance) REQUIRE i.id IS UNIQUE;
CREATE CONSTRAINT instance_uid_unique IF NOT EXISTS FOR (i:Instance) REQUIRE i.sop_instance_uid IS UNIQUE;
