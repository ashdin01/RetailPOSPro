import hashlib
from unittest.mock import patch
import models.operator as op
from models.operator import _hash_pin, _verify_hash


class TestConstantTimeComparison:
    def test_pbkdf2_path_uses_hmac_compare_digest(self):
        from unittest.mock import patch
        import hmac as hmac_mod
        stored = _hash_pin("1234")
        with patch.object(hmac_mod, 'compare_digest', wraps=hmac_mod.compare_digest) as mock_cd:
            _verify_hash("1234", stored)
            assert mock_cd.called, "_verify_hash must use hmac.compare_digest"

    def test_legacy_path_uses_hmac_compare_digest(self):
        import hashlib, hmac as hmac_mod
        legacy = hashlib.sha256(b"1234").hexdigest()
        with patch.object(hmac_mod, 'compare_digest', wraps=hmac_mod.compare_digest) as mock_cd:
            _verify_hash("1234", legacy)
            assert mock_cd.called, "_verify_hash legacy path must use hmac.compare_digest"

    def test_correct_pin_still_verified(self):
        stored = _hash_pin("9999")
        assert _verify_hash("9999", stored) is True

    def test_wrong_pin_still_rejected(self):
        stored = _hash_pin("9999")
        assert _verify_hash("0000", stored) is False


class TestPbkdf2Hashing:
    def test_hash_format_is_pbkdf2(self, test_db):
        op.set_pin("admin", "1234")
        from database.connection import get_connection
        conn = get_connection()
        row = conn.execute("SELECT pin FROM operators WHERE username='admin'").fetchone()
        conn.close()
        assert ':' in row['pin'], "PIN must be stored in PBKDF2 'salt_hex:dk_hex' format"

    def test_same_pin_produces_different_hashes(self):
        h1 = _hash_pin("1234")
        h2 = _hash_pin("1234")
        assert h1 != h2, "Random salt must produce a different hash each time"

    def test_verify_hash_accepts_pbkdf2(self):
        stored = _hash_pin("5678")
        assert _verify_hash("5678", stored) is True
        assert _verify_hash("0000", stored) is False

    def test_verify_hash_accepts_legacy_sha256(self):
        legacy = hashlib.sha256(b"1234").hexdigest()
        assert _verify_hash("1234", legacy) is True
        assert _verify_hash("9999", legacy) is False

    def test_legacy_hash_upgraded_on_login(self, test_db):
        legacy = hashlib.sha256(b"1234").hexdigest()
        from database.connection import get_connection
        conn = get_connection()
        conn.execute("UPDATE operators SET pin=? WHERE username='admin'", (legacy,))
        conn.commit()
        conn.close()
        op.verify_pin("admin", "1234")  # triggers upgrade
        conn = get_connection()
        row = conn.execute("SELECT pin FROM operators WHERE username='admin'").fetchone()
        conn.close()
        assert ':' in row['pin'], "Hash must be upgraded to PBKDF2 after successful login"

    def test_legacy_wrong_pin_does_not_upgrade(self, test_db):
        legacy = hashlib.sha256(b"1234").hexdigest()
        from database.connection import get_connection
        conn = get_connection()
        conn.execute("UPDATE operators SET pin=? WHERE username='admin'", (legacy,))
        conn.commit()
        conn.close()
        op.verify_pin("admin", "9999")  # wrong pin — must not upgrade
        conn = get_connection()
        row = conn.execute("SELECT pin FROM operators WHERE username='admin'").fetchone()
        conn.close()
        assert ':' not in row['pin'], "Incorrect login must not modify the stored hash"


class TestVerifyPin:
    def test_correct_pin(self, test_db):
        op.set_pin("admin", "1234")
        assert op.verify_pin("admin", "1234") is True

    def test_wrong_pin(self, test_db):
        op.set_pin("admin", "1234")
        assert op.verify_pin("admin", "9999") is False

    def test_no_pin_set_returns_false(self, test_db):
        # admin user exists but has no PIN after schema setup
        assert op.verify_pin("admin", "0000") is False

    def test_unknown_user_returns_false(self, test_db):
        assert op.verify_pin("nobody", "1234") is False

    def test_inactive_user_returns_false(self, test_db, db_conn):
        db_conn.execute(
            "INSERT INTO operators (username, role, active) VALUES ('inactive_op','CASHIER',0)"
        )
        db_conn.commit()
        op.set_pin("inactive_op", "0000")
        # verify_pin checks active=1
        assert op.verify_pin("inactive_op", "0000") is False


class TestSetPin:
    def test_set_creates_hash(self, test_db):
        op.set_pin("admin", "5678")
        assert op.verify_pin("admin", "5678") is True

    def test_update_pin(self, test_db):
        op.set_pin("admin", "1111")
        op.set_pin("admin", "2222")
        assert op.verify_pin("admin", "2222") is True
        assert op.verify_pin("admin", "1111") is False


class TestHasAnyPin:
    def test_no_pins_returns_false(self, test_db):
        assert op.has_any_pin() is False

    def test_pin_set_returns_true(self, test_db):
        op.set_pin("admin", "0000")
        assert op.has_any_pin() is True


class TestGetAllActive:
    def test_returns_list(self, test_db):
        result = op.get_all_active()
        assert isinstance(result, list)

    def test_contains_default_admin(self, test_db):
        result = op.get_all_active()
        usernames = [r["username"] for r in result]
        assert "admin" in usernames

    def test_includes_new_active_operator(self, test_db, operator_id):
        result = op.get_all_active()
        ids = [r["id"] for r in result]
        assert operator_id in ids

    def test_excludes_inactive_operator(self, test_db, db_conn):
        db_conn.execute(
            "INSERT INTO operators (username, role, active) VALUES ('gone','CASHIER',0)"
        )
        db_conn.commit()
        result = op.get_all_active()
        assert not any(r["username"] == "gone" for r in result)
