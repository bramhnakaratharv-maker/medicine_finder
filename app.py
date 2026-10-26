import os
import logging
from functools import wraps

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    render_template_string,
    jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash
from psycopg.rows import dict_row
from database import get_connection


app = Flask(__name__)
print("APP ROOT:", app.root_path)
print("TEMPLATE FOLDER:", app.template_folder)
print("TEMPLATE EXISTS:", os.path.exists(
    os.path.join(app.root_path, "templates", "view_medicines.html")
))

app.secret_key = os.getenv(
    "FLASK_SECRET_KEY",
    "medfind-local-development-secret"
)

logging.basicConfig(level=logging.INFO)


# ---------------- DISABLE PAGE CACHE ----------------

@app.after_request
def disable_page_cache(response):
    response.headers["Cache-Control"] = (
        "no-store, no-cache, must-revalidate, max-age=0"
    )
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


# ---------------- DATABASE HELPERS ----------------

def query_all(sql, params=()):
    with get_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def query_one(sql, params=()):
    with get_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return cur.fetchone()


def execute_sql(sql, params=()):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            count = cur.rowcount
        conn.commit()
        return count


# ---------------- LOGIN DECORATORS ----------------

def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            flash("Please log in first.")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapper


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            flash("Please log in first.")
            return redirect(url_for("login"))

        role = str(session.get("role", "")).strip().lower()

        if role not in ("admin", "administrator"):
            flash("Admin access required.")
            return redirect(url_for("dashboard"))

        return view(*args, **kwargs)
    return wrapper


# ---------------- DASHBOARD STATISTICS ----------------

def get_stats():
    try:
        stats = query_one("""
            SELECT
                COUNT(*) AS total_medicines,
                COUNT(*) FILTER (
                    WHERE COALESCE(stock, 0) > 0
                ) AS available_medicines,
                COUNT(*) FILTER (
                    WHERE COALESCE(stock, 0) <= 0
                ) AS out_of_stock,
                COALESCE(SUM(stock), 0) AS total_stock
            FROM medicines
        """)

        return stats or {
            "total_medicines": 0,
            "available_medicines": 0,
            "out_of_stock": 0,
            "total_stock": 0
        }

    except Exception:
        app.logger.exception("Dashboard statistics error")
        return {
            "total_medicines": 0,
            "available_medicines": 0,
            "out_of_stock": 0,
            "total_stock": 0
        }


# ---------------- HOME ----------------

@app.route("/")
def home():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


# ---------------- REGISTER ----------------

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        return render_template("register.html")

    username = (
        request.form.get("username")
        or request.form.get("full_name")
        or ""
    ).strip()

    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    role = request.form.get("role", "Customer").strip()
    pharmacy_name = request.form.get("pharmacy_name", "").strip()

    if not username or not email or not password:
        flash("Please fill in all required fields.")
        return redirect(url_for("register"))

    # Public registration cannot create an admin account.
    if role.lower() in ("admin", "administrator"):
        role = "Customer"

    try:
        execute_sql("""
            INSERT INTO users
                (username, email, password_hash, role, pharmacy_name)
            VALUES (%s, %s, %s, %s, %s)
        """, (
            username,
            email,
            generate_password_hash(password),
            role,
            pharmacy_name or None
        ))

        flash("Registration successful. Please log in.")
        return redirect(url_for("login"))

    except Exception:
        app.logger.exception("Registration error")
        flash("Registration failed. Check the VS Code terminal.")
        return redirect(url_for("register"))


