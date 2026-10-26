from getpass import getpass
from werkzeug.security import generate_password_hash
from database import get_connection


def reset_admin_password():
    print("=== MedFind Admin Password Reset ===")

    username = input("Existing Admin username: ").strip()
    password = getpass("Enter new password (minimum 8 characters): ")

    if not username or len(password) < 8:
        print("Error: Username is required and password must be at least 8 characters.")
        return

    password_hash = generate_password_hash(password)
    conn = None

    try:
        conn = get_connection()

        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE users
                SET password_hash = %s
                WHERE LOWER(username) = LOWER(%s)
                  AND LOWER(role) = 'admin'
                RETURNING username
                """,
                (password_hash, username)
            )
            updated_user = cur.fetchone()

        if updated_user:
            conn.commit()
            print("Admin password reset successfully!")
        else:
            conn.rollback()
            print("No matching Admin account found. Password was not changed.")

    except Exception as error:
        if conn:
            conn.rollback()
        print("Password reset failed:", error)

    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    reset_admin_password()