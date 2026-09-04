from kestrel.ask import extract_sql, FORBIDDEN


def test_extract_sql_handles_prose_and_fences():
    assert extract_sql("SELECT 1") == "SELECT 1"
    assert extract_sql("Here you go:\n```sql\nSELECT a FROM b;\n```\nHope that helps") == "SELECT a FROM b"
    assert extract_sql("Sure. WITH x AS (SELECT 1) SELECT * FROM x") == "WITH x AS (SELECT 1) SELECT * FROM x"


def test_extract_sql_rejects_no_query():
    import pytest
    with pytest.raises(ValueError):
        extract_sql("I cannot answer that.")


def test_forbidden_keywords():
    assert FORBIDDEN.search("DROP TABLE orders")
    assert not FORBIDDEN.search("SELECT updated_at FROM outlets")   # word boundary, not substring
