"""Semantic vector similarity search and clinical NLU utterance parsing service."""

from __future__ import annotations

import math
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, LiteralString, Optional, Sequence, Type, TypeVar, Union, cast

from gosmart_clinical_nlu import IntentModels, ParsedUtterance, load_intent_models
from gosmart_clinical_nlu import parse_utterance as _nlu_parse_utterance
from neo4j.exceptions import ClientError, CypherSyntaxError
from pydantic import BaseModel

from gosmart_graphite.core.config import PROJECT_ROOT, get_settings
from gosmart_graphite.core.logging import get_logger
from gosmart_graphite.db.neo4j import Neo4jClient, neo4j_client
from gosmart_graphite.model.embedding import EMBEDDING_VECTOR_INDEX_NAME
from gosmart_graphite.model.fhir import EncounterNode, FHIRPatientNode, ImagingStudyNode
from gosmart_graphite.model.graph import PatientNode, StudyNode
from gosmart_graphite.model.search import SearchIncludeLabel, SearchResult
from gosmart_graphite.nlu import register_graphite_intents
from gosmart_graphite.service.embedding_service import DEFAULT_EMBEDDING_TAG, EmbeddingService
from gosmart_graphite.service.group_service import (
    DEFAULT_MY_GROUP_MAX_DEPTH,
    UPWARD_GROUP_RELATION_TYPES,
    _clean_neo4j_value,
    _validate_max_depth,
)

logger = get_logger("gosmart_gs.search_service")

DEFAULT_SEARCH_LIMIT: int = 20
DEFAULT_NLU_MODEL_PATH: str = "./nlu_models/intent_models.zip"
# HNSW stands for Hierarchical Navigable Small World, an algorithm for approximate nearest-neighbor vector search.
_HNSW_OVERSAMPLE_FACTOR: int = 10
_HNSW_MIN_CANDIDATES: int = 100
_HNSW_MAX_CANDIDATES: int = 2000

_TModel = TypeVar("_TModel", bound=BaseModel)

_NLU_MODEL_CACHE_LOCK = threading.Lock()
_NLU_MODEL_CACHE: Dict[tuple[str, int, int], IntentModels] = {}


def _resolve_nlu_model_path(model_path: Union[str, Path]) -> Path:
    """Resolve an NLU model bundle path against CWD first, then project root for relative paths."""
    raw = Path(model_path).expanduser()
    if raw.is_absolute():
        return raw
    cwd_candidate = raw.resolve()
    if cwd_candidate.is_file():
        return cwd_candidate
    project_candidate = (PROJECT_ROOT / raw).resolve()
    if project_candidate.is_file():
        return project_candidate
    return cwd_candidate


def _load_cached_intent_models(resolved_path: Path) -> IntentModels:
    """Load an immutable IntentModels bundle with mtime/size-keyed thread-safe caching."""
    stat = resolved_path.stat()
    cache_key = (str(resolved_path), stat.st_mtime_ns, stat.st_size)
    with _NLU_MODEL_CACHE_LOCK:
        cached = _NLU_MODEL_CACHE.get(cache_key)
        if cached is not None:
            return cached
        register_graphite_intents()
        models = load_intent_models(resolved_path)
        _NLU_MODEL_CACHE[cache_key] = models
        return models


def _escape_cypher_identifier(identifier: str) -> str:
    """Validate and backtick-escape a Cypher identifier (such as a vector index name)."""
    if not isinstance(identifier, str) or not identifier.strip():
        raise ValueError(f"index_name must be a non-empty string, got {identifier!r}")
    clean = identifier.strip()
    if any(ch in clean for ch in ("\x00", "\n", "\r")):
        raise ValueError(f"index_name contains invalid control characters: {identifier!r}")
    escaped = clean.replace("`", "``")
    return f"`{escaped}`"


def _is_unsupported_cypher25_search_error(exc: Exception) -> bool:
    """Return True when a Neo4j server rejects Cypher 25 / native SEARCH syntax (e.g., Neo4j 5.x)."""
    if isinstance(exc, CypherSyntaxError):
        return True
    if isinstance(exc, ClientError):
        code = str(getattr(exc, "code", "") or "")
        msg = str(exc).upper()
        if "SYNTAXERROR" in code.upper():
            return True
        if "INVALID" in code.upper() and ("CYPHER 25" in msg or "SEARCH" in msg or "VERSION" in msg):
            return True
    return False


