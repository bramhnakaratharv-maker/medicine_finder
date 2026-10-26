
import psycopg

def get_connection():
    return psycopg.connect(
        host="localhost",
        port=5433,
        dbname="medicine_finder_db",
        user="postgres",
        password="NewPass2026!",
        connect_timeout=10
    )

if __name__ == "__main__":
    try:
        connection = get_connection()
        print("Database connected successfully!")
        connection.close()
    except Exception as error:
        print("Database connection failed:")
        print(error)
