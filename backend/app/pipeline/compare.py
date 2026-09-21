"""Stage 3 -- compare the SI (reference) against the draft BL.

Outcome per email:
    OK            all seven fields present on both sides and equal
    MISMATCH      at least one field differs
    NEEDS_REVIEW  the comparison could not be trusted

NEEDS_REVIEW is deliberately narrow. Escalating a real discrepancy loses the
end-to-end point outright, so a case escalates only when a value is genuinely
absent or a document could not be read -- never merely because a value looked
unusual.
"""
from .fields import FIELDS
from .normalize import normalise

REVIEW_REASONS = ("wrong_doc_type", "missing_attachment", "unreadable", "missing_value")


class Result:
    def __init__(self, status, defect_fields=None, review_reason=None,
                 evidence=None, notes=None):
        self.status = status
        self.defect_fields = sorted(defect_fields or [])
        self.review_reason = review_reason
        self.evidence = evidence or {}       # field -> {"si":…, "bl":…}
        self.notes = notes or []

    @property
    def has_defect(self):
        return self.status == "MISMATCH"

    def to_submission(self):
        return {
            "status": self.status,
            "review_reason": self.review_reason,
            "has_defect": self.has_defect,
            "defect_fields": self.defect_fields,
        }


def compare(si_fields, bl_fields):
    """Compare two extracted field maps and return a Result."""
    defects, evidence, missing = [], {}, []

    for field in FIELDS:
        si_raw, bl_raw = si_fields.get(field), bl_fields.get(field)
        si_val, bl_val = normalise(field, si_raw), normalise(field, bl_raw)

        # Record what the documents literally said, for the report and for
        # the reviewer who may have to overrule us.
        evidence[field] = {"si": si_raw, "bl": bl_raw}

        if si_val is None or bl_val is None:
            missing.append(field)
            continue
        if si_val != bl_val:
            defects.append(field)

    # A value we could not read is not a discrepancy -- it is a question.
    if missing:
        return Result("NEEDS_REVIEW", review_reason="missing_value",
                      evidence=evidence,
                      notes=[f"no comparable value for: {', '.join(missing)}"])
    if defects:
        return Result("MISMATCH", defect_fields=defects, evidence=evidence)
    return Result("OK", evidence=evidence)
