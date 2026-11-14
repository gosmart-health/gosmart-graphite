from typing import Any, List, Mapping, Optional, Sequence, Union

from pydantic import BaseModel, Field, model_validator

# Maximum raw FHIR JSON size (in UTF-8 bytes) stored inline in the `resource` property.
# Payloads larger than 4 KB (4096 bytes) must be stored externally via `resourceUri`.
MAX_INLINE_RESOURCE_BYTES: int = 4096


def format_identifier_macro(
    system: Optional[str] = None,
    value: Optional[str] = None,
) -> List[str]:
    """Canonicalize a FHIR Identifier (system, value) into the search macro array.

    Generates up to three canonical strings:
    1. ``"<system>|<value>"`` (when both system and value are present)
    2. ``"|<value>"`` (when value is present, enabling bare value lookups such as MRN or AccessionNumber)
    3. ``"<system>|"`` (when system is present, enabling system-scoped queries)
    """
    sys_str = (system or "").strip()
    val_str = (value or "").strip()

    entries: List[str] = []
    if sys_str and val_str:
        entries.append(f"{sys_str}|{val_str}")
    if val_str:
        entries.append(f"|{val_str}")
    if sys_str:
        entries.append(f"{sys_str}|")
    return entries


def flatten_identifiers(
    identifiers: Optional[Sequence[Mapping[str, Any]]],
) -> List[str]:
    """Flatten a sequence of raw FHIR Identifier dicts into deduplicated Identifier Macro strings."""
    if not identifiers:
        return []
    result: List[str] = []
    seen: set[str] = set()
    for ident in identifiers:
        if not isinstance(ident, Mapping):
            continue
        for entry in format_identifier_macro(ident.get("system"), ident.get("value")):
            if entry not in seen:
                seen.add(entry)
                result.append(entry)
    return result


def format_coding_macro(
    system: Optional[str] = None,
    code: Optional[str] = None,
    display: Optional[str] = None,
) -> str:
    """Format a FHIR Coding into ``"system|code|display"`` (or ``"system|code"`` when display is absent)."""
    sys_str = (system or "").strip()
    code_str = (code or "").strip()
    disp_str = (display or "").strip()
    if disp_str:
        return f"{sys_str}|{code_str}|{disp_str}"
    return f"{sys_str}|{code_str}"


def flatten_codings(
    codings_or_concepts: Optional[Union[Mapping[str, Any], Sequence[Mapping[str, Any]]]],
) -> List[str]:
    """Flatten a FHIR Coding, CodeableConcept, or list of CodeableConcepts into Coding Macro strings."""
    if not codings_or_concepts:
        return []

    items: Sequence[Mapping[str, Any]]
    if isinstance(codings_or_concepts, Mapping):
        items = [codings_or_concepts]
    else:
        items = codings_or_concepts

    result: List[str] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, Mapping):
            continue
        nested_codings = item.get("coding")
        if isinstance(nested_codings, Sequence) and not isinstance(nested_codings, (str, bytes)):
            for c in nested_codings:
                if isinstance(c, Mapping):
                    macro = format_coding_macro(c.get("system"), c.get("code"), c.get("display"))
                    if macro not in seen:
                        seen.add(macro)
                        result.append(macro)
        elif "code" in item or "system" in item:
            macro = format_coding_macro(item.get("system"), item.get("code"), item.get("display"))
            if macro not in seen:
                seen.add(macro)
                result.append(macro)
    return result


def format_reference_macro(
    reference: Optional[str] = None,
    display: Optional[str] = None,
) -> str:
    """Format a FHIR Reference into ``"reference|display"`` (or ``"reference"`` when display is absent)."""
    ref_str = (reference or "").strip()
    disp_str = (display or "").strip()
    if disp_str:
        return f"{ref_str}|{disp_str}"
    return ref_str


class CodedValueMacro(BaseModel):
    """Parallel array representation of coded numeric values for Cypher nodes.

    Synchronized by array index across ``codes``, ``numericValues``, and ``units``.
    """

    codes: List[str] = Field(
        default_factory=list,
        description="Coding Macros (system|code|display) for the measured observation or report code",
    )
    numericValues: List[float] = Field(
        default_factory=list,
        description="Numeric measurement values synchronized by index with codes and units",
    )
    units: List[str] = Field(
        default_factory=list,
        description="Unit Coding Macros (system|code|display) synchronized by index with codes and numericValues",
    )

    @model_validator(mode="after")
    def validate_parallel_lengths(self) -> "CodedValueMacro":
        if not (len(self.codes) == len(self.numericValues) == len(self.units)):
            raise ValueError(
                f"CodedValueMacro parallel arrays must have identical lengths; "
                f"got codes={len(self.codes)}, numericValues={len(self.numericValues)}, units={len(self.units)}"
            )
        return self
