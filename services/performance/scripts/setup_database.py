import os
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine, text


def get_engine():
    sslmode = os.environ.get("DB_SSLMODE", "prefer")
    url = (
        f"postgresql+psycopg2://{os.environ['DB_USER']}:{os.environ['DB_PASSWORD']}"
        f"@{os.environ['DB_HOST']}:{os.environ['DB_PORT']}/{os.environ['DB_NAME']}"
        f"?sslmode={sslmode}"
    )
    return create_engine(url)


def split_sql_statements(sql: str) -> list[str]:
    # Real bug, found by running this against 002_create_indexes.sql after adding a comment
    # that happened to contain a semicolon: naively splitting the raw file text on ";" also
    # splits INSIDE "--" line comments, producing a comment-only chunk with no actual SQL in
    # it. Postgres (via psycopg2) doesn't silently no-op that -- it raises "can't execute an
    # empty query". Fixed by stripping "--" line comments before splitting, so only real
    # semicolons (statement terminators) are split on. Sufficient for this project's SQL
    # files (no "--" appears inside a string literal in any of them); not a general SQL
    # tokenizer, and doesn't need to be one here. Shared with scripts/run_sql_analysis.py,
    # which also needs to split multi-statement analysis files.
    without_comments = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
    return [s.strip() for s in without_comments.split(";") if s.strip()]


def run_sql_file(engine, path: Path):
    statements = split_sql_statements(path.read_text())
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))


def main():
    load_dotenv()
    engine = get_engine()

    schema_dir = Path(__file__).resolve().parent.parent / "sql" / "schema"
    schema_files = sorted(schema_dir.glob("*.sql"))

    if not schema_files:
        raise FileNotFoundError(f"No schema files found in {schema_dir}")

    # 004_create_roles.sql is deliberately excluded from the automatic loop below.
    # It creates DB roles and needs (a) superuser/CREATEROLE privileges the app's own
    # DB_USER shouldn't have, and (b) psql-style `:'variable'` password substitution
    # this script's naive `sql.split(";")` runner can't handle anyway (it would also
    # break on the file's DO $$ ... $$ block, which contains semicolons of its own).
    # Apply it manually as an admin, once per environment:
    #   psql -h $DB_HOST -U <admin_user> -d $DB_NAME \
    #        -v pipeline_writer_password='...' \
    #        -v api_reader_password='...' \
    #        -v readonly_report_password='...' \
    #        -f sql/schema/004_create_roles.sql
    schema_files = [f for f in schema_files if f.name != "004_create_roles.sql"]

    for f in schema_files:
        print(f"Applying {f.name}...")
        run_sql_file(engine, f)

    print("Database setup complete.")
    print("Note: sql/schema/004_create_roles.sql was NOT applied automatically -- "
          "see the comment in this script for how to run it manually as an admin.")


if __name__ == "__main__":
    main()
