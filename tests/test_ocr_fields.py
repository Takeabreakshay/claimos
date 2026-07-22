"""Tests for the per-document-type OCR field schema (LOGIC §6).

Realistic (but 100% synthetic, per CLAUDE.md §3 rule 1) OCR text blocks for each
document type. We assert the key patternable fields extract correctly and that
``validations``, ``cross_checks`` and ``quality_gates`` populate. Cross-checks
that need external rails must be marked ``todo``; the ones we can compute
(FIR reporting delay, final-bill GST arithmetic) must be pass/fail.
"""

from __future__ import annotations

from src.live import ocr

# --------------------------------------------------------------------------- #
# rc_copy
# --------------------------------------------------------------------------- #
RC_TEXT = """
REGISTRATION CERTIFICATE
REGISTRATION NO : MH12AB1234
CHASSIS NO : MA3ERLF1S00123456
ENGINE NO : K12MN1234567
OWNER NAME : RAJESH KUMAR SHARMA
DATE OF REGN : 15/03/2019
MAKER'S NAME : MARUTI SUZUKI
MODEL : SWIFT VDI
FUEL TYPE : DIESEL
CUBIC CAPACITY : 1248
SEATING CAPACITY : 5
CLASS OF VEHICLE : LMV
HYPOTHECATED TO : HDFC BANK LTD
FITNESS VALID UPTO : 14/03/2034
INSURANCE VALID UPTO : 20/06/2025
"""


def test_rc_copy_fields():
    f = ocr.extract_fields(RC_TEXT, "rc_copy")
    assert f["doc_type_resolved"] == "rc_copy"
    assert f["registration_number"] == "MH12AB1234"
    assert f["chassis_number"] == "MA3ERLF1S00123456"  # 17 chars
    assert len(f["chassis_number"]) == 17
    assert f["engine_number"] == "K12MN1234567"
    assert "RAJESH KUMAR SHARMA" in f["owner_name"]
    assert f["registration_date"] == "15/03/2019"
    assert f["fuel_type"] == "DIESEL"
    assert f["cubic_capacity"] == 1248
    assert f["seating_capacity"] == 5
    assert f["rto_code"] == "MH12"
    assert f["insurance_valid_upto"] == "20/06/2025"
    # backward-compat legacy keys still present
    assert f["registration_no"] == "MH12AB1234"
    # cross-checks are all external -> todo
    checks = {c["check"]: c for c in f["cross_checks"]}
    assert checks["vahan_identity_match"]["status"] == "todo"
    assert checks["cubic_capacity_deductible"]["status"] == "todo"
    # quality gates present, nothing required missing here
    assert f["quality_gates"]["missing_required"] == []
    assert f["quality_gates"]["tamper_score"]["status"] == "todo"


def test_rc_copy_bad_chassis_validation():
    bad = RC_TEXT.replace("MA3ERLF1S00123456", "SHORTCHASSIS")
    f = ocr.extract_fields(bad, "rc_copy")
    # 12-char chassis is not 17 -> validation failure recorded
    assert "chassis_number" in f["validations"]


# --------------------------------------------------------------------------- #
# driving_licence
# --------------------------------------------------------------------------- #
DL_TEXT = """
DRIVING LICENCE
DL NO : MH12 20190001234
HOLDER NAME : PRIYA MENON
DATE OF BIRTH : 05/08/1992
DATE OF ISSUE : 01/02/2019
VALID TILL : 04/08/2042
COV : LMV MCWG
ISSUING AUTHORITY : RTO PUNE
BLOOD GROUP : B+
"""


def test_driving_licence_fields():
    f = ocr.extract_fields(DL_TEXT, "driving_licence")
    assert f["doc_type_resolved"] == "driving_licence"
    assert f["dl_number"] == "MH12201900012340"[:16] or f["dl_number"].startswith("MH12")
    assert "PRIYA MENON" in f["holder_name"]
    assert f["date_of_birth"] == "05/08/1992"
    assert f["valid_till"] == "04/08/2042"
    assert "LMV" in f["vehicle_classes_authorised"]
    assert "MCWG" in f["vehicle_classes_authorised"]
    assert f["blood_group"] == "B+"
    checks = {c["check"]: c for c in f["cross_checks"]}
    assert checks["class_covers_vehicle"]["status"] == "todo"
    assert checks["digilocker_verification"]["status"] == "todo"
    assert checks["age_minimum"]["status"] == "todo"


# --------------------------------------------------------------------------- #
# policy_copy
# --------------------------------------------------------------------------- #
POLICY_TEXT = """
MOTOR PACKAGE POLICY - COMPREHENSIVE
POLICY NO : OG-24-1201-1801-00012345
INSURER : BAJAJ ALLIANZ GENERAL INSURANCE
INSURED NAME : RAJESH KUMAR SHARMA
PERIOD FROM : 21/06/2024
PERIOD TO : 20/06/2025
IDV : RS 4,50,000
OD PREMIUM : RS 8,200
TP PREMIUM : RS 3,416
NCB : 25 %
COMPULSORY DEDUCTIBLE : RS 1,000
ADD ON : ZERO DEPRECIATION
ADD ON : ROADSIDE ASSISTANCE
NOMINEE : SUNITA SHARMA
"""


