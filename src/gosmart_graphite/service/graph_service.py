import asyncio
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from gosmart_graphite.core.logging import get_logger
from gosmart_graphite.db.neo4j import Neo4jClient, neo4j_client
from gosmart_graphite.db.schema_loader import (
    SCHEMA_SCRIPT_FILES,
    SchemaInitializationError,
    extract_error_location,
    load_schema_statements,
)
from gosmart_graphite.model.audit import AuditNode
from gosmart_graphite.model.auditable import (
    generate_deterministic_id,
    to_negated_relation_type,
    to_positive_relation_type,
)
from gosmart_graphite.model.auth import BelongsToRelation, PerformedAtRelation
from gosmart_graphite.model.dicom import DicomHierarchy
from gosmart_graphite.model.graph import TransferStatusNode

logger = get_logger("gosmart_gs.graph")


INGEST_CYPHER = """
MERGE (p:Patient {id: $patient.id})
ON CREATE SET p.patient_id = $patient.patient_id,
              p.patient_name = $patient.patient_name,
              p.patient_birth_date = $patient.patient_birth_date,
              p.patient_sex = $patient.patient_sex,
              p.test_id = $patient.test_id,
              p.Action = "CREATE",
              p.ActedAt = datetime({timezone: 'UTC'}),
              p.ActedBy = $patient.ActedBy,
              p.CreatedAt = datetime({timezone: 'UTC'}),
              p.ExpiresAt = CASE WHEN $patient.ExpiresAt IS NOT NULL THEN datetime($patient.ExpiresAt) ELSE null END
ON MATCH SET  p.patient_name = coalesce($patient.patient_name, p.patient_name),
              p.patient_birth_date = coalesce($patient.patient_birth_date, p.patient_birth_date),
              p.patient_sex = coalesce($patient.patient_sex, p.patient_sex),
              p.test_id = coalesce($patient.test_id, p.test_id),
              p.Action = "UPDATE",
              p.ActedAt = datetime({timezone: 'UTC'}),
              p.ActedBy = $patient.ActedBy,
              p.UpdatedAt = datetime({timezone: 'UTC'}),
              p.ExpiresAt = CASE WHEN $patient.ExpiresAt IS NOT NULL
                                 THEN datetime($patient.ExpiresAt)
                                 ELSE p.ExpiresAt END

MERGE (s:Study {id: $study.id})
ON CREATE SET s.study_instance_uid = $study.study_instance_uid,
              s.study_date = $study.study_date,
              s.study_time = $study.study_time,
              s.study_description = $study.study_description,
              s.accession_number = $study.accession_number,
              s.test_id = $study.test_id,
              s.Action = "CREATE",
              s.ActedAt = datetime({timezone: 'UTC'}),
              s.ActedBy = $study.ActedBy,
              s.CreatedAt = datetime({timezone: 'UTC'}),
              s.ExpiresAt = CASE WHEN $study.ExpiresAt IS NOT NULL THEN datetime($study.ExpiresAt) ELSE null END
ON MATCH SET  s.study_date = coalesce($study.study_date, s.study_date),
              s.study_time = coalesce($study.study_time, s.study_time),
              s.study_description = coalesce($study.study_description, s.study_description),
              s.accession_number = coalesce($study.accession_number, s.accession_number),
              s.test_id = coalesce($study.test_id, s.test_id),
              s.Action = "UPDATE",
              s.ActedAt = datetime({timezone: 'UTC'}),
              s.ActedBy = $study.ActedBy,
              s.UpdatedAt = datetime({timezone: 'UTC'}),
              s.ExpiresAt = CASE WHEN $study.ExpiresAt IS NOT NULL
                                 THEN datetime($study.ExpiresAt)
                                 ELSE s.ExpiresAt END

MERGE (se:Series {id: $series.id})
ON CREATE SET se.series_instance_uid = $series.series_instance_uid,
              se.series_number = $series.series_number,
              se.modality = $series.modality,
              se.series_description = $series.series_description,
              se.body_part_examined = $series.body_part_examined,
              se.test_id = $series.test_id,
              se.Action = "CREATE",
              se.ActedAt = datetime({timezone: 'UTC'}),
              se.ActedBy = $series.ActedBy,
              se.CreatedAt = datetime({timezone: 'UTC'}),
              se.ExpiresAt = CASE WHEN $series.ExpiresAt IS NOT NULL THEN datetime($series.ExpiresAt) ELSE null END
ON MATCH SET  se.series_number = coalesce($series.series_number, se.series_number),
              se.modality = coalesce($series.modality, se.modality),
              se.series_description = coalesce($series.series_description, se.series_description),
              se.body_part_examined = coalesce($series.body_part_examined, se.body_part_examined),
              se.test_id = coalesce($series.test_id, se.test_id),
              se.Action = "UPDATE",
              se.ActedAt = datetime({timezone: 'UTC'}),
              se.ActedBy = $series.ActedBy,
              se.UpdatedAt = datetime({timezone: 'UTC'}),
              se.ExpiresAt = CASE WHEN $series.ExpiresAt IS NOT NULL
                                 THEN datetime($series.ExpiresAt)
                                 ELSE se.ExpiresAt END

MERGE (i:Instance {id: $instance.id})
ON CREATE SET i.sop_instance_uid = $instance.sop_instance_uid,
              i.sop_class_uid = $instance.sop_class_uid,
              i.instance_number = $instance.instance_number,
              i.transfer_syntax_uid = $instance.transfer_syntax_uid,
              i.storage_uri = $instance.storage_uri,
              i.file_size_bytes = $instance.file_size_bytes,
              i.Source = $instance.Source,
              i.SourceIdentity = $instance.SourceIdentity,
              i.Destination = $instance.Destination,
              i.DestinationIdentity = $instance.DestinationIdentity,
              i.test_id = $instance.test_id,
              i.Action = "CREATE",
              i.ActedAt = datetime({timezone: 'UTC'}),
              i.ActedBy = $instance.ActedBy,
              i.CreatedAt = datetime({timezone: 'UTC'}),
              i.ExpiresAt = CASE WHEN $instance.ExpiresAt IS NOT NULL THEN datetime($instance.ExpiresAt) ELSE null END
ON MATCH SET  i.storage_uri = coalesce($instance.storage_uri, i.storage_uri),
              i.file_size_bytes = coalesce($instance.file_size_bytes, i.file_size_bytes),
              i.Source = coalesce(i.Source, $instance.Source),
              i.SourceIdentity = coalesce(i.SourceIdentity, $instance.SourceIdentity),
              i.Destination = coalesce(i.Destination, $instance.Destination),
              i.DestinationIdentity = coalesce(i.DestinationIdentity, $instance.DestinationIdentity),
              i.test_id = coalesce($instance.test_id, i.test_id),
              i.Action = "UPDATE",
              i.ActedAt = datetime({timezone: 'UTC'}),
              i.ActedBy = $instance.ActedBy,
              i.UpdatedAt = datetime({timezone: 'UTC'}),
              i.ExpiresAt = CASE WHEN $instance.ExpiresAt IS NOT NULL
                                 THEN datetime($instance.ExpiresAt)
                                 ELSE i.ExpiresAt END

MERGE (p)-[r_ps:HAS_STUDY {id: $rel_ps.id}]->(s)
ON CREATE SET r_ps.test_id = $rel_ps.test_id,
              r_ps.Action = "CREATE",
              r_ps.ActedAt = datetime({timezone: 'UTC'}),
              r_ps.ActedBy = $rel_ps.ActedBy,
              r_ps.CreatedAt = datetime({timezone: 'UTC'}),
              r_ps.ExpiresAt = CASE WHEN $rel_ps.ExpiresAt IS NOT NULL THEN datetime($rel_ps.ExpiresAt) ELSE null END
ON MATCH SET  r_ps.test_id = coalesce($rel_ps.test_id, r_ps.test_id)

MERGE (s)-[r_se:HAS_SERIES {id: $rel_se.id}]->(se)
ON CREATE SET r_se.test_id = $rel_se.test_id,
              r_se.Action = "CREATE",
              r_se.ActedAt = datetime({timezone: 'UTC'}),
              r_se.ActedBy = $rel_se.ActedBy,
              r_se.CreatedAt = datetime({timezone: 'UTC'}),
              r_se.ExpiresAt = CASE WHEN $rel_se.ExpiresAt IS NOT NULL THEN datetime($rel_se.ExpiresAt) ELSE null END
ON MATCH SET  r_se.test_id = coalesce($rel_se.test_id, r_se.test_id)

MERGE (se)-[r_ei:HAS_INSTANCE {id: $rel_ei.id}]->(i)
ON CREATE SET r_ei.test_id = $rel_ei.test_id,
              r_ei.Action = "CREATE",
              r_ei.ActedAt = datetime({timezone: 'UTC'}),
              r_ei.ActedBy = $rel_ei.ActedBy,
              r_ei.CreatedAt = datetime({timezone: 'UTC'}),
              r_ei.ExpiresAt = CASE WHEN $rel_ei.ExpiresAt IS NOT NULL THEN datetime($rel_ei.ExpiresAt) ELSE null END
ON MATCH SET  r_ei.test_id = coalesce($rel_ei.test_id, r_ei.test_id)

WITH p, s, se, i
CALL (p, s) {
    WITH p, s, $group_id AS gid
    WHERE gid IS NOT NULL AND gid <> ''
    MATCH (g:Group {id: gid})
    MERGE (p)-[r_pg:BELONGS_TO {id: $rel_pg.id}]->(g)
    ON CREATE SET r_pg.test_id = $rel_pg.test_id,
                  r_pg.Action = "CREATE",
                  r_pg.ActedAt = datetime({timezone: 'UTC'}),
                  r_pg.ActedBy = $rel_pg.ActedBy,
                  r_pg.CreatedAt = datetime({timezone: 'UTC'})
    ON MATCH SET  r_pg.test_id = coalesce($rel_pg.test_id, r_pg.test_id)

    MERGE (s)-[r_sg:PERFORMED_AT {id: $rel_sg.id}]->(g)
    ON CREATE SET r_sg.test_id = $rel_sg.test_id,
                  r_sg.Action = "CREATE",
                  r_sg.ActedAt = datetime({timezone: 'UTC'}),
                  r_sg.ActedBy = $rel_sg.ActedBy,
                  r_sg.CreatedAt = datetime({timezone: 'UTC'})
    ON MATCH SET  r_sg.test_id = coalesce($rel_sg.test_id, r_sg.test_id)
    RETURN count(g) AS attached_count
}

RETURN p.id AS p_id,
       p.patient_id AS patient_id,
       s.id AS s_id,
       s.study_instance_uid AS study_instance_uid,
       se.id AS se_id,
       se.series_instance_uid AS series_instance_uid,
       i.id AS i_id,
       i.sop_instance_uid AS sop_instance_uid
"""


