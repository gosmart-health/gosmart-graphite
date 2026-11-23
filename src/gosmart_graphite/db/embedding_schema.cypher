// =============================================================================
// GoSmart DICOM-FHIR Graph Server — Decoupled Vector Embedding Schema
// =============================================================================

// -----------------------------------------------------------------------------
// 1. Embedding Node Uniqueness Constraints
// -----------------------------------------------------------------------------
CREATE CONSTRAINT embedding_sys_id_unique IF NOT EXISTS FOR (e:Embedding) REQUIRE e.id IS UNIQUE;

// -----------------------------------------------------------------------------
// 2. Embedding Property Indexes
// -----------------------------------------------------------------------------
CREATE INDEX embedding_test_id_idx IF NOT EXISTS FOR (e:Embedding) ON (e.test_id);
CREATE INDEX embedding_model_tag_idx IF NOT EXISTS FOR (e:Embedding) ON (e.modelTag);
CREATE INDEX embedding_target_field_tag_idx IF NOT EXISTS FOR (e:Embedding) ON (e.targetFieldTag);

// -----------------------------------------------------------------------------
// 3. Embedding Relationship Indexes (:EMBEDS & :NOT_EMBEDS)
// -----------------------------------------------------------------------------
CREATE INDEX embeds_id_idx IF NOT EXISTS FOR ()-[r:EMBEDS]-() ON (r.id);
CREATE INDEX not_embeds_id_idx IF NOT EXISTS FOR ()-[r:NOT_EMBEDS]-() ON (r.id);

// -----------------------------------------------------------------------------
// 4. Vector Similarity Index (768-dim Cosine)
// -----------------------------------------------------------------------------
CREATE VECTOR INDEX embedding_vector_idx IF NOT EXISTS
FOR (e:Embedding)
ON (e.vector)
OPTIONS {
  indexConfig: {
    `vector.dimensions`: 768,
    `vector.similarity_function`: 'cosine'
  }
};
