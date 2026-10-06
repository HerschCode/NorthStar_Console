"""scripts.setup_database.run_sql_file's statement splitter: naively splitting raw SQL text on
";" also splits inside "--" line comments. Real bug, found by running this against
sql/schema/002_create_indexes.sql after a comment happened to contain a semicolon -- Postgres
raised "can't execute an empty query" for the resulting comment-only chunk. These tests pin the
fixed behaviour (strip "--" comments before splitting) so it can't silently regress."""
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.setup_database import run_sql_file


def _statements_executed(sql_text: str, tmp_path: Path) -> list[str]:
    path = tmp_path / "schema.sql"
    path.write_text(sql_text)
    engine = MagicMock()
    conn = MagicMock()
    engine.begin.return_value.__enter__.return_value = conn
    with patch("scripts.setup_database.text", side_effect=lambda s: s):
        run_sql_file(engine, path)
    return [call.args[0] for call in conn.execute.call_args_list]


def test_semicolon_inside_a_line_comment_does_not_split_the_statement(tmp_path):
    sql = (
        "-- a note about something; with a stray semicolon in it\n"
        "CREATE TABLE t (id INT);\n"
    )
    statements = _statements_executed(sql, tmp_path)
    assert statements == ["CREATE TABLE t (id INT)"]


def test_two_real_statements_still_split_correctly(tmp_path):
    sql = "CREATE TABLE a (id INT);\nCREATE TABLE b (id INT);\n"
    statements = _statements_executed(sql, tmp_path)
    assert statements == ["CREATE TABLE a (id INT)", "CREATE TABLE b (id INT)"]


def test_comment_only_file_produces_no_statements(tmp_path):
    sql = "-- just a comment, nothing to run\n-- another line; with a semicolon\n"
    statements = _statements_executed(sql, tmp_path)
    assert statements == []
