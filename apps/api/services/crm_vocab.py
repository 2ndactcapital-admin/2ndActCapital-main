"""CRMDETAIL1 — the CRM's governed vocabularies, as ONE code constant served in the API envelope.

WHY A CODE CONSTANT AND NOT A REFERENCE TABLE (Task 2 decision)
  * The allowed (from type, to type) pairs are application rules the API enforces on every write; a table row
    would still need this code to read and apply it, and a per-org edit of "who may be a trustee" is not a feature
    anyone has asked for.
  * ``entity_relationships.relationship_type`` stays free text in the schema (no CHECK, no FK) — it has carried
    only 'ownership' in production — so the API is the one gate, and readers that assume ownership already filter
    ``relationship_type = 'ownership'`` explicitly (Task 1b).
  * The page never hard-codes a label: every key, label, reverse label, allowed pair and field type below reaches
    the browser through ``vocabularies`` in the response envelope (CLAUDE.md Rule 1).

DIRECTION. A relationship row reads "FROM is <label> TO": ``(Dominic, parent_of, Anthony)`` — on Dominic's record
"Parent of · Anthony D. Olivo"; on Anthony's record the reverse label, "Child of · Dominic J. Olivo". For a role the
reverse label names the role the other record holds: ``(Wilmington Fiduciary Co., trustee, Dynasty Trust)`` reads
"Trustee of · Olivo 2014 Dynasty Trust" on Wilmington's record and "Trustee · Wilmington Fiduciary Co." on the
trust's. A symmetric type (spouse, sibling …) reads the same both ways.

``ownership`` is in the vocabulary unchanged (any pair, a percentage) but is entered on the Ownership tab, never
the Relationships panel (``panel = False``).

``beneficiary`` keeps its existing meaning elsewhere: services.entity_graph.get_beneficiary_visible and the
ownership tree's default edge types already read it (member visibility flows from a beneficiary edge exactly as
from ownership). income_beneficiary / remainder_beneficiary do NOT confer visibility.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

# ── Entity-type groups (values of the Postgres entity_type enum) ────────────────────────────────────────────────
PERSON = frozenset({"individual"})
TRUSTS = frozenset({"trust"})
CHARITIES = frozenset({"foundation"})
LLC_LIKE = frozenset({"llc", "family_office", "gp"})
PARTNERSHIPS = frozenset({"lp"})
CORPORATIONS = frozenset({"corporation", "s_corp", "c_corp", "corp_uk", "corp_eu", "corp_cayman", "corp_luxembourg",
                          "corp_other_intl"})
#: every legal (non-person) CRM record. household / account are operational, never a relationship party here.
LEGAL = TRUSTS | CHARITIES | LLC_LIKE | PARTNERSHIPS | CORPORATIONS | frozenset({"spv", "other"})
PARTY = PERSON | LEGAL
OPERATING = LLC_LIKE | PARTNERSHIPS | CORPORATIONS | frozenset({"other"})
ANY = None          # "any entity type" (the existing ownership edge, unchanged)


@dataclass(frozen=True)
class RelType:
    key: str
    label: str
    reverse_label: str
    from_types: frozenset | None
    to_types: frozenset | None
    symmetric: bool = False
    group: str = "role"
    panel: bool = True          # offered on the Relationships panel (ownership: the Ownership tab)
    help: str = ""

    def as_json(self) -> dict:
        return {"key": self.key, "label": self.label, "reverse_label": self.reverse_label,
                "from_types": sorted(self.from_types) if self.from_types is not None else None,
                "to_types": sorted(self.to_types) if self.to_types is not None else None,
                "symmetric": self.symmetric, "group": self.group, "panel": self.panel, "help": self.help}


def _t(key, label, reverse, frm, to, *, symmetric=False, group="role", panel=True, help=""):
    return RelType(key, label, reverse, frm, to, symmetric, group, panel, help)


RELATIONSHIP_TYPES: dict[str, RelType] = {r.key: r for r in (
    # family
    _t("spouse", "Spouse", "Spouse", PERSON, PERSON, symmetric=True, group="family"),
    _t("former_spouse", "Former spouse", "Former spouse", PERSON, PERSON, symmetric=True, group="family"),
    _t("partner", "Partner", "Partner", PERSON, PERSON, symmetric=True, group="family"),
    _t("parent_of", "Parent of", "Child of", PERSON, PERSON, group="family"),
    _t("sibling", "Sibling", "Sibling", PERSON, PERSON, symmetric=True, group="family"),
    # trusts
    _t("grantor", "Grantor of", "Grantor", PARTY, TRUSTS, group="trust"),
    _t("trustee", "Trustee of", "Trustee", PARTY, TRUSTS | CHARITIES, group="trust"),
    _t("successor_trustee", "Successor trustee of", "Successor trustee", PARTY, TRUSTS | CHARITIES, group="trust"),
    _t("co_trustee", "Co-trustee of", "Co-trustee", PARTY, TRUSTS | CHARITIES, group="trust"),
    _t("trust_protector", "Trust protector of", "Trust protector", PARTY, TRUSTS, group="trust"),
    _t("distribution_committee", "Distribution committee of", "Distribution committee", PERSON, TRUSTS,
       group="trust"),
    _t("beneficiary", "Beneficiary of", "Beneficiary", PARTY, LEGAL, group="trust",
       help="Also grants the beneficiary's members visibility of the record (existing member-visibility rule)."),
    _t("income_beneficiary", "Income beneficiary of", "Income beneficiary", PARTY, TRUSTS, group="trust"),
    _t("remainder_beneficiary", "Remainder beneficiary of", "Remainder beneficiary", PARTY, TRUSTS, group="trust"),
    _t("annuitant", "Annuitant of", "Annuitant", PARTY, TRUSTS, group="trust"),
    # companies and partnerships
    _t("managing_member", "Managing member of", "Managing member", PARTY, LLC_LIKE, group="company"),
    _t("sole_member", "Sole member of", "Sole member", PARTY, LLC_LIKE, group="company"),
    _t("manager", "Manager of", "Manager", PARTY, OPERATING, group="company"),
    _t("general_partner", "General partner of", "General partner", PARTY, PARTNERSHIPS, group="company"),
    _t("limited_partner", "Limited partner of", "Limited partner", PARTY, PARTNERSHIPS, group="company"),
    _t("investment_committee", "Investment committee of", "Investment committee", PERSON, LEGAL, group="company"),
    _t("board_chair", "Board chair of", "Board chair", PERSON, LEGAL, group="company"),
    _t("board_member", "Board member of", "Board member", PERSON, LEGAL, group="company"),
    _t("officer", "Officer of", "Officer", PERSON, LEGAL, group="company"),
    _t("owner", "Owner of", "Owner", PARTY, LEGAL, group="company",
       help="An owner without a percentage. A percentage stake is entered on the Ownership tab."),
    # charities
    _t("donor_advisor", "Donor advisor of", "Donor advisor", PERSON, CHARITIES, group="charity"),
    _t("sponsor", "Sponsor of", "Sponsor", LEGAL, CHARITIES, group="charity"),
    # advisers
    _t("advisor", "Advisor to", "Advisor", PARTY, PARTY, group="advisor"),
    # the existing edge, unchanged — entered on the Ownership tab
    _t("ownership", "Owns", "Owned by", ANY, ANY, group="ownership", panel=False),
)}

RELATIONSHIP_GROUPS = (("family", "Family"), ("trust", "Trusts"), ("company", "Companies and partnerships"),
                       ("charity", "Charities"), ("advisor", "Advisers"), ("ownership", "Ownership"))


class VocabError(ValueError):
    """A write refused for a reason the caller can fix (the message says which)."""


def type_label(entity_type: str) -> str:
    return ENTITY_TYPE_LABELS.get(entity_type, entity_type)


def a_type(entity_type: str) -> str:
    """'an Individual', 'a Trust', 'an LLC' — the article read from the label's sound."""
    lbl = type_label(entity_type)
    return ("an " if lbl[:1] in "AEIOU" or lbl in ("LLC", "LP", "S-Corp", "SPV") else "a ") + lbl