def _validate_limit(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError(f"limit must be a positive integer, got {limit!r}")
    return limit


def _validate_min_score(min_score: Optional[float]) -> Optional[float]:
    if min_score is None:
        return None
    if isinstance(min_score, bool) or not isinstance(min_score, (int, float)):
        raise ValueError(f"min_score must be a float in [0.0, 1.0], got {min_score!r}")
    score = float(min_score)
    if not math.isfinite(score) or score < 0.0 or score > 1.0:
        raise ValueError(f"min_score must be finite and within [0.0, 1.0], got {min_score!r}")
    return score


def _normalize_include_labels(
    include: Optional[Sequence[Union[SearchIncludeLabel, str]]],
) -> FrozenSet[SearchIncludeLabel]:
    if not include:
        return frozenset()
    normalized: set[SearchIncludeLabel] = set()
    for item in include:
        if isinstance(item, SearchIncludeLabel):
            normalized.add(item)
        elif isinstance(item, str):
            try:
                normalized.add(SearchIncludeLabel(item.strip()))
            except ValueError as exc:
                valid = ", ".join(lbl.value for lbl in SearchIncludeLabel)
                raise ValueError(f"Unsupported include label {item!r}. Expected one of: {valid}") from exc
        else:
            raise ValueError(f"include entries must be SearchIncludeLabel or str, got {item!r}")
    return frozenset(normalized)


def _normalize_string_filter(values: Optional[Sequence[str]]) -> Optional[List[str]]:
    if not values:
        return None
    cleaned = [v.strip() for v in values if isinstance(v, str) and v.strip()]
    return list(dict.fromkeys(cleaned)) if cleaned else None


def _compute_candidate_k(limit: int) -> int:
    return min(max(limit * _HNSW_OVERSAMPLE_FACTOR, _HNSW_MIN_CANDIDATES), _HNSW_MAX_CANDIDATES)


def _build_group_scope_preamble() -> str:
    return """
    OPTIONAL MATCH (root_grp:Group {id: $group_id})
    WHERE $include_deleted
       OR ((root_grp.Action IS NULL OR root_grp.Action <> 'DELETE')
           AND coalesce(root_grp.is_active, true) = true)
    OPTIONAL MATCH sub_path = (sub_grp:Group)-[:BELONGS_TO*1..]->(root_grp)
    WHERE root_grp IS NOT NULL
      AND ALL(
        n IN nodes(sub_path)
        WHERE 'Group' IN labels(n)
          AND ($include_deleted
               OR ((n.Action IS NULL OR n.Action <> 'DELETE')
                   AND coalesce(n.is_active, true) = true))
      )
      AND ALL(
        gr IN relationships(sub_path)
        WHERE NOT type(gr) STARTS WITH 'NOT_'
          AND ($include_deleted OR gr.Action IS NULL OR gr.Action <> 'DELETE')
      )
    WITH root_grp, collect(DISTINCT sub_grp.id) AS sub_ids
    WITH CASE
      WHEN root_grp IS NULL THEN []
      ELSE [root_grp.id] + sub_ids
    END AS allowed_group_ids
    WHERE size(allowed_group_ids) > 0
    """


def _build_nearest_group_filter_clause(max_group_depth: int) -> str:
    """Build a deduplicated level-by-level BFS Cypher clause to find each target's nearest owning :Group."""
    depth = _validate_max_depth(max_group_depth)
    hop_clauses = [
        """
        CALL (found, frontier, visited) {
            UNWIND CASE WHEN size(found) = 0 AND size(frontier) > 0 THEN frontier ELSE [] END AS a
            MATCH (a)-[rel]-(b)
            WHERE type(rel) IN $upward_rel_types
              AND NOT b IN visited
              AND NOT 'System' IN labels(b)
              AND NOT type(rel) STARTS WITH 'NOT_'
              AND ($include_deleted OR b.Action IS NULL OR b.Action <> 'DELETE')
              AND ($include_deleted OR rel.Action IS NULL OR rel.Action <> 'DELETE')
              AND (type(rel) <> 'MEMBER_OF' OR coalesce(rel.status, 'ACTIVE') = 'ACTIVE')
            WITH b,
                 min(
                   CASE
                     WHEN type(rel) = 'MEMBER_OF' AND coalesce(rel.is_primary, false) = true
                     THEN 0
                     ELSE 1
                   END
                 ) AS pri
            WITH collect(
                   CASE
                     WHEN 'Group' IN labels(b)
                          AND ($include_deleted OR coalesce(b.is_active, true) = true)
                     THEN {g: b, pri: pri}
                   END
                 ) AS raw_hits,
                 collect(CASE WHEN NOT 'Group' IN labels(b) THEN b END) AS raw_next
            RETURN [x IN raw_hits WHERE x IS NOT NULL] AS step_found,
                   [x IN raw_next WHERE x IS NOT NULL] AS step_next
        }
        WITH CASE WHEN size(found) > 0 THEN found ELSE step_found END AS found,
             CASE WHEN size(found) > 0 THEN [] ELSE step_next END AS frontier,
             CASE WHEN size(found) > 0 THEN visited ELSE visited + step_next END AS visited
        """
        for _ in range(depth)
    ]
    hops_cypher = "\n".join(hop_clauses)
    return f"""
    CALL (target) {{
        WITH target,
             CASE
               WHEN 'Group' IN labels(target)
                    AND ($include_deleted
                         OR ((target.Action IS NULL OR target.Action <> 'DELETE')
                             AND coalesce(target.is_active, true) = true))
               THEN [{{g: target, pri: 1}}]
               ELSE []
             END AS found,
             CASE
               WHEN 'Group' IN labels(target) OR 'System' IN labels(target)
               THEN []
               ELSE [target]
             END AS frontier,
             [target] AS visited
        {hops_cypher}
        UNWIND found AS hit
        WITH hit.g AS g, hit.pri AS pri
        RETURN g.id AS owning_group_id
        ORDER BY pri ASC, g.code ASC, g.id ASC
        LIMIT 1
    }}
    WITH target, best, allowed_group_ids, owning_group_id
    WHERE owning_group_id IN allowed_group_ids
    """


def _build_context_hydration_clauses(
    include_set: FrozenSet[SearchIncludeLabel],
) -> tuple[str, list[str]]:
    clauses: list[str] = []
    return_fields: list[str] = []

    if SearchIncludeLabel.ENCOUNTER in include_set:
        clauses.append(
            """
            CALL (target) {
                OPTIONAL MATCH (target)-[rel_enc:PERFORMED_IN|HAS_ENCOUNTER]->(enc_rel:Encounter)
                WHERE ($include_deleted OR rel_enc.Action IS NULL OR rel_enc.Action <> 'DELETE')
                  AND ($include_deleted OR enc_rel.Action IS NULL OR enc_rel.Action <> 'DELETE')
                WITH CASE WHEN 'Encounter' IN labels(target) THEN target ELSE enc_rel END AS enc
                ORDER BY enc.id ASC
                RETURN properties(enc) AS encounter_props
                LIMIT 1
            }
            """
        )
        return_fields.append("encounter_props: encounter_props")

    if SearchIncludeLabel.FHIR_PATIENT in include_set:
        clauses.append(
            """
            CALL (target) {
                OPTIONAL MATCH (target)-[rel_fp1:SUBJECT]->(fp1:FHIR_Patient)
                WHERE ($include_deleted OR rel_fp1.Action IS NULL OR rel_fp1.Action <> 'DELETE')
                  AND ($include_deleted OR fp1.Action IS NULL OR fp1.Action <> 'DELETE')
                OPTIONAL MATCH (target)-[rel_enc_fp:PERFORMED_IN|HAS_ENCOUNTER]->(enc_fp:Encounter)
                               -[rel_fp2:SUBJECT]->(fp2:FHIR_Patient)
                WHERE ($include_deleted OR rel_enc_fp.Action IS NULL OR rel_enc_fp.Action <> 'DELETE')
                  AND ($include_deleted OR enc_fp.Action IS NULL OR enc_fp.Action <> 'DELETE')
                  AND ($include_deleted OR rel_fp2.Action IS NULL OR rel_fp2.Action <> 'DELETE')
                  AND ($include_deleted OR fp2.Action IS NULL OR fp2.Action <> 'DELETE')
                WITH coalesce(CASE WHEN 'FHIR_Patient' IN labels(target) THEN target ELSE null END, fp1, fp2) AS fp
                ORDER BY fp.id ASC
                RETURN properties(fp) AS fhir_patient_props
                LIMIT 1
            }
            """
        )
        return_fields.append("fhir_patient_props: fhir_patient_props")

    if SearchIncludeLabel.IMAGING_STUDY in include_set:
        clauses.append(
            """
            CALL (target) {
                OPTIONAL MATCH (target)-[rel_is1:BASED_ON_STUDY]->(is1:ImagingStudy)
                WHERE ($include_deleted OR rel_is1.Action IS NULL OR rel_is1.Action <> 'DELETE')
                  AND ($include_deleted OR is1.Action IS NULL OR is1.Action <> 'DELETE')
                OPTIONAL MATCH (target)-[rel_enc_is:PERFORMED_IN|HAS_ENCOUNTER]->(enc_is:Encounter)
                               <-[rel_is2:PERFORMED_IN]-(is2:ImagingStudy)
                WHERE ($include_deleted OR rel_enc_is.Action IS NULL OR rel_enc_is.Action <> 'DELETE')
                  AND ($include_deleted OR enc_is.Action IS NULL OR enc_is.Action <> 'DELETE')
                  AND ($include_deleted OR rel_is2.Action IS NULL OR rel_is2.Action <> 'DELETE')
                  AND ($include_deleted OR is2.Action IS NULL OR is2.Action <> 'DELETE')
                WITH target, collect(DISTINCT is1) AS is1_list, collect(DISTINCT is2) AS is2_list
                WITH [
                    x IN (CASE WHEN 'ImagingStudy' IN labels(target) THEN [target] ELSE [] END)
                         + is1_list + is2_list
                    WHERE x IS NOT NULL
                ] AS imgs
                UNWIND CASE WHEN size(imgs) = 0 THEN [null] ELSE imgs END AS img
                WITH DISTINCT img
                ORDER BY img.id ASC
                RETURN [x IN collect(properties(img)) WHERE x IS NOT NULL] AS imaging_studies_props
            }
            """
        )
        return_fields.append("imaging_studies_props: imaging_studies_props")

    if SearchIncludeLabel.PATIENT in include_set:
        clauses.append(
            """
            CALL (target) {
                OPTIONAL MATCH (target)-[rel_p1:SUBJECT]->(fp_p1:FHIR_Patient)<-[rel_sa1:SAME_AS]-(p1:Patient)
                WHERE ($include_deleted OR rel_p1.Action IS NULL OR rel_p1.Action <> 'DELETE')
                  AND ($include_deleted OR fp_p1.Action IS NULL OR fp_p1.Action <> 'DELETE')
                  AND ($include_deleted OR rel_sa1.Action IS NULL OR rel_sa1.Action <> 'DELETE')
                  AND ($include_deleted OR p1.Action IS NULL OR p1.Action <> 'DELETE')
                OPTIONAL MATCH (target)-[rel_pe:PERFORMED_IN|HAS_ENCOUNTER]->(enc_p:Encounter)
                               -[rel_p2:SUBJECT]->(fp_p2:FHIR_Patient)<-[rel_sa2:SAME_AS]-(p2:Patient)
                WHERE ($include_deleted OR rel_pe.Action IS NULL OR rel_pe.Action <> 'DELETE')
                  AND ($include_deleted OR enc_p.Action IS NULL OR enc_p.Action <> 'DELETE')
                  AND ($include_deleted OR rel_p2.Action IS NULL OR rel_p2.Action <> 'DELETE')
                  AND ($include_deleted OR fp_p2.Action IS NULL OR fp_p2.Action <> 'DELETE')
                  AND ($include_deleted OR rel_sa2.Action IS NULL OR rel_sa2.Action <> 'DELETE')
                  AND ($include_deleted OR p2.Action IS NULL OR p2.Action <> 'DELETE')
                OPTIONAL MATCH (p3:Patient)-[rel_hs3:HAS_STUDY]->(target)
                WHERE 'Study' IN labels(target)
                  AND ($include_deleted OR rel_hs3.Action IS NULL OR rel_hs3.Action <> 'DELETE')
                  AND ($include_deleted OR p3.Action IS NULL OR p3.Action <> 'DELETE')
                OPTIONAL MATCH (target)-[rel_bs_p:BASED_ON_STUDY]->(is_p:ImagingStudy)
                               -[rel_rs_p:REFERENCES_STUDY]->(st_p:Study)<-[rel_hs4:HAS_STUDY]-(p4:Patient)
                WHERE ($include_deleted OR rel_bs_p.Action IS NULL OR rel_bs_p.Action <> 'DELETE')
                  AND ($include_deleted OR is_p.Action IS NULL OR is_p.Action <> 'DELETE')
                  AND ($include_deleted OR rel_rs_p.Action IS NULL OR rel_rs_p.Action <> 'DELETE')
                  AND ($include_deleted OR st_p.Action IS NULL OR st_p.Action <> 'DELETE')
                  AND ($include_deleted OR rel_hs4.Action IS NULL OR rel_hs4.Action <> 'DELETE')
                  AND ($include_deleted OR p4.Action IS NULL OR p4.Action <> 'DELETE')
                WITH coalesce(
                  CASE WHEN 'Patient' IN labels(target) THEN target ELSE null END,
                  p1, p2, p3, p4
                ) AS pat
                ORDER BY pat.id ASC
                RETURN properties(pat) AS patient_props
                LIMIT 1
            }
            """
        )
        return_fields.append("patient_props: patient_props")

    if SearchIncludeLabel.STUDY in include_set:
        clauses.append(
            """
            CALL (target) {
                OPTIONAL MATCH (target)-[rel_bs1:BASED_ON_STUDY]->(is_s1:ImagingStudy)
                               -[rel_rs1:REFERENCES_STUDY]->(st1:Study)
                WHERE ($include_deleted OR rel_bs1.Action IS NULL OR rel_bs1.Action <> 'DELETE')
                  AND ($include_deleted OR is_s1.Action IS NULL OR is_s1.Action <> 'DELETE')
                  AND ($include_deleted OR rel_rs1.Action IS NULL OR rel_rs1.Action <> 'DELETE')
                  AND ($include_deleted OR st1.Action IS NULL OR st1.Action <> 'DELETE')
                OPTIONAL MATCH (target)-[rel_enc_s:PERFORMED_IN|HAS_ENCOUNTER]->(enc_s:Encounter)
                               <-[rel_pi_s:PERFORMED_IN]-(is_s2:ImagingStudy)
                               -[rel_rs2:REFERENCES_STUDY]->(st2:Study)
                WHERE ($include_deleted OR rel_enc_s.Action IS NULL OR rel_enc_s.Action <> 'DELETE')
                  AND ($include_deleted OR enc_s.Action IS NULL OR enc_s.Action <> 'DELETE')
                  AND ($include_deleted OR rel_pi_s.Action IS NULL OR rel_pi_s.Action <> 'DELETE')
                  AND ($include_deleted OR is_s2.Action IS NULL OR is_s2.Action <> 'DELETE')
                  AND ($include_deleted OR rel_rs2.Action IS NULL OR rel_rs2.Action <> 'DELETE')
                  AND ($include_deleted OR st2.Action IS NULL OR st2.Action <> 'DELETE')
                OPTIONAL MATCH (target)-[rel_bo_s:BASED_ON]->(sr_s:ServiceRequest)-[rel_fb_s:FULFILLED_BY]->(st3:Study)
                WHERE ($include_deleted OR rel_bo_s.Action IS NULL OR rel_bo_s.Action <> 'DELETE')
                  AND ($include_deleted OR sr_s.Action IS NULL OR sr_s.Action <> 'DELETE')
                  AND ($include_deleted OR rel_fb_s.Action IS NULL OR rel_fb_s.Action <> 'DELETE')
                  AND ($include_deleted OR st3.Action IS NULL OR st3.Action <> 'DELETE')
                WITH target,
                     collect(DISTINCT st1) AS st1_list,
                     collect(DISTINCT st2) AS st2_list,
                     collect(DISTINCT st3) AS st3_list
                WITH [
                    x IN (CASE WHEN 'Study' IN labels(target) THEN [target] ELSE [] END)
                         + st1_list + st2_list + st3_list
                    WHERE x IS NOT NULL
                ] AS sts
                UNWIND CASE WHEN size(sts) = 0 THEN [null] ELSE sts END AS st
                WITH DISTINCT st
                ORDER BY st.id ASC
                RETURN [x IN collect(properties(st)) WHERE x IS NOT NULL] AS studies_props
            }
            """
        )
        return_fields.append("studies_props: studies_props")

    return "\n".join(clauses), return_fields


def _build_return_map(extra_fields: list[str]) -> str:
    base_fields = [
        "score: best.score",
        "node_label: head(labels(target))",
        "node_id: target.id",
        "text: best.text",
        "target_field_tag: best.target_field_tag",
        "model_tag: best.model_tag",
        "embedding_id: best.embedding_id",
        "is_deleted: best.is_deleted",
    ]
    return "{\n" + ",\n".join(f"        {f}" for f in base_fields + extra_fields) + "\n    }"


def _build_hnsw_search_cypher(
    *,
    index_name: str = EMBEDDING_VECTOR_INDEX_NAME,
    has_group_filter: bool,
    has_test_id: bool,
    include_set: FrozenSet[SearchIncludeLabel],
    max_group_depth: int,
    use_cypher25_search: bool = True,
) -> LiteralString:
    group_preamble = _build_group_scope_preamble() if has_group_filter else ""
    group_carry = "allowed_group_ids, " if has_group_filter else ""
    call_scope = "allowed_group_ids, raw_hits" if has_group_filter else "raw_hits"
    test_id_clause = "e.test_id = $test_id" if has_test_id else "e.test_id IS NULL"
    group_filter_clause = _build_nearest_group_filter_clause(max_group_depth) if has_group_filter else ""
    context_clauses, extra_fields = _build_context_hydration_clauses(include_set)
    return_map = _build_return_map(extra_fields)

    if use_cypher25_search:
        escaped_index = _escape_cypher_identifier(index_name)
        cypher_header = "CYPHER 25"
        vector_lookup_clause = f"""
    MATCH (e:Embedding)
    SEARCH e IN (
        VECTOR INDEX {escaped_index}
        FOR $query_vector
        LIMIT $candidate_k
    ) SCORE AS score"""
    else:
        cypher_header = ""
        vector_lookup_clause = """
    CALL db.index.vector.queryNodes($index_name, $candidate_k, $query_vector)
    YIELD node AS e, score"""

    query = f"""{cypher_header}
    {group_preamble}
    {vector_lookup_clause}
    WITH {group_carry}collect({{e: e, score: score}}) AS raw_hits
    WITH {group_carry}size(raw_hits) AS raw_hit_count, raw_hits
    CALL ({call_scope}) {{
        UNWIND raw_hits AS hit
        WITH {group_carry}hit.e AS e, hit.score AS score
        MATCH (e)-[r:EMBEDS]->(target)
        WHERE e.modelTag = $tag
          AND {test_id_clause}
          AND ($min_score IS NULL OR score >= $min_score)
          AND ($target_field_tags IS NULL OR e.targetFieldTag IN $target_field_tags)
          AND ($target_labels IS NULL OR any(lbl IN labels(target) WHERE lbl IN $target_labels))
          AND ($include_deleted
               OR ((e.Action IS NULL OR e.Action <> 'DELETE')
                   AND (r.Action IS NULL OR r.Action <> 'DELETE')
                   AND (target.Action IS NULL OR target.Action <> 'DELETE')))
        WITH {group_carry}target, e, r, score,
             (coalesce(e.Action, '') = 'DELETE'
              OR coalesce(r.Action, '') = 'DELETE'
              OR coalesce(target.Action, '') = 'DELETE') AS is_deleted
        ORDER BY score DESC, e.targetFieldTag ASC, e.id ASC
        WITH {group_carry}target,
             collect({{
                 score: score,
                 text: coalesce(e.text, ''),
                 target_field_tag: e.targetFieldTag,
                 model_tag: e.modelTag,
                 embedding_id: e.id,
                 is_deleted: is_deleted
             }})[0] AS best
        {group_filter_clause}
        ORDER BY best.score DESC, target.id ASC
        LIMIT $limit
        {context_clauses}
        RETURN collect({return_map}) AS results
    }}
    RETURN raw_hit_count, results
    """
    return cast(LiteralString, query)


def _build_exact_fallback_cypher(
    *,
    has_group_filter: bool,
    has_test_id: bool,
    include_set: FrozenSet[SearchIncludeLabel],
    max_group_depth: int,
) -> LiteralString:
    group_preamble = _build_group_scope_preamble() if has_group_filter else ""
    group_carry = "allowed_group_ids, " if has_group_filter else ""
    test_id_clause = "e.test_id = $test_id" if has_test_id else "e.test_id IS NULL"
    group_filter_clause = _build_nearest_group_filter_clause(max_group_depth) if has_group_filter else ""
    context_clauses, extra_fields = _build_context_hydration_clauses(include_set)
    return_map = _build_return_map(extra_fields)

    query = f"""
    {group_preamble}
    MATCH (e:Embedding)-[r:EMBEDS]->(target)
    WHERE e.modelTag = $tag
      AND {test_id_clause}
      AND e.vector IS NOT NULL
      AND size(e.vector) = size($query_vector)
      AND ($target_field_tags IS NULL OR e.targetFieldTag IN $target_field_tags)
      AND ($target_labels IS NULL OR any(lbl IN labels(target) WHERE lbl IN $target_labels))
      AND ($include_deleted
           OR ((e.Action IS NULL OR e.Action <> 'DELETE')
               AND (r.Action IS NULL OR r.Action <> 'DELETE')
               AND (target.Action IS NULL OR target.Action <> 'DELETE')))
    WITH {group_carry}e, r, target,
         vector.similarity.cosine(e.vector, $query_vector) AS score
    WHERE score IS NOT NULL
      AND ($min_score IS NULL OR score >= $min_score)
    WITH {group_carry}target, e, r, score,
         (coalesce(e.Action, '') = 'DELETE'
          OR coalesce(r.Action, '') = 'DELETE'
          OR coalesce(target.Action, '') = 'DELETE') AS is_deleted
    ORDER BY score DESC, e.targetFieldTag ASC, e.id ASC
    WITH {group_carry}target,
         collect({{
             score: score,
             text: coalesce(e.text, ''),
             target_field_tag: e.targetFieldTag,
             model_tag: e.modelTag,
             embedding_id: e.id,
             is_deleted: is_deleted
         }})[0] AS best
    {group_filter_clause}
    ORDER BY best.score DESC, target.id ASC
    LIMIT $limit
    {context_clauses}
    RETURN collect({return_map}) AS results
    """
    return cast(LiteralString, query)


def _hydrate_node(model_cls: Type[_TModel], raw: Any) -> Optional[_TModel]:
    if not raw or not isinstance(raw, dict):
        return None
    cleaned = _clean_neo4j_value(raw)
    if not isinstance(cleaned, dict):
        return None
    return model_cls.model_validate(cleaned)


def _hydrate_node_list(model_cls: Type[_TModel], raw_list: Any) -> List[_TModel]:
    if not raw_list or not isinstance(raw_list, list):
        return []
    hydrated: List[_TModel] = []
    for item in raw_list:
        node = _hydrate_node(model_cls, item)
        if node is not None:
            hydrated.append(node)
    return hydrated


def _hydrate_results(
    raw_items: Sequence[Any],
    include_set: FrozenSet[SearchIncludeLabel],
) -> List[SearchResult]:
    results: List[SearchResult] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        clean = _clean_neo4j_value(item)
        encounter = (
            _hydrate_node(EncounterNode, clean.get("encounter_props"))
            if SearchIncludeLabel.ENCOUNTER in include_set
            else None
        )
        fhir_patient = (
            _hydrate_node(FHIRPatientNode, clean.get("fhir_patient_props"))
            if SearchIncludeLabel.FHIR_PATIENT in include_set
            else None
        )
        imaging_studies = (
            _hydrate_node_list(ImagingStudyNode, clean.get("imaging_studies_props"))
            if SearchIncludeLabel.IMAGING_STUDY in include_set
            else None
        )
        patient = (
            _hydrate_node(PatientNode, clean.get("patient_props"))
            if SearchIncludeLabel.PATIENT in include_set
            else None
        )
        studies = (
            _hydrate_node_list(StudyNode, clean.get("studies_props"))
            if SearchIncludeLabel.STUDY in include_set
            else None
        )
        results.append(
            SearchResult(
                score=float(clean["score"]),
                node_label=str(clean.get("node_label") or ""),
                node_id=str(clean["node_id"]),
                text=str(clean.get("text") or ""),
                target_field_tag=clean.get("target_field_tag"),
                model_tag=clean.get("model_tag"),
                embedding_id=clean.get("embedding_id"),
                is_deleted=bool(clean.get("is_deleted", False)),
                encounter=encounter,
                fhir_patient=fhir_patient,
                imaging_studies=imaging_studies,
                patient=patient,
                studies=studies,
            )
        )
    return results


class SearchService:
    """Performs semantic vector similarity search and clinical NLU utterance parsing."""

    def __init__(
        self,
        embedding_service: Optional[EmbeddingService] = None,
        client: Optional[Neo4jClient] = None,
        index_name: str = EMBEDDING_VECTOR_INDEX_NAME,
        *,
        nlu_model_path: Optional[Union[str, Path]] = None,
        intent_models: Optional[IntentModels] = None,
        require_nlu_model: bool = False,
    ) -> None:
        _escape_cypher_identifier(index_name)
        self.client = client or neo4j_client
        self.embedding_service = embedding_service or EmbeddingService()
        self.index_name = index_name.strip()
        self._use_cypher25_search: bool = True
        self.nlu_model_path: Optional[Path] = None
        self.intent_models: Optional[IntentModels] = self._init_nlu_models(
            nlu_model_path=nlu_model_path,
            intent_models=intent_models,
            require_nlu_model=require_nlu_model,
        )

    def _init_nlu_models(
        self,
        *,
        nlu_model_path: Optional[Union[str, Path]],
        intent_models: Optional[IntentModels],
        require_nlu_model: bool,
    ) -> Optional[IntentModels]:
        if intent_models is not None:
            return intent_models

        explicit_path = nlu_model_path is not None
        configured_path: Union[str, Path] = (
            nlu_model_path if nlu_model_path is not None else get_settings().NLU_MODEL_PATH
        )
        resolved = _resolve_nlu_model_path(configured_path)
        self.nlu_model_path = resolved

        if not resolved.is_file():
            if explicit_path or require_nlu_model:
                raise FileNotFoundError(
                    f"Clinical NLU model bundle not found at '{resolved}'. "
                    "Train the bundle with: uv run gosmart-clinical-nlu train "
                    "--training-dir training --out nlu_models/intent_models.zip"
                )
            logger.warning(
                "nlu_model_bundle_missing",
                nlu_model_path=str(resolved),
                hint="Run 'uv run gosmart-clinical-nlu train --training-dir training "
                "--out nlu_models/intent_models.zip'",
            )
            return None

        models = _load_cached_intent_models(resolved)
        logger.info(
            "nlu_models_loaded",
            nlu_model_path=str(resolved),
            intents=list(models.names),
            spacy_version=models.spacy_version,
        )
        return models

    @property
    def nlu_ready(self) -> bool:
        """Return True when the clinical NLU intent models are loaded and ready to parse utterances."""
        return self.intent_models is not None

    def parse_utterance(
        self,
        utterance: str,
        *,
        ref_date: Optional[datetime] = None,
    ) -> ParsedUtterance:
        """Parse a clinical query utterance into its matched intent, confidence scores, and slots.

        Proxies :func:`gosmart_clinical_nlu.parse_utterance` against the preloaded
        :class:`IntentModels` bundle initialized during :meth:`__init__`.
        """
        if not isinstance(utterance, str):
            raise ValueError(f"utterance must be a string, got {type(utterance).__name__}")
        if self.intent_models is None:
            raise RuntimeError(
                f"Clinical NLU intent models are not loaded (expected bundle at '{self.nlu_model_path}'). "
                "Train the bundle with: uv run gosmart-clinical-nlu train "
                "--training-dir training --out nlu_models/intent_models.zip"
            )
        return _nlu_parse_utterance(utterance.strip(), self.intent_models, ref_date=ref_date)

    @staticmethod
    def _prepare_search_context(
        query: str,
        group_id: Optional[str],
        *,
        limit: int,
        min_score: Optional[float],
        tag: str,
        include: Optional[Sequence[Union[SearchIncludeLabel, str]]],
        target_labels: Optional[Sequence[str]],
        target_field_tags: Optional[Sequence[str]],
        include_deleted: bool,
        test_id: Optional[str],
        max_group_depth: int,
    ) -> Optional[Dict[str, Any]]:
        valid_limit = _validate_limit(limit)
        valid_min_score = _validate_min_score(min_score)
        valid_depth = _validate_max_depth(max_group_depth)
        include_set = _normalize_include_labels(include)

        clean_query = query.strip() if isinstance(query, str) else ""
        if not clean_query:
            return None

        clean_group_id = group_id.strip() if isinstance(group_id, str) and group_id.strip() else None
        clean_test_id = test_id.strip() if isinstance(test_id, str) and test_id.strip() else None
        clean_tag = tag.strip() if isinstance(tag, str) and tag.strip() else DEFAULT_EMBEDDING_TAG
        norm_target_labels = _normalize_string_filter(target_labels)
        norm_field_tags = _normalize_string_filter(target_field_tags)

        return {
            "query": clean_query,
            "group_id": clean_group_id,
            "limit": valid_limit,
            "candidate_k": _compute_candidate_k(valid_limit),
            "min_score": valid_min_score,
            "tag": clean_tag,
            "include_set": include_set,
            "target_labels": norm_target_labels,
            "target_field_tags": norm_field_tags,
            "include_deleted": bool(include_deleted),
            "test_id": clean_test_id,
            "max_group_depth": valid_depth,
            "has_filters": bool(
                clean_group_id or clean_test_id or norm_target_labels or norm_field_tags or not include_deleted
            ),
        }

    def _build_query_params(self, ctx: Dict[str, Any], query_vector: List[float]) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "index_name": self.index_name,
            "candidate_k": ctx["candidate_k"],
            "query_vector": query_vector,
            "limit": ctx["limit"],
            "min_score": ctx["min_score"],
            "tag": ctx["tag"],
            "target_labels": ctx["target_labels"],
            "target_field_tags": ctx["target_field_tags"],
            "include_deleted": ctx["include_deleted"],
        }
        if ctx["group_id"] is not None:
            params["group_id"] = ctx["group_id"]
            params["upward_rel_types"] = list(UPWARD_GROUP_RELATION_TYPES)
        if ctx["test_id"] is not None:
            params["test_id"] = ctx["test_id"]
        return params

    def _execute_query_sync(self, cypher: LiteralString, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        driver = self.client.get_sync_driver()
        with driver.session(database=self.client.settings.DB_DATABASE) as session:
            result = session.run(cypher, params)
            return [record.data() for record in result]

    async def _run_hnsw_query_async(
        self,
        *,
        has_group: bool,
        has_test_id: bool,
        include_set: FrozenSet[SearchIncludeLabel],
        max_group_depth: int,
        params: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        use_c25 = self._use_cypher25_search
        hnsw_cypher = _build_hnsw_search_cypher(
            index_name=self.index_name,
            has_group_filter=has_group,
            has_test_id=has_test_id,
            include_set=include_set,
            max_group_depth=max_group_depth,
            use_cypher25_search=use_c25,
        )
        try:
            return await self.client.execute_query(hnsw_cypher, params)
        except Exception as exc:
            if not use_c25 or not _is_unsupported_cypher25_search_error(exc):
                raise
            self._use_cypher25_search = False
            logger.info(
                "search_cypher25_unsupported_falling_back_to_query_nodes",
                index_name=self.index_name,
                error=str(exc),
            )
            legacy_cypher = _build_hnsw_search_cypher(
                index_name=self.index_name,
                has_group_filter=has_group,
                has_test_id=has_test_id,
                include_set=include_set,
                max_group_depth=max_group_depth,
                use_cypher25_search=False,
            )
            return await self.client.execute_query(legacy_cypher, params)

    def _run_hnsw_query_sync(
        self,
        *,
        has_group: bool,
        has_test_id: bool,
        include_set: FrozenSet[SearchIncludeLabel],
        max_group_depth: int,
        params: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        use_c25 = self._use_cypher25_search
        hnsw_cypher = _build_hnsw_search_cypher(
            index_name=self.index_name,
            has_group_filter=has_group,
            has_test_id=has_test_id,
            include_set=include_set,
            max_group_depth=max_group_depth,
            use_cypher25_search=use_c25,
        )
        try:
            return self._execute_query_sync(hnsw_cypher, params)
        except Exception as exc:
            if not use_c25 or not _is_unsupported_cypher25_search_error(exc):
                raise
            self._use_cypher25_search = False
            logger.info(
                "search_cypher25_unsupported_falling_back_to_query_nodes",
                index_name=self.index_name,
                error=str(exc),
            )
            legacy_cypher = _build_hnsw_search_cypher(
                index_name=self.index_name,
                has_group_filter=has_group,
                has_test_id=has_test_id,
                include_set=include_set,
                max_group_depth=max_group_depth,
                use_cypher25_search=False,
            )
            return self._execute_query_sync(legacy_cypher, params)

    async def search_async(
        self,
        query: str,
        group_id: Optional[str] = None,
        *,
        limit: int = DEFAULT_SEARCH_LIMIT,
        min_score: Optional[float] = None,
        tag: str = DEFAULT_EMBEDDING_TAG,
        include: Optional[Sequence[Union[SearchIncludeLabel, str]]] = None,
        target_labels: Optional[Sequence[str]] = None,
        target_field_tags: Optional[Sequence[str]] = None,
        include_deleted: bool = False,
        test_id: Optional[str] = None,
        max_group_depth: int = DEFAULT_MY_GROUP_MAX_DEPTH,
    ) -> List[SearchResult]:
        """Asynchronously perform semantic vector search over Neo4j :Embedding nodes."""
        ctx = self._prepare_search_context(
            query,
            group_id,
            limit=limit,
            min_score=min_score,
            tag=tag,
            include=include,
            target_labels=target_labels,
            target_field_tags=target_field_tags,
            include_deleted=include_deleted,
            test_id=test_id,
            max_group_depth=max_group_depth,
        )
        if ctx is None:
            return []

        query_vector = await self.embedding_service.embed_text_async(
            ctx["query"],
            is_query=True,
            group_id=ctx["group_id"],
            tag=ctx["tag"],
            test_id=ctx["test_id"],
        )

        params = self._build_query_params(ctx, query_vector)
        has_group = ctx["group_id"] is not None
        has_test_id = ctx["test_id"] is not None

        rows = await self._run_hnsw_query_async(
            has_group=has_group,
            has_test_id=has_test_id,
            include_set=ctx["include_set"],
            max_group_depth=ctx["max_group_depth"],
            params=params,
        )
        if not rows:
            return []

        raw_hit_count = int(rows[0].get("raw_hit_count") or 0)
        raw_results = rows[0].get("results") or []

        if ctx["has_filters"] and len(raw_results) < ctx["limit"] and raw_hit_count >= ctx["candidate_k"]:
            logger.debug(
                "search_hnsw_saturated_running_exact_fallback",
                group_id=ctx["group_id"],
                tag=ctx["tag"],
                raw_hit_count=raw_hit_count,
                candidate_k=ctx["candidate_k"],
                survived=len(raw_results),
                limit=ctx["limit"],
            )
            fallback_cypher = _build_exact_fallback_cypher(
                has_group_filter=has_group,
                has_test_id=has_test_id,
                include_set=ctx["include_set"],
                max_group_depth=ctx["max_group_depth"],
            )
            fallback_rows = await self.client.execute_query(fallback_cypher, params)
            if fallback_rows:
                raw_results = fallback_rows[0].get("results") or []

        return _hydrate_results(raw_results, ctx["include_set"])

    def search(
        self,
        query: str,
        group_id: Optional[str] = None,
        *,
        limit: int = DEFAULT_SEARCH_LIMIT,
        min_score: Optional[float] = None,
        tag: str = DEFAULT_EMBEDDING_TAG,
        include: Optional[Sequence[Union[SearchIncludeLabel, str]]] = None,
        target_labels: Optional[Sequence[str]] = None,
        target_field_tags: Optional[Sequence[str]] = None,
        include_deleted: bool = False,
        test_id: Optional[str] = None,
        max_group_depth: int = DEFAULT_MY_GROUP_MAX_DEPTH,
    ) -> List[SearchResult]:
        """Synchronously perform semantic vector search over Neo4j :Embedding nodes."""
        ctx = self._prepare_search_context(
            query,
            group_id,
            limit=limit,
            min_score=min_score,
            tag=tag,
            include=include,
            target_labels=target_labels,
            target_field_tags=target_field_tags,
            include_deleted=include_deleted,
            test_id=test_id,
            max_group_depth=max_group_depth,
        )
        if ctx is None:
            return []

        query_vector = self.embedding_service.embed_text(
            ctx["query"],
            is_query=True,
            group_id=ctx["group_id"],
            tag=ctx["tag"],
            test_id=ctx["test_id"],
        )

        params = self._build_query_params(ctx, query_vector)
        has_group = ctx["group_id"] is not None
        has_test_id = ctx["test_id"] is not None

        rows = self._run_hnsw_query_sync(
            has_group=has_group,
            has_test_id=has_test_id,
            include_set=ctx["include_set"],
            max_group_depth=ctx["max_group_depth"],
            params=params,
        )
        if not rows:
            return []

        raw_hit_count = int(rows[0].get("raw_hit_count") or 0)
        raw_results = rows[0].get("results") or []

        if ctx["has_filters"] and len(raw_results) < ctx["limit"] and raw_hit_count >= ctx["candidate_k"]:
            logger.debug(
                "search_hnsw_saturated_running_exact_fallback",
                group_id=ctx["group_id"],
                tag=ctx["tag"],
                raw_hit_count=raw_hit_count,
                candidate_k=ctx["candidate_k"],
                survived=len(raw_results),
                limit=ctx["limit"],
            )
            fallback_cypher = _build_exact_fallback_cypher(
                has_group_filter=has_group,
                has_test_id=has_test_id,
                include_set=ctx["include_set"],
                max_group_depth=ctx["max_group_depth"],
            )
            fallback_rows = self._execute_query_sync(fallback_cypher, params)
            if fallback_rows:
                raw_results = fallback_rows[0].get("results") or []

        return _hydrate_results(raw_results, ctx["include_set"])


search_service = SearchService()

__all__ = [
    "DEFAULT_NLU_MODEL_PATH",
    "DEFAULT_SEARCH_LIMIT",
    "IntentModels",
    "ParsedUtterance",
    "SearchService",
    "search_service",
]
