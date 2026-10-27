"""Finora Company Finance Management System - educational full-stack demo."""
from flask import Flask, jsonify, request, render_template, redirect, url_for, session, Response
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from pathlib import Path
from datetime import datetime
import csv, io, os, sqlite3

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", "local-demo-key-change-before-deploy"),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "0") == "1",
    MAX_CONTENT_LENGTH=1 * 1024 * 1024,
)
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("FINANCE_DB_PATH", str(BASE_DIR / "finance.db")))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def connect_db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with connect_db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            type TEXT NOT NULL CHECK(type IN ('Income','Expense')),
            description TEXT NOT NULL,
            date TEXT NOT NULL,
            amount REAL NOT NULL CHECK(amount > 0),
            category TEXT NOT NULL
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS employees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            type TEXT NOT NULL CHECK(type IN ('Permanent','Contract')),
            salary REAL NOT NULL CHECK(salary >= 0)
        )""")
        # Seed one administrator only on first run. Override credentials with environment variables.
        if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            username = os.environ.get("ADMIN_USERNAME", "admin").strip() or "admin"
            password = os.environ.get("ADMIN_PASSWORD", "Finance@2026")
            conn.execute("INSERT INTO users(username,password_hash,created_at) VALUES(?,?,?)",
                         (username, generate_password_hash(password), datetime.utcnow().isoformat(timespec="seconds")))


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            if request.path.startswith("/api/"):
                return jsonify(error="Please sign in to continue."), 401
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper


def row_dict(row):
    return dict(row) if row else None


@app.get("/login")
def login():
    if "user_id" in session:
        return redirect(url_for("home"))
    return render_template("login.html", error=None)


@app.post("/login")
def login_post():
    username = str(request.form.get("username", "")).strip()
    password = str(request.form.get("password", ""))
    with connect_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not user or not check_password_hash(user["password_hash"], password):
        return render_template("login.html", error="Incorrect username or password."), 401
    session.clear()
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    session.permanent = True
    return redirect(url_for("home"))


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/")
@login_required
def home():
    return render_template("index.html", username=session.get("username", "Administrator"))


@app.get("/api/data")
@login_required
def get_data():
    uid = session["user_id"]
    with connect_db() as conn:
        transactions = [row_dict(r) for r in conn.execute("SELECT * FROM transactions WHERE user_id=? ORDER BY date DESC,id DESC", (uid,))]
        employees = [row_dict(r) for r in conn.execute("SELECT * FROM employees WHERE user_id=? ORDER BY id DESC", (uid,))]
    return jsonify({"transactions": transactions, "employees": employees})


@app.post("/api/transactions")
@login_required
def add_transaction():
    data = request.get_json(silent=True) or {}
    ttype = data.get("type")
    description = str(data.get("description", "")).strip()
    date = str(data.get("date", "")).strip()
    category = str(data.get("category", "Other")).strip() or "Other"
    try:
        amount = float(data.get("amount", 0))
        datetime.strptime(date, "%Y-%m-%d")
    except (TypeError, ValueError):
        return jsonify(error="Enter a valid date and numeric amount."), 400
    if ttype not in ("Income", "Expense") or not description or len(description) > 100 or len(category) > 50 or amount <= 0:
        return jsonify(error="Provide valid transaction details and an amount greater than zero."), 400
    with connect_db() as conn:
        cur = conn.execute("INSERT INTO transactions(user_id,type,description,date,amount,category) VALUES(?,?,?,?,?,?)",
                           (session["user_id"], ttype, description, date, amount, category))
        row = conn.execute("SELECT * FROM transactions WHERE id=? AND user_id=?", (cur.lastrowid, session["user_id"])).fetchone()
    return jsonify(row_dict(row)), 201


@app.delete("/api/transactions/<int:transaction_id>")
@login_required
def delete_transaction(transaction_id):
    with connect_db() as conn:
        cur = conn.execute("DELETE FROM transactions WHERE id=? AND user_id=?", (transaction_id, session["user_id"]))
    if cur.rowcount == 0:
        return jsonify(error="Transaction not found."), 404
    return jsonify(ok=True)


@app.post("/api/employees")
@login_required
def add_employee():
    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "")).strip()
    etype = data.get("type")
    try:
        salary = float(data.get("salary", 0))
    except (TypeError, ValueError):
        return jsonify(error="Enter a valid salary."), 400
    if not name or len(name) > 80 or etype not in ("Permanent", "Contract") or salary < 0:
        return jsonify(error="Provide a valid name, employee type and non-negative salary."), 400
    with connect_db() as conn:
        cur = conn.execute("INSERT INTO employees(user_id,name,type,salary) VALUES(?,?,?,?)", (session["user_id"], name, etype, salary))
        row = conn.execute("SELECT * FROM employees WHERE id=? AND user_id=?", (cur.lastrowid, session["user_id"])).fetchone()
    return jsonify(row_dict(row)), 201


@app.delete("/api/employees/<int:employee_id>")
@login_required
def delete_employee(employee_id):
    with connect_db() as conn:
        cur = conn.execute("DELETE FROM employees WHERE id=? AND user_id=?", (employee_id, session["user_id"]))
    if cur.rowcount == 0:
        return jsonify(error="Employee not found."), 404
    return jsonify(ok=True)


@app.get("/api/export/transactions.csv")
@login_required
def export_transactions():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Type", "Description", "Date", "Category", "Amount (INR)"])
    with connect_db() as conn:
        rows = conn.execute("SELECT id,type,description,date,category,amount FROM transactions WHERE user_id=? ORDER BY date DESC,id DESC", (session["user_id"],)).fetchall()
    writer.writerows([tuple(r) for r in rows])
    return Response("\ufeff" + output.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=finance-transactions.csv"})


@app.get("/health")
def health():
    return jsonify(status="ok")


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False)
