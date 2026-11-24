"""Extended clinical NLU intent specifications for Gosmart GRAPHITE.

Extends the built-in ``find_study`` and ``get_report`` intents from
``gosmart-clinical-nlu`` with an ``accession_number`` slot bound to the
``ACCESSION_NUMBER`` EntityRuler label, registered both programmatically
(from :mod:`gosmart_graphite.service.search_service`) and declaratively via
the ``gosmart_clinical_nlu.intents`` Python entry-point group.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Mapping, Optional

from gosmart_clinical_nlu import EntityBinding, IntentSpec, register_intent_spec
from gosmart_clinical_nlu.intents.find_study import ENTITY_MAP as _BASE_FIND_STUDY_ENTITY_MAP
from gosmart_clinical_nlu.intents.find_study import StudyQueryIntent
from gosmart_clinical_nlu.intents.get_report import ENTITY_MAP as _BASE_GET_REPORT_ENTITY_MAP
from gosmart_clinical_nlu.intents.get_report import ReportQueryIntent

_DICOM_SH_MAX_LENGTH = 16
_ACCESSION_PREFIX_RE = re.compile(
    r"^(?:accession|acc)\b(?:\s*(?:number|no|num)\b)?[\s.#:]*",
    flags=re.IGNORECASE,
)


def normalize_accession_number(raw: str, ref_date: Optional[datetime] = None) -> Optional[str]:
    """Strip leading 'accession'/'acc' prefix words and validate DICOM SH constraints.

    Returns the normalized accession number string (1..16 chars containing at
    least one digit) or ``None`` if the input does not yield a valid identifier.
    """
    del ref_date
    cleaned = _ACCESSION_PREFIX_RE.sub("", raw.strip()).strip(" \t\r\n.,;:")
    if not cleaned or len(cleaned) > _DICOM_SH_MAX_LENGTH:
        return None
    if not any(ch.isdigit() for ch in cleaned):
        return None
    return cleaned


class GraphiteStudyQueryIntent(StudyQueryIntent):
    """Slots for a DICOM study search request, extended with ``accession_number``."""

    accession_number: Optional[str] = None


class GraphiteReportQueryIntent(ReportQueryIntent):
    """Slots for a radiology report retrieval request, extended with ``accession_number``."""

    accession_number: Optional[str] = None


_ACCESSION_BINDING = EntityBinding(field="accession_number", normalize=normalize_accession_number)

GRAPHITE_FIND_STUDY_ENTITY_MAP: Mapping[str, EntityBinding] = {
    **_BASE_FIND_STUDY_ENTITY_MAP,
    "ACCESSION_NUMBER": _ACCESSION_BINDING,
}

GRAPHITE_GET_REPORT_ENTITY_MAP: Mapping[str, EntityBinding] = {
    **_BASE_GET_REPORT_ENTITY_MAP,
    "ACCESSION_NUMBER": _ACCESSION_BINDING,
}

GRAPHITE_FIND_STUDY_SPEC = IntentSpec(
    name="find_study",
    label="FIND_STUDY",
    model_cls=GraphiteStudyQueryIntent,
    entity_map=GRAPHITE_FIND_STUDY_ENTITY_MAP,
    description="Search for imaging studies by patient, modality, date, or accession number.",
)

GRAPHITE_GET_REPORT_SPEC = IntentSpec(
    name="get_report",
    label="GET_REPORT",
    model_cls=GraphiteReportQueryIntent,
    entity_map=GRAPHITE_GET_REPORT_ENTITY_MAP,
    description="Fetch the radiology report for a study by patient, modality, date, or accession number.",
)

GRAPHITE_INTENT_SPECS: tuple[IntentSpec, ...] = (
    GRAPHITE_FIND_STUDY_SPEC,
    GRAPHITE_GET_REPORT_SPEC,
)


def register_graphite_intents() -> tuple[IntentSpec, ...]:
    """Idempotently register GRAPHITE's extended intent specs in ``gosmart_clinical_nlu``.

    Used both as the ``gosmart_clinical_nlu.intents`` entry-point target and
    directly before loading NLU bundles in :mod:`gosmart_graphite.service.search_service`.
    """
    for spec in GRAPHITE_INTENT_SPECS:
        register_intent_spec(spec, replace=True)
    return GRAPHITE_INTENT_SPECS


__all__ = [
    "GRAPHITE_FIND_STUDY_ENTITY_MAP",
    "GRAPHITE_FIND_STUDY_SPEC",
    "GRAPHITE_GET_REPORT_ENTITY_MAP",
    "GRAPHITE_GET_REPORT_SPEC",
    "GRAPHITE_INTENT_SPECS",
    "GraphiteReportQueryIntent",
    "GraphiteStudyQueryIntent",
    "normalize_accession_number",
    "register_graphite_intents",
]