def _prepare_hierarchy_params(hierarchy: DicomHierarchy) -> Dict[str, Any]:
    """Extract and serialize dictionary parameters from DicomHierarchy for Cypher execution."""
    p_node, s_node, se_node, i_node = hierarchy.to_nodes()
    r_ps, r_se, r_ei = hierarchy.to_relations(p_node, s_node, se_node, i_node)

    def _serialize(model_obj: Any) -> Dict[str, Any]:
        d = model_obj.model_dump()
        for k, v in d.items():
            if isinstance(v, datetime):
                d[k] = v.isoformat()
        return d

    group_id = hierarchy.group_id
    if not group_id:
        try:
            from gosmart_graphite.service.group_service import group_service

            catch_all = group_service.get_or_create_catch_all_group_sync(
                test_id=hierarchy.test_id, acted_by=hierarchy.acted_by or "system"
            )
            if catch_all and catch_all.get("id"):
                group_id = catch_all["id"]
        except Exception as why:
            logger.warning("failed_to_get_or_create_catch_all_group", why=why)

    params = {
        "patient": _serialize(p_node),
        "study": _serialize(s_node),
        "series": _serialize(se_node),
        "instance": _serialize(i_node),
        "rel_ps": _serialize(r_ps),
        "rel_se": _serialize(r_se),
        "rel_ei": _serialize(r_ei),
        "group_id": group_id,
    }

    if group_id:
        r_pg = BelongsToRelation(
            start_node_id=p_node.id,
            end_node_id=group_id,
            test_id=hierarchy.test_id,
            ActedBy=hierarchy.acted_by,
        )
        r_sg = PerformedAtRelation(
            start_node_id=s_node.id,
            end_node_id=group_id,
            test_id=hierarchy.test_id,
            ActedBy=hierarchy.acted_by,
        )
        params["rel_pg"] = _serialize(r_pg)
        params["rel_sg"] = _serialize(r_sg)
    else:
        params["rel_pg"] = {"id": "", "test_id": None, "ActedBy": "system"}
        params["rel_sg"] = {"id": "", "test_id": None, "ActedBy": "system"}

    return params