def check_relationship(key: str, from_type: str, to_type: str, *, from_id=None, to_id=None) -> RelType:
    """The relationship type, or VocabError naming exactly why it is refused."""
    rel = RELATIONSHIP_TYPES.get((key or "").strip())
    if rel is None:
        raise VocabError(f"'{key}' is not a relationship type. Choose one of: "
                         + ", ".join(sorted(RELATIONSHIP_TYPES)))
    if from_id is not None and to_id is not None and str(from_id) == str(to_id):
        raise VocabError("a record cannot have a relationship with itself")
    if rel.from_types is not None and from_type not in rel.from_types:
        raise VocabError(f"'{rel.label}' cannot start from {a_type(from_type)} record — it starts from "
                         + _types_phrase(rel.from_types))
    if rel.to_types is not None and to_type not in rel.to_types:
        raise VocabError(f"'{rel.label}' cannot point to {a_type(to_type)} record — it points to "
                         + _types_phrase(rel.to_types))
    return rel


def _types_phrase(types: frozenset) -> str:
    return a_type(sorted(types)[0]).split(" ", 1)[0] + " " + " / ".join(type_label(t) for t in sorted(types)) + " record"


def relationship_vocab() -> dict:
    return {"relationship_types": [r.as_json() for r in RELATIONSHIP_TYPES.values()],
            "relationship_groups": [{"key": k, "label": v} for k, v in RELATIONSHIP_GROUPS]}


