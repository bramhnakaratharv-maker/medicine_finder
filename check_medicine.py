from database import get_connection
from psycopg.rows import dict_row

name = input("Medicine name: ").strip()

with get_connection() as conn:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""
            SELECT id, medicine_name, pharmacy_name, stock
            FROM medicines
            WHERE medicine_name ILIKE %s
            ORDER BY id
        """, (name,))

        rows = cur.fetchall()

        if rows:
            for row in rows:
                print(row)
        else:
            print("Medicine database mein nahi mili.")