# ---------------- LOGIN ----------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html")

    identity = (
        request.form.get("username")
        or request.form.get("email")
        or ""
    ).strip()

    password = request.form.get("password", "")

    try:
        user = query_one("""
            SELECT id, username, email, password_hash,
                   role, pharmacy_name
            FROM users
            WHERE LOWER(username) = LOWER(%s)
               OR LOWER(email) = LOWER(%s)
            LIMIT 1
        """, (identity, identity))

        if user and check_password_hash(
            user["password_hash"], password
        ):
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["email"] = user["email"]
            session["role"] = user["role"]
            session["pharmacy_name"] = user.get("pharmacy_name")

            try:
                execute_sql("""
                    INSERT INTO login_logs
                        (username, role, login_time)
                    VALUES (%s, %s, CURRENT_TIMESTAMP)
                """, (user["username"], user["role"]))
            except Exception:
                app.logger.exception("Login history error")

            flash("Login successful.")
            return redirect(url_for("dashboard"))

        flash("Invalid username/email or password.")

    except Exception:
        app.logger.exception("Login error")
        flash("Login failed. Check the VS Code terminal.")

    return redirect(url_for("login"))


# ---------------- LOGOUT ----------------

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------- DASHBOARD ----------------

@app.route("/dashboard")
@login_required
def dashboard():
    return render_template(
        "dashboard.html",
        stats=get_stats()
    )


# ---------------- ADD MEDICINE ----------------

