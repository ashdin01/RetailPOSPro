import models.product as product
import api.backoffice_client as bop

_PRODUCT = {
    "barcode": "9300675009657",
    "plu": "100",
    "description": "Test Apples",
    "brand": "TestBrand",
    "dept_name": "Produce",
    "unit": "KG",
    "sell_price": 3.99,
    "tax_rate": 10.0,
}


class TestCacheProduct:
    def test_cache_and_retrieve(self, test_db):
        product._cache_product(_PRODUCT)
        result = product.get_by_barcode("9300675009657")
        assert result is not None
        assert result["description"] == "Test Apples"

    def test_upsert_updates_price(self, test_db):
        product._cache_product(_PRODUCT)
        product._cache_product(dict(_PRODUCT, sell_price=4.50))
        result = product.get_by_barcode("9300675009657")
        assert result["sell_price"] == 4.50

    def test_inactive_not_returned(self, test_db, db_conn):
        product._cache_product(_PRODUCT)
        db_conn.execute(
            "UPDATE product_cache SET active=0 WHERE barcode=?",
            ("9300675009657",),
        )
        db_conn.commit()
        result = product.get_by_barcode("9300675009657")
        # Cache miss; API also returns None (patched via monkeypatch in other test)
        assert result is None or result.get("active") == 1

    def test_unknown_barcode_no_api(self, test_db, monkeypatch):
        monkeypatch.setattr(bop, "get_product", lambda bc: None)
        assert product.get_by_barcode("0000000000000") is None

    def test_api_hit_caches_product(self, test_db, monkeypatch):
        monkeypatch.setattr(bop, "get_product", lambda bc: dict(_PRODUCT, barcode=bc))
        result = product.get_by_barcode("9300675009657")
        assert result is not None
        assert result["description"] == "Test Apples"


class TestGetByPlu:
    def test_returns_by_plu(self, test_db):
        product._cache_product(_PRODUCT)
        result = product.get_by_plu("100")
        assert result is not None
        assert result["barcode"] == "9300675009657"

    def test_unknown_plu_returns_none(self, test_db, monkeypatch):
        monkeypatch.setattr(bop, "get_product_by_plu", lambda plu: None)
        assert product.get_by_plu("99999") is None


class TestSearch:
    def test_finds_by_description(self, test_db):
        product._cache_product(_PRODUCT)
        results = product.search("Apples")
        assert any(r["barcode"] == "9300675009657" for r in results)

    def test_case_insensitive(self, test_db):
        product._cache_product(_PRODUCT)
        results = product.search("apples")
        assert len(results) > 0

    def test_multi_word_all_terms_required(self, test_db):
        product._cache_product(_PRODUCT)
        product._cache_product(dict(_PRODUCT, barcode="AAA", description="Test Oranges", plu="101"))
        results = product.search("Test Apples")
        barcodes = [r["barcode"] for r in results]
        assert "9300675009657" in barcodes
        assert "AAA" not in barcodes

    def test_finds_by_barcode(self, test_db):
        product._cache_product(_PRODUCT)
        results = product.search("9300675009657")
        assert any(r["barcode"] == "9300675009657" for r in results)

    def test_finds_by_brand(self, test_db):
        product._cache_product(_PRODUCT)
        results = product.search("TestBrand")
        assert any(r["barcode"] == "9300675009657" for r in results)

    def test_empty_term_returns_empty(self, test_db):
        product._cache_product(_PRODUCT)
        assert product.search("") == []

    def test_no_match_returns_empty(self, test_db):
        product._cache_product(_PRODUCT)
        assert product.search("xxxxnoexistxxxx") == []