# Entity-type labels (the enum values' display labels) — served in the envelope, so the panels never hard-code them.
ENTITY_TYPE_LABELS = {
    "individual": "Individual", "trust": "Trust", "llc": "LLC", "lp": "LP", "gp": "GP", "corporation": "Corporation",
    "s_corp": "S-Corp", "c_corp": "C-Corp", "corp_uk": "UK Corp", "corp_eu": "EU Corp", "corp_cayman": "Cayman Corp",
    "corp_luxembourg": "Luxembourg Corp", "corp_other_intl": "Intl Corp (Other)", "foundation": "Foundation",
    "family_office": "Family Office", "household": "Household", "spv": "SPV", "account": "Account", "other": "Other",
}


# ── Typed detail fields (Task 3) ──────────────────────────────────────────────────────────────────────────────
# Stored as entity_attributes rows under the reserved key prefix DETAIL_PREFIX ("detail.trust_type"), value_type
# from the field's type. Valid-time restatement per key (Rule 3): an edit closes the live row and inserts the new
# value; clearing a field closes it and inserts nothing.
DETAIL_PREFIX = "detail."

FIELD_TYPES = ("choice", "boolean", "text", "integer", "decimal", "date", "entity", "entities", "region", "masked")


@dataclass(frozen=True)
class DetailField:
    key: str
    label: str
    type: str
    choices: tuple = ()           # ((value, label), …) for type 'choice'
    sets_sub_type: bool = False   # trust_type / charity_type also set entities.sub_type to the choice's label
    help: str = ""
    min: Decimal | None = None
    max: Decimal | None = None
    entity_types: frozenset | None = None    # for type 'entity' / 'entities': which record types may be chosen
    source: str = "attribute"     # 'attribute' (entity_attributes) or 'column:<name>' (an entities column)
    group: str = ""               # PORTENTRY1: a field group shown under one heading (the ILIT's insurance policy)

    @property
    def attribute_key(self) -> str:
        return DETAIL_PREFIX + self.key

    def as_json(self) -> dict:
        return {"key": self.key, "label": self.label, "type": self.type,
                "choices": [{"value": v, "label": lbl} for v, lbl in self.choices],
                "sets_sub_type": self.sets_sub_type, "help": self.help,
                "min": str(self.min) if self.min is not None else None,
                "max": str(self.max) if self.max is not None else None,
                "entity_types": sorted(self.entity_types) if self.entity_types is not None else None,
                "source": self.source, "editable_here": self.source == "attribute", "group": self.group}


TRUST_TYPES = (("revocable", "Revocable"), ("irrevocable", "Irrevocable"), ("dynasty", "Dynasty"),
               ("slat", "SLAT"), ("grat", "GRAT"), ("bypass", "Bypass (credit shelter)"), ("2503c", "2503(c)"),
               ("ilit", "ILIT"), ("crut", "CRUT"), ("crat", "CRAT"), ("clat", "CLAT"), ("idgt", "IDGT"),
               ("qprt", "QPRT"), ("other", "Other"))