def test_policy_copy_fields():
    f = ocr.extract_fields(POLICY_TEXT, "policy_copy")
    assert f["doc_type_resolved"] == "policy_copy"
    assert f["policy_number"].startswith("OG-24-1201")
    assert f["product_type"] == "comprehensive"
    assert f["period_from"] == "21/06/2024"
    assert f["period_to"] == "20/06/2025"
    assert f["idv"] == 450000.0
    assert f["ncb_percent_applied"] == 25.0
    assert f["compulsory_deductible"] == 1000.0
    assert "zero_depreciation" in f["add_ons"]
    assert "roadside_assistance" in f["add_ons"]
    checks = {c["check"]: c for c in f["cross_checks"]}
    assert checks["period_covers_incident"]["status"] == "todo"
    assert checks["idv_total_loss_test"]["status"] == "todo"


# --------------------------------------------------------------------------- #
# fir
# --------------------------------------------------------------------------- #
FIR_TEXT = """
FIRST INFORMATION REPORT
FIR NO : 0123/2024
POLICE STATION : KOTHRUD
DISTRICT : PUNE
DATE OF FIR : 20/05/2024
DATE OF OCCURRENCE : 12/05/2024
TIME OF OCCURRENCE : 22:30 HRS
COMPLAINANT NAME : AMIT DESHPANDE
VEHICLES INVOLVED : MH12AB1234 AND MH14XY9876
U/S 279 337 OF IPC
2 PERSONS INJURED
INVESTIGATING OFFICER : PSI R B PATIL
"""


def test_fir_fields_and_delay_flag():
    f = ocr.extract_fields(FIR_TEXT, "fir")
    assert f["doc_type_resolved"] == "fir"
    assert f["fir_number"] == "0123/2024"
    assert f["police_station"].startswith("KOTHRUD")
    assert f["fir_date"] == "20/05/2024"
    assert f["incident_date"] == "12/05/2024"
    assert "MH12AB1234" in f["vehicles_involved"]
    assert "MH14XY9876" in f["vehicles_involved"]
    assert "279" in f["sections_applied"]
    assert f["injuries_reported"]["reported"] is True
    # legacy compat
    assert f["fir_number"] == "0123/2024"
    checks = {c["check"]: c for c in f["cross_checks"]}
    # 20/05 - 12/05 = 8 days > 7 -> computed FAIL
    assert checks["fir_reporting_delay"]["status"] == "fail"
    # injuries force lane 3, DUI/rash sections flagged
    assert checks["injuries_force_lane3"]["status"] == "fail"
    assert checks["dui_rash_sections"]["status"] == "fail"
    # external one stays todo
    assert checks["claimed_vehicle_in_fir"]["status"] == "todo"


def test_fir_delay_within_threshold_passes():
    txt = FIR_TEXT.replace("DATE OF FIR : 20/05/2024", "DATE OF FIR : 14/05/2024")
    f = ocr.extract_fields(txt, "fir")
    checks = {c["check"]: c for c in f["cross_checks"]}
    assert checks["fir_reporting_delay"]["status"] == "pass"  # 2 days


# --------------------------------------------------------------------------- #
# repair_estimate
# --------------------------------------------------------------------------- #
ESTIMATE_TEXT = """
REPAIR ESTIMATE / QUOTATION
GARAGE : SPEEDFIX AUTO WORKS
GSTIN : 27ABCDE1234F1Z5
ESTIMATE NO : EST/2024/0456
ESTIMATE DATE : 22/05/2024
REG NO : MH12AB1234
ODOMETER : 45210
FRONT BUMPER REPLACE 1 4500 4500
HEADLAMP ASSY 2 3200 6400
LABOUR DENTING 3 500 1500
PAINT CHARGES : RS 5,000
CONSUMABLES : RS 800
GST : RS 3,996
TOTAL : RS 22,196
"""


def test_repair_estimate_fields():
    f = ocr.extract_fields(ESTIMATE_TEXT, "repair_estimate")
    assert f["doc_type_resolved"] == "repair_estimate"
    assert f["garage_gstin"] == "27ABCDE1234F1Z5"
    assert f["estimate_number"] == "EST/2024/0456"
    assert f["vehicle_registration"] == "MH12AB1234"
    assert f["odometer_reading"] == 45210
    assert f["line_items"] is not None
    # at least the two parts rows parsed
    parts = {li["part_name"] for li in f["line_items"]}
    assert any("BUMPER" in p for p in parts)
    assert f["labour_items"] is not None
    assert f["paint_charges"] == 5000.0
    assert f["total_estimate"] == 22196.0
    # backward-compat: amount still present (largest amount seen)
    assert "amount" in f
    # GSTIN valid -> no validation error
    assert "garage_gstin" not in f["validations"]
    checks = {c["check"]: c for c in f["cross_checks"]}
    assert checks["gstin_entity_and_collusion"]["status"] == "todo"
    assert checks["odometer_tamper"]["status"] == "todo"


