// =============================================================================
// GoSmart DICOM-FHIR Graph Server — FHIR R4B Schema Constraints & Indexes
// Generated from model.fhir (see docs/design-notes-informal/fhir_schema.md)
// =============================================================================

// -----------------------------------------------------------------------------
// 1. Uniqueness Constraints (System UUIDv5 `id` and FHIR Ingestion `uid`)
// -----------------------------------------------------------------------------
CREATE CONSTRAINT fhir_patient_sys_id_unique IF NOT EXISTS FOR (n:FHIR_Patient) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT fhir_patient_uid_unique IF NOT EXISTS FOR (n:FHIR_Patient) REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT encounter_sys_id_unique IF NOT EXISTS FOR (n:Encounter) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT encounter_uid_unique IF NOT EXISTS FOR (n:Encounter) REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT condition_sys_id_unique IF NOT EXISTS FOR (n:Condition) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT condition_uid_unique IF NOT EXISTS FOR (n:Condition) REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT imaging_study_sys_id_unique IF NOT EXISTS FOR (n:ImagingStudy) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT imaging_study_uid_unique IF NOT EXISTS FOR (n:ImagingStudy) REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT location_sys_id_unique IF NOT EXISTS FOR (n:Location) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT location_uid_unique IF NOT EXISTS FOR (n:Location) REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT observation_sys_id_unique IF NOT EXISTS FOR (n:Observation) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT observation_uid_unique IF NOT EXISTS FOR (n:Observation) REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT organization_sys_id_unique IF NOT EXISTS FOR (n:Organization) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT organization_uid_unique IF NOT EXISTS FOR (n:Organization) REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT diagnostic_report_sys_id_unique IF NOT EXISTS FOR (n:DiagnosticReport) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT diagnostic_report_uid_unique IF NOT EXISTS FOR (n:DiagnosticReport) REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT practitioner_sys_id_unique IF NOT EXISTS FOR (n:Practitioner) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT practitioner_uid_unique IF NOT EXISTS FOR (n:Practitioner) REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT service_request_sys_id_unique IF NOT EXISTS FOR (n:ServiceRequest) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT service_request_uid_unique IF NOT EXISTS FOR (n:ServiceRequest) REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT procedure_sys_id_unique IF NOT EXISTS FOR (n:Procedure) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT procedure_uid_unique IF NOT EXISTS FOR (n:Procedure) REQUIRE n.uid IS UNIQUE;

// -----------------------------------------------------------------------------
// 2. Cross-Domain DICOM Correlation Index
// -----------------------------------------------------------------------------
CREATE INDEX study_accession_number_idx IF NOT EXISTS FOR (s:Study) ON (s.accession_number);

// -----------------------------------------------------------------------------
// FHIR_Patient Property Indexes
// -----------------------------------------------------------------------------
CREATE INDEX fhir_fhir_patient_test_id_idx IF NOT EXISTS FOR (n:FHIR_Patient) ON (n.test_id);
CREATE INDEX fhir_fhir_patient_resource_id_idx IF NOT EXISTS FOR (n:FHIR_Patient) ON (n.resource_id);
CREATE INDEX fhir_fhir_patient_status_idx IF NOT EXISTS FOR (n:FHIR_Patient) ON (n.status);
CREATE INDEX fhir_fhir_patient_resource_uri_idx IF NOT EXISTS FOR (n:FHIR_Patient) ON (n.resourceUri);
CREATE INDEX fhir_fhir_patient_identifiers_idx IF NOT EXISTS FOR (n:FHIR_Patient) ON (n.identifiers);
CREATE INDEX fhir_fhir_patient_identifier_type_idx IF NOT EXISTS FOR (n:FHIR_Patient) ON (n.identifierType);
CREATE INDEX fhir_fhir_patient_active_idx IF NOT EXISTS FOR (n:FHIR_Patient) ON (n.active);
CREATE INDEX fhir_fhir_patient_name_idx IF NOT EXISTS FOR (n:FHIR_Patient) ON (n.name);
CREATE INDEX fhir_fhir_patient_gender_idx IF NOT EXISTS FOR (n:FHIR_Patient) ON (n.gender);
CREATE INDEX fhir_fhir_patient_birth_date_idx IF NOT EXISTS FOR (n:FHIR_Patient) ON (n.birthDate);
CREATE INDEX fhir_fhir_patient_managing_organization_idx IF NOT EXISTS FOR (n:FHIR_Patient) ON (n.managingOrganization);