CHARITY_TYPES = (("private_foundation", "Private foundation"), ("daf", "Donor-advised fund"),
                 ("public_charity", "Public charity"), ("supporting_org", "Supporting organization"))
MANAGEMENT = (("member_managed", "Member-managed"), ("manager_managed", "Manager-managed"))
# PORTENTRY1 (GAP-CRM-04)
FUND_ROLES = (("family_partnership", "Family partnership"), ("fund", "Fund"), ("spv", "SPV"), ("other", "Other"))
EIN_STATUSES = (("501c3", "501(c)(3)"), ("501c4", "501(c)(4)"), ("other", "Other"))
POLICY_TYPES = (("survivorship_ul", "Survivorship universal life"), ("universal_life", "Universal life"),
                ("whole_life", "Whole life"), ("term", "Term"), ("variable_ul", "Variable universal life"),
                ("other", "Other"))
POLICY_GROUP = "insurance_policy"

STATE_OF_FORMATION = DetailField(
    "state_of_formation", "State of formation", "region", source="column:region_code",
    help="The record's Country / State of formation on the Details form (entities.country_code / region_code).")

TRUST_FIELDS = (
    DetailField("trust_type", "Trust type", "choice", TRUST_TYPES, sets_sub_type=True,
                help="Also sets the record's sub-type."),
    DetailField("revocable", "Revocable", "boolean"),
    DetailField("situs_state", "Situs state", "text", help="Governing-law state, e.g. DE"),
    DetailField("gst_exempt", "GST exempt", "boolean"),
    DetailField("vesting_age", "Vesting age", "integer", min=Decimal(0), max=Decimal(120)),
    DetailField("termination", "Termination", "text"),
    DetailField("annuity_term_years", "Annuity term (years)", "integer", min=Decimal(0), max=Decimal(99)),
    DetailField("annuity_end", "Annuity end", "date"),
    DetailField("unitrust_rate_pct", "Unitrust rate (%)", "decimal", min=Decimal(0), max=Decimal(100)),
    DetailField("crummey_notices", "Crummey notices", "boolean"),
    DetailField("funded_with", "Funded with", "text"),
    # PORTENTRY2 (MUSE-OLIVO-D6 / D7 / D8 / D18-01) — a GRAT's / CRUT's remainder beneficiary (a CRM record: a
    # person, a trust or a charity), a bypass trust's shares (text), and a trust's beneficiaries note (a SLAT's).
    DetailField("remainder_to", "Remainder to (GRAT / CRUT)", "entity",
                help="The CRM record that receives the remainder — a person, a trust or a charity."),
    DetailField("shares", "Shares (bypass trust)", "text",
                help="How the trust is divided, e.g. '1/3 each to the three children, per stirpes'."),
    DetailField("beneficiaries_note", "Beneficiaries note", "text",
                help="Who benefits and how, e.g. a SLAT's spouse and descendants."),
    # PORTENTRY1 (GAP-CRM-04) — the policy an ILIT holds; the insurer and the insureds are CRM records
    DetailField("policy_insurer", "Insurer", "entity", entity_types=LEGAL, group=POLICY_GROUP,
                help="The insurance company's CRM record."),
    DetailField("policy_type", "Policy type", "choice", POLICY_TYPES, group=POLICY_GROUP),
    DetailField("policy_face_amount", "Face amount", "decimal", min=Decimal(0), group=POLICY_GROUP),
    DetailField("policy_insureds", "Insureds", "entities", entity_types=PERSON, group=POLICY_GROUP,
                help="The insured people's CRM records."),
    DetailField("policy_number_masked", "Policy number (masked)", "masked", group=POLICY_GROUP,
                help="Masked only, e.g. POL-****77 — never the full number."),
)
CHARITY_FIELDS = (
    DetailField("charity_type", "Charity type", "choice", CHARITY_TYPES, sets_sub_type=True,
                help="Also sets the record's sub-type."),
    DetailField("sponsor", "Sponsor", "entity", entity_types=LEGAL,
                help="The sponsoring organisation's CRM record (a donor-advised fund's sponsor)."),
    DetailField("min_distribution_pct", "Minimum distribution (%)", "decimal", min=Decimal(0), max=Decimal(100)),
    DetailField("ein_status", "EIN / exemption status", "choice", EIN_STATUSES),     # PORTENTRY1 (GAP-CRM-04)
)
COMPANY_FIELDS = (
    DetailField("management", "Management", "choice", MANAGEMENT),
    DetailField("purpose", "Purpose", "text"),
    DetailField("valuation_policy", "Valuation policy", "text"),
)