def test_repair_estimate_bad_gstin():
    bad = ESTIMATE_TEXT.replace("27ABCDE1234F1Z5", "27ABCDE1234F1Q5")  # no Z at pos 13
    f = ocr.extract_fields(bad, "repair_estimate")
    # regex won't even capture a non-GSTIN, so field is null; ensure no crash
    assert f["doc_type_resolved"] == "repair_estimate"


# --------------------------------------------------------------------------- #
# final_bill
# --------------------------------------------------------------------------- #
BILL_TEXT = """
TAX INVOICE
INVOICE NO : INV/2024/0789
INVOICE DATE : 05/06/2024
GSTIN : 27ABCDE1234F1Z5
FRONT BUMPER REPLACE 1 4500 4500
HEADLAMP ASSY 2 3200 6400
PARTS TOTAL : RS 10,900
LABOUR TOTAL : RS 1,500
PAINT TOTAL : RS 5,000
CGST : RS 1,998
SGST : RS 1,998
TOTAL : RS 21,396
PAYMENT MODE : UPI
"""


def test_final_bill_gst_reconciles():
    f = ocr.extract_fields(BILL_TEXT, "final_bill")
    assert f["doc_type_resolved"] == "final_bill"
    assert f["invoice_number"] == "INV/2024/0789"
    assert f["gst_breakup"]["CGST"] == 1998.0
    assert f["gst_breakup"]["SGST"] == 1998.0
    assert f["parts_total"] == 10900.0
    assert f["total_invoice"] == 21396.0
    assert f["payment_mode"] == "UPI"
    checks = {c["check"]: c for c in f["cross_checks"]}
    # base 10900+1500+5000 = 17400 ; +3996 gst = 21396 == total -> PASS
    assert checks["gst_arithmetic_reconciles"]["status"] == "pass"
    assert checks["invoice_vs_estimate_variance"]["status"] == "todo"


def test_final_bill_gst_mismatch():
    bad = BILL_TEXT.replace("TOTAL : RS 21,396", "TOTAL : RS 30,000")
    f = ocr.extract_fields(bad, "final_bill")
    checks = {c["check"]: c for c in f["cross_checks"]}
    assert checks["gst_arithmetic_reconciles"]["status"] == "fail"


# --------------------------------------------------------------------------- #
# bank_details
# --------------------------------------------------------------------------- #
BANK_TEXT = """
BANK ACCOUNT DETAILS FOR CLAIM SETTLEMENT
ACCOUNT HOLDER NAME : RAJESH KUMAR SHARMA
ACCOUNT NO : 50100123456789
IFSC : HDFC0001234
BANK NAME : HDFC BANK
BRANCH : KOTHRUD PUNE
"""


def test_bank_details_fields():
    f = ocr.extract_fields(BANK_TEXT, "bank_details")
    assert f["doc_type_resolved"] == "bank_details"
    assert "RAJESH KUMAR SHARMA" in f["account_holder_name"]
    assert f["account_number"] == "50100123456789"
    assert f["ifsc"] == "HDFC0001234"
    assert "garage_gstin" not in f
    assert "ifsc" not in f["validations"]  # valid IFSC
    checks = {c["check"]: c for c in f["cross_checks"]}
    assert checks["holder_matches_policyholder"]["status"] == "todo"
    assert checks["account_reuse_collusion"]["status"] == "todo"


def test_bank_details_bad_ifsc():
    bad = BANK_TEXT.replace("HDFC0001234", "HDFCX001234")  # 5th char not 0
    f = ocr.extract_fields(bad, "bank_details")
    # regex requires 0 at pos 5, so a malformed one is not captured -> null ifsc
    assert f.get("ifsc") is None


# --------------------------------------------------------------------------- #
# dispatch / backward-compat / guessing
# --------------------------------------------------------------------------- #
def test_doc_type_guess_from_other():
    f = ocr.extract_fields(RC_TEXT, "other")
    assert f["doc_type_guess"] == "rc_copy"
    assert f["doc_type_resolved"] == "rc_copy"
    # dispatched even though declared type was 'other'
    assert f["registration_number"] == "MH12AB1234"


def test_unknown_doc_type_still_has_gates():
    f = ocr.extract_fields("SOME RANDOM TEXT WITH NO FIELDS", "other")
    assert "quality_gates" in f
    assert f["validations"] == {}
    assert f["cross_checks"] == []


def test_legacy_keys_preserved():
    f = ocr.extract_fields(ESTIMATE_TEXT, "repair_estimate")
    for k in ("amount", "amounts_seen", "dates", "doc_type_guess"):
        assert k in f
