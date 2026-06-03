import os
import sqlite3
from datetime import date, datetime
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL and "sslmode" not in DATABASE_URL:
    DATABASE_URL += "?sslmode=require"

# ── Database abstraction ──────────────────────────────────────────────────────

def get_db():
    if DATABASE_URL:
        import psycopg2
        import psycopg2.extras
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = False
        return conn, "pg"
    else:
        conn = sqlite3.connect("tasks.db")
        conn.row_factory = sqlite3.Row
        return conn, "sqlite"

def rows_to_dicts(rows, db_type):
    if db_type == "pg":
        return [dict(r) for r in rows]
    return [dict(r) for r in rows]

PH = "%s"  # postgres placeholder; sqlite uses ?

def ph(db_type):
    return "%s" if db_type == "pg" else "?"

def init_db():
    conn, db_type = get_db()
    p = ph(db_type)
    try:
        cur = conn.cursor()
        if db_type == "pg":
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id SERIAL PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT,
                    assignee TEXT NOT NULL,
                    owner TEXT DEFAULT 'Me',
                    due_date TEXT NOT NULL,
                    priority TEXT DEFAULT 'Medium',
                    status TEXT DEFAULT 'Pending',
                    category TEXT DEFAULT 'General',
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS notes (
                    id SERIAL PRIMARY KEY,
                    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    note TEXT NOT NULL,
                    author TEXT DEFAULT 'Me',
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS team_members (
                    id SERIAL PRIMARY KEY,
                    name TEXT UNIQUE NOT NULL
                )
            """)
            cur.execute("INSERT INTO team_members (name) VALUES ('Me') ON CONFLICT DO NOTHING")
        else:
            cur.executescript("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL, description TEXT,
                    assignee TEXT NOT NULL, owner TEXT DEFAULT 'Me',
                    due_date TEXT NOT NULL, priority TEXT DEFAULT 'Medium',
                    status TEXT DEFAULT 'Pending', category TEXT DEFAULT 'General',
                    created_at TEXT DEFAULT (datetime('now')),
                    updated_at TEXT DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL, note TEXT NOT NULL,
                    author TEXT DEFAULT 'Me',
                    created_at TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS team_members (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL
                );
                INSERT OR IGNORE INTO team_members (name) VALUES ('Me');
            """)
        conn.commit()
    finally:
        conn.close()

def fetchall(cur, db_type):
    rows = cur.fetchall()
    return [dict(r) if db_type == "sqlite" else dict(zip([d[0] for d in cur.description], r)) for r in rows]

def fetchone(cur, db_type):
    row = cur.fetchone()
    if row is None:
        return None
    return dict(row) if db_type == "sqlite" else dict(zip([d[0] for d in cur.description], row))

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    today = date.today().isoformat()
    return render_template("index.html", today=today)

@app.route("/api/tasks")
def get_tasks():
    conn, db_type = get_db()
    p = ph(db_type)
    today = date.today().isoformat()
    view = request.args.get("view", "all")
    assignee = request.args.get("assignee", "")
    status = request.args.get("status", "")

    where = ["1=1"]
    params = []

    if view == "today":
        where.append(f"due_date <= {p}")
        params.append(today)
    elif view == "mine":
        where.append("assignee = 'Me'")
    elif view == "team":
        where.append("assignee != 'Me'")

    if assignee:
        where.append(f"assignee = {p}")
        params.append(assignee)
    if status:
        where.append(f"status = {p}")
        params.append(status)

    order = ("ORDER BY CASE WHEN status='Done' THEN 1 ELSE 0 END, due_date ASC, "
             "CASE priority WHEN 'Critical' THEN 1 WHEN 'High' THEN 2 WHEN 'Medium' THEN 3 ELSE 4 END")
    query = f"SELECT * FROM tasks WHERE {' AND '.join(where)} {order}"

    try:
        cur = conn.cursor()
        cur.execute(query, params)
        tasks = fetchall(cur, db_type)
        today_d = date.today()
        for t in tasks:
            due = date.fromisoformat(str(t["due_date"])[:10])
            t["days_left"] = (due - today_d).days
            t["due_date"] = str(t["due_date"])[:10]
            cur.execute(f"SELECT * FROM notes WHERE task_id={p} ORDER BY created_at DESC", (t["id"],))
            t["notes"] = fetchall(cur, db_type)
            for n in t["notes"]:
                n["created_at"] = str(n["created_at"])[:10]
        return jsonify(tasks)
    finally:
        conn.close()

