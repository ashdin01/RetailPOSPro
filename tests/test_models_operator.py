import models.operator as op


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