// -----------------------------------------------------------------------------
// Encounter Property Indexes
// -----------------------------------------------------------------------------
CREATE INDEX fhir_encounter_test_id_idx IF NOT EXISTS FOR (n:Encounter) ON (n.test_id);
CREATE INDEX fhir_encounter_resource_id_idx IF NOT EXISTS FOR (n:Encounter) ON (n.resource_id);
CREATE INDEX fhir_encounter_status_idx IF NOT EXISTS FOR (n:Encounter) ON (n.status);
CREATE INDEX fhir_encounter_resource_uri_idx IF NOT EXISTS FOR (n:Encounter) ON (n.resourceUri);
CREATE INDEX fhir_encounter_identifiers_idx IF NOT EXISTS FOR (n:Encounter) ON (n.identifiers);
CREATE INDEX fhir_encounter_class_idx IF NOT EXISTS FOR (n:Encounter) ON (n.`class`);
CREATE INDEX fhir_encounter_type_idx IF NOT EXISTS FOR (n:Encounter) ON (n.type);
CREATE INDEX fhir_encounter_subject_idx IF NOT EXISTS FOR (n:Encounter) ON (n.subject);
CREATE INDEX fhir_encounter_participant_type_idx IF NOT EXISTS FOR (n:Encounter) ON (n.participantType);
CREATE INDEX fhir_encounter_participant_period_start_idx IF NOT EXISTS FOR (n:Encounter) ON (n.participantPeriodStart);
CREATE INDEX fhir_encounter_participant_period_end_idx IF NOT EXISTS FOR (n:Encounter) ON (n.participantPeriodEnd);
CREATE INDEX fhir_encounter_participant_individual_idx IF NOT EXISTS FOR (n:Encounter) ON (n.participantIndividual);
CREATE INDEX fhir_encounter_period_start_idx IF NOT EXISTS FOR (n:Encounter) ON (n.periodStart);
CREATE INDEX fhir_encounter_period_end_idx IF NOT EXISTS FOR (n:Encounter) ON (n.periodEnd);
CREATE INDEX fhir_encounter_location_idx IF NOT EXISTS FOR (n:Encounter) ON (n.location);
CREATE INDEX fhir_encounter_service_provider_idx IF NOT EXISTS FOR (n:Encounter) ON (n.serviceProvider);

// -----------------------------------------------------------------------------
// Condition Property Indexes
// -----------------------------------------------------------------------------
CREATE INDEX fhir_condition_test_id_idx IF NOT EXISTS FOR (n:Condition) ON (n.test_id);
CREATE INDEX fhir_condition_resource_id_idx IF NOT EXISTS FOR (n:Condition) ON (n.resource_id);
CREATE INDEX fhir_condition_status_idx IF NOT EXISTS FOR (n:Condition) ON (n.status);
CREATE INDEX fhir_condition_resource_uri_idx IF NOT EXISTS FOR (n:Condition) ON (n.resourceUri);
CREATE INDEX fhir_condition_identifiers_idx IF NOT EXISTS FOR (n:Condition) ON (n.identifiers);
CREATE INDEX fhir_condition_clinical_status_idx IF NOT EXISTS FOR (n:Condition) ON (n.clinicalStatus);
CREATE INDEX fhir_condition_verification_status_idx IF NOT EXISTS FOR (n:Condition) ON (n.verificationStatus);
CREATE INDEX fhir_condition_category_idx IF NOT EXISTS FOR (n:Condition) ON (n.category);
CREATE INDEX fhir_condition_code_idx IF NOT EXISTS FOR (n:Condition) ON (n.code);
CREATE INDEX fhir_condition_subject_idx IF NOT EXISTS FOR (n:Condition) ON (n.subject);
CREATE INDEX fhir_condition_encounter_idx IF NOT EXISTS FOR (n:Condition) ON (n.encounter);
CREATE INDEX fhir_condition_onset_date_time_idx IF NOT EXISTS FOR (n:Condition) ON (n.onsetDateTime);
CREATE INDEX fhir_condition_abatement_date_time_idx IF NOT EXISTS FOR (n:Condition) ON (n.abatementDateTime);
CREATE INDEX fhir_condition_recorded_date_idx IF NOT EXISTS FOR (n:Condition) ON (n.recordedDate);

