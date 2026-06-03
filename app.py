import os
import sqlite3
import smtplib
import threading
import time
from datetime import date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
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

GMAIL_USER = os.environ.get("GMAIL_USER", "lakshya110292@gmail.com")
GMAIL_PASS = os.environ.get("GMAIL_PASS", "")
NOTIFY_EMAIL = os.environ.get("NOTIFY_EMAIL", "lakshya110292@gmail.com")

def send_daily_email():
    conn, db_type = get_db()
    p = ph(db_type)
    today = date.today().isoformat()
    try:
        cur = conn.cursor()

        cur.execute(f"SELECT * FROM tasks WHERE due_date < {p} AND status != 'Done' ORDER BY due_date ASC", (today,))
        overdue = fetchall(cur, db_type)

        cur.execute(f"SELECT * FROM tasks WHERE due_date = {p} AND status != 'Done' ORDER BY assignee ASC", (today,))
        due_today = fetchall(cur, db_type)

        cur.execute(f"""SELECT * FROM tasks WHERE due_date > {p} AND due_date <= date({p}, '+7 days') AND status != 'Done' ORDER BY due_date ASC""" if db_type == "sqlite"
                    else f"SELECT * FROM tasks WHERE due_date > {p} AND due_date <= CURRENT_DATE + INTERVAL '7 days' AND status != 'Done' ORDER BY due_date ASC",
                    (today, today) if db_type == "sqlite" else (today,))
        upcoming = fetchall(cur, db_type)

    finally:
        conn.close()

    if not overdue and not due_today and not upcoming:
        return

    today_fmt = date.today().strftime("%A, %B %d %Y")

    def task_rows(tasks):
        if not tasks:
            return "<p style='color:#888'>None</p>"
        rows = ""
        for t in tasks:
            due = str(t["due_date"])[:10]
            color = "#e74c3c" if str(t["due_date"])[:10] < today else "#333"
            rows += f"""
            <tr>
              <td style='padding:8px 12px;border-bottom:1px solid #f0f0f0'>{t['title']}</td>
              <td style='padding:8px 12px;border-bottom:1px solid #f0f0f0;color:#6c63ff'>{t['assignee']}</td>
              <td style='padding:8px 12px;border-bottom:1px solid #f0f0f0;color:{color};font-weight:600'>{due}</td>
              <td style='padding:8px 12px;border-bottom:1px solid #f0f0f0'>{t['priority']}</td>
            </tr>"""
        return f"<table style='width:100%;border-collapse:collapse'><tr style='background:#f8f9fa'><th style='padding:8px 12px;text-align:left'>Task</th><th style='padding:8px 12px;text-align:left'>Assignee</th><th style='padding:8px 12px;text-align:left'>Due</th><th style='padding:8px 12px;text-align:left'>Priority</th></tr>{rows}</table>"

    html = f"""
    <div style='font-family:sans-serif;max-width:700px;margin:auto'>
      <div style='background:#1a1a2e;padding:24px 32px;border-radius:14px 14px 0 0'>
        <h1 style='color:#fff;margin:0;font-size:22px'>TaskFlow <span style='color:#6c63ff'>Daily Briefing</span></h1>
        <p style='color:#aaa;margin:6px 0 0'>{today_fmt}</p>
      </div>
      <div style='background:#fff;padding:32px;border:1px solid #e0e0e0;border-top:none;border-radius:0 0 14px 14px'>

        <div style='background:#fde8e8;border-left:4px solid #e74c3c;padding:16px 20px;border-radius:8px;margin-bottom:24px'>
          <h2 style='margin:0 0 12px;color:#e74c3c;font-size:16px'>Overdue ({len(overdue)})</h2>
          {task_rows(overdue)}
        </div>

        <div style='background:#fff3cd;border-left:4px solid #f39c12;padding:16px 20px;border-radius:8px;margin-bottom:24px'>
          <h2 style='margin:0 0 12px;color:#856404;font-size:16px'>Due Today ({len(due_today)})</h2>
          {task_rows(due_today)}
        </div>

        <div style='background:#e8f4fd;border-left:4px solid #6c63ff;padding:16px 20px;border-radius:8px'>
          <h2 style='margin:0 0 12px;color:#1976d2;font-size:16px'>Due This Week ({len(upcoming)})</h2>
          {task_rows(upcoming)}
        </div>

        <p style='margin-top:24px;color:#aaa;font-size:12px;text-align:center'>Sent by TaskFlow &bull; Your daily task briefing</p>
      </div>
    </div>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"TaskFlow Daily Briefing — {today_fmt}"
    msg["From"] = GMAIL_USER
    msg["To"] = NOTIFY_EMAIL
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_USER, GMAIL_PASS)
            server.sendmail(GMAIL_USER, NOTIFY_EMAIL, msg.as_string())
        print(f"Daily email sent to {NOTIFY_EMAIL}")
    except Exception as e:
        print(f"Email error: {e}")

def email_scheduler():
    while True:
        now = datetime.now()
        # Send at 8:00 AM
        if now.hour == 8 and now.minute == 0:
            send_daily_email()
            time.sleep(61)  # avoid double-send within the same minute
        time.sleep(30)

with app.app_context():
    try:
        init_db()
    except Exception as e:
        print(f"DB init error: {e}")

# Start background email scheduler
if GMAIL_PASS:
    t = threading.Thread(target=email_scheduler, daemon=True)
    t.start()

if __name__ == "__main__":
    print("\n Task Tracker running at: http://localhost:5000\n")
    app.run(debug=False, port=5000)
