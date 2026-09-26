import logging
from typing import Any, Dict, List, Optional

from db.neo4j import Neo4jClient, neo4j_client
from model.dicom import DicomHierarchy

logger = logging.getLogger(__name__)

INGEST_CYPHER = """
MERGE (p:Patient {patient_id: $patient.patient_id})
ON CREATE SET p.patient_name = $patient.patient_name,
              p.patient_birth_date = $patient.patient_birth_date,
              p.patient_sex = $patient.patient_sex,
              p.created_at = datetime()
ON MATCH SET  p.patient_name = coalesce($patient.patient_name, p.patient_name),
              p.updated_at = datetime()

MERGE (s:Study {study_instance_uid: $study.study_instance_uid})
ON CREATE SET s.study_date = $study.study_date,
              s.study_time = $study.study_time,
              s.study_description = $study.study_description,
              s.accession_number = $study.accession_number,
              s.created_at = datetime()

MERGE (se:Series {series_instance_uid: $series.series_instance_uid})
ON CREATE SET se.series_number = $series.series_number,
              se.modality = $series.modality,
              se.series_description = $series.series_description,
              se.body_part_examined = $series.body_part_examined,
              se.created_at = datetime()

MERGE (i:Instance {sop_instance_uid: $instance.sop_instance_uid})
ON CREATE SET i.sop_class_uid = $instance.sop_class_uid,
              i.instance_number = $instance.instance_number,
              i.transfer_syntax_uid = $instance.transfer_syntax_uid,
              i.storage_uri = $instance.storage_uri,
              i.file_size_bytes = $instance.file_size_bytes,
              i.created_at = datetime()

MERGE (p)-[:HAS_STUDY]->(s)
MERGE (s)-[:HAS_SERIES]->(se)
MERGE (se)-[:HAS_INSTANCE]->(i)

RETURN p.patient_id AS patient_id,
       s.study_instance_uid AS study_instance_uid,
       se.series_instance_uid AS series_instance_uid,
       i.sop_instance_uid AS sop_instance_uid
"""


class GraphService:
    """Service for managing DICOM graph nodes and relationships in Neo4j."""

    def __init__(self, client: Optional[Neo4jClient] = None) -> None:
        self.client = client or neo4j_client
        self._constraints_initialized: bool = False

    async def save_hierarchy(self, hierarchy: DicomHierarchy) -> Dict[str, Any]:
        """Save a DICOM hierarchy asynchronously into Neo4j."""
        if not self._constraints_initialized:
            await self.init_schema_constraints()

        params = {
            "patient": hierarchy.patient.model_dump(),
            "study": hierarchy.study.model_dump(),
            "series": hierarchy.series.model_dump(),
            "instance": hierarchy.instance.model_dump(),
        }
        records = await self.client.execute_query(INGEST_CYPHER, params)
        if records:
            return records[0]
        return {}

    def save_hierarchy_sync(self, hierarchy: DicomHierarchy) -> Dict[str, Any]:
        """Save a DICOM hierarchy synchronously into Neo4j (used by Pykka actors)."""
        params = {
            "patient": hierarchy.patient.model_dump(),
            "study": hierarchy.study.model_dump(),
            "series": hierarchy.series.model_dump(),
            "instance": hierarchy.instance.model_dump(),
        }
        driver = self.client.get_sync_driver()
        with driver.session(database=self.client.settings.DB_DATABASE) as session:
            result = session.run(INGEST_CYPHER, params)
            record = result.single()
            return record.data() if record else {}

    async def list_patients(self, limit: int = 50) -> List[Dict[str, Any]]:
        """List patients with study counts."""
        query = """
        MATCH (p:Patient)
        OPTIONAL MATCH (p)-[:HAS_STUDY]->(s:Study)
        RETURN p.patient_id AS patient_id,
               p.patient_name AS patient_name,
               p.patient_birth_date AS patient_birth_date,
               p.patient_sex AS patient_sex,
               count(s) AS study_count
        ORDER BY p.patient_id
        LIMIT $limit
        """
        return await self.client.execute_query(query, {"limit": limit})

    async def get_patient_graph(self, patient_id: str) -> Dict[str, Any]:
        """Retrieve full hierarchy for a patient."""
        query = """
        MATCH (p:Patient {patient_id: $patient_id})
        OPTIONAL MATCH (p)-[:HAS_STUDY]->(s:Study)
        OPTIONAL MATCH (s)-[:HAS_SERIES]->(se:Series)
        OPTIONAL MATCH (se)-[:HAS_INSTANCE]->(i:Instance)
        RETURN p, collect(distinct s) AS studies, collect(distinct se) AS series, collect(distinct i) AS instances
        """
        records = await self.client.execute_query(query, {"patient_id": patient_id})
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
            "CREATE CONSTRAINT patient_id_unique IF NOT EXISTS FOR (p:Patient) REQUIRE p.patient_id IS UNIQUE",
            "CREATE CONSTRAINT study_uid_unique IF NOT EXISTS FOR (s:Study) REQUIRE s.study_instance_uid IS UNIQUE",
            (
                "CREATE CONSTRAINT series_uid_unique IF NOT EXISTS "
                "FOR (se:Series) REQUIRE se.series_instance_uid IS UNIQUE"
            ),
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