// -----------------------------------------------------------------------------
// ImagingStudy Property Indexes
// -----------------------------------------------------------------------------
CREATE INDEX fhir_imaging_study_test_id_idx IF NOT EXISTS FOR (n:ImagingStudy) ON (n.test_id);
CREATE INDEX fhir_imaging_study_resource_id_idx IF NOT EXISTS FOR (n:ImagingStudy) ON (n.resource_id);
CREATE INDEX fhir_imaging_study_status_idx IF NOT EXISTS FOR (n:ImagingStudy) ON (n.status);
CREATE INDEX fhir_imaging_study_resource_uri_idx IF NOT EXISTS FOR (n:ImagingStudy) ON (n.resourceUri);
CREATE INDEX fhir_imaging_study_identifiers_idx IF NOT EXISTS FOR (n:ImagingStudy) ON (n.identifiers);
CREATE INDEX fhir_imaging_study_identifier_type_idx IF NOT EXISTS FOR (n:ImagingStudy) ON (n.identifierType);
CREATE INDEX fhir_imaging_study_identifier_use_idx IF NOT EXISTS FOR (n:ImagingStudy) ON (n.identifierUse);
CREATE INDEX fhir_imaging_study_subject_idx IF NOT EXISTS FOR (n:ImagingStudy) ON (n.subject);
CREATE INDEX fhir_imaging_study_encounter_idx IF NOT EXISTS FOR (n:ImagingStudy) ON (n.encounter);
CREATE INDEX fhir_imaging_study_based_on_idx IF NOT EXISTS FOR (n:ImagingStudy) ON (n.basedOn);
CREATE INDEX fhir_imaging_study_started_idx IF NOT EXISTS FOR (n:ImagingStudy) ON (n.started);
CREATE INDEX fhir_imaging_study_procedure_code_idx IF NOT EXISTS FOR (n:ImagingStudy) ON (n.procedureCode);
CREATE INDEX fhir_imaging_study_procedure_code_text_idx IF NOT EXISTS FOR (n:ImagingStudy) ON (n.procedureCodeText);
CREATE INDEX fhir_imaging_study_location_idx IF NOT EXISTS FOR (n:ImagingStudy) ON (n.location);
CREATE INDEX fhir_imaging_study_study_uid_idx IF NOT EXISTS FOR (n:ImagingStudy) ON (n.studyUid);
CREATE INDEX fhir_imaging_study_series_uid_idx IF NOT EXISTS FOR (n:ImagingStudy) ON (n.seriesUid);
CREATE INDEX fhir_imaging_study_instance_uid_idx IF NOT EXISTS FOR (n:ImagingStudy) ON (n.instanceUid);
CREATE INDEX fhir_imaging_study_series_modality_idx IF NOT EXISTS FOR (n:ImagingStudy) ON (n.seriesModality);
CREATE INDEX fhir_imaging_study_body_site_idx IF NOT EXISTS FOR (n:ImagingStudy) ON (n.bodySite);
CREATE INDEX fhir_imaging_study_series_started_idx IF NOT EXISTS FOR (n:ImagingStudy) ON (n.seriesStarted);

