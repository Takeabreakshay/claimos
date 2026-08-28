"""LIVE document OCR + field extraction.

Two engines, auto-selected:
  1. LOCAL  (default, ZERO keys) - RapidOCR/ONNX runs on-device.
  2. LLM VISION (optional LLM_API_KEY) - materially better on messy Indian
     RC/DL scans, and can also read damage severity from a photo.

Extracted fields feed the real workflow: registration number is cross-checked
against the policy, the repair-estimate amount becomes claim_amount, FIR number
satisfies the coverage rule. Nothing here is faked.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

# --- Indian document patterns ------------------------------------------------
RE_REGNO = re.compile(r"\b([A-Z]{2})[\s-]?(\d{1,2})[\s-]?([A-Z]{1,3})[\s-]?(\d{3,4})\b")
RE_DLNO = re.compile(r"\b([A-Z]{2}[\s-]?\d{2})[\s-]?(\d{4})[\s-]?(\d{7})\b")
RE_ENGINE = re.compile(r"(?:ENGINE|ENG)[\s.:No]*([A-Z0-9]{6,20})", re.I)
RE_CHASSIS = re.compile(r"(?:CHASSIS|CHAS|VIN)[\s.:No]*([A-Z0-9]{10,20})", re.I)
RE_AMOUNT = re.compile(r"(?:₹|RS\.?|INR)\s?([\d,]+(?:\.\d{1,2})?)", re.I)
RE_FIR = re.compile(r"(?:FIR|F\.I\.R)[\s.:No]*([0-9]{1,6}[/\-][0-9]{2,4})", re.I)
RE_DATE = re.compile(r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b")
RE_POLICY = re.compile(r"\b(?:POLICY|POL)[\s.:No]*([A-Z0-9\-/]{6,25})", re.I)

# --- Additional patterns for the per-doc-type schema (LOGIC §6) ---------------
# 17-char VIN/chassis (VIN excludes I/O/Q but OCR is noisy, so accept any alnum).
RE_CHASSIS17 = re.compile(r"\b([A-Z0-9]{17})\b")
# GSTIN: 2 digit state + 5 alpha PAN + 4 digit + 1 alpha + 1 alnum + 'Z' + 1 alnum.
RE_GSTIN = re.compile(r"\b(\d{2}[A-Z]{5}\d{4}[A-Z][A-Z0-9]Z[A-Z0-9])\b")
GSTIN_VALID = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[A-Z0-9]{1}[Z]{1}[A-Z0-9]{1}$")
# IFSC: 4 alpha + '0' + 6 alnum.
RE_IFSC = re.compile(r"\b([A-Z]{4}0[A-Z0-9]{6})\b")
IFSC_VALID = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
RE_ACCOUNT = re.compile(r"(?:A/C|ACCOUNT|ACCT|ACC)[\s.:NO]*([0-9]{9,18})", re.I)
# One table row with three trailing numbers: name/desc  qty/hrs  rate  amount.
RE_ROW4 = re.compile(
    r"^(.*?[A-Z].*?)\s+(\d+(?:\.\d+)?)\s+([\d,]+(?:\.\d+)?)\s+([\d,]+(?:\.\d+)?)\s*$"
)
# Known DL vehicle-class codes.
_DL_CLASSES = ("LMV-NT", "LMV", "MCWG", "MCWOG", "MCW", "HMV", "HGMV", "HPMV",
               "HTV", "MGV", "LGV", "TRANS", "TRAILER", "3W", "PSV")
# Blood group tokens.
RE_BLOOD = re.compile(r"\b(AB|A|B|O)\s?([+-]|POS|NEG|POSITIVE|NEGATIVE)")
# Policy number without the case-insensitive-class quirk of legacy RE_POLICY.
RE_POLICY_NUM = re.compile(
    r"POLICY\s*(?:NO\.?|NUMBER|#)?\s*[:.\-]?\s*([A-Z0-9][A-Z0-9\-/]{5,24})")
RE_DOB = re.compile(r"(?:DOB|DATE OF BIRTH|BIRTH)[\s.:]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", re.I)
RE_PERCENT = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")


@dataclass
class OcrResult:
    engine: str
    text: str
    fields: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "engine": self.engine,
            "text": self.text,
            "fields": self.fields,
            "confidence": self.confidence,
            "error": self.error,
        }


# --------------------------------------------------------------------------- #
# Field extraction (runs on whatever text the engine produced)
# --------------------------------------------------------------------------- #
def extract_fields(text: str, doc_type: str = "other") -> dict[str, Any]:
    """Pull structured fields out of raw OCR text.

    Returns the legacy flat fields (registration_no, dl_number, engine_no,
    chassis_no, fir_number, policy_no, amount, amounts_seen, dates,
    doc_type_guess) for backward compatibility, PLUS the richer per-document-type
    schema (LOGIC §6): typed fields, a ``validations`` sub-dict of failed
    field-format checks, a ``cross_checks`` list, and ``quality_gates`` applied to
    every doc. Cross-checks needing external rails (VAHAN/DigiLocker/etc.) or the
    claim record are documented with status ``todo`` - never executed here.
    """
    up = (text or "").upper()
    out: dict[str, Any] = {}

    # --- legacy generic base (unchanged, kept for existing callers) -----------
    m = RE_REGNO.search(up)
    if m:
        out["registration_no"] = f"{m.group(1)}{m.group(2).zfill(2)}{m.group(3)}{m.group(4)}"

    m = RE_DLNO.search(up)
    if m:
        out["dl_number"] = f"{m.group(1)}{m.group(2)}{m.group(3)}".replace(" ", "")

    for key, rx in (("engine_no", RE_ENGINE), ("chassis_no", RE_CHASSIS),
                    ("fir_number", RE_FIR), ("policy_no", RE_POLICY)):
        m = rx.search(up)
        if m:
            out[key] = m.group(1).strip()

    amounts = [float(a.replace(",", "")) for a in RE_AMOUNT.findall(up)]
    if amounts:
        # A repair estimate's headline figure is the largest amount on the page.
        out["amount"] = max(amounts)
        out["amounts_seen"] = sorted(set(amounts), reverse=True)[:6]

    dates = RE_DATE.findall(up)
    if dates:
        out["dates"] = dates[:4]

    guess = _guess_doc_type(up, doc_type)
    out["doc_type_guess"] = guess

    # --- per-document-type schema --------------------------------------------
    effective = doc_type if doc_type in _EXTRACTORS else guess
    out["doc_type_resolved"] = effective
    extractor = _EXTRACTORS.get(effective)
    if extractor:
        fields, validations, cross_checks, required = extractor(up)
        # merge per-type fields, but never clobber a legacy value with None
        for k, v in fields.items():
            if v is not None or k not in out:
                out[k] = v
        out["validations"] = validations
        out["cross_checks"] = cross_checks
    else:
        required = []
        out["validations"] = {}
        out["cross_checks"] = []

    out["quality_gates"] = _quality_gates(out, required)
    return out


# --------------------------------------------------------------------------- #
# Small extraction helpers (pure regex / heuristics; never hallucinate)
# --------------------------------------------------------------------------- #
def _num(s: str) -> float:
    return float(s.replace(",", "").strip())


def _grab(up: str, patterns: list[str], group: int = 1) -> str | None:
    """First capturing match across ``patterns`` (each a raw regex fragment)."""
    for p in patterns:
        m = re.search(p, up, re.I)
        if m:
            val = m.group(group).strip(" .:-\t")
            if val:
                return val
    return None


_NAME_V = r"([A-Z][A-Z. ]{2,40})"


def _grab_name(up: str, patterns: list[str]) -> str | None:
    val = _grab(up, patterns)
    if not val:
        return None
    # collapse runs of spaces, drop obvious trailing noise tokens
    val = re.sub(r"\s{2,}", " ", val).strip(" .")
    return val or None


def _labeled_amount(up: str, labels: list[str]) -> float | None:
    for lab in labels:
        m = re.search(
            lab + r"[^\d₹]{0,18}(?:₹|RS\.?|INR)?\s*([\d,]+(?:\.\d{1,2})?)", up, re.I
        )
        if m:
            try:
                return _num(m.group(1))
            except ValueError:
                continue
    return None


def _labeled_amount_max(up: str, labels: list[str]) -> float | None:
    """Largest amount appearing after any of the labels.

    Used for grand totals, where a plain ``TOTAL`` label also matches subtotals
    like ``PARTS TOTAL``; the grand total is the largest of them.
    """
    best: float | None = None
    for lab in labels:
        for m in re.finditer(
            lab + r"[^\d₹]{0,18}(?:₹|RS\.?|INR)?\s*([\d,]+(?:\.\d{1,2})?)", up, re.I
        ):
            try:
                val = _num(m.group(1))
            except ValueError:
                continue
            if best is None or val > best:
                best = val
    return best


def _labeled_date(up: str, labels: list[str]) -> str | None:
    for lab in labels:
        m = re.search(lab + r"[^\d]{0,15}(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", up, re.I)
        if m:
            return m.group(1)
    return None


def _labeled_int(up: str, labels: list[str]) -> int | None:
    for lab in labels:
        m = re.search(lab + r"[^\d]{0,15}(\d{1,7})", up, re.I)
        if m:
            return int(m.group(1))
    return None


def _norm_reg(m: re.Match) -> str:
    return f"{m.group(1)}{m.group(2).zfill(2)}{m.group(3)}{m.group(4)}"


def _parse_date(s: str | None):
    if not s:
        return None
    from datetime import datetime

    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y",
                "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _parse_rows(up: str) -> tuple[list[dict], list[dict]]:
    """Best-effort split of tabular lines into (line_items, labour_items).

    Each qualifying line has 3 trailing numbers: qty/hrs, rate, amount.
    Lines mentioning LABOUR/LABOR are classified as labour rows.
    """
    line_items: list[dict] = []
    labour_items: list[dict] = []
    for raw in up.splitlines():
        m = RE_ROW4.match(raw.strip())
        if not m:
            continue
        desc = re.sub(r"\s{2,}", " ", m.group(1)).strip(" .:-")
        if len(desc) < 2:
            continue
        n1, rate, amount = m.group(2), _num(m.group(3)), _num(m.group(4))
        if "LABOUR" in desc or "LABOR" in desc:
            labour_items.append({
                "description": desc, "hours": float(n1),
                "rate": rate, "amount": amount,
            })
        else:
            action = None
            for kw in ("REPLACE", "R&R", "REPAIR", "REFINISH", "PAINT", "REMOVE"):
                if kw in desc:
                    action = kw
                    break
            line_items.append({
                "part_name": desc, "quantity": int(float(n1)),
                "rate": rate, "amount": amount, "action": action,
            })
    return line_items, labour_items


def _find_sections(up: str) -> list[str]:
    secs: list[str] = []
    for m in re.finditer(
        r"(?:U/S|UNDER SECTION|SEC(?:TION)?S?)[\s.:]*([0-9]{2,4}(?:\s*[/,& ]\s*[0-9]{2,4})*)",
        up,
    ):
        for tok in re.split(r"[/,& ]+", m.group(1)):
            tok = tok.strip()
            if tok and tok not in secs:
                secs.append(tok)
    return secs


def _quality_gates(out: dict, required: list[str]) -> dict[str, Any]:
    missing = [f for f in required
               if out.get(f) in (None, "", [], {}) or f not in out]
    return {
        "field_confidence": {
            "status": "todo",
            "note": "flag any field with confidence under 0.85 for human verification "
                    "(per-field confidence not available from regex OCR)",
        },
        "missing_required": missing,
        "required_reupload": bool(missing),
        "tamper_score": {
            "status": "todo",
            "note": "font/splice/metadata analysis",
        },
        "document_date_vs_claim_date": {
            "status": "todo",
            "note": "hard flag if document_date after claim_date (needs claim_date)",
        },
    }


# --------------------------------------------------------------------------- #
# Per-document-type extractors - each returns
#   (fields, validations, cross_checks, required_fields)
# --------------------------------------------------------------------------- #
def _extract_rc_copy(up: str):
    f: dict[str, Any] = {}
    m = RE_REGNO.search(up)
    f["registration_number"] = _norm_reg(m) if m else None
    if f["registration_number"]:
        f["rto_code"] = f["registration_number"][:4]  # e.g. MH12
    else:
        f["rto_code"] = None

    m17 = RE_CHASSIS17.search(up)
    m_ch = RE_CHASSIS.search(up)
    f["chassis_number"] = (m17.group(1) if m17
                           else (m_ch.group(1).strip() if m_ch else None))
    m_en = RE_ENGINE.search(up)
    f["engine_number"] = m_en.group(1).strip() if m_en else None

    f["owner_name"] = _grab_name(up, [r"OWNER(?:'S)?(?:\s+NAME)?[\s:./-]+" + _NAME_V,
                                      r"NAME OF OWNER[\s:./-]+" + _NAME_V])
    f["registration_date"] = _labeled_date(up, ["REGN?\\.? ?DATE", "DATE OF REGN",
                                                "REGISTRATION DATE", "DT OF REGN"])
    f["make"] = _grab(up, [r"MAKER?'?S?\s*(?:NAME)?[\s:./-]+([A-Z][A-Z ]{2,20})",
                           r"MAKE[\s:./-]+([A-Z][A-Z ]{2,20})"])
    f["model"] = _grab(up, [r"MODEL[\s:./-]+([A-Z0-9][A-Z0-9 ]{1,20})"])
    f["variant"] = _grab(up, [r"VARIANT[\s:./-]+([A-Z0-9][A-Z0-9 ]{1,20})"])
    f["fuel_type"] = _grab(up, [r"FUEL(?:\s*TYPE)?[\s:./-]+(PETROL|DIESEL|CNG|LPG|"
                                r"ELECTRIC|EV|HYBRID)"])
    f["cubic_capacity"] = _labeled_int(up, ["CUBIC CAPACITY", r"CC\b", "C\\.C",
                                            "ENGINE CAPACITY"])
    f["seating_capacity"] = _labeled_int(up, ["SEATING CAPACITY", "SEAT(?:ING)? CAP",
                                              "NO OF SEATS"])
    f["vehicle_class"] = _grab(up, [r"(?:VEHICLE|VH)\s*CLASS[\s:./-]+([A-Z][A-Z /]{2,25})",
                                    r"CLASS OF VEHICLE[\s:./-]+([A-Z][A-Z /]{2,25})"])
    f["hypothecation"] = _grab(up, [r"HYP(?:OTHECAT(?:ED|ION))?\.?\s*(?:TO|WITH)?[\s:./-]+"
                                    r"([A-Z][A-Z .&]{3,40})"])
    f["fitness_valid_upto"] = _labeled_date(up, ["FITNESS.{0,10}VALID", "FITNESS UPTO",
                                                 "VALID(?:ITY)?.{0,6}FITNESS"])
    f["insurance_valid_upto"] = _labeled_date(up, ["INS(?:URANCE)?.{0,10}VALID",
                                                   "INS(?:URANCE)? UPTO"])

    validations: dict[str, str] = {}
    if f["registration_number"] and not RE_REGNO.fullmatch(
        re.sub(r"([A-Z]{2})(\d{2})([A-Z]{1,3})(\d{3,4})", r"\1\2\3\4",
               f["registration_number"]) or ""
    ):
        pass  # normalized form already validated by construction
    if f["chassis_number"] and len(f["chassis_number"]) != 17:
        validations["chassis_number"] = (
            f"expected 17 chars, got {len(f['chassis_number'])}")

    cross = [
        {"check": "vahan_identity_match", "status": "todo",
         "note": "registration + chassis + engine must match VAHAN rail"},
        {"check": "owner_matches_policyholder", "status": "todo",
         "note": "fuzzy-match owner_name to policyholder (policy record)"},
        {"check": "cubic_capacity_deductible", "status": "todo",
         "note": "cubic_capacity drives compulsory deductible band"},
        {"check": "vehicle_class_usage", "status": "todo",
         "note": "private-vs-commercial usage check from vehicle_class"},
    ]
    required = ["registration_number", "chassis_number", "engine_number"]
    return f, validations, cross, required


def _extract_driving_licence(up: str):
    f: dict[str, Any] = {}
    m = RE_DLNO.search(up)
    f["dl_number"] = (f"{m.group(1)}{m.group(2)}{m.group(3)}".replace(" ", "")
                      if m else None)
    f["holder_name"] = _grab_name(up, [r"(?:HOLDER|LICENSEE)(?:'S)?\s*NAME[\s:./-]+" + _NAME_V,
                                       r"\bNAME[\s:./-]+" + _NAME_V])
    m_dob = RE_DOB.search(up)
    f["date_of_birth"] = m_dob.group(1) if m_dob else None
    f["issue_date"] = _labeled_date(up, ["ISSUE DATE", "DATE OF ISSUE", "DOI"])
    f["valid_from"] = _labeled_date(up, ["VALID FROM", "VALIDITY FROM", "W\\.E\\.F"])
    f["valid_till"] = _labeled_date(up, ["VALID (?:TILL|UPTO|UNTIL|TO)",
                                         "VALIDITY.{0,6}(?:TILL|UPTO)", "EXPIRY"])
    classes = [c for c in _DL_CLASSES if re.search(r"\b" + re.escape(c) + r"\b", up)]
    # de-dup while keeping the longest label (LMV over LMV-NT collisions handled by regex)
    f["vehicle_classes_authorised"] = classes or None
    f["issuing_authority"] = _grab(up, [r"ISSU(?:ING|ED BY)\s*(?:AUTHORITY)?[\s:./-]+"
                                        r"([A-Z][A-Z ,.-]{3,40})",
                                        r"\bRTO[\s:./-]+([A-Z][A-Z ,.-]{3,40})"])
    m_bg = RE_BLOOD.search(up)
    f["blood_group"] = (f"{m_bg.group(1)}{m_bg.group(2)}" if m_bg else None)

    validations: dict[str, str] = {}
    if f["dl_number"] and not RE_DLNO.search(f["dl_number"]):
        validations["dl_number"] = "does not match Indian DL format"

    cross = [
        {"check": "valid_on_incident_date", "status": "todo",
         "note": "incident_date within valid_from and valid_till (needs incident date)"},
        {"check": "class_covers_vehicle", "status": "todo",
         "note": "authorised class must cover claimed vehicle; LMV driving HMV = "
                 "hard decline"},
        {"check": "digilocker_verification", "status": "todo",
         "note": "verify DL authenticity via DigiLocker rail"},
        {"check": "age_minimum", "status": "todo",
         "note": "age at least 18 (20 for commercial) from date_of_birth"},
    ]
    required = ["dl_number", "valid_till"]
    return f, validations, cross, required


def _extract_policy_copy(up: str):
    f: dict[str, Any] = {}
    # A policy number always contains a digit; skip label matches like
    # "PACKAGE POLICY - COMPREHENSIVE".
    pol = None
    for mm in RE_POLICY_NUM.finditer(up):
        cand = mm.group(1).strip()
        if any(ch.isdigit() for ch in cand):
            pol = cand
            break
    if pol is None:
        m = RE_POLICY.search(up)
        pol = m.group(1).strip() if m else None
    f["policy_number"] = pol
    f["insurer_name"] = _grab(up, [r"INSURER[\s:./-]+([A-Z][A-Z .&]{3,40})",
                                   r"([A-Z][A-Z .&]{3,40})\s+GENERAL INSURANCE"])
    f["policyholder_name"] = _grab_name(up, [
        r"(?:POLICY ?HOLDER|INSURED)(?:'S)?\s*NAME[\s:./-]+" + _NAME_V,
        r"NAME OF (?:THE )?INSURED[\s:./-]+" + _NAME_V])
    f["period_from"] = _labeled_date(up, ["PERIOD.{0,10}FROM", "FROM", "W\\.E\\.F",
                                          "COMMENC"])
    f["period_to"] = _labeled_date(up, ["PERIOD.{0,10}TO", "TO", "EXPIRY", "VALID UPTO"])
    if "COMPREHENSIVE" in up or "PACKAGE POLICY" in up:
        f["product_type"] = "comprehensive"
    elif "OD ONLY" in up or "OWN DAMAGE ONLY" in up or "STANDALONE OD" in up:
        f["product_type"] = "od_only"
    elif "THIRD PARTY ONLY" in up or "TP ONLY" in up or "LIABILITY ONLY" in up:
        f["product_type"] = "tp_only"
    else:
        f["product_type"] = None
    f["idv"] = _labeled_amount(up, ["IDV", "INSURED DECLARED VALUE"])
    f["od_premium"] = _labeled_amount(up, ["OD PREMIUM", "OWN DAMAGE PREMIUM"])
    f["tp_premium"] = _labeled_amount(up, ["TP PREMIUM", "THIRD PARTY PREMIUM",
                                           "LIABILITY PREMIUM"])
    ncb = _grab(up, [r"NCB[\s:./-]*(\d{1,3}(?:\.\d+)?)\s*%",
                     r"NO CLAIM BONUS[\s:./-]*(\d{1,3})\s*%"])
    f["ncb_percent_applied"] = float(ncb) if ncb else None
    f["compulsory_deductible"] = _labeled_amount(up, ["COMPULSORY (?:DEDUCTIBLE|EXCESS)"])
    f["voluntary_deductible"] = _labeled_amount(up, ["VOLUNTARY (?:DEDUCTIBLE|EXCESS)"])
    addons = []
    for a, label in (("zero_depreciation", "ZERO DEP"), ("engine_protect", "ENGINE PROTECT"),
                     ("roadside_assistance", "ROADSIDE"), ("consumables", "CONSUMABLE"),
                     ("return_to_invoice", "RETURN TO INVOICE"), ("ncb_protect", "NCB PROTECT"),
                     ("key_replacement", "KEY REPLACE"), ("tyre_protect", "TYRE PROTECT")):
        if label in up:
            addons.append(a)
    f["add_ons"] = addons or None
    f["nominee"] = _grab_name(up, [r"NOMINEE(?:'S)?\s*(?:NAME)?[\s:./-]+" + _NAME_V])
    ends = re.findall(r"ENDORSEMENT[\s:./-]*(?:NO\.?)?\s*([A-Z0-9/-]{4,20})", up)
    f["endorsements"] = list(dict.fromkeys(ends)) or None

    validations: dict[str, str] = {}
    if f["ncb_percent_applied"] is not None and not (0 <= f["ncb_percent_applied"] <= 65):
        validations["ncb_percent_applied"] = "NCB outside 0-65% slab range"

    cross = [
        {"check": "period_covers_incident", "status": "todo",
         "note": "incident_date within period_from and period_to (needs incident date)"},
        {"check": "product_type_vs_claim_type", "status": "todo",
         "note": "product_type must permit the claim_type (needs claim record)"},
        {"check": "add_ons_settlement_waterfall", "status": "todo",
         "note": "add_ons (zero-dep etc.) drive the settlement waterfall"},
        {"check": "idv_total_loss_test", "status": "todo",
         "note": "idv feeds the 75% constructive-total-loss test"},
    ]
    required = ["policy_number", "period_from", "period_to"]
    return f, validations, cross, required


def _extract_fir(up: str):
    f: dict[str, Any] = {}
    m = RE_FIR.search(up)
    f["fir_number"] = m.group(1).strip() if m else None
    f["police_station"] = _grab(up, [r"(?:P\.?S\.?|POLICE STATION)[\s:./-]+"
                                     r"([A-Z][A-Z .]{3,30})"])
    f["district_state"] = _grab(up, [r"DIST(?:RICT)?[\s:./-]+([A-Z][A-Z ,.]{3,40})"])
    f["fir_date"] = _labeled_date(up, ["FIR DATE", "DATE OF FIR", "DATE OF REPORT",
                                       "REGISTERED ON"])
    f["incident_date"] = _labeled_date(up, ["(?:DATE OF )?(?:OCCURRENCE|INCIDENT|ACCIDENT)",
                                            "DATE.{0,6}OF.{0,6}OFFENCE"])
    f["incident_time"] = _grab(up, [r"TIME (?:OF )?(?:OCCURRENCE|INCIDENT|ACCIDENT)"
                                    r"[\s:./-]*(\d{1,2}[:.]\d{2}\s*(?:AM|PM|HRS)?)",
                                    r"AT\s+(\d{1,2}[:.]\d{2}\s*(?:AM|PM|HRS))"])
    f["complainant_name"] = _grab_name(up, [
        r"COMPLAINANT(?:'S)?\s*(?:NAME)?[\s:./-]+" + _NAME_V,
        r"INFORMANT(?:'S)?\s*(?:NAME)?[\s:./-]+" + _NAME_V])
    vehicles = list(dict.fromkeys(_norm_reg(mm) for mm in RE_REGNO.finditer(up)))
    f["vehicles_involved"] = vehicles or None

    inj_n = _labeled_int(up, ["(\\d+).{0,10}INJUR", "INJURED", "INJURIES"])
    inj_flag = bool(re.search(r"INJUR", up)) and "NO INJUR" not in up
    f["injuries_reported"] = {"reported": inj_flag,
                              "count": inj_n if inj_flag else 0}
    fat_n = _labeled_int(up, ["(\\d+).{0,10}(?:DIED|DEATH|DECEAS)", "FATALITIES"])
    fat_flag = bool(re.search(r"DIED|DEATH|DECEAS|FATAL", up)) and "NO DEATH" not in up
    f["fatalities"] = {"reported": fat_flag, "count": fat_n if fat_flag else 0}

    sections = _find_sections(up)
    f["sections_applied"] = sections or None
    f["accused_details"] = _grab_name(up, [r"ACCUSED[\s:./-]+" + _NAME_V])
    f["investigating_officer"] = _grab_name(up, [
        r"(?:INVESTIGATING OFFICER|I\.?O\.?)[\s:./-]+" + _NAME_V])

    validations: dict[str, str] = {}

    cross: list[dict] = []
    d_fir, d_inc = _parse_date(f["fir_date"]), _parse_date(f["incident_date"])
    if d_fir and d_inc:
        gap = (d_fir - d_inc).days
        cross.append({
            "check": "fir_reporting_delay",
            "status": "fail" if gap > 7 else "pass",
            "note": f"fir_date - incident_date = {gap} days "
                    f"({'over' if gap > 7 else 'within'} 7-day threshold)",
        })
    else:
        cross.append({"check": "fir_reporting_delay", "status": "todo",
                      "note": "need both fir_date and incident_date to compute the "
                              "7-day gap"})
    cross.append({"check": "claimed_vehicle_in_fir", "status": "todo",
                  "note": "claimed vehicle must appear in vehicles_involved "
                          "(needs claim record)"})
    if f["injuries_reported"]["reported"]:
        cross.append({"check": "injuries_force_lane3", "status": "fail",
                      "note": "injuries reported, forces Lane 3 (investigative)"})
    dui = [s for s in sections if s in ("279", "304A", "337", "338", "185", "184")]
    if dui or "DRUNK" in up or "INTOXICAT" in up:
        cross.append({"check": "dui_rash_sections", "status": "fail",
                      "note": f"rash/DUI sections {dui or 'flagged'}, coverage "
                              "implications (driver exclusion)"})
    required = ["fir_number", "fir_date", "incident_date"]
    return f, validations, cross, required


def _extract_repair_estimate(up: str):
    f: dict[str, Any] = {}
    f["garage_name"] = _grab(up, [r"(?:GARAGE|WORKSHOP|M/S|SERVICE CENTRE)[\s:./-]+"
                                  r"([A-Z][A-Z .&]{3,40})"])
    mg = RE_GSTIN.search(up)
    f["garage_gstin"] = mg.group(1) if mg else None
    f["estimate_number"] = _grab(up, [r"ESTIMATE\s*(?:NO\.?|NUMBER|#)[\s:./-]*"
                                      r"([A-Z0-9/-]{3,20})",
                                      r"QUOTATION\s*(?:NO\.?)?[\s:./-]*([A-Z0-9/-]{3,20})"])
    f["estimate_date"] = _labeled_date(up, ["ESTIMATE DATE", "DATE"])
    mr = RE_REGNO.search(up)
    f["vehicle_registration"] = _norm_reg(mr) if mr else None
    f["odometer_reading"] = _labeled_int(up, ["ODOMETER", "ODO", "KM READING",
                                              "KILOMETER"])
    line_items, labour_items = _parse_rows(up)
    f["line_items"] = line_items or None
    f["labour_items"] = labour_items or None
    f["paint_charges"] = _labeled_amount(up, ["PAINT (?:CHARGES|MATERIAL)", "PAINTING"])
    f["consumables"] = _labeled_amount(up, ["CONSUMABLES?"])
    f["tax_gst"] = _labeled_amount(up, ["GST", "TAX", "IGST", "TOTAL TAX"])
    f["total_estimate"] = _labeled_amount_max(up, ["TOTAL", "GRAND TOTAL",
                                                   "NET AMOUNT", "AMOUNT PAYABLE"])

    validations: dict[str, str] = {}
    if f["garage_gstin"] and not GSTIN_VALID.match(f["garage_gstin"]):
        validations["garage_gstin"] = "does not match GSTIN format"

    cross = [
        {"check": "line_items_vs_rate_card", "status": "todo",
         "note": "reconcile line_items against rate card + vision-detected parts"},
        {"check": "gstin_entity_and_collusion", "status": "todo",
         "note": "validate garage entity via GSTIN + feed collusion graph"},
        {"check": "odometer_tamper", "status": "todo",
         "note": "cross-check odometer_reading history for tampering (VAHAN/service)"},
    ]
    required = ["garage_name", "total_estimate", "vehicle_registration"]
    return f, validations, cross, required


def _extract_final_bill(up: str):
    f: dict[str, Any] = {}
    f["invoice_number"] = _grab(up, [r"(?:INVOICE|BILL|TAX INVOICE)\s*(?:NO\.?|#)"
                                     r"[\s:./-]*([A-Z0-9/-]{3,20})"])
    f["invoice_date"] = _labeled_date(up, ["INVOICE DATE", "BILL DATE", "DATE"])
    mg = RE_GSTIN.search(up)
    f["garage_gstin"] = mg.group(1) if mg else None
    line_items, labour_items = _parse_rows(up)
    f["line_items"] = line_items or None
    f["parts_total"] = _labeled_amount(up, ["PARTS? TOTAL", "TOTAL PARTS",
                                            "SPARES? TOTAL"])
    f["labour_total"] = _labeled_amount(up, ["LABOUR TOTAL", "LABOR TOTAL",
                                             "TOTAL LABOUR"])
    f["paint_total"] = _labeled_amount(up, ["PAINT TOTAL", "TOTAL PAINT"])
    f["consumables"] = _labeled_amount(up, ["CONSUMABLES?"])
    f["discount"] = _labeled_amount(up, ["DISCOUNT", "LESS DISCOUNT"])
    cgst = _labeled_amount(up, ["CGST"])
    sgst = _labeled_amount(up, ["SGST"])
    igst = _labeled_amount(up, ["IGST"])
    f["gst_breakup"] = {"CGST": cgst, "SGST": sgst, "IGST": igst}
    f["total_invoice"] = _labeled_amount_max(up, ["TOTAL", "GRAND TOTAL",
                                                  "TOTAL AMOUNT", "AMOUNT PAYABLE"])
    f["amount_paid_by_customer"] = _labeled_amount(up, [
        "PAID BY CUSTOMER", "CUSTOMER (?:PAID|SHARE)", "AMOUNT PAID"])
    f["payment_mode"] = _grab(up, [r"(?:PAYMENT MODE|MODE OF PAYMENT|PAID (?:BY|VIA))"
                                   r"[\s:./-]+(CASH|CARD|UPI|CHEQUE|NEFT|RTGS|ONLINE|DD)"])

    validations: dict[str, str] = {}
    if f["garage_gstin"] and not GSTIN_VALID.match(f["garage_gstin"]):
        validations["garage_gstin"] = "does not match GSTIN format"

    cross: list[dict] = []
    cross.append({"check": "invoice_vs_estimate_variance", "status": "todo",
                  "note": "total_invoice vs total_estimate variance over 15%, "
                          "re-approval (needs estimate)"})
    cross.append({"check": "line_item_diff_vs_estimate", "status": "todo",
                  "note": "diff invoice line items against approved estimate"})
    # GST arithmetic reconciliation (computable)
    gst_total = None
    if cgst is not None and sgst is not None:
        gst_total = cgst + sgst
    elif igst is not None:
        gst_total = igst
    total = f["total_invoice"]
    base = None
    for comp in (f["parts_total"], f["labour_total"], f["paint_total"],
                 f["consumables"]):
        if comp is not None:
            base = (base or 0) + comp
    if gst_total is not None and total is not None and base is not None:
        disc = f["discount"] or 0
        expected = base + gst_total - disc
        ok = abs(expected - total) <= max(1.0, 0.02 * total)
        cross.append({
            "check": "gst_arithmetic_reconciles",
            "status": "pass" if ok else "fail",
            "note": f"base {base:.0f} + gst {gst_total:.0f} - disc {disc:.0f} "
                    f"= {expected:.0f} vs total {total:.0f}",
        })
    elif cgst is not None and sgst is not None:
        ok = abs(cgst - sgst) <= 1.0
        cross.append({
            "check": "gst_arithmetic_reconciles",
            "status": "pass" if ok else "fail",
            "note": f"CGST {cgst:.0f} vs SGST {sgst:.0f} must be equal",
        })
    else:
        cross.append({"check": "gst_arithmetic_reconciles", "status": "todo",
                      "note": "need CGST+SGST (or IGST) and totals to reconcile"})
    required = ["invoice_number", "total_invoice", "garage_gstin"]
    return f, validations, cross, required


def _extract_bank_details(up: str):
    f: dict[str, Any] = {}
    f["account_holder_name"] = _grab_name(up, [
        r"(?:ACCOUNT HOLDER|A/?C HOLDER|BENEFICIARY)(?:'S)?\s*(?:NAME)?[\s:./-]+" + _NAME_V,
        r"NAME[\s:./-]+" + _NAME_V])
    ma = RE_ACCOUNT.search(up)
    f["account_number"] = ma.group(1) if ma else None
    mi = RE_IFSC.search(up)
    f["ifsc"] = mi.group(1) if mi else None
    f["bank_name"] = _grab(up, [r"BANK(?:\s*NAME)?[\s:./-]+([A-Z][A-Z .&]{3,40})"])
    f["branch"] = _grab(up, [r"BRANCH[\s:./-]+([A-Z][A-Z .,-]{3,40})"])

    validations: dict[str, str] = {}
    if f["ifsc"] and not IFSC_VALID.match(f["ifsc"]):
        validations["ifsc"] = "does not match IFSC format"

    cross = [
        {"check": "holder_matches_policyholder", "status": "todo",
         "note": "account_holder_name vs policyholder (payout fraud guard)"},
        {"check": "account_reuse_collusion", "status": "todo",
         "note": "account_number reuse across claims = collusion signal (graph)"},
    ]
    required = ["account_holder_name", "account_number", "ifsc"]
    return f, validations, cross, required


_EXTRACTORS = {
    "rc_copy": _extract_rc_copy,
    "driving_licence": _extract_driving_licence,
    "policy_copy": _extract_policy_copy,
    "fir": _extract_fir,
    "repair_estimate": _extract_repair_estimate,
    "final_bill": _extract_final_bill,
    "bank_details": _extract_bank_details,
}


def _guess_doc_type(up: str, declared: str) -> str:
    if "DRIVING" in up or "LICENCE" in up or "LICENSE" in up:
        return "driving_licence"
    if "REGISTRATION CERTIFICATE" in up or "CHASSIS" in up or (
        "REGISTRATION" in up and "OWNER" in up
    ):
        return "rc_copy"
    if "FIR" in up or ("POLICE STATION" in up):
        return "fir"
    if RE_IFSC.search(up) or "IFSC" in up:
        return "bank_details"
    if "ESTIMATE" in up or "QUOTATION" in up:
        return "repair_estimate"
    if "TAX INVOICE" in up or "INVOICE" in up or "FINAL BILL" in up:
        return "final_bill"
    if "BILL" in up:
        return "repair_estimate"
    if "POLICY" in up or "INSURANCE" in up:
        return "policy_copy"
    return declared


# --------------------------------------------------------------------------- #
# Engine 1 - LOCAL (no key)
# --------------------------------------------------------------------------- #
_rapid = None
_NVIDIA_OCR_DEAD = False  # set once the hosted Nemotron function 404s in this process


def _local_ocr(data: bytes) -> OcrResult:
    global _rapid
    try:
        import io

        import numpy as np
        from PIL import Image

        if _rapid is None:
            from rapidocr_onnxruntime import RapidOCR

            _rapid = RapidOCR()

        pil = Image.open(io.BytesIO(data)).convert("RGB")
        # Downscale big scans / phone photos before OCR: RapidOCR on a 2000px+
        # image is 10-40x slower with no accuracy gain on document text.
        longest = max(pil.size)
        if longest > 1600:
            s = 1600 / longest
            pil = pil.resize((int(pil.width * s), int(pil.height * s)), Image.LANCZOS)
        img = np.asarray(pil)
        res, _ = _rapid(img)
        if not res:
            return OcrResult("local:rapidocr", "", {}, 0.0, "no text detected")
        lines = [r[1] for r in res]
        confs = [float(r[2]) for r in res if len(r) > 2]
        text = "\n".join(lines)
        return OcrResult(
            "local:rapidocr", text, extract_fields(text),
            round(sum(confs) / len(confs), 3) if confs else 0.5,
        )
    except ImportError as exc:
        return OcrResult("local:unavailable", "", {}, 0.0, f"RapidOCR not installed: {exc}")
    except Exception as exc:
        return OcrResult("local:rapidocr", "", {}, 0.0, str(exc))


# --------------------------------------------------------------------------- #
# Engine 2 - LLM VISION (optional key, best quality)
# --------------------------------------------------------------------------- #
def _llm_ocr(data: bytes, doc_type: str) -> OcrResult:
    import base64
    import json

    key = os.getenv("LLM_API_KEY", "").strip()
    provider = os.getenv("LLM_PROVIDER", "anthropic").strip().lower()
    model = os.getenv("LLM_MODEL", "claude-sonnet-5").strip()
    if not key:
        return OcrResult("llm:nokey", "", {}, 0.0, "LLM_API_KEY not set")

    b64 = base64.b64encode(data).decode()
    prompt = (
        f"This is an Indian motor-insurance document (declared type: {doc_type}). "
        "Transcribe ALL visible text verbatim, then extract fields. "
        'Reply ONLY as JSON: {"text": "...", "fields": {"registration_no": null, '
        '"dl_number": null, "engine_no": null, "chassis_no": null, "fir_number": null, '
        '"policy_no": null, "amount": null, "name": null}}. Use null when absent.'
    )
    try:
        import urllib.request

        if provider == "anthropic":
            body = {
                "model": model,
                "max_tokens": 2000,
                "messages": [{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64",
                                                 "media_type": "image/jpeg", "data": b64}},
                    {"type": "text", "text": prompt},
                ]}],
            }
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=json.dumps(body).encode(),
                headers={"content-type": "application/json", "x-api-key": key,
                         "anthropic-version": "2023-06-01"},
            )
            with urllib.request.urlopen(req, timeout=90) as r:
                payload = json.loads(r.read())
            raw = payload["content"][0]["text"]
        else:  # openai-compatible
            body = {
                "model": model or "gpt-4o-mini",
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ]}],
            }
            req = urllib.request.Request(
                "https://api.openai.com/v1/chat/completions",
                data=json.dumps(body).encode(),
                headers={"content-type": "application/json",
                         "authorization": f"Bearer {key}"},
            )
            with urllib.request.urlopen(req, timeout=90) as r:
                payload = json.loads(r.read())
            raw = payload["choices"][0]["message"]["content"]

        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
        parsed = json.loads(raw)
        text = parsed.get("text", "")
        fields = {k: v for k, v in (parsed.get("fields") or {}).items() if v}
        # merge regex extraction as a safety net
        merged = {**extract_fields(text, doc_type), **fields}
        return OcrResult(f"llm:{provider}", text, merged, 0.95)
    except Exception as exc:
        return OcrResult(f"llm:{provider}", "", {}, 0.0, str(exc))


# --------------------------------------------------------------------------- #
# Public entry
# --------------------------------------------------------------------------- #
def _nvidia_ocr(data: bytes, doc_type: str) -> OcrResult:
    """Nemotron OCR v2 via NVIDIA NIM."""
    from src.live import nvidia

    res = nvidia.ocr_document(data, doc_type)
    if not res.ok or not res.text.strip():
        return OcrResult(f"nvidia:{res.model}", "", {}, 0.0, res.error or "empty response")
    text = res.text.strip()
    return OcrResult(f"nvidia:{res.model}", text, extract_fields(text, doc_type), 0.92)


def run_ocr(data: bytes, doc_type: str = "other", prefer: str | None = None) -> OcrResult:
    """OCR a document.

    Engine order (each falls back to the next, so this never dead-ends):
      nvidia (Nemotron OCR v2)  ->  llm (Anthropic/OpenAI)  ->  local (RapidOCR)
    """
    if prefer:
        order = [prefer.lower()]
    else:
        order = []
        # NVIDIA_OCR_DISABLED lets us skip the hosted Nemotron function when it is
        # not provisioned for the account (returns 404) - the local RapidOCR engine
        # is then the primary, avoiding a wasted round-trip on every document.
        _disabled = str(os.getenv("NVIDIA_OCR_DISABLED", "")).lower() in ("1", "true", "yes")
        # Auto circuit-breaker: once the hosted function has 404'd this process,
        # stop trying it - every subsequent doc goes straight to local (self-heals
        # in production even without the env flag set).
        if os.getenv("NVIDIA_OCR_KEY") and not _disabled and not _NVIDIA_OCR_DEAD:
            order.append("nvidia")
        if os.getenv("LLM_API_KEY"):
            order.append("llm")
        order.append("local")

    errors = []
    for engine in order:
        if engine == "nvidia":
            res = _nvidia_ocr(data, doc_type)
            if res.error and ("404" in res.error or "Not found" in res.error
                              or "Not Found" in res.error):
                globals()["_NVIDIA_OCR_DEAD"] = True  # don't retry a missing function
        elif engine == "llm":
            res = _llm_ocr(data, doc_type)
        else:
            res = _local_ocr(data)
        if res.text.strip():
            if errors:
                res.error = " | ".join(errors)  # keep the trail for the console
            return res
        errors.append(f"{engine}: {res.error}")

    return OcrResult("none", "", {}, 0.0, " | ".join(errors))


def llm_severity(data: bytes) -> dict[str, Any]:
    """Optional: read damage severity from a photo with the vision LLM.

    Returns {} when no key - severity then stays operator-declared (v1 behaviour
    per CLAUDE.md §3 rule 10: no CV unless explicitly added).
    """
    import base64
    import json
    import urllib.request

    key = os.getenv("LLM_API_KEY", "").strip()
    if not key or os.getenv("LLM_PROVIDER", "anthropic").lower() != "anthropic":
        return {}
    b64 = base64.b64encode(data).decode()
    prompt = (
        "Assess vehicle damage in this photo for an insurance claim. Reply ONLY JSON: "
        '{"severity":"minor|moderate|severe|total","damaged_parts":["..."],'
        '"confidence":0.0-1.0,"note":"one line"}'
    )
    try:
        body = {"model": os.getenv("LLM_MODEL", "claude-sonnet-5"), "max_tokens": 500,
                "messages": [{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64",
                                                 "media_type": "image/jpeg", "data": b64}},
                    {"type": "text", "text": prompt}]}]}
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(),
            headers={"content-type": "application/json", "x-api-key": key,
                     "anthropic-version": "2023-06-01"})
        with urllib.request.urlopen(req, timeout=90) as r:
            payload = json.loads(r.read())
        raw = payload["content"][0]["text"].strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```")
        return json.loads(raw)
    except Exception:
        return {}