class GraphService:
    """Service for managing DICOM graph nodes and relationships in Neo4j."""

    SCHEMA_SCRIPTS: tuple[str, ...] = SCHEMA_SCRIPT_FILES

    def __init__(
        self,
        client: Optional[Neo4jClient] = None,
        schema_dir: Optional[Path] = None,
        schema_scripts: Optional[Sequence[str]] = None,
    ) -> None:
        self.client = client or neo4j_client
        self._schema_dir: Optional[Path] = schema_dir
        self._schema_scripts: Optional[tuple[str, ...]] = tuple(schema_scripts) if schema_scripts is not None else None
        self._constraints_initialized: bool = False

    @staticmethod
    def _prepare_audit_log_params(node: AuditNode) -> Dict[str, Any]:
        """Format AuditNode attributes for Cypher persistence."""
        params = {
            "id": node.id,
            "action": node.action,
            "statusCode": node.statusCode,
            "statusMessage": node.statusMessage,
            "severity": node.severity,
            "actorId": node.actorId,
            "actor": node.actor,
            "targetId": node.targetId,
            "target": node.target,
            "result": node.result,
            "details": node.details,
            "event_id": node.event_id,
            "test_id": node.test_id,
            "Action": node.Action,
            "ActedBy": node.ActedBy,
            "ExpiresAt": node.ExpiresAt.isoformat() if node.ExpiresAt else None,
        }
        return params

    @staticmethod
    def _prepare_transfer_status_params(node: TransferStatusNode) -> Dict[str, Any]:
        """Format TransferStatusNode attributes for Cypher persistence."""
        params = {
            "id": node.id,
            "sender": node.sender,
            "recipient": node.recipient,
            "objectRef": node.objectRef,
            "transferTimeMs": node.transferTimeMs,
            "transferBytes": node.transferBytes,
            "statusCode": node.statusCode,
            "statusMessage": node.statusMessage,
            "sop_instance_uid": node.sop_instance_uid,
            "direction": node.direction,
            "service_type": node.service_type,
            "test_id": node.test_id,
            "Action": node.Action,
            "ActedBy": node.ActedBy,
            "ExpiresAt": node.ExpiresAt.isoformat() if node.ExpiresAt else None,
        }
        return params

    async def cleanup_test_data(self, test_id: str) -> int:
        """Detach and delete all nodes and associated relationships tagged with test_id."""
        if not test_id:
            return 0
        query = """
        MATCH (n {test_id: $test_id})
        DETACH DELETE n
        RETURN count(n) AS deleted_count
        """
        records = await self.client.execute_query(query, {"test_id": test_id})
        return records[0].get("deleted_count", 0) if records else 0

    def cleanup_test_data_sync(self, test_id: str) -> int:
        """Detach and delete all nodes tagged with test_id synchronously."""
        if not test_id:
            return 0
        query = """
        MATCH (n {test_id: $test_id})
        DETACH DELETE n
        RETURN count(n) AS deleted_count
        """
        try:
            driver = self.client.get_sync_driver()
            with driver.session() as session:
                result = session.run(query, {"test_id": test_id})
                record = result.single()
                return record["deleted_count"] if record else 0
        except Exception as e:
            logger.warning("cleanup_test_data_sync_failed", error=str(e))
            return 0

    async def execute_custom_query(
        self,
        query: str,
        parameters: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Execute an arbitrary read or write Cypher query."""
        return await self.client.execute_query(query, parameters or {})

    def find_destination_ae_sync(self, ae_title: str) -> Optional[Dict[str, Any]]:
        """Look up destination AE connection parameters from active DICOMConfiguration nodes."""
        query = """
        MATCH (c:DICOMConfiguration)
        WHERE (c.Action IS NULL OR c.Action <> 'DELETE')
          AND c.ae_title = $ae_title
        RETURN c.host AS host, c.port AS port, c.ae_title AS ae_title
        LIMIT 1
        """
        try:
            driver = self.client.get_sync_driver()
            with driver.session() as session:
                result = session.run(query, ae_title=ae_title.strip())
                record = result.single()
                if record:
                    return record.data()
        except Exception as e:
            logger.warning("find_destination_ae_failed", ae_title=ae_title, error=str(e))
        return None

    def find_instances_for_retrieve_sync(
        self,
        patient_id: Optional[str] = None,
        study_uid: Optional[str] = None,
        series_uid: Optional[str] = None,
        sop_uid: Optional[str] = None,
        include_tests: bool = True,
        test_id: Optional[str] = None,
        accessible_group_ids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Synchronously retrieve instance records and storage URIs for C-MOVE and C-GET."""
        query = """
        MATCH (p:Patient)-[:HAS_STUDY]->(s:Study)-[:HAS_SERIES]->(se:Series)-[:HAS_INSTANCE]->(i:Instance)
        WHERE (i.Action IS NULL OR i.Action <> 'DELETE')
          AND (i.ExpiresAt IS NULL OR i.ExpiresAt > datetime({timezone: 'UTC'}))
          AND ($patient_id IS NULL OR $patient_id = '' OR p.patient_id = $patient_id)
          AND ($study_uid IS NULL OR $study_uid = '' OR s.study_instance_uid = $study_uid)
          AND ($series_uid IS NULL OR $series_uid = '' OR se.series_instance_uid = $series_uid)
          AND ($sop_uid IS NULL OR $sop_uid = '' OR i.sop_instance_uid = $sop_uid)
          AND (
            $include_tests = true
            OR i.test_id IS NULL
            OR i.test_id = ''
            OR ($test_id IS NOT NULL AND i.test_id = $test_id)
          )
        OPTIONAL MATCH (s)-[:PERFORMED_AT]->(g_s:Group)
          WHERE (g_s.Action IS NULL OR g_s.Action <> 'DELETE')
        OPTIONAL MATCH (p)-[:BELONGS_TO]->(g_p:Group)
          WHERE (g_p.Action IS NULL OR g_p.Action <> 'DELETE')
        WITH p, s, se, i, g_s, g_p
        WHERE (
            $accessible_group_ids IS NULL
            OR g_s.id IN $accessible_group_ids
            OR g_p.id IN $accessible_group_ids
          )
        RETURN i.sop_instance_uid AS sop_instance_uid,
               i.sop_class_uid AS sop_class_uid,
               i.transfer_syntax_uid AS transfer_syntax_uid,
               i.storage_uri AS storage_uri
        ORDER BY i.instance_number ASC, i.sop_instance_uid ASC
        """
        try:
            driver = self.client.get_sync_driver()
            with driver.session() as session:
                result = session.run(
                    query,
                    patient_id=patient_id,
                    study_uid=study_uid,
                    series_uid=series_uid,
                    sop_uid=sop_uid,
                    include_tests=include_tests,
                    test_id=test_id,
                    accessible_group_ids=accessible_group_ids,
                )
                return [record.data() for record in result]
        except Exception as e:
            logger.warning("graph_find_instances_failed", error=str(e), exc_info=True)
            return []

    def find_patient_records_sync(
        self,
        patient_id: Optional[str] = None,
        patient_name: Optional[str] = None,
        include_tests: bool = True,
        test_id: Optional[str] = None,
        accessible_group_ids: Optional[List[str]] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Synchronous search for Patient Root C-FIND queries."""
        query = """
        MATCH (p:Patient)
        WHERE (p.Action IS NULL OR p.Action <> 'DELETE')
          AND (p.ExpiresAt IS NULL OR p.ExpiresAt > datetime({timezone: 'UTC'}))
          AND (
            $patient_id IS NULL OR $patient_id = '' OR $patient_id = '*'
            OR p.patient_id = $patient_id OR toLower(p.patient_id) CONTAINS toLower(replace($patient_id, '*', ''))
          )
          AND (
            $patient_name IS NULL OR $patient_name = '' OR $patient_name = '*'
            OR p.patient_name = $patient_name
            OR toLower(p.patient_name) CONTAINS toLower(replace($patient_name, '*', ''))
          )
          AND (
            $include_tests = true
            OR p.test_id IS NULL
            OR p.test_id = ''
            OR ($test_id IS NOT NULL AND p.test_id = $test_id)
          )
        OPTIONAL MATCH (p)-[:BELONGS_TO]->(g_p:Group)
          WHERE (g_p.Action IS NULL OR g_p.Action <> 'DELETE')
        WITH p, g_p
        WHERE ($accessible_group_ids IS NULL OR g_p.id IN $accessible_group_ids)
        RETURN p.patient_id AS patient_id,
               p.patient_name AS patient_name,
               p.patient_birth_date AS patient_birth_date,
               p.patient_sex AS patient_sex
        ORDER BY p.patient_id ASC
        LIMIT $limit
        """
        try:
            driver = self.client.get_sync_driver()
            with driver.session() as session:
                result = session.run(
                    query,
                    patient_id=patient_id,
                    patient_name=patient_name,
                    include_tests=include_tests,
                    test_id=test_id,
                    accessible_group_ids=accessible_group_ids,
                    limit=limit,
                )
                return [record.data() for record in result]
        except Exception as e:
            logger.warning("graph_find_patients_failed", error=str(e), exc_info=True)
            return []

    def get_instance_storage_uri_sync(
        self,
        sop_uid: str,
        accessible_group_ids: Optional[List[str]] = None,
    ) -> Optional[str]:
        """Resolve storage URI for an instance synchronously from Neo4j."""
        query = """
        MATCH (s:Study)-[:HAS_SERIES]->(se:Series)-[:HAS_INSTANCE]->(i:Instance {sop_instance_uid: $sop_uid})
        WHERE (i.Action IS NULL OR i.Action <> 'DELETE')
          AND (i.ExpiresAt IS NULL OR i.ExpiresAt > datetime({timezone: 'UTC'}))
        OPTIONAL MATCH (s)-[:PERFORMED_AT]->(g_s:Group)
          WHERE (g_s.Action IS NULL OR g_s.Action <> 'DELETE')
        WITH s, se, i, g_s
        WHERE ($accessible_group_ids IS NULL OR g_s.id IN $accessible_group_ids)
        RETURN i.storage_uri AS storage_uri
        LIMIT 1
        """
        try:
            driver = self.client.get_sync_driver()
            with driver.session() as session:
                result = session.run(query, sop_uid=sop_uid, accessible_group_ids=accessible_group_ids)
                record = result.single()
                if record:
                    return record.get("storage_uri")
        except Exception as e:
            logger.warning("graph_instance_storage_uri_failed", sop_uid=sop_uid, error=str(e))
        return None

    async def get_patient_graph(
        self,
        patient_id: str,
        include_tests: bool = False,
    ) -> Dict[str, Any]:
        """Retrieve full hierarchy for a patient by patient_id or deterministic id."""
        query = """
        MATCH (p:Patient)
        WHERE (p.patient_id = $patient_id OR p.id = $patient_id)
          AND ($include_tests = true OR p.test_id IS NULL OR p.test_id = '')
          AND (p.Action IS NULL OR p.Action <> 'DELETE')
          AND (p.ExpiresAt IS NULL OR p.ExpiresAt > datetime({timezone: 'UTC'}))
        OPTIONAL MATCH (p)-[:HAS_STUDY]->(s:Study)
          WHERE (s.Action IS NULL OR s.Action <> 'DELETE')
            AND (s.ExpiresAt IS NULL OR s.ExpiresAt > datetime({timezone: 'UTC'}))
        OPTIONAL MATCH (s)-[:HAS_SERIES]->(se:Series)
          WHERE (se.Action IS NULL OR se.Action <> 'DELETE')
            AND (se.ExpiresAt IS NULL OR se.ExpiresAt > datetime({timezone: 'UTC'}))
        OPTIONAL MATCH (se)-[:HAS_INSTANCE]->(i:Instance)
          WHERE (i.Action IS NULL OR i.Action <> 'DELETE')
            AND (i.ExpiresAt IS NULL OR i.ExpiresAt > datetime({timezone: 'UTC'}))
        RETURN p,
               collect(distinct s) AS studies,
               collect(distinct se) AS series,
               collect(distinct i) AS instances
        """
        records = await self.client.execute_query(
            query,
            {"patient_id": patient_id, "include_tests": include_tests},
        )
        if records:
            return records[0]
        return {}

    def get_series_storage_uris_sync(
        self,
        study_uid: str,
        series_uid: str,
        accessible_group_ids: Optional[List[str]] = None,
    ) -> List[Dict[str, str]]:
        """Resolve all storage URIs for a series synchronously from Neo4j."""
        query = """
        MATCH (s:Study {study_instance_uid: $study_uid})
              -[:HAS_SERIES]->(se:Series {series_instance_uid: $series_uid})
              -[:HAS_INSTANCE]->(i:Instance)
        WHERE (i.Action IS NULL OR i.Action <> 'DELETE')
          AND (i.ExpiresAt IS NULL OR i.ExpiresAt > datetime({timezone: 'UTC'}))
        OPTIONAL MATCH (s)-[:PERFORMED_AT]->(g_s:Group)
          WHERE (g_s.Action IS NULL OR g_s.Action <> 'DELETE')
        WITH s, se, i, g_s
        WHERE ($accessible_group_ids IS NULL OR g_s.id IN $accessible_group_ids)
        RETURN i.sop_instance_uid AS sop_instance_uid, i.storage_uri AS storage_uri
        """
        try:
            driver = self.client.get_sync_driver()
            with driver.session() as session:
                result = session.run(
                    query,
                    study_uid=study_uid,
                    series_uid=series_uid,
                    accessible_group_ids=accessible_group_ids,
                )
                return [record.data() for record in result]
        except Exception as e:
            logger.warning("graph_series_storage_uris_failed", study_uid=study_uid, series_uid=series_uid, error=str(e))
        return []

    def get_study_storage_uris_sync(
        self,
        study_uid: str,
        accessible_group_ids: Optional[List[str]] = None,
    ) -> List[Dict[str, str]]:
        """Resolve all storage URIs for a study synchronously from Neo4j."""
        query = """
        MATCH (s:Study {study_instance_uid: $study_uid})-[:HAS_SERIES]->(se:Series)-[:HAS_INSTANCE]->(i:Instance)
        WHERE (i.Action IS NULL OR i.Action <> 'DELETE')
          AND (i.ExpiresAt IS NULL OR i.ExpiresAt > datetime({timezone: 'UTC'}))
        OPTIONAL MATCH (s)-[:PERFORMED_AT]->(g_s:Group)
          WHERE (g_s.Action IS NULL OR g_s.Action <> 'DELETE')
        WITH s, se, i, g_s
        WHERE ($accessible_group_ids IS NULL OR g_s.id IN $accessible_group_ids)
        RETURN i.sop_instance_uid AS sop_instance_uid, i.storage_uri AS storage_uri
        """
        try:
            driver = self.client.get_sync_driver()
            with driver.session() as session:
                result = session.run(query, study_uid=study_uid, accessible_group_ids=accessible_group_ids)
                return [record.data() for record in result]
        except Exception as e:
            logger.warning("graph_study_storage_uris_failed", study_uid=study_uid, error=str(e))
        return []

    def _get_bool_setting(self, name: str, default: bool) -> bool:
        """Safely read a boolean setting from client.settings, falling back to default for mocks."""
        settings = getattr(self.client, "settings", None)
        value = getattr(settings, name, default)
        return value if isinstance(value, bool) else default

    def _get_float_setting(self, name: str, default: float) -> float:
        """Safely read a numeric setting from client.settings, falling back to default for mocks."""
        settings = getattr(self.client, "settings", None)
        value = getattr(settings, name, default)
        if isinstance(value, bool):
            return default
        return float(value) if isinstance(value, (int, float)) else default

    async def _ensure_neo4j_reachable_for_schema_init(self) -> bool:
        """Verify Neo4j connectivity according to DEV_MODE vs production retry policy.

        - When ``GOSMART_GS_DEV_MODE=true``: check connectivity once; if Neo4j is offline,
          log a warning and return ``False`` so local development can proceed.
        - When ``GOSMART_GS_DEV_MODE=false`` (production): retry connectivity with capped
          exponential backoff up to ``DB_CONNECT_RETRY_TIMEOUT_SEC`` (``0`` = retry
          indefinitely) to ride out transient cloud outages or database upgrades, raising
          ``SchemaInitializationError`` if the retry window is exhausted.
        """
        uri = getattr(getattr(self.client, "settings", None), "DB_CONNECTION_URI", "<unknown>")
        dev_mode = self._get_bool_setting("DEV_MODE", False)

        if dev_mode:
            if await self.client.verify_connectivity():
                return True
            logger.warning(
                "schema_init_skipped_neo4j_offline_dev_mode",
                uri=str(uri),
                dev_mode=True,
            )
            return False

        timeout_sec = max(self._get_float_setting("DB_CONNECT_RETRY_TIMEOUT_SEC", 600.0), 0.0)
        initial_delay = max(self._get_float_setting("DB_CONNECT_RETRY_INITIAL_DELAY_SEC", 2.0), 0.001)
        max_delay = max(self._get_float_setting("DB_CONNECT_RETRY_MAX_DELAY_SEC", 30.0), initial_delay)

        start_time = time.monotonic()
        attempt = 0
        delay = initial_delay

        while True:
            attempt += 1
            if await self.client.verify_connectivity():
                if attempt > 1:
                    logger.info(
                        "neo4j_connectivity_restored",
                        attempts=attempt,
                        elapsed_sec=round(time.monotonic() - start_time, 2),
                        uri=str(uri),
                    )
                return True

            elapsed = time.monotonic() - start_time
            if 0 < timeout_sec <= elapsed:
                msg = (
                    f"Neo4j at '{uri}' remained unreachable after {attempt} attempt(s) "
                    f"over {elapsed:.1f}s (timeout={timeout_sec:.1f}s) while initializing schema constraints"
                )
                logger.error(
                    "schema_init_neo4j_unreachable_timeout",
                    uri=str(uri),
                    attempts=attempt,
                    elapsed_sec=round(elapsed, 2),
                    timeout_sec=timeout_sec,
                )
                raise SchemaInitializationError(msg)

            sleep_sec = min(delay, timeout_sec - elapsed) if timeout_sec > 0 else delay
            logger.warning(
                "neo4j_connectivity_retry",
                uri=str(uri),
                attempt=attempt,
                elapsed_sec=round(elapsed, 2),
                next_retry_sec=round(sleep_sec, 2),
                timeout_sec=timeout_sec,
            )
            await asyncio.sleep(sleep_sec)
            delay = min(delay * 2.0, max_delay)

    async def init_schema_constraints(self, force: bool = False) -> None:
        """Load allowlisted Cypher schema scripts from `db/` and apply constraints & indexes in Neo4j."""
        if self._constraints_initialized and not force:
            return

        script_files = self._schema_scripts if self._schema_scripts is not None else self.SCHEMA_SCRIPTS
        statements = load_schema_statements(script_files=script_files, schema_dir=self._schema_dir)

        if not await self._ensure_neo4j_reachable_for_schema_init():
            return

        for stmt in statements:
            try:
                await self.client.execute_query(stmt.statement)
                logger.debug(
                    "schema_statement_applied",
                    script_file=stmt.source_file,
                    line=stmt.start_line,
                    statement_index=stmt.statement_index,
                )
            except Exception as exc:
                error_line, error_col = extract_error_location(stmt, exc)
                col_suffix = f", column {error_col}" if error_col is not None else ""
                if stmt.start_line != stmt.end_line or error_col is not None:
                    location_detail = f"line {error_line}{col_suffix} (statement at {stmt.line_range_label})"
                else:
                    location_detail = f"line {error_line}"
                msg = (
                    f"Failed to execute Cypher schema script '{stmt.source_file}' at {location_detail} "
                    f"(statement #{stmt.statement_index}): {stmt.statement} | "
                    f"Cause: {type(exc).__name__}: {exc}"
                )
                logger.error(
                    "schema_statement_execution_failed",
                    script_file=stmt.source_file,
                    start_line=stmt.start_line,
                    end_line=stmt.end_line,
                    error_line=error_line,
                    error_column=error_col,
                    statement_index=stmt.statement_index,
                    statement=stmt.statement,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                raise SchemaInitializationError(
                    msg,
                    script_file=stmt.source_file,
                    start_line=stmt.start_line,
                    end_line=stmt.end_line,
                    error_line=error_line,
                    error_column=error_col,
                    statement_index=stmt.statement_index,
                    statement=stmt.statement,
                    cause=exc,
                ) from exc

        try:
            from gosmart_graphite.service.group_service import GroupService, group_service

            target_group_svc = group_service if self.client is neo4j_client else GroupService(client=self.client)
            await target_group_svc.ensure_lost_and_found_group()
        except Exception as e:
            logger.warning("Failed to initialize LostAndFound group during schema init: %s", e)

        self._constraints_initialized = True
        logger.info(
            "schema_constraints_verified",
            total_scripts=len(script_files),
            total_statements=len(statements),
        )

    async def list_patients(
        self,
        limit: int = 50,
        include_tests: bool = False,
        test_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List patients with study counts, omitting deleted and expired nodes, and filtering test data."""
        query = """
        MATCH (p:Patient)
        WHERE ($include_tests = true OR p.test_id IS NULL OR p.test_id = '')
          AND ($test_id IS NULL OR p.test_id = $test_id)
          AND (p.Action IS NULL OR p.Action <> 'DELETE')
          AND (p.ExpiresAt IS NULL OR p.ExpiresAt > datetime({timezone: 'UTC'}))
        OPTIONAL MATCH (p)-[:HAS_STUDY]->(s:Study)
        WHERE (s.Action IS NULL OR s.Action <> 'DELETE')
          AND (s.ExpiresAt IS NULL OR s.ExpiresAt > datetime({timezone: 'UTC'}))
        RETURN p.id AS id,
               p.patient_id AS patient_id,
               p.patient_name AS patient_name,
               p.patient_birth_date AS patient_birth_date,
               p.patient_sex AS patient_sex,
               p.test_id AS test_id,
               p.Action AS Action,
               p.ActedAt AS ActedAt,
               p.ActedBy AS ActedBy,
               p.CreatedAt AS CreatedAt,
               p.ExpiresAt AS ExpiresAt,
               count(s) AS study_count
        ORDER BY p.patient_id
        LIMIT $limit
        """
        return await self.client.execute_query(
            query,
            {"limit": limit, "include_tests": include_tests, "test_id": test_id},
        )

    async def restore_node(
        self,
        node_id: str,
        acted_by: str = "system",
    ) -> Optional[Dict[str, Any]]:
        """Restore a soft-deleted node by updating Action='RESTORE', ActedAt, and ActedBy."""
        query = """
        MATCH (n {id: $node_id})
        SET n.Action = "RESTORE",
            n.ActedAt = datetime({timezone: 'UTC'}),
            n.ActedBy = $acted_by
        RETURN properties(n) AS n
        """
        records = await self.client.execute_query(query, {"node_id": node_id, "acted_by": acted_by})
        if records:
            return records[0].get("n", records[0])
        return None

    async def restore_relationship(
        self,
        start_node_id: str,
        end_node_id: str,
        rel_type: str,
        acted_by: str = "system",
    ) -> Optional[Dict[str, Any]]:
        """Atomically swap negated NOT_<rel_type> relationship back to positive under a write transaction."""
        pos_type = to_positive_relation_type(rel_type)
        neg_type = to_negated_relation_type(rel_type)
        new_rel_id = generate_deterministic_id(
            {"start_node_id": start_node_id, "type": pos_type, "end_node_id": end_node_id}
        )

        query = f"""
        MATCH (a {{id: $start_node_id}})-[r:{neg_type}]->(b {{id: $end_node_id}})
        WITH a, b, r, properties(r) AS old_props
        CREATE (a)-[r_pos:{pos_type}]->(b)
        SET r_pos = old_props,
            r_pos.id = $new_rel_id,
            r_pos.Action = "RESTORE",
            r_pos.ActedAt = datetime({{timezone: 'UTC'}}),
            r_pos.ActedBy = $acted_by
        DELETE r
        RETURN properties(r_pos) AS r_pos
        """

        async def _restore_tx(tx: Any) -> Optional[Dict[str, Any]]:
            res = await tx.run(
                query,
                start_node_id=start_node_id,
                end_node_id=end_node_id,
                new_rel_id=new_rel_id,
                acted_by=acted_by,
            )
            record = await res.single()
            if not record:
                return None
            data = record.data()
            return data.get("r_pos", data)

        return await self.client.execute_write_tx(_restore_tx)

    async def save_audit_log(self, node: AuditNode) -> Dict[str, Any]:
        """Asynchronously persist a standalone auditable AuditNode in Neo4j."""
        query = """
        CREATE (a:AuditLog {
            id: $id,
            action: $action,
            statusCode: $statusCode,
            statusMessage: $statusMessage,
            severity: $severity,
            actorId: $actorId,
            actor: $actor,
            targetId: $targetId,
            target: $target,
            result: $result,
            details: $details,
            event_id: $event_id,
            test_id: $test_id,
            Action: $Action,
            ActedAt: datetime({timezone: 'UTC'}),
            ActedBy: $ActedBy,
            CreatedAt: datetime({timezone: 'UTC'}),
            ExpiresAt: CASE WHEN $ExpiresAt IS NOT NULL
                            THEN datetime($ExpiresAt)
                            ELSE datetime({timezone: 'UTC'}) + duration({days: 365}) END
        })
        RETURN a.id AS id
        """
        params = self._prepare_audit_log_params(node)
        try:
            records = await self.client.execute_query(query, params)
            if records:
                return {"id": records[0].get("id", node.id), "success": True}
            return {"id": node.id, "success": True}
        except Exception as e:
            logger.warning("save_audit_log_failed", error=str(e), exc_info=True)
            return {"id": node.id, "success": False, "error": str(e)}

    def save_audit_log_sync(self, node: AuditNode) -> Dict[str, Any]:
        """Synchronously persist a standalone auditable AuditNode in Neo4j."""
        query = """
        CREATE (a:AuditLog {
            id: $id,
            action: $action,
            statusCode: $statusCode,
            statusMessage: $statusMessage,
            severity: $severity,
            actorId: $actorId,
            actor: $actor,
            targetId: $targetId,
            target: $target,
            result: $result,
            details: $details,
            event_id: $event_id,
            test_id: $test_id,
            Action: $Action,
            ActedAt: datetime({timezone: 'UTC'}),
            ActedBy: $ActedBy,
            CreatedAt: datetime({timezone: 'UTC'}),
            ExpiresAt: CASE WHEN $ExpiresAt IS NOT NULL
                            THEN datetime($ExpiresAt)
                            ELSE datetime({timezone: 'UTC'}) + duration({days: 365}) END
        })
        RETURN a.id AS id
        """
        params = self._prepare_audit_log_params(node)
        try:
            driver = self.client.get_sync_driver()
            with driver.session() as session:
                result = session.run(query, **params)
                record = result.single()
                return {"id": record["id"] if record else node.id, "success": True}
        except Exception as e:
            logger.warning("save_audit_log_sync_failed", error=str(e), exc_info=True)
            return {"id": node.id, "success": False, "error": str(e)}

    async def save_hierarchy(self, hierarchy: DicomHierarchy) -> Dict[str, Any]:
        """Save a DICOM hierarchy asynchronously into Neo4j."""
        if not self._constraints_initialized:
            await self.init_schema_constraints()

        params = _prepare_hierarchy_params(hierarchy)
        records = await self.client.execute_query(INGEST_CYPHER, params)
        if records:
            return records[0]
        return {}

    def save_hierarchy_sync(self, hierarchy: DicomHierarchy) -> Dict[str, Any]:
        """Save a DICOM hierarchy synchronously into Neo4j (used by Pykka actors)."""
        params = _prepare_hierarchy_params(hierarchy)
        driver = self.client.get_sync_driver()
        with driver.session(database=self.client.settings.DB_DATABASE) as session:
            result = session.run(INGEST_CYPHER, params)
            record = result.single()
            return record.data() if record else {}

    async def save_transfer_status(self, node: TransferStatusNode) -> Dict[str, Any]:
        """Asynchronously persist a standalone auditable TransferStatusNode in Neo4j."""
        query = """
        CREATE (t:TransferStatus {
            id: $id,
            sender: $sender,
            recipient: $recipient,
            objectRef: $objectRef,
            transferTimeMs: $transferTimeMs,
            transferBytes: $transferBytes,
            statusCode: $statusCode,
            statusMessage: $statusMessage,
            sop_instance_uid: $sop_instance_uid,
            direction: $direction,
            service_type: $service_type,
            test_id: $test_id,
            Action: $Action,
            ActedAt: datetime({timezone: 'UTC'}),
            ActedBy: $ActedBy,
            CreatedAt: datetime({timezone: 'UTC'}),
            ExpiresAt: CASE WHEN $ExpiresAt IS NOT NULL
                            THEN datetime($ExpiresAt)
                            ELSE datetime({timezone: 'UTC'}) + duration({days: 30}) END
        })
        RETURN t.id AS id
        """
        params = self._prepare_transfer_status_params(node)
        try:
            records = await self.client.execute_query(query, params)
            if records:
                return {"id": records[0].get("id", node.id), "success": True}
            return {"id": node.id, "success": True}
        except Exception as e:
            logger.warning("save_transfer_status_failed", error=str(e), exc_info=True)
            return {"id": node.id, "success": False, "error": str(e)}

    def save_transfer_status_sync(self, node: TransferStatusNode) -> Dict[str, Any]:
        """Synchronously persist a standalone auditable TransferStatusNode in Neo4j."""
        query = """
        CREATE (t:TransferStatus {
            id: $id,
            sender: $sender,
            recipient: $recipient,
            objectRef: $objectRef,
            transferTimeMs: $transferTimeMs,
            transferBytes: $transferBytes,
            statusCode: $statusCode,
            statusMessage: $statusMessage,
            sop_instance_uid: $sop_instance_uid,
            direction: $direction,
            service_type: $service_type,
            test_id: $test_id,
            Action: $Action,
            ActedAt: datetime({timezone: 'UTC'}),
            ActedBy: $ActedBy,
            CreatedAt: datetime({timezone: 'UTC'}),
            ExpiresAt: CASE WHEN $ExpiresAt IS NOT NULL
                            THEN datetime($ExpiresAt)
                            ELSE datetime({timezone: 'UTC'}) + duration({days: 30}) END
        })
        RETURN t.id AS id
        """
        params = self._prepare_transfer_status_params(node)
        try:
            driver = self.client.get_sync_driver()
            with driver.session() as session:
                result = session.run(query, **params)
                record = result.single()
                return {"id": record["id"] if record else node.id, "success": True}
        except Exception as e:
            logger.warning("save_transfer_status_sync_failed", error=str(e), exc_info=True)
            return {"id": node.id, "success": False, "error": str(e)}

    async def search_audit_logs(
        self,
        action: Optional[str] = None,
        actor_id: Optional[str] = None,
        target_id: Optional[str] = None,
        severity: Optional[str] = None,
        include_tests: bool = False,
        test_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Asynchronously search and retrieve AuditLog records from Neo4j."""
        query = """
        MATCH (a:AuditLog)
        WHERE ($action IS NULL OR $action = '' OR a.action = $action)
          AND ($actor_id IS NULL OR $actor_id = '' OR a.actorId = $actor_id)
          AND ($target_id IS NULL OR $target_id = '' OR a.targetId = $target_id)
          AND ($severity IS NULL OR $severity = '' OR a.severity = $severity)
          AND (
            CASE
              WHEN $test_id IS NOT NULL AND $test_id <> '' THEN a.test_id = $test_id
              WHEN $include_tests = true THEN true
              ELSE (a.test_id IS NULL OR a.test_id = '')
            END
          )
        RETURN a.id AS id,
               a.action AS action,
               a.statusCode AS statusCode,
               a.statusMessage AS statusMessage,
               a.severity AS severity,
               a.actorId AS actorId,
               a.actor AS actor,
               a.targetId AS targetId,
               a.target AS target,
               a.result AS result,
               a.details AS details,
               a.test_id AS test_id,
               toString(a.CreatedAt) AS created_at,
               toString(a.ExpiresAt) AS expires_at
        ORDER BY a.CreatedAt DESC
        LIMIT $limit
        """
        return await self.client.execute_query(
            query,
            {
                "action": action,
                "actor_id": actor_id,
                "target_id": target_id,
                "severity": severity,
                "include_tests": include_tests,
                "test_id": test_id,
                "limit": limit,
            },
        )

    def search_audit_logs_sync(
        self,
        action: Optional[str] = None,
        actor_id: Optional[str] = None,
        target_id: Optional[str] = None,
        severity: Optional[str] = None,
        include_tests: bool = False,
        test_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Synchronously search and retrieve AuditLog records from Neo4j."""
        query = """
        MATCH (a:AuditLog)
        WHERE ($action IS NULL OR $action = '' OR a.action = $action)
          AND ($actor_id IS NULL OR $actor_id = '' OR a.actorId = $actor_id)
          AND ($target_id IS NULL OR $target_id = '' OR a.targetId = $target_id)
          AND ($severity IS NULL OR $severity = '' OR a.severity = $severity)
          AND (
            CASE
              WHEN $test_id IS NOT NULL AND $test_id <> '' THEN a.test_id = $test_id
              WHEN $include_tests = true THEN true
              ELSE (a.test_id IS NULL OR a.test_id = '')
            END
          )
        RETURN a.id AS id,
               a.action AS action,
               a.statusCode AS statusCode,
               a.statusMessage AS statusMessage,
               a.severity AS severity,
               a.actorId AS actorId,
               a.actor AS actor,
               a.targetId AS targetId,
               a.target AS target,
               a.result AS result,
               a.details AS details,
               a.test_id AS test_id,
               toString(a.CreatedAt) AS created_at,
               toString(a.ExpiresAt) AS expires_at
        ORDER BY a.CreatedAt DESC
        LIMIT $limit
        """
        try:
            driver = self.client.get_sync_driver()
            with driver.session() as session:
                result = session.run(
                    query,
                    action=action,
                    actor_id=actor_id,
                    target_id=target_id,
                    severity=severity,
                    include_tests=include_tests,
                    test_id=test_id,
                    limit=limit,
                )
                return [record.data() for record in result]
        except Exception as e:
            logger.warning("search_audit_logs_sync_failed", error=str(e), exc_info=True)
            return []

    async def search_instances_qido(
        self,
        study_uid: Optional[str] = None,
        series_uid: Optional[str] = None,
        sop_uid: Optional[str] = None,
        include_tests: bool = False,
        test_id: Optional[str] = None,
        accessible_group_ids: Optional[List[str]] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """QIDO-RS: Search for instances."""
        query = """
        MATCH (s:Study)-[:HAS_SERIES]->(se:Series)-[:HAS_INSTANCE]->(i:Instance)
        WHERE (i.Action IS NULL OR i.Action <> 'DELETE')
          AND (i.ExpiresAt IS NULL OR i.ExpiresAt > datetime({timezone: 'UTC'}))
          AND ($study_uid IS NULL OR $study_uid = '' OR $study_uid = '*' OR s.study_instance_uid = $study_uid)
          AND ($series_uid IS NULL OR $series_uid = '' OR $series_uid = '*' OR se.series_instance_uid = $series_uid)
          AND ($sop_uid IS NULL OR $sop_uid = '' OR $sop_uid = '*' OR i.sop_instance_uid = $sop_uid)
          AND (
            ($test_id IS NOT NULL AND $test_id <> '' AND i.test_id = $test_id)
            OR (
              ($test_id IS NULL OR $test_id = '')
              AND ($include_tests = true OR i.test_id IS NULL OR i.test_id = '')
            )
          )
        OPTIONAL MATCH (s)-[:PERFORMED_AT]->(g_s:Group)
          WHERE (g_s.Action IS NULL OR g_s.Action <> 'DELETE')
        WITH s, se, i, g_s
        WHERE ($accessible_group_ids IS NULL OR g_s.id IN $accessible_group_ids)
        RETURN s.study_instance_uid AS study_instance_uid,
               se.series_instance_uid AS series_instance_uid,
               i.sop_instance_uid AS sop_instance_uid,
               i.sop_class_uid AS sop_class_uid,
               i.instance_number AS instance_number,
               i.storage_uri AS storage_uri
        ORDER BY i.instance_number ASC
        LIMIT $limit
        """
        return await self.client.execute_query(
            query,
            {
                "study_uid": study_uid,
                "series_uid": series_uid,
                "sop_uid": sop_uid,
                "accessible_group_ids": accessible_group_ids,
                "include_tests": include_tests,
                "test_id": test_id,
                "limit": limit,
            },
        )

    def search_instances_qido_sync(
        self,
        study_uid: Optional[str] = None,
        series_uid: Optional[str] = None,
        sop_uid: Optional[str] = None,
        include_tests: bool = False,
        test_id: Optional[str] = None,
        accessible_group_ids: Optional[List[str]] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Synchronous QIDO-RS search for instances."""
        query = """
        MATCH (s:Study)-[:HAS_SERIES]->(se:Series)-[:HAS_INSTANCE]->(i:Instance)
        WHERE (i.Action IS NULL OR i.Action <> 'DELETE')
          AND (i.ExpiresAt IS NULL OR i.ExpiresAt > datetime({timezone: 'UTC'}))
          AND ($study_uid IS NULL OR $study_uid = '' OR $study_uid = '*' OR s.study_instance_uid = $study_uid)
          AND ($series_uid IS NULL OR $series_uid = '' OR $series_uid = '*' OR se.series_instance_uid = $series_uid)
          AND ($sop_uid IS NULL OR $sop_uid = '' OR $sop_uid = '*' OR i.sop_instance_uid = $sop_uid)
          AND (
            ($test_id IS NOT NULL AND $test_id <> '' AND i.test_id = $test_id)
            OR (
              ($test_id IS NULL OR $test_id = '')
              AND ($include_tests = true OR i.test_id IS NULL OR i.test_id = '')
            )
          )
        OPTIONAL MATCH (s)-[:PERFORMED_AT]->(g_s:Group)
          WHERE (g_s.Action IS NULL OR g_s.Action <> 'DELETE')
        WITH s, se, i, g_s
        WHERE ($accessible_group_ids IS NULL OR g_s.id IN $accessible_group_ids)
        RETURN s.study_instance_uid AS study_instance_uid,
               se.series_instance_uid AS series_instance_uid,
               i.sop_instance_uid AS sop_instance_uid,
               i.sop_class_uid AS sop_class_uid,
               i.instance_number AS instance_number,
               i.storage_uri AS storage_uri
        ORDER BY i.instance_number ASC
        LIMIT $limit
        """
        driver = self.client.get_sync_driver()
        with driver.session() as session:
            result = session.run(
                query,
                study_uid=study_uid,
                series_uid=series_uid,
                sop_uid=sop_uid,
                accessible_group_ids=accessible_group_ids,
                include_tests=include_tests,
                test_id=test_id,
                limit=limit,
            )
            return [record.data() for record in result]

    async def search_series_qido(
        self,
        study_uid: Optional[str] = None,
        series_uid: Optional[str] = None,
        modality: Optional[str] = None,
        include_tests: bool = False,
        test_id: Optional[str] = None,
        accessible_group_ids: Optional[List[str]] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """QIDO-RS: Search for series with real instance counts via traversal."""
        query = """
        MATCH (s:Study)-[:HAS_SERIES]->(se:Series)
        WHERE (se.Action IS NULL OR se.Action <> 'DELETE')
          AND (se.ExpiresAt IS NULL OR se.ExpiresAt > datetime({timezone: 'UTC'}))
          AND ($study_uid IS NULL OR $study_uid = '' OR $study_uid = '*' OR s.study_instance_uid = $study_uid)
          AND ($series_uid IS NULL OR $series_uid = '' OR $series_uid = '*' OR se.series_instance_uid = $series_uid)
          AND ($modality IS NULL OR $modality = '' OR $modality = '*' OR se.modality = $modality)
          AND (
            ($test_id IS NOT NULL AND $test_id <> '' AND se.test_id = $test_id)
            OR (
              ($test_id IS NULL OR $test_id = '')
              AND ($include_tests = true OR se.test_id IS NULL OR se.test_id = '')
            )
          )
        OPTIONAL MATCH (s)-[:PERFORMED_AT]->(g_s:Group)
          WHERE (g_s.Action IS NULL OR g_s.Action <> 'DELETE')
        WITH s, se, g_s
        WHERE ($accessible_group_ids IS NULL OR g_s.id IN $accessible_group_ids)
        OPTIONAL MATCH (se)-[:HAS_INSTANCE]->(i:Instance)
          WHERE (i.Action IS NULL OR i.Action <> 'DELETE')
            AND (i.ExpiresAt IS NULL OR i.ExpiresAt > datetime({timezone: 'UTC'}))
        RETURN s.study_instance_uid AS study_instance_uid,
               se.series_instance_uid AS series_instance_uid,
               se.series_number AS series_number,
               se.modality AS modality,
               se.series_description AS series_description,
               se.body_part_examined AS body_part_examined,
               count(distinct i) AS num_instances
        ORDER BY se.series_number ASC
        LIMIT $limit
        """
        return await self.client.execute_query(
            query,
            {
                "study_uid": study_uid,
                "series_uid": series_uid,
                "modality": modality,
                "accessible_group_ids": accessible_group_ids,
                "include_tests": include_tests,
                "test_id": test_id,
                "limit": limit,
            },
        )

    def search_series_qido_sync(
        self,
        study_uid: Optional[str] = None,
        series_uid: Optional[str] = None,
        modality: Optional[str] = None,
        include_tests: bool = False,
        test_id: Optional[str] = None,
        accessible_group_ids: Optional[List[str]] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Synchronous QIDO-RS search for series."""
        query = """
        MATCH (s:Study)-[:HAS_SERIES]->(se:Series)
        WHERE (se.Action IS NULL OR se.Action <> 'DELETE')
          AND (se.ExpiresAt IS NULL OR se.ExpiresAt > datetime({timezone: 'UTC'}))
          AND ($study_uid IS NULL OR $study_uid = '' OR $study_uid = '*' OR s.study_instance_uid = $study_uid)
          AND ($series_uid IS NULL OR $series_uid = '' OR $series_uid = '*' OR se.series_instance_uid = $series_uid)
          AND ($modality IS NULL OR $modality = '' OR $modality = '*' OR se.modality = $modality)
          AND (
            ($test_id IS NOT NULL AND $test_id <> '' AND se.test_id = $test_id)
            OR (
              ($test_id IS NULL OR $test_id = '')
              AND ($include_tests = true OR se.test_id IS NULL OR se.test_id = '')
            )
          )
        OPTIONAL MATCH (s)-[:PERFORMED_AT]->(g_s:Group)
          WHERE (g_s.Action IS NULL OR g_s.Action <> 'DELETE')
        WITH s, se, g_s
        WHERE ($accessible_group_ids IS NULL OR g_s.id IN $accessible_group_ids)
        OPTIONAL MATCH (se)-[:HAS_INSTANCE]->(i:Instance)
          WHERE (i.Action IS NULL OR i.Action <> 'DELETE')
            AND (i.ExpiresAt IS NULL OR i.ExpiresAt > datetime({timezone: 'UTC'}))
        RETURN s.study_instance_uid AS study_instance_uid,
               se.series_instance_uid AS series_instance_uid,
               se.series_number AS series_number,
               se.modality AS modality,
               se.series_description AS series_description,
               se.body_part_examined AS body_part_examined,
               count(distinct i) AS num_instances
        ORDER BY se.series_number ASC
        LIMIT $limit
        """
        driver = self.client.get_sync_driver()
        with driver.session() as session:
            result = session.run(
                query,
                study_uid=study_uid,
                series_uid=series_uid,
                modality=modality,
                accessible_group_ids=accessible_group_ids,
                include_tests=include_tests,
                test_id=test_id,
                limit=limit,
            )
            return [record.data() for record in result]

    async def search_studies_qido(
        self,
        patient_id: Optional[str] = None,
        patient_name: Optional[str] = None,
        study_uid: Optional[str] = None,
        study_date: Optional[str] = None,
        accession_number: Optional[str] = None,
        include_tests: bool = False,
        test_id: Optional[str] = None,
        accessible_group_ids: Optional[List[str]] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """QIDO-RS: Search for studies traversing to Series and Instances to compute real counts."""
        query = """
        MATCH (p:Patient)-[:HAS_STUDY]->(s:Study)
        WHERE (s.Action IS NULL OR s.Action <> 'DELETE')
          AND (s.ExpiresAt IS NULL OR s.ExpiresAt > datetime({timezone: 'UTC'}))
          AND ($patient_id IS NULL OR $patient_id = '' OR $patient_id = '*' OR p.patient_id = $patient_id)
          AND ($patient_name IS NULL OR $patient_name = '' OR $patient_name = '*' OR p.patient_name = $patient_name)
          AND ($study_uid IS NULL OR $study_uid = '' OR $study_uid = '*' OR s.study_instance_uid = $study_uid)
          AND ($study_date IS NULL OR $study_date = '' OR $study_date = '*' OR s.study_date = $study_date)
          AND (
            $accession_number IS NULL OR $accession_number = '' OR $accession_number = '*'
            OR s.accession_number = $accession_number
          )
          AND (
            ($test_id IS NOT NULL AND $test_id <> '' AND s.test_id = $test_id)
            OR (($test_id IS NULL OR $test_id = '') AND ($include_tests = true OR s.test_id IS NULL OR s.test_id = ''))
          )
        OPTIONAL MATCH (s)-[:PERFORMED_AT]->(g_s:Group)
          WHERE (g_s.Action IS NULL OR g_s.Action <> 'DELETE')
        OPTIONAL MATCH (p)-[:BELONGS_TO]->(g_p:Group)
          WHERE (g_p.Action IS NULL OR g_p.Action <> 'DELETE')
        WITH p, s, g_s, g_p
        WHERE (
            $accessible_group_ids IS NULL
            OR g_s.id IN $accessible_group_ids
            OR g_p.id IN $accessible_group_ids
          )
        OPTIONAL MATCH (s)-[:HAS_SERIES]->(se:Series)
          WHERE (se.Action IS NULL OR se.Action <> 'DELETE')
            AND (se.ExpiresAt IS NULL OR se.ExpiresAt > datetime({timezone: 'UTC'}))
        OPTIONAL MATCH (se)-[:HAS_INSTANCE]->(i:Instance)
          WHERE (i.Action IS NULL OR i.Action <> 'DELETE')
            AND (i.ExpiresAt IS NULL OR i.ExpiresAt > datetime({timezone: 'UTC'}))
        RETURN p.patient_id AS patient_id,
               p.patient_name AS patient_name,
               p.patient_birth_date AS patient_birth_date,
               p.patient_sex AS patient_sex,
               s.study_instance_uid AS study_instance_uid,
               s.study_date AS study_date,
               s.study_time AS study_time,
               s.study_description AS study_description,
               s.accession_number AS accession_number,
               collect(distinct se.modality) AS modalities,
               count(distinct se) AS num_series,
               count(distinct i) AS num_instances
        ORDER BY s.study_date DESC, s.study_time DESC
        LIMIT $limit
        """
        return await self.client.execute_query(
            query,
            {
                "patient_id": patient_id,
                "patient_name": patient_name,
                "study_uid": study_uid,
                "study_date": study_date,
                "accession_number": accession_number,
                "accessible_group_ids": accessible_group_ids,
                "include_tests": include_tests,
                "test_id": test_id,
                "limit": limit,
            },
        )

    def search_studies_qido_sync(
        self,
        patient_id: Optional[str] = None,
        patient_name: Optional[str] = None,
        study_uid: Optional[str] = None,
        study_date: Optional[str] = None,
        accession_number: Optional[str] = None,
        include_tests: bool = False,
        test_id: Optional[str] = None,
        accessible_group_ids: Optional[List[str]] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Synchronous QIDO-RS search for studies."""
        query = """
        MATCH (p:Patient)-[:HAS_STUDY]->(s:Study)
        WHERE (s.Action IS NULL OR s.Action <> 'DELETE')
          AND (s.ExpiresAt IS NULL OR s.ExpiresAt > datetime({timezone: 'UTC'}))
          AND ($patient_id IS NULL OR $patient_id = '' OR $patient_id = '*' OR p.patient_id = $patient_id)
          AND ($patient_name IS NULL OR $patient_name = '' OR $patient_name = '*' OR p.patient_name = $patient_name)
          AND ($study_uid IS NULL OR $study_uid = '' OR $study_uid = '*' OR s.study_instance_uid = $study_uid)
          AND ($study_date IS NULL OR $study_date = '' OR $study_date = '*' OR s.study_date = $study_date)
          AND (
            $accession_number IS NULL OR $accession_number = '' OR $accession_number = '*'
            OR s.accession_number = $accession_number
          )
          AND (
            ($test_id IS NOT NULL AND $test_id <> '' AND s.test_id = $test_id)
            OR (($test_id IS NULL OR $test_id = '') AND ($include_tests = true OR s.test_id IS NULL OR s.test_id = ''))
          )
        OPTIONAL MATCH (s)-[:PERFORMED_AT]->(g_s:Group)
          WHERE (g_s.Action IS NULL OR g_s.Action <> 'DELETE')
        OPTIONAL MATCH (p)-[:BELONGS_TO]->(g_p:Group)
          WHERE (g_p.Action IS NULL OR g_p.Action <> 'DELETE')
        WITH p, s, g_s, g_p
        WHERE (
            $accessible_group_ids IS NULL
            OR g_s.id IN $accessible_group_ids
            OR g_p.id IN $accessible_group_ids
          )
        OPTIONAL MATCH (s)-[:HAS_SERIES]->(se:Series)
          WHERE (se.Action IS NULL OR se.Action <> 'DELETE')
            AND (se.ExpiresAt IS NULL OR se.ExpiresAt > datetime({timezone: 'UTC'}))
        OPTIONAL MATCH (se)-[:HAS_INSTANCE]->(i:Instance)
          WHERE (i.Action IS NULL OR i.Action <> 'DELETE')
            AND (i.ExpiresAt IS NULL OR i.ExpiresAt > datetime({timezone: 'UTC'}))
        RETURN p.patient_id AS patient_id,
               p.patient_name AS patient_name,
               p.patient_birth_date AS patient_birth_date,
               p.patient_sex AS patient_sex,
               s.study_instance_uid AS study_instance_uid,
               s.study_date AS study_date,
               s.study_time AS study_time,
               s.study_description AS study_description,
               s.accession_number AS accession_number,
               collect(distinct se.modality) AS modalities,
               count(distinct se) AS num_series,
               count(distinct i) AS num_instances
        ORDER BY s.study_date DESC, s.study_time DESC
        LIMIT $limit
        """
        driver = self.client.get_sync_driver()
        with driver.session() as session:
            result = session.run(
                query,
                patient_id=patient_id,
                patient_name=patient_name,
                study_uid=study_uid,
                study_date=study_date,
                accession_number=accession_number,
                accessible_group_ids=accessible_group_ids,
                include_tests=include_tests,
                test_id=test_id,
                limit=limit,
            )
            return [record.data() for record in result]

    async def soft_delete_node(
        self,
        node_id: str,
        acted_by: str = "system",
    ) -> Optional[Dict[str, Any]]:
        """Soft-delete a node by updating Action='DELETE', ActedAt, and ActedBy."""
        query = """
        MATCH (n {id: $node_id})
        SET n.Action = "DELETE",
            n.ActedAt = datetime({timezone: 'UTC'}),
            n.ActedBy = $acted_by
        RETURN properties(n) AS n
        """
        records = await self.client.execute_query(query, {"node_id": node_id, "acted_by": acted_by})
        if records:
            return records[0].get("n", records[0])
        return None

    async def soft_delete_relationship(
        self,
        start_node_id: str,
        end_node_id: str,
        rel_type: str,
        acted_by: str = "system",
    ) -> Optional[Dict[str, Any]]:
        """Atomically swap positive relationship to NOT_<rel_type> under a write transaction, cloning properties."""
        pos_type = to_positive_relation_type(rel_type)
        neg_type = to_negated_relation_type(rel_type)
        new_rel_id = generate_deterministic_id(
            {"start_node_id": start_node_id, "type": neg_type, "end_node_id": end_node_id}
        )

        query = f"""
        MATCH (a {{id: $start_node_id}})-[r:{pos_type}]->(b {{id: $end_node_id}})
        WITH a, b, r, properties(r) AS old_props
        CREATE (a)-[r_not:{neg_type}]->(b)
        SET r_not = old_props,
            r_not.id = $new_rel_id,
            r_not.Action = "DELETE",
            r_not.ActedAt = datetime({{timezone: 'UTC'}}),
            r_not.ActedBy = $acted_by
        DELETE r
        RETURN properties(r_not) AS r_not
        """

        async def _swap_tx(tx: Any) -> Optional[Dict[str, Any]]:
            res = await tx.run(
                query,
                start_node_id=start_node_id,
                end_node_id=end_node_id,
                new_rel_id=new_rel_id,
                acted_by=acted_by,
            )
            record = await res.single()
            if not record:
                return None
            data = record.data()
            return data.get("r_not", data)

        return await self.client.execute_write_tx(_swap_tx)


graph_service = GraphService()