// -----------------------------------------------------------------------------
// Location Property Indexes
// -----------------------------------------------------------------------------
CREATE INDEX fhir_location_test_id_idx IF NOT EXISTS FOR (n:Location) ON (n.test_id);
CREATE INDEX fhir_location_resource_id_idx IF NOT EXISTS FOR (n:Location) ON (n.resource_id);
CREATE INDEX fhir_location_status_idx IF NOT EXISTS FOR (n:Location) ON (n.status);
CREATE INDEX fhir_location_resource_uri_idx IF NOT EXISTS FOR (n:Location) ON (n.resourceUri);
CREATE INDEX fhir_location_identifiers_idx IF NOT EXISTS FOR (n:Location) ON (n.identifiers);
CREATE INDEX fhir_location_identifier_type_idx IF NOT EXISTS FOR (n:Location) ON (n.identifierType);
CREATE INDEX fhir_location_operational_status_idx IF NOT EXISTS FOR (n:Location) ON (n.operationalStatus);
CREATE INDEX fhir_location_name_idx IF NOT EXISTS FOR (n:Location) ON (n.name);
CREATE INDEX fhir_location_alias_idx IF NOT EXISTS FOR (n:Location) ON (n.alias);
CREATE INDEX fhir_location_description_idx IF NOT EXISTS FOR (n:Location) ON (n.description);
CREATE INDEX fhir_location_mode_idx IF NOT EXISTS FOR (n:Location) ON (n.mode);
CREATE INDEX fhir_location_type_idx IF NOT EXISTS FOR (n:Location) ON (n.type);
CREATE INDEX fhir_location_physical_type_idx IF NOT EXISTS FOR (n:Location) ON (n.physicalType);
CREATE INDEX fhir_location_telecom_idx IF NOT EXISTS FOR (n:Location) ON (n.telecom);
CREATE INDEX fhir_location_address_idx IF NOT EXISTS FOR (n:Location) ON (n.address);
CREATE INDEX fhir_location_managing_organization_idx IF NOT EXISTS FOR (n:Location) ON (n.managingOrganization);
CREATE INDEX fhir_location_part_of_idx IF NOT EXISTS FOR (n:Location) ON (n.partOf);

// -----------------------------------------------------------------------------
// Observation Property Indexes
// -----------------------------------------------------------------------------
CREATE INDEX fhir_observation_test_id_idx IF NOT EXISTS FOR (n:Observation) ON (n.test_id);
CREATE INDEX fhir_observation_resource_id_idx IF NOT EXISTS FOR (n:Observation) ON (n.resource_id);
CREATE INDEX fhir_observation_status_idx IF NOT EXISTS FOR (n:Observation) ON (n.status);
CREATE INDEX fhir_observation_resource_uri_idx IF NOT EXISTS FOR (n:Observation) ON (n.resourceUri);
CREATE INDEX fhir_observation_identifiers_idx IF NOT EXISTS FOR (n:Observation) ON (n.identifiers);
CREATE INDEX fhir_observation_based_on_idx IF NOT EXISTS FOR (n:Observation) ON (n.basedOn);
CREATE INDEX fhir_observation_encounter_idx IF NOT EXISTS FOR (n:Observation) ON (n.encounter);
CREATE INDEX fhir_observation_code_idx IF NOT EXISTS FOR (n:Observation) ON (n.code);
CREATE INDEX fhir_observation_subject_idx IF NOT EXISTS FOR (n:Observation) ON (n.subject);
CREATE INDEX fhir_observation_performer_idx IF NOT EXISTS FOR (n:Observation) ON (n.performer);
CREATE INDEX fhir_observation_codes_idx IF NOT EXISTS FOR (n:Observation) ON (n.codes);
CREATE INDEX fhir_observation_numeric_values_idx IF NOT EXISTS FOR (n:Observation) ON (n.numericValues);
CREATE INDEX fhir_observation_units_idx IF NOT EXISTS FOR (n:Observation) ON (n.units);
CREATE INDEX fhir_observation_body_site_idx IF NOT EXISTS FOR (n:Observation) ON (n.bodySite);
CREATE INDEX fhir_observation_text_idx IF NOT EXISTS FOR (n:Observation) ON (n.text);