@app.route("/add", methods=["GET", "POST"])
@app.route("/add-medicine", methods=["GET", "POST"])
@login_required
def add_medicine():
    if request.method == "GET":
        return render_template("add_medicine.html")

    try:
        medicine_name = request.form.get(
            "medicine_name", ""
        ).strip()

        pharmacy_name = request.form.get(
            "pharmacy_name", ""
        ).strip()

        address = request.form.get("address", "").strip()
        phone = request.form.get("phone", "").strip()
        expiry_date = request.form.get(
            "expiry_date", ""
        ).strip() or None

        price = float(request.form.get("price", ""))
        stock = int(request.form.get("stock", ""))

        if not medicine_name or not pharmacy_name or not address:
            flash("Please fill in medicine name, pharmacy and address.")
            return redirect(url_for("add_medicine"))

        if price < 0 or stock < 0:
            flash("Price and stock cannot be negative.")
            return redirect(url_for("add_medicine"))

        execute_sql("""
            INSERT INTO medicines
                (medicine_name, pharmacy_name, address,
                 price, stock, phone, expiry_date)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            medicine_name,
            pharmacy_name,
            address,
            price,
            stock,
            phone,
            expiry_date
        ))

        flash("Medicine added successfully!")
        return redirect(url_for("add_medicine"))

    except ValueError:
        flash("Enter a valid price and stock quantity.")
        return redirect(url_for("add_medicine"))

    except Exception:
        app.logger.exception("Add medicine error")
        flash("Medicine could not be added. Check the VS Code terminal.")
        return redirect(url_for("add_medicine"))


# ---------------- VIEW MEDICINES ----------------




@app.route("/view-medicines")
@login_required
def view_medicines():
    try:
        medicines = query_all("""
            SELECT id, medicine_name, pharmacy_name, address,
                   price, stock, phone, expiry_date
            FROM medicines
            ORDER BY id DESC
        """)
        return render_template(
            "view_medicines.html",
            medicines=medicines
        )
    except Exception:
        app.logger.exception("View medicines error")
        return "Could not load medicines. Check the VS Code terminal.", 500

# ---------------- SEARCH MEDICINE ----------------

@app.route("/search", methods=["GET", "POST"])
@login_required
def search():
    term = request.values.get(
        "medicine_name",
        request.values.get("q", "")
    ).strip()

    medicines = []

    if term:
        try:
            medicines = query_all("""
                SELECT id, medicine_name, pharmacy_name,
                       address, price, stock, phone, expiry_date,
                       latitude, longitude
                FROM medicines
                WHERE medicine_name ILIKE %s
                ORDER BY price ASC
            """, (f"%{term}%",))

        except Exception:
            app.logger.exception("Search error")
            flash("Search failed. Check the VS Code terminal.")

    # JSON data for the Leaflet map in search.html
    return render_template(
        "search.html",
        medicines=medicines,
        results=medicines,
        query=term,
        search_term=term
    )


# ---------------- COMPARE PRICES ----------------

@app.route("/compare", methods=["GET", "POST"])
@app.route("/compare-prices", methods=["GET", "POST"])
@login_required
def compare_prices():
    query = request.values.get(
        "medicine_name",
        request.values.get("q", "")
    ).strip()

    try:
        medicines = []

        if query:
            medicines = query_all("""
                SELECT medicine_name, pharmacy_name, address,
                       price, stock, phone
                FROM medicines
                WHERE medicine_name ILIKE %s
                ORDER BY price ASC
            """, (f"%{query}%",))

        return render_template(
            "compare_prices.html",
            medicines=medicines,
            results=medicines,
            query=query
        )

    except Exception:
        app.logger.exception("Compare prices page error")
        return "Compare Prices error. Check VS Code terminal.", 500


# ---------------- DELETE MEDICINE ----------------

@app.route("/delete-medicine", methods=["GET", "POST"])
@login_required
def delete_medicine():
    if request.method == "GET":
        return render_template("delete_medicine.html")

    name = request.form.get("medicine_name", "").strip()
    pharmacy = request.form.get("pharmacy_name", "").strip()

    if not name or not pharmacy:
        flash("Please enter both medicine name and pharmacy name.")
        return redirect(url_for("delete_medicine"))

    role = str(session.get("role", "")).strip().lower()

    if role not in ("admin", "administrator", "pharmacist", "pharmacy"):
        flash("Permission denied. Admin or Pharmacist login required.")
        return redirect(url_for("delete_medicine"))

    if role in ("pharmacist", "pharmacy"):
        own_pharmacy = str(session.get("pharmacy_name") or "").strip()

        if own_pharmacy.casefold() != pharmacy.casefold():
            flash("You can delete medicines only from your own pharmacy.")
            return redirect(url_for("delete_medicine"))

    try:
        count = execute_sql("""
            DELETE FROM medicines
            WHERE LOWER(TRIM(medicine_name)) = LOWER(TRIM(%s))
              AND LOWER(TRIM(pharmacy_name)) = LOWER(TRIM(%s))
        """, (name, pharmacy))

        if count > 0:
            flash(f"Successfully deleted {count} medicine record(s).")
        else:
            flash(
                "No matching medicine found. Check both names "
                "on the View Medicines page."
            )

    except Exception:
        app.logger.exception("Delete medicine database error")
        flash("Database error while deleting. Check the VS Code terminal.")

    return redirect(url_for("delete_medicine"))


# ---------------- EDIT MEDICINE ----------------

@app.route("/edit-medicine", methods=["GET", "POST"])
@login_required
def edit_medicine():
    if request.method == "GET":
        medicine_id = request.args.get("id", "").strip()
        medicine = None

        try:
            if medicine_id.isdigit():
                medicine = query_one(
                    "SELECT * FROM medicines WHERE id = %s",
                    (int(medicine_id),)
                )
        except Exception:
            app.logger.exception("Find medicine error")
            flash("Could not find medicine. Check the terminal.")

        return render_template_string("""
        <!doctype html>
        <html>
        <head><title>Edit Medicine</title></head>
        <body>
            <h2>Edit Medicine</h2>
            <form method="get">
                Medicine ID:
                <input type="number" name="id" required>
                <button type="submit">Find</button>
            </form>

            {% if medicine %}
            <form method="post">
                <input type="hidden" name="id" value="{{ medicine.id }}">

                Medicine Name:
                <input name="medicine_name"
                       value="{{ medicine.medicine_name }}" required><br>

                Pharmacy Name:
                <input name="pharmacy_name"
                       value="{{ medicine.pharmacy_name }}" required><br>

                Address:
                <input name="address"
                       value="{{ medicine.address or '' }}"><br>

                Price:
                <input type="number" step="0.01" min="0"
                       name="price" value="{{ medicine.price }}" required><br>

                Stock:
                <input type="number" min="0"
                       name="stock" value="{{ medicine.stock }}" required><br>

                Phone:
                <input name="phone"
                       value="{{ medicine.phone or '' }}"><br>

                Expiry Date:
                <input type="date" name="expiry_date"
                       value="{{ medicine.expiry_date or '' }}"><br>

                <button type="submit">Save Changes</button>
            </form>
            {% elif request_id %}
                <p>Medicine not found.</p>
            {% endif %}

            <p><a href="{{ url_for('dashboard') }}">Dashboard</a></p>
        </body>
        </html>
        """, medicine=medicine, request_id=medicine_id)

    try:
        medicine_id = int(request.form.get("id", ""))
        price = float(request.form.get("price", ""))
        stock = int(request.form.get("stock", ""))

        medicine_name = request.form.get(
            "medicine_name", ""
        ).strip()

        pharmacy_name = request.form.get(
            "pharmacy_name", ""
        ).strip()

        if not medicine_name or not pharmacy_name:
            flash("Medicine name and pharmacy name are required.")
            return redirect(url_for("edit_medicine"))

        if price < 0 or stock < 0:
            raise ValueError

        count = execute_sql("""
            UPDATE medicines
            SET medicine_name = %s,
                pharmacy_name = %s,
                address = %s,
                price = %s,
                stock = %s,
                phone = %s,
                expiry_date = %s
            WHERE id = %s
        """, (
            medicine_name,
            pharmacy_name,
            request.form.get("address", "").strip(),
            price,
            stock,
            request.form.get("phone", "").strip(),
            request.form.get("expiry_date", "").strip() or None,
            medicine_id
        ))

        flash(
            "Medicine updated successfully."
            if count else "Medicine ID not found."
        )

    except ValueError:
        flash("Enter a valid medicine ID, price and stock.")

    except Exception:
        app.logger.exception("Edit medicine error")
        flash("Could not update medicine. Check the terminal.")

    return redirect(url_for("edit_medicine"))


# ---------------- UPDATE STOCK ----------------

@app.route("/update-stock", methods=["GET", "POST"])
@login_required
def update_stock():
    if request.method == "GET":
        return render_template_string("""
        <!doctype html>
        <html>
        <head><title>Update Stock</title></head>
        <body>
            <h2>Update Medicine Stock</h2>
            <form method="post">
                Medicine ID:
                <input type="number" name="medicine_id" required><br>

                New Stock:
                <input type="number" name="stock" min="0" required><br>

                <button type="submit">Update Stock</button>
            </form>
            <a href="{{ url_for('dashboard') }}">Dashboard</a>
        </body>
        </html>
        """)

    try:
        medicine_id = int(request.form.get("medicine_id", ""))
        stock = int(request.form.get("stock", ""))

        if medicine_id <= 0 or stock < 0:
            raise ValueError

        count = execute_sql(
            "UPDATE medicines SET stock = %s WHERE id = %s",
            (stock, medicine_id)
        )

        flash("Stock updated." if count else "Medicine ID not found.")

    except ValueError:
        flash("Enter a valid medicine ID and non-negative stock.")

    except Exception:
        app.logger.exception("Update stock error")
        flash("Could not update stock. Check the terminal.")

    return redirect(url_for("update_stock"))


# ---------------- LOW STOCK ----------------

@app.route("/low-stock")
@login_required
def low_stock():
    try:
        medicines = query_all("""
            SELECT *
            FROM medicines
            WHERE COALESCE(stock, 0) <= 5
            ORDER BY stock ASC
        """)

        return render_template(
            "low_stock.html",
            medicines=medicines,
            rows=medicines
        )

    except Exception:
        app.logger.exception("Low stock error")
        flash("Could not load low-stock medicines.")
        return redirect(url_for("dashboard"))


# ---------------- EXPIRY ALERT ----------------

@app.route("/expiry-alert")
@login_required
def expiry_alert():
    try:
        medicines = query_all("""
            SELECT *
            FROM medicines
            WHERE expiry_date IS NOT NULL
              AND expiry_date <= CURRENT_DATE + INTERVAL '30 days'
            ORDER BY expiry_date ASC
        """)

        return render_template(
            "expiry_alert.html",
            medicines=medicines,
            rows=medicines
        )

    except Exception:
        app.logger.exception("Expiry alert error")
        flash("Could not load expiry alerts.")
        return redirect(url_for("dashboard"))


# ---------------- PHARMACY REPORT ----------------

@app.route("/pharmacy-report")
@login_required
def pharmacy_report():
    try:
        rows = query_all("""
            SELECT pharmacy_name,
                   COUNT(*) AS medicine_count,
                   COALESCE(SUM(stock), 0) AS total_stock,
                   COALESCE(AVG(price), 0) AS average_price
            FROM medicines
            GROUP BY pharmacy_name
            ORDER BY pharmacy_name
        """)

        return render_template(
            "pharmacy_report.html",
            rows=rows,
            report=rows,
            pharmacies=rows
        )

    except Exception:
        app.logger.exception("Pharmacy report error")
        flash("Could not load pharmacy report.")
        return redirect(url_for("dashboard"))


# ---------------- MANAGE USERS ----------------

@app.route("/manage-users", methods=["GET", "POST"])
@admin_required
def manage_users():
    if request.method == "POST":
        user_id = request.form.get("user_id", "").strip()
        role = request.form.get("role", "").strip().lower()
        pharmacy_name = request.form.get("pharmacy_name", "").strip()

        allowed_roles = {
            "customer": "Customer",
            "user": "User",
            "pharmacist": "Pharmacist",
            "admin": "Admin",
            "administrator": "Admin"
        }

        if not user_id.isdigit() or role not in allowed_roles:
            flash("Please enter valid user details.")
            return redirect(url_for("manage_users"))

        new_role = allowed_roles[role]
        current_user_id = session.get("user_id")

        if (
            current_user_id is not None
            and str(current_user_id) == user_id
            and new_role != "Admin"
        ):
            flash("You cannot remove your own Admin role.")
            return redirect(url_for("manage_users"))

        try:
            count = execute_sql("""
                UPDATE users
                SET role = %s,
                    pharmacy_name = %s
                WHERE id = %s
            """, (new_role, pharmacy_name or None, int(user_id)))

            if count:
                if (
                    current_user_id is not None
                    and str(current_user_id) == user_id
                ):
                    session["role"] = new_role
                    session["pharmacy_name"] = pharmacy_name or None

                flash("User updated successfully.")
            else:
                flash("User ID not found.")

        except Exception:
            app.logger.exception("Manage users update error")
            flash("Could not update user. Check the terminal.")

        return redirect(url_for("manage_users"))

    try:
        users = query_all("""
            SELECT id, username, email, role, pharmacy_name
            FROM users
            ORDER BY id
        """)

        return render_template(
            "manage_users.html",
            users=users,
            rows=users
        )

    except Exception:
        app.logger.exception("Manage users loading error")
        flash("Could not load users. Check the VS Code terminal.")
        return redirect(url_for("dashboard"))


# ---------------- LOGIN HISTORY ----------------

@app.route("/login-history")
@admin_required
def login_history():
    try:
        rows = query_all("""
            SELECT id, username, role, login_time
            FROM login_logs
            ORDER BY login_time DESC
            LIMIT 200
        """)

        return render_template(
            "login_history.html",
            rows=rows,
            logs=rows
        )

    except Exception:
        app.logger.exception("Login history error")
        flash("Could not load login history.")
        return redirect(url_for("dashboard"))


# ---------------- RUN APPLICATION ----------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)