import re
from typing import List, Tuple, Type

from gosmart_graphite.model.auditable import Auditable
from gosmart_graphite.model.fhir.nodes import FHIR_NODE_CLASSES, FHIRDomainResourceNode

# Base Auditable fields inherited from model.auditable.Auditable
_AUDITABLE_INTERNAL_FIELDS: frozenset[str] = frozenset(Auditable.model_fields.keys()) - {"test_id"}


def _to_snake_case(name: str) -> str:
    """Convert CamelCase or mixed label/property names to snake_case for index identifiers."""
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    s2 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1)
    return re.sub(r"_+", "_", s2).lower().strip("_")


def get_node_indexed_properties(node_cls: Type[FHIRDomainResourceNode]) -> List[str]:
    """Return all Neo4j property names to index for a given FHIR node class.

    Note: ``id`` and ``uid`` are covered by uniqueness constraints (which create
    backing unique indexes in Neo4j), and large blob fields listed in
    ``__non_indexed_fields__`` (``resource``, ``textDiv``, ``presentedFormData``)
    are excluded from range indexing.
    """
    excluded = set(node_cls.__non_indexed_fields__) | {"id", "uid"} | set(_AUDITABLE_INTERNAL_FIELDS)
    props: List[str] = []
    for field_name, field_info in node_cls.model_fields.items():
        if field_name in excluded:
            continue
        prop_name = field_info.serialization_alias or field_name
        if prop_name in excluded:
            continue
        props.append(prop_name)
    return props


def build_fhir_schema_constraints() -> List[str]:
    """Build Neo4j uniqueness constraints for all FHIR resource node labels."""
    statements: List[str] = []
    for node_cls in FHIR_NODE_CLASSES:
        label = node_cls.__label__
        label_snake = _to_snake_case(label)
        statements.append(
            f"CREATE CONSTRAINT {label_snake}_sys_id_unique IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE"
        )
        statements.append(
            f"CREATE CONSTRAINT {label_snake}_uid_unique IF NOT EXISTS FOR (n:{label}) REQUIRE n.uid IS UNIQUE"
        )
    return statements


def build_fhir_schema_indexes() -> List[str]:
    """Build Neo4j property indexes for all searchable properties across all FHIR node classes."""
    statements: List[str] = [
        # Ensure DICOM Study.accession_number is indexed for fast DICOM-to-FHIR correlation
        "CREATE INDEX study_accession_number_idx IF NOT EXISTS FOR (s:Study) ON (s.accession_number)",
    ]
    for node_cls in FHIR_NODE_CLASSES:
        label = node_cls.__label__
        label_snake = _to_snake_case(label)
        for prop_name in get_node_indexed_properties(node_cls):
            prop_snake = _to_snake_case(prop_name)
            idx_name = f"fhir_{label_snake}_{prop_snake}_idx"
            # Escape Cypher reserved keywords like `class` if needed, though property access n.`prop` is always safe
            prop_expr = f"n.`{prop_name}`" if prop_name == "class" else f"n.{prop_name}"
            statements.append(f"CREATE INDEX {idx_name} IF NOT EXISTS FOR (n:{label}) ON ({prop_expr})")
    return statements


FHIR_SCHEMA_CONSTRAINTS: Tuple[str, ...] = tuple(build_fhir_schema_constraints())
FHIR_SCHEMA_INDEXES: Tuple[str, ...] = tuple(build_fhir_schema_indexes())
FHIR_ALL_SCHEMA_DDL: Tuple[str, ...] = FHIR_SCHEMA_CONSTRAINTS + FHIR_SCHEMA_INDEXES


def generate_fhir_cypher_script() -> str:
    """Render a standalone, idempotent Cypher schema script (.cypher) with ";" terminators."""
    lines: List[str] = [
        "// =============================================================================",
        "// GoSmart DICOM-FHIR Graph Server — FHIR R4B Schema Constraints & Indexes",
        "// Generated from model.fhir (see docs/design-notes-informal/fhir_schema.md)",
        "// =============================================================================",
        "",
        "// -----------------------------------------------------------------------------",
        "// 1. Uniqueness Constraints (System UUIDv5 `id` and FHIR Ingestion `uid`)",
        "// -----------------------------------------------------------------------------",
    ]
    for stmt in FHIR_SCHEMA_CONSTRAINTS:
        lines.append(f"{stmt};")

    lines.extend(
        [
            "",
            "// -----------------------------------------------------------------------------",
            "// 2. Cross-Domain DICOM Correlation Index",
            "// -----------------------------------------------------------------------------",
            f"{FHIR_SCHEMA_INDEXES[0]};",
        ]
    )

    for node_cls in FHIR_NODE_CLASSES:
        label = node_cls.__label__
        label_snake = _to_snake_case(label)
        lines.extend(
            [
                "",
                "// -----------------------------------------------------------------------------",
                f"// {label} Property Indexes",
                "// -----------------------------------------------------------------------------",
            ]
        )
        for prop_name in get_node_indexed_properties(node_cls):
            prop_snake = _to_snake_case(prop_name)
            idx_name = f"fhir_{label_snake}_{prop_snake}_idx"
            prop_expr = f"n.`{prop_name}`" if prop_name == "class" else f"n.{prop_name}"
            lines.append(f"CREATE INDEX {idx_name} IF NOT EXISTS FOR (n:{label}) ON ({prop_expr});")

    lines.append("")
    return "\n".join(lines)