// -----------------------------------------------------------------------------
// Organization Property Indexes
// -----------------------------------------------------------------------------
CREATE INDEX fhir_organization_test_id_idx IF NOT EXISTS FOR (n:Organization) ON (n.test_id);
CREATE INDEX fhir_organization_resource_id_idx IF NOT EXISTS FOR (n:Organization) ON (n.resource_id);
CREATE INDEX fhir_organization_status_idx IF NOT EXISTS FOR (n:Organization) ON (n.status);
CREATE INDEX fhir_organization_resource_uri_idx IF NOT EXISTS FOR (n:Organization) ON (n.resourceUri);
CREATE INDEX fhir_organization_identifiers_idx IF NOT EXISTS FOR (n:Organization) ON (n.identifiers);
CREATE INDEX fhir_organization_identifier_type_idx IF NOT EXISTS FOR (n:Organization) ON (n.identifierType);
CREATE INDEX fhir_organization_active_idx IF NOT EXISTS FOR (n:Organization) ON (n.active);
CREATE INDEX fhir_organization_type_idx IF NOT EXISTS FOR (n:Organization) ON (n.type);
CREATE INDEX fhir_organization_name_idx IF NOT EXISTS FOR (n:Organization) ON (n.name);
CREATE INDEX fhir_organization_alias_idx IF NOT EXISTS FOR (n:Organization) ON (n.alias);
CREATE INDEX fhir_organization_telecom_idx IF NOT EXISTS FOR (n:Organization) ON (n.telecom);
CREATE INDEX fhir_organization_address_idx IF NOT EXISTS FOR (n:Organization) ON (n.address);
CREATE INDEX fhir_organization_part_of_idx IF NOT EXISTS FOR (n:Organization) ON (n.partOf);

// -----------------------------------------------------------------------------
// DiagnosticReport Property Indexes
// -----------------------------------------------------------------------------
CREATE INDEX fhir_diagnostic_report_test_id_idx IF NOT EXISTS FOR (n:DiagnosticReport) ON (n.test_id);
CREATE INDEX fhir_diagnostic_report_resource_id_idx IF NOT EXISTS FOR (n:DiagnosticReport) ON (n.resource_id);
CREATE INDEX fhir_diagnostic_report_status_idx IF NOT EXISTS FOR (n:DiagnosticReport) ON (n.status);
CREATE INDEX fhir_diagnostic_report_resource_uri_idx IF NOT EXISTS FOR (n:DiagnosticReport) ON (n.resourceUri);
CREATE INDEX fhir_diagnostic_report_identifiers_idx IF NOT EXISTS FOR (n:DiagnosticReport) ON (n.identifiers);
CREATE INDEX fhir_diagnostic_report_identifier_type_idx IF NOT EXISTS FOR (n:DiagnosticReport) ON (n.identifierType);
CREATE INDEX fhir_diagnostic_report_based_on_idx IF NOT EXISTS FOR (n:DiagnosticReport) ON (n.basedOn);
CREATE INDEX fhir_diagnostic_report_imaging_study_idx IF NOT EXISTS FOR (n:DiagnosticReport) ON (n.imagingStudy);
CREATE INDEX fhir_diagnostic_report_category_idx IF NOT EXISTS FOR (n:DiagnosticReport) ON (n.category);
CREATE INDEX fhir_diagnostic_report_code_idx IF NOT EXISTS FOR (n:DiagnosticReport) ON (n.code);
CREATE INDEX fhir_diagnostic_report_subject_idx IF NOT EXISTS FOR (n:DiagnosticReport) ON (n.subject);
CREATE INDEX fhir_diagnostic_report_encounter_idx IF NOT EXISTS FOR (n:DiagnosticReport) ON (n.encounter);
CREATE INDEX fhir_diagnostic_report_effective_date_time_idx IF NOT EXISTS FOR (n:DiagnosticReport) ON (n.effectiveDateTime);
CREATE INDEX fhir_diagnostic_report_issued_idx IF NOT EXISTS FOR (n:DiagnosticReport) ON (n.issued);
CREATE INDEX fhir_diagnostic_report_performer_idx IF NOT EXISTS FOR (n:DiagnosticReport) ON (n.performer);
CREATE INDEX fhir_diagnostic_report_result_idx IF NOT EXISTS FOR (n:DiagnosticReport) ON (n.result);
CREATE INDEX fhir_diagnostic_report_codes_idx IF NOT EXISTS FOR (n:DiagnosticReport) ON (n.codes);
CREATE INDEX fhir_diagnostic_report_numeric_values_idx IF NOT EXISTS FOR (n:DiagnosticReport) ON (n.numericValues);
CREATE INDEX fhir_diagnostic_report_units_idx IF NOT EXISTS FOR (n:DiagnosticReport) ON (n.units);
CREATE INDEX fhir_diagnostic_report_conclusion_idx IF NOT EXISTS FOR (n:DiagnosticReport) ON (n.conclusion);
CREATE INDEX fhir_diagnostic_report_text_status_idx IF NOT EXISTS FOR (n:DiagnosticReport) ON (n.textStatus);

