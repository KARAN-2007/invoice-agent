from pathlib import Path

from db import get_connection

SQL_FILE = Path(__file__).parent / "schema_agent.sql"


def main():
    sql = SQL_FILE.read_text(encoding="utf-8")
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            conn.commit()
            print("Agent tables are ready.")
            cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name;")
            print("Tables in the database:")
            for (name,) in cur.fetchall():
                print("  - " + name)
    finally:
        conn.close()


if __name__ == "__main__":
    main()