@app.route("/api/tasks", methods=["POST"])
def add_task():
    data = request.json
    conn, db_type = get_db()
    p = ph(db_type)
    try:
        cur = conn.cursor()
        cur.execute(
            f"INSERT INTO tasks (title, description, assignee, owner, due_date, priority, status, category) VALUES ({p},{p},{p},{p},{p},{p},{p},{p})",
            (data["title"], data.get("description",""), data["assignee"], data.get("owner","Me"),
             data["due_date"], data.get("priority","Medium"), "Pending", data.get("category","General"))
        )
        cur.execute(f"INSERT INTO team_members (name) VALUES ({p}) ON CONFLICT DO NOTHING" if db_type == "pg"
                    else f"INSERT OR IGNORE INTO team_members (name) VALUES ({p})", (data["assignee"],))
        conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()

@app.route("/api/tasks/<int:task_id>", methods=["PUT"])
def update_task(task_id):
    data = request.json
    conn, db_type = get_db()
    p = ph(db_type)
    allowed = ["title","description","assignee","due_date","priority","status","category"]
    fields, params = [], []
    for k in allowed:
        if k in data:
            fields.append(f"{k}={p}")
            params.append(data[k])
    if not fields:
        return jsonify({"ok": True})
    now_expr = "NOW()" if db_type == "pg" else "datetime('now')"
    fields.append(f"updated_at={now_expr}")
    params.append(task_id)
    try:
        cur = conn.cursor()
        cur.execute(f"UPDATE tasks SET {', '.join(fields)} WHERE id={p}", params)
        if "assignee" in data:
            cur.execute(f"INSERT INTO team_members (name) VALUES ({p}) ON CONFLICT DO NOTHING" if db_type == "pg"
                        else f"INSERT OR IGNORE INTO team_members (name) VALUES ({p})", (data["assignee"],))
        conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()

@app.route("/api/tasks/<int:task_id>", methods=["DELETE"])
def delete_task(task_id):
    conn, db_type = get_db()
    p = ph(db_type)
    try:
        cur = conn.cursor()
        cur.execute(f"DELETE FROM tasks WHERE id={p}", (task_id,))
        conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()

@app.route("/api/tasks/<int:task_id>/notes", methods=["POST"])
def add_note(task_id):
    data = request.json
    conn, db_type = get_db()
    p = ph(db_type)
    try:
        cur = conn.cursor()
        cur.execute(f"INSERT INTO notes (task_id, note, author) VALUES ({p},{p},{p})",
                    (task_id, data["note"], data.get("author","Me")))
        conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()

@app.route("/api/team")
def get_team():
    conn, db_type = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM team_members ORDER BY name")
        return jsonify([r[0] for r in cur.fetchall()])
    finally:
        conn.close()

@app.route("/api/team", methods=["POST"])
def add_member():
    data = request.json
    conn, db_type = get_db()
    p = ph(db_type)
    try:
        cur = conn.cursor()
        cur.execute(f"INSERT INTO team_members (name) VALUES ({p}) ON CONFLICT DO NOTHING" if db_type == "pg"
                    else f"INSERT OR IGNORE INTO team_members (name) VALUES ({p})", (data["name"],))
        conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()

@app.route("/api/stats")
def get_stats():
    conn, db_type = get_db()
    p = ph(db_type)
    today = date.today().isoformat()
    try:
        cur = conn.cursor()
        def count(q, args=()):
            cur.execute(q, args)
            return cur.fetchone()[0]
        today_date = "date(updated_at)" if db_type == "sqlite" else "updated_at::date"
        return jsonify({
            "total":      count(f"SELECT COUNT(*) FROM tasks WHERE status!='Done'"),
            "due_today":  count(f"SELECT COUNT(*) FROM tasks WHERE due_date={p} AND status!='Done'", (today,)),
            "overdue":    count(f"SELECT COUNT(*) FROM tasks WHERE due_date<{p} AND status!='Done'", (today,)),
            "mine":       count(f"SELECT COUNT(*) FROM tasks WHERE assignee='Me' AND status!='Done'"),
            "done_today": count(f"SELECT COUNT(*) FROM tasks WHERE status='Done' AND {today_date}={p}", (today,)),
        })
    finally:
        conn.close()

if __name__ == "__main__":
    init_db()
    print("\n Task Tracker running at: http://localhost:5000\n")
    app.run(debug=False, port=5000)

init_db()