// -----------------------------------------------------------------------------
// Practitioner Property Indexes
// -----------------------------------------------------------------------------
CREATE INDEX fhir_practitioner_test_id_idx IF NOT EXISTS FOR (n:Practitioner) ON (n.test_id);
CREATE INDEX fhir_practitioner_resource_id_idx IF NOT EXISTS FOR (n:Practitioner) ON (n.resource_id);
CREATE INDEX fhir_practitioner_status_idx IF NOT EXISTS FOR (n:Practitioner) ON (n.status);
CREATE INDEX fhir_practitioner_resource_uri_idx IF NOT EXISTS FOR (n:Practitioner) ON (n.resourceUri);
CREATE INDEX fhir_practitioner_identifiers_idx IF NOT EXISTS FOR (n:Practitioner) ON (n.identifiers);
CREATE INDEX fhir_practitioner_identifier_type_idx IF NOT EXISTS FOR (n:Practitioner) ON (n.identifierType);
CREATE INDEX fhir_practitioner_active_idx IF NOT EXISTS FOR (n:Practitioner) ON (n.active);
CREATE INDEX fhir_practitioner_name_idx IF NOT EXISTS FOR (n:Practitioner) ON (n.name);
CREATE INDEX fhir_practitioner_telecom_idx IF NOT EXISTS FOR (n:Practitioner) ON (n.telecom);
CREATE INDEX fhir_practitioner_address_idx IF NOT EXISTS FOR (n:Practitioner) ON (n.address);
CREATE INDEX fhir_practitioner_gender_idx IF NOT EXISTS FOR (n:Practitioner) ON (n.gender);
CREATE INDEX fhir_practitioner_birth_date_idx IF NOT EXISTS FOR (n:Practitioner) ON (n.birthDate);
CREATE INDEX fhir_practitioner_qualification_code_idx IF NOT EXISTS FOR (n:Practitioner) ON (n.qualificationCode);
CREATE INDEX fhir_practitioner_qualification_issuer_idx IF NOT EXISTS FOR (n:Practitioner) ON (n.qualificationIssuer);
CREATE INDEX fhir_practitioner_communication_idx IF NOT EXISTS FOR (n:Practitioner) ON (n.communication);
CREATE INDEX fhir_practitioner_organization_idx IF NOT EXISTS FOR (n:Practitioner) ON (n.organization);
CREATE INDEX fhir_practitioner_specialty_idx IF NOT EXISTS FOR (n:Practitioner) ON (n.specialty);

// -----------------------------------------------------------------------------
// ServiceRequest Property Indexes
// -----------------------------------------------------------------------------
CREATE INDEX fhir_service_request_test_id_idx IF NOT EXISTS FOR (n:ServiceRequest) ON (n.test_id);
CREATE INDEX fhir_service_request_resource_id_idx IF NOT EXISTS FOR (n:ServiceRequest) ON (n.resource_id);
CREATE INDEX fhir_service_request_status_idx IF NOT EXISTS FOR (n:ServiceRequest) ON (n.status);
CREATE INDEX fhir_service_request_resource_uri_idx IF NOT EXISTS FOR (n:ServiceRequest) ON (n.resourceUri);
CREATE INDEX fhir_service_request_identifiers_idx IF NOT EXISTS FOR (n:ServiceRequest) ON (n.identifiers);
CREATE INDEX fhir_service_request_identifier_type_idx IF NOT EXISTS FOR (n:ServiceRequest) ON (n.identifierType);
CREATE INDEX fhir_service_request_intent_idx IF NOT EXISTS FOR (n:ServiceRequest) ON (n.intent);
CREATE INDEX fhir_service_request_category_idx IF NOT EXISTS FOR (n:ServiceRequest) ON (n.category);
CREATE INDEX fhir_service_request_priority_idx IF NOT EXISTS FOR (n:ServiceRequest) ON (n.priority);
CREATE INDEX fhir_service_request_code_idx IF NOT EXISTS FOR (n:ServiceRequest) ON (n.code);
CREATE INDEX fhir_service_request_code_text_idx IF NOT EXISTS FOR (n:ServiceRequest) ON (n.codeText);
CREATE INDEX fhir_service_request_subject_idx IF NOT EXISTS FOR (n:ServiceRequest) ON (n.subject);
CREATE INDEX fhir_service_request_encounter_idx IF NOT EXISTS FOR (n:ServiceRequest) ON (n.encounter);
CREATE INDEX fhir_service_request_occurrence_date_time_idx IF NOT EXISTS FOR (n:ServiceRequest) ON (n.occurrenceDateTime);
CREATE INDEX fhir_service_request_authored_on_idx IF NOT EXISTS FOR (n:ServiceRequest) ON (n.authoredOn);
CREATE INDEX fhir_service_request_requester_idx IF NOT EXISTS FOR (n:ServiceRequest) ON (n.requester);
CREATE INDEX fhir_service_request_reason_code_idx IF NOT EXISTS FOR (n:ServiceRequest) ON (n.reasonCode);
CREATE INDEX fhir_service_request_body_site_idx IF NOT EXISTS FOR (n:ServiceRequest) ON (n.bodySite);
CREATE INDEX fhir_service_request_body_site_text_idx IF NOT EXISTS FOR (n:ServiceRequest) ON (n.bodySiteText);
CREATE INDEX fhir_service_request_patient_instruction_idx IF NOT EXISTS FOR (n:ServiceRequest) ON (n.patientInstruction);
CREATE INDEX fhir_service_request_supporting_info_idx IF NOT EXISTS FOR (n:ServiceRequest) ON (n.supportingInfo);