#: PORTENTRY1 (GAP-CRM-04) — an LP's role
LP_FIELDS = COMPANY_FIELDS + (DetailField("fund_role", "Fund role", "choice", FUND_ROLES),)

DETAIL_FIELDS_BY_TYPE: dict[str, tuple] = {
    "trust": TRUST_FIELDS,
    "foundation": CHARITY_FIELDS,
    **{t: COMPANY_FIELDS for t in ("llc", "gp", "family_office")},
    "lp": LP_FIELDS,
}


def detail_fields_for(entity_type: str) -> tuple:
    """The typed detail fields of one entity type; every legal entity also shows its state of formation."""
    fields = DETAIL_FIELDS_BY_TYPE.get(entity_type, ())
    if entity_type in LEGAL:
        return fields + (STATE_OF_FORMATION,)
    return fields


def detail_field(entity_type: str, key: str) -> DetailField | None:
    return next((f for f in detail_fields_for(entity_type) if f.key == key), None)


_VALUE_TYPES = {"choice": "string", "text": "string", "boolean": "boolean", "integer": "integer",
                "decimal": "decimal", "date": "date", "entity": "entity", "entities": "json", "region": "string",
                "masked": "string"}


def value_type_of(f: DetailField) -> str:
    return _VALUE_TYPES[f.type]


def coerce_detail(f: DetailField, raw) -> str | None:
    """The canonical stored text for ``raw``, None to clear, or VocabError naming what is wrong."""
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        return None
    if f.type == "choice":
        allowed = [v for v, _ in f.choices]
        if raw not in allowed:
            raise VocabError(f"{f.label}: '{raw}' is not one of {', '.join(allowed)}")
        return raw
    if f.type == "boolean":
        if isinstance(raw, bool):
            return "true" if raw else "false"
        if isinstance(raw, str) and raw.strip().lower() in ("true", "false"):
            return raw.strip().lower()
        raise VocabError(f"{f.label}: a yes / no value (true or false), not '{raw}'")
    if f.type == "integer":
        if isinstance(raw, bool) or not (isinstance(raw, int) or (isinstance(raw, str) and raw.strip().lstrip("-").isdigit())):
            raise VocabError(f"{f.label}: a whole number, not '{raw}'")
        v = Decimal(int(raw))
        _bounds(f, v)
        return str(int(v))
    if f.type == "decimal":
        if isinstance(raw, (bool, float)):
            raise VocabError(f"{f.label}: send the number as text (e.g. \"5\" or \"5.25\") — a float is refused so "
                             "no rounding enters")
        try:
            v = Decimal(str(raw).strip())
        except InvalidOperation:
            raise VocabError(f"{f.label}: a number, not '{raw}'") from None
        if not v.is_finite():
            raise VocabError(f"{f.label}: a finite number")
        _bounds(f, v)
        return format(v.normalize(), "f") if v == v.to_integral() else str(v)
    if f.type == "date":
        try:
            return date.fromisoformat(str(raw).strip()).isoformat()
        except ValueError:
            raise VocabError(f"{f.label}: a date as YYYY-MM-DD, not '{raw}'") from None
    if f.type == "text":
        if not isinstance(raw, str):
            raise VocabError(f"{f.label}: text")
        s = raw.strip()
        if len(s) > 2000:
            raise VocabError(f"{f.label}: at most 2,000 characters")
        return s
    if f.type == "entity":
        import uuid

        try:
            return str(uuid.UUID(str(raw)))
        except ValueError:
            raise VocabError(f"{f.label}: choose a CRM record") from None
    if f.type == "entities":
        import json
        import uuid

        vals = raw if isinstance(raw, list) else [raw]
        try:
            ids = sorted({str(uuid.UUID(str(v))) for v in vals if str(v).strip()})
        except ValueError:
            raise VocabError(f"{f.label}: choose CRM records") from None
        return json.dumps(ids) if ids else None
    if f.type == "masked":
        import re

        if not isinstance(raw, str):
            raise VocabError(f"{f.label}: text")
        s = raw.strip()
        if "*" not in s or len(re.findall(r"\d", s)) > 4 or len(s) > 40:
            raise VocabError(f"{f.label}: a MASKED number only (asterisks, at most the last 4 digits, e.g. "
                             "POL-****77) — never the full number")
        return s
    raise VocabError(f"{f.label} is not edited here")


