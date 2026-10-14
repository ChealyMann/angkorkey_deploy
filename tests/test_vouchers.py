import pytest

from extensions import db
from models import Voucher, VoucherRedemption
from services.vouchers import VoucherError, check_voucher, normalize_phone, redeem_voucher


def make_voucher(code="FREE", min_spend=0.0, status="true", usage_limit=None, usage_count=0):
    voucher = Voucher(code=code, min_spend=min_spend, status=status,
                      usage_limit=usage_limit, usage_count=usage_count)
    db.session.add(voucher)
    db.session.commit()
    return voucher


def test_normalize_phone():
    assert normalize_phone("+855 12 345 678") == "012345678"
    assert normalize_phone("855-12-345-678") == "012345678"
    assert normalize_phone("012 345 678") == "012345678"
    assert normalize_phone("") == "" and normalize_phone(None) == ""


def test_check_voucher_valid(app):
    voucher = make_voucher(min_spend=10.0)
    assert check_voucher(" free ", 10.0, "012345678") == (voucher, None)


@pytest.mark.parametrize("code", ["", "   ", None])
def test_check_voucher_required(app, code):
    assert check_voucher(code, 10.0, "012345678") == (None, "voucher_required")


def test_check_voucher_invalid(app):
    make_voucher()
    assert check_voucher("NOPE", 10.0, "012345678") == (None, "voucher_invalid")


def test_check_voucher_inactive(app):
    make_voucher(status="false")
    assert check_voucher("FREE", 10.0, "012345678") == (None, "voucher_inactive")


def test_check_voucher_limit_reached(app):
    make_voucher(usage_limit=2, usage_count=2)
    assert check_voucher("FREE", 10.0, "012345678") == (None, "voucher_limit_reached")


def test_check_voucher_min_spend(app):
    make_voucher(min_spend=20.0)
    assert check_voucher("FREE", 19.99, "012345678") == (None, "voucher_min_spend")


def test_check_voucher_already_used_matches_normalized_phone(app):
    voucher = make_voucher()
    db.session.add(VoucherRedemption(voucher_id=voucher.id, phone_number="012345678"))
    db.session.commit()
    assert check_voucher("FREE", 10.0, "+855 12 345 678") == (None, "voucher_already_used")
    assert check_voucher("FREE", 10.0, "098765432") == (voucher, None)
    assert check_voucher("FREE", 10.0, "") == (voucher, None)  # no phone: not checked


def test_check_voucher_standard_only(app):
    make_voucher()
    assert check_voucher("FREE", 10.0, "012345678", "grab") == (None, "voucher_standard_only")
    assert check_voucher("FREE", 10.0, "012345678", "express") == (None, "voucher_standard_only")


def test_redeem_voucher_adds_redemption_without_commit(app):
    voucher = make_voucher()
    redeem_voucher(voucher, "+855 12 345 678")
    assert voucher.usage_count == 1
    assert VoucherRedemption.query.one().phone_number == "012345678"
    db.session.rollback()
    assert VoucherRedemption.query.count() == 0
    assert db.session.get(Voucher, voucher.id).usage_count == 0


def test_redeem_voucher_refuses_when_limit_reached_since_check(app):
    voucher = make_voucher(usage_limit=1)
    assert check_voucher("FREE", 10.0, "012345678") == (voucher, None)
    # Another order used the last slot between check and redeem.
    db.session.execute(db.update(Voucher).values(usage_count=1))
    db.session.commit()
    with pytest.raises(VoucherError) as exc:
        redeem_voucher(voucher, "012345678")
    assert exc.value.code == "voucher_limit_reached"


def test_redeem_voucher_refuses_phone_used_since_check(app):
    voucher = make_voucher()
    assert check_voucher("FREE", 10.0, "012345678") == (voucher, None)
    db.session.add(VoucherRedemption(voucher_id=voucher.id, phone_number="012345678"))
    db.session.commit()
    with pytest.raises(VoucherError) as exc:
        redeem_voucher(voucher, "012345678")
    assert exc.value.code == "voucher_already_used"


def test_validate_voucher_endpoint_messages(client):
    make_voucher(min_spend=5.0)
    r = client.get("/api/validate_voucher?code=free&subtotal=10&phone=012345678")
    assert r.status_code == 200 and r.json == {
        "valid": True,
        "message": "Voucher applied successfully! Free standard delivery.",
        "code": "FREE",
        "min_spend": 5.0,
    }
    r = client.get("/api/validate_voucher?code=FREE&subtotal=1")
    assert r.json == {"valid": False,
                      "message": "Minimum spend of $5.00 required to apply this voucher."}
    r = client.get("/api/validate_voucher?code=FREE&subtotal=10&delivery=grab")
    assert r.json == {"valid": False, "message": "Vouchers apply to Standard delivery only."}
    r = client.get("/api/validate_voucher?code=NOPE&subtotal=10")
    assert r.json == {"valid": False, "message": "Invalid voucher code."}
    r = client.get("/api/validate_voucher?code=")
    assert r.status_code == 400 and r.json == {"valid": False, "message": "Voucher code is required."}


def test_validate_voucher_endpoint_does_not_redeem(client):
    voucher = make_voucher()
    client.get("/api/validate_voucher?code=FREE&subtotal=10&phone=012345678")
    assert db.session.get(Voucher, voucher.id).usage_count == 0
    assert VoucherRedemption.query.count() == 0