// -----------------------------------------------------------------------------
// Procedure Property Indexes
// -----------------------------------------------------------------------------
CREATE INDEX fhir_procedure_test_id_idx IF NOT EXISTS FOR (n:Procedure) ON (n.test_id);
CREATE INDEX fhir_procedure_resource_id_idx IF NOT EXISTS FOR (n:Procedure) ON (n.resource_id);
CREATE INDEX fhir_procedure_status_idx IF NOT EXISTS FOR (n:Procedure) ON (n.status);
CREATE INDEX fhir_procedure_resource_uri_idx IF NOT EXISTS FOR (n:Procedure) ON (n.resourceUri);
CREATE INDEX fhir_procedure_identifiers_idx IF NOT EXISTS FOR (n:Procedure) ON (n.identifiers);
CREATE INDEX fhir_procedure_identifier_type_idx IF NOT EXISTS FOR (n:Procedure) ON (n.identifierType);
CREATE INDEX fhir_procedure_based_on_idx IF NOT EXISTS FOR (n:Procedure) ON (n.basedOn);
CREATE INDEX fhir_procedure_part_of_idx IF NOT EXISTS FOR (n:Procedure) ON (n.partOf);
CREATE INDEX fhir_procedure_category_idx IF NOT EXISTS FOR (n:Procedure) ON (n.category);
CREATE INDEX fhir_procedure_code_idx IF NOT EXISTS FOR (n:Procedure) ON (n.code);
CREATE INDEX fhir_procedure_subject_idx IF NOT EXISTS FOR (n:Procedure) ON (n.subject);
CREATE INDEX fhir_procedure_encounter_idx IF NOT EXISTS FOR (n:Procedure) ON (n.encounter);
CREATE INDEX fhir_procedure_performed_date_time_idx IF NOT EXISTS FOR (n:Procedure) ON (n.performedDateTime);
CREATE INDEX fhir_procedure_recorder_idx IF NOT EXISTS FOR (n:Procedure) ON (n.recorder);
CREATE INDEX fhir_procedure_asserter_idx IF NOT EXISTS FOR (n:Procedure) ON (n.asserter);
CREATE INDEX fhir_procedure_performer_idx IF NOT EXISTS FOR (n:Procedure) ON (n.performer);
CREATE INDEX fhir_procedure_location_idx IF NOT EXISTS FOR (n:Procedure) ON (n.location);
CREATE INDEX fhir_procedure_reason_code_idx IF NOT EXISTS FOR (n:Procedure) ON (n.reasonCode);
CREATE INDEX fhir_procedure_body_site_idx IF NOT EXISTS FOR (n:Procedure) ON (n.bodySite);
CREATE INDEX fhir_procedure_report_idx IF NOT EXISTS FOR (n:Procedure) ON (n.report);
