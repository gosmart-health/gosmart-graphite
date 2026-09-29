import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from db.neo4j import Neo4jClient, neo4j_client
from model.dicom import DicomHierarchy
from model.graph import (
    generate_deterministic_id,
    to_negated_relation_type,
    to_positive_relation_type,
)

logger = logging.getLogger(__name__)

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

MERGE (s)-[r_se:HAS_SERIES {id: $rel_se.id}]->(se)
ON CREATE SET r_se.test_id = $rel_se.test_id,
              r_se.Action = "CREATE",
              r_se.ActedAt = datetime({timezone: 'UTC'}),
              r_se.ActedBy = $rel_se.ActedBy,
              r_se.CreatedAt = datetime({timezone: 'UTC'}),
              r_se.ExpiresAt = CASE WHEN $rel_se.ExpiresAt IS NOT NULL THEN datetime($rel_se.ExpiresAt) ELSE null END

MERGE (se)-[r_ei:HAS_INSTANCE {id: $rel_ei.id}]->(i)
ON CREATE SET r_ei.test_id = $rel_ei.test_id,
              r_ei.Action = "CREATE",
              r_ei.ActedAt = datetime({timezone: 'UTC'}),
              r_ei.ActedBy = $rel_ei.ActedBy,
              r_ei.CreatedAt = datetime({timezone: 'UTC'}),
              r_ei.ExpiresAt = CASE WHEN $rel_ei.ExpiresAt IS NOT NULL THEN datetime($rel_ei.ExpiresAt) ELSE null END

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

    return {
        "patient": _serialize(p_node),
        "study": _serialize(s_node),
        "series": _serialize(se_node),
        "instance": _serialize(i_node),
        "rel_ps": _serialize(r_ps),
        "rel_se": _serialize(r_se),
        "rel_ei": _serialize(r_ei),
    }


class GraphService:
    """Service for managing DICOM graph nodes and relationships in Neo4j."""

    def __init__(self, client: Optional[Neo4jClient] = None) -> None:
        self.client = client or neo4j_client
        self._constraints_initialized: bool = False

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

    async def execute_custom_query(
        self,
        query: str,
        parameters: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Execute an arbitrary read or write Cypher query."""
        return await self.client.execute_query(query, parameters or {})

    async def init_schema_constraints(self, force: bool = False) -> None:
        """Create standard DICOM graph schema constraints & indexes in Neo4j."""
        if self._constraints_initialized and not force:
            return

        constraints = [
            "CREATE CONSTRAINT patient_sys_id_unique IF NOT EXISTS FOR (p:Patient) REQUIRE p.id IS UNIQUE",
            "CREATE CONSTRAINT patient_pid_unique IF NOT EXISTS FOR (p:Patient) REQUIRE p.patient_id IS UNIQUE",
            "CREATE CONSTRAINT study_sys_id_unique IF NOT EXISTS FOR (s:Study) REQUIRE s.id IS UNIQUE",
            "CREATE CONSTRAINT study_uid_unique IF NOT EXISTS FOR (s:Study) REQUIRE s.study_instance_uid IS UNIQUE",
            "CREATE CONSTRAINT series_sys_id_unique IF NOT EXISTS FOR (se:Series) REQUIRE se.id IS UNIQUE",
            (
                "CREATE CONSTRAINT series_uid_unique IF NOT EXISTS "
                "FOR (se:Series) REQUIRE se.series_instance_uid IS UNIQUE"
            ),
            "CREATE CONSTRAINT instance_sys_id_unique IF NOT EXISTS FOR (i:Instance) REQUIRE i.id IS UNIQUE",
            (
                "CREATE CONSTRAINT instance_uid_unique IF NOT EXISTS "
                "FOR (i:Instance) REQUIRE i.sop_instance_uid IS UNIQUE"
            ),
        ]
        if not await self.client.verify_connectivity():
            logger.warning(
                "Skipping schema constraint creation: Neo4j not reachable at %s",
                self.client.settings.DB_CONNECTION_URI,
            )
            return

        for constraint in constraints:
            try:
                await self.client.execute_query(constraint)
                logger.debug("Applied constraint: %s", constraint)
            except Exception as e:
                logger.warning("Failed to apply constraint '%s': %s", constraint, e)

        self._constraints_initialized = True


graph_service = GraphService()