def _bounds(f: DetailField, v: Decimal) -> None:
    if f.min is not None and v < f.min:
        raise VocabError(f"{f.label}: at least {f.min}")
    if f.max is not None and v > f.max:
        raise VocabError(f"{f.label}: at most {f.max}")


def decode_detail(f: DetailField, stored: str | None):
    """Stored text → the JSON value the page shows (decimals stay strings: never a float)."""
    if stored is None:
        return None
    if f.type == "boolean":
        return stored == "true"
    if f.type == "integer":
        return int(stored)
    if f.type == "entities":
        import json

        return json.loads(stored)
    return stored


def choice_label(f: DetailField, value) -> str | None:
    return next((lbl for v, lbl in f.choices if v == value), None)


def detail_vocab() -> dict:
    return {"detail_fields_by_type": {t: [f.as_json() for f in detail_fields_for(t)]
                                      for t in sorted(set(DETAIL_FIELDS_BY_TYPE) | LEGAL)},
            "field_types": list(FIELD_TYPES)}


# ── People (Task 4) ─────────────────────────────────────────────────────────────────────────────────────────────
STATUS_DECEASED = "deceased"
MINOR_AGE = 18


def age_on(dob: date | None, today: date) -> int | None:
    if dob is None:
        return None
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def person_flags(entity_type: str, dob: date | None, date_of_death: date | None, today: date) -> dict:
    """age / is_minor / is_deceased for a person (computed at ``today``, never stored). A deceased person is
    never a minor; a record that is not a person carries none of these."""
    if entity_type not in PERSON:
        return {"age": None, "is_minor": False, "is_deceased": False}
    end = date_of_death if date_of_death is not None and date_of_death < today else today
    age = age_on(dob, end)
    return {"age": age, "is_minor": bool(age is not None and date_of_death is None and age < MINOR_AGE),
            "is_deceased": date_of_death is not None}


# ── Liabilities (Task 5) ─────────────────────────────────────────────────────────────────────────────────────────
#: CATCHALL1 (HollisMuse Low) — the labels use the Olivo plan's own wording ("Secured credit line", for BOTH L.1 and
#: L.7) with what it is secured by, so the one phrase picks between the two kinds. Labels only: the stored keys (and
#: liabilities_kind_chk) are unchanged.
LIABILITY_KINDS = (("mortgage", "Mortgage"), ("securities_backed_line", "Secured credit line — securities"),
                   ("margin", "Margin loan"), ("term_loan", "Term loan"),
                   ("asset_secured_line", "Secured credit line — other assets (art, property)"),
                   ("personal_loan", "Personal loan"), ("other", "Other"))
RATE_TYPES = (("fixed", "Fixed"), ("floating", "Floating"))
LIABILITY_KIND_KEYS = frozenset(k for k, _ in LIABILITY_KINDS)
RATE_TYPE_KEYS = frozenset(k for k, _ in RATE_TYPES)


def liability_vocab() -> dict:
    return {"liability_kinds": [{"value": k, "label": v} for k, v in LIABILITY_KINDS],
            "rate_types": [{"value": k, "label": v} for k, v in RATE_TYPES],
            "net_worth_lines": [{"key": "gross", "label": "Gross assets"},
                                {"key": "liabilities", "label": "Liabilities"},
                                {"key": "net", "label": "Net worth"}],
            "net_worth_note": "Unfunded commitments are not liabilities; they stay with the commitment."}


def all_vocab() -> dict:
    return {**relationship_vocab(), **detail_vocab(), **liability_vocab(),
            "entity_type_labels": dict(ENTITY_TYPE_LABELS),
            "person_status_deceased": {"value": STATUS_DECEASED, "label": "Deceased"},
            "minor_age": MINOR_AGE}
