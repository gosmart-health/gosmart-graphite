// =============================================================================
// GoSmart DICOM-FHIR Graph Server — System, Auth, Group, & Audit Schema
// =============================================================================

// -----------------------------------------------------------------------------
// 1. System, Auth, Group, & AuditLog Uniqueness Constraints
// -----------------------------------------------------------------------------
CREATE CONSTRAINT system_sys_id_unique IF NOT EXISTS FOR (sys:System) REQUIRE sys.id IS UNIQUE;
CREATE CONSTRAINT user_sys_id_unique IF NOT EXISTS FOR (u:User) REQUIRE u.id IS UNIQUE;
CREATE CONSTRAINT user_email_unique IF NOT EXISTS FOR (u:User) REQUIRE u.primary_email IS UNIQUE;
CREATE CONSTRAINT identity_sys_id_unique IF NOT EXISTS FOR (i:Identity) REQUIRE i.id IS UNIQUE;
CREATE CONSTRAINT authenticator_sys_id_unique IF NOT EXISTS FOR (a:Authenticator) REQUIRE a.id IS UNIQUE;
CREATE CONSTRAINT group_sys_id_unique IF NOT EXISTS FOR (g:Group) REQUIRE g.id IS UNIQUE;
CREATE CONSTRAINT group_code_unique IF NOT EXISTS FOR (g:Group) REQUIRE g.code IS UNIQUE;
CREATE CONSTRAINT audit_log_sys_id_unique IF NOT EXISTS FOR (a:AuditLog) REQUIRE a.id IS UNIQUE;

// -----------------------------------------------------------------------------
// 2. Group & Organizational Relationship Indexes
// -----------------------------------------------------------------------------
CREATE INDEX belongs_to_id_idx IF NOT EXISTS FOR ()-[r:BELONGS_TO]-() ON (r.id);
CREATE INDEX not_belongs_to_id_idx IF NOT EXISTS FOR ()-[r:NOT_BELONGS_TO]-() ON (r.id);
