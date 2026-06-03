import os
import sqlite3
import smtplib
import threading
import time
from datetime import date, datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL and "sslmode" not in DATABASE_URL:
    DATABASE_URL += "?sslmode=require"

GMAIL_USER  = os.environ.get("GMAIL_USER", "lakshya110292@gmail.com")
GMAIL_PASS  = os.environ.get("GMAIL_PASS", "")
NOTIFY_EMAIL = os.environ.get("NOTIFY_EMAIL", "lakshya110292@gmail.com")
# Timezone offset in hours (IST = +5.5)
TZ_OFFSET = float(os.environ.get("TZ_OFFSET", "5.5"))

# ── Database helpers ──────────────────────────────────────────────────────────

def get_db():
    if DATABASE_URL:
        import psycopg2
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = False
        return conn, "pg"
    else:
        conn = sqlite3.connect("tasks.db")
        conn.row_factory = sqlite3.Row
        return conn, "sqlite"

def ph(db_type):
    return "%s" if db_type == "pg" else "?"

def fetchall(cur, db_type):
    rows = cur.fetchall()
    if db_type == "sqlite":
        return [dict(r) for r in rows]
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in rows]

def init_db():
    conn, db_type = get_db()
    try:
        cur = conn.cursor()
        if db_type == "pg":
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id SERIAL PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT DEFAULT '',
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
                    title TEXT NOT NULL, description TEXT DEFAULT '',
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

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", today=date.today().isoformat())

@app.route("/api/tasks")
def get_tasks():
    conn, db_type = get_db()
    p = ph(db_type)
    today = date.today().isoformat()
    view     = request.args.get("view", "all")
    assignee = request.args.get("assignee", "")
    status   = request.args.get("status", "")

    where, params = ["1=1"], []
    if view == "today":
        where.append(f"due_date <= {p}"); params.append(today)
    elif view == "mine":
        where.append("assignee = 'Me'")
    elif view == "team":
        where.append("assignee != 'Me'")
    if assignee:
        where.append(f"assignee = {p}"); params.append(assignee)
    if status:
        where.append(f"status = {p}"); params.append(status)

    order = ("ORDER BY CASE WHEN status='Done' THEN 1 ELSE 0 END, due_date ASC, "
             "CASE priority WHEN 'Critical' THEN 1 WHEN 'High' THEN 2 WHEN 'Medium' THEN 3 ELSE 4 END")
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT * FROM tasks WHERE {' AND '.join(where)} {order}", params)
        tasks = fetchall(cur, db_type)
        today_d = date.today()
        for t in tasks:
            due = date.fromisoformat(str(t["due_date"])[:10])
            t["days_left"] = (due - today_d).days
            t["due_date"]  = str(t["due_date"])[:10]
            cur.execute(f"SELECT * FROM notes WHERE task_id={p} ORDER BY created_at DESC", (t["id"],))
            notes = fetchall(cur, db_type)
            for n in notes:
                n["created_at"] = str(n["created_at"])[:10]
            t["notes"] = notes
        return jsonify(tasks)
    finally:
        conn.close()

@app.route("/api/tasks", methods=["POST"])
def add_task():
    try:
        data = request.json
        if not data.get("title") or not data.get("due_date") or not data.get("assignee"):
            return jsonify({"ok": False, "error": "title, due_date and assignee are required"}), 400
        conn, db_type = get_db()
        p = ph(db_type)
        try:
            cur = conn.cursor()
            cur.execute(
                f"INSERT INTO tasks (title,description,assignee,owner,due_date,priority,status,category) VALUES ({p},{p},{p},{p},{p},{p},{p},{p})",
                (data["title"], data.get("description",""), data["assignee"], data.get("owner","Me"),
                 data["due_date"], data.get("priority","Medium"), "Pending", data.get("category","General"))
            )
            if db_type == "pg":
                cur.execute(f"INSERT INTO team_members (name) VALUES ({p}) ON CONFLICT DO NOTHING", (data["assignee"],))
            else:
                cur.execute(f"INSERT OR IGNORE INTO team_members (name) VALUES ({p})", (data["assignee"],))
            conn.commit()
            return jsonify({"ok": True})
        except Exception as e:
            print(f"add_task DB error: {e}")
            return jsonify({"ok": False, "error": str(e)}), 500
        finally:
            conn.close()
    except Exception as e:
        print(f"add_task error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/tasks/<int:task_id>", methods=["PUT"])
def update_task(task_id):
    data = request.json
    conn, db_type = get_db()
    p = ph(db_type)
    allowed = ["title","description","assignee","due_date","priority","status","category"]
    fields, params = [], []
    for k in allowed:
        if k in data:
            fields.append(f"{k}={p}"); params.append(data[k])
    if not fields:
        return jsonify({"ok": True})
    now_expr = "NOW()" if db_type == "pg" else "datetime('now')"
    fields.append(f"updated_at={now_expr}")
    params.append(task_id)
    try:
        cur = conn.cursor()
        cur.execute(f"UPDATE tasks SET {', '.join(fields)} WHERE id={p}", params)
        if "assignee" in data:
            if db_type == "pg":
                cur.execute(f"INSERT INTO team_members (name) VALUES ({p}) ON CONFLICT DO NOTHING", (data["assignee"],))
            else:
                cur.execute(f"INSERT OR IGNORE INTO team_members (name) VALUES ({p})", (data["assignee"],))
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
    if not data.get("note"):
        return jsonify({"ok": False}), 400
    conn, db_type = get_db()
    p = ph(db_type)
    try:
        cur = conn.cursor()
        cur.execute(f"INSERT INTO notes (task_id,note,author) VALUES ({p},{p},{p})",
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
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False}), 400
    conn, db_type = get_db()
    p = ph(db_type)
    try:
        cur = conn.cursor()
        if db_type == "pg":
            cur.execute(f"INSERT INTO team_members (name) VALUES ({p}) ON CONFLICT DO NOTHING", (name,))
        else:
            cur.execute(f"INSERT OR IGNORE INTO team_members (name) VALUES ({p})", (name,))
        conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()

@app.route("/api/stats")
def get_stats():
    conn, db_type = get_db()
    p = ph(db_type)
    today = date.today().isoformat()
    today_date_expr = "date(updated_at)" if db_type == "sqlite" else "updated_at::date"
    try:
        cur = conn.cursor()
        def count(q, args=()):
            cur.execute(q, args); return cur.fetchone()[0]
        return jsonify({
            "total":      count("SELECT COUNT(*) FROM tasks WHERE status!='Done'"),
            "due_today":  count(f"SELECT COUNT(*) FROM tasks WHERE due_date={p} AND status!='Done'", (today,)),
            "overdue":    count(f"SELECT COUNT(*) FROM tasks WHERE due_date<{p} AND status!='Done'", (today,)),
            "mine":       count("SELECT COUNT(*) FROM tasks WHERE assignee='Me' AND status!='Done'"),
            "done_today": count(f"SELECT COUNT(*) FROM tasks WHERE status='Done' AND {today_date_expr}={p}", (today,)),
        })
    finally:
        conn.close()

# ── Email ─────────────────────────────────────────────────────────────────────

def build_email_html(overdue, due_today, upcoming, today):
    today_fmt = date.today().strftime("%A, %B %d %Y")

    def task_rows(tasks, section_today):
        if not tasks:
            return "<p style='color:#888;margin:0'>No tasks</p>"
        rows = ""
        for t in tasks:
            due = str(t["due_date"])[:10]
            color = "#e74c3c" if due < section_today else "#333"
            rows += f"""<tr>
              <td style='padding:8px 12px;border-bottom:1px solid #f0f0f0;font-weight:500'>{t['title']}</td>
              <td style='padding:8px 12px;border-bottom:1px solid #f0f0f0;color:#6c63ff'>{t['assignee']}</td>
              <td style='padding:8px 12px;border-bottom:1px solid #f0f0f0;color:{color};font-weight:600'>{due}</td>
              <td style='padding:8px 12px;border-bottom:1px solid #f0f0f0'>{t['priority']}</td>
            </tr>"""
        return f"""<table style='width:100%;border-collapse:collapse;font-size:14px'>
          <tr style='background:#f8f9fa'>
            <th style='padding:8px 12px;text-align:left;font-size:12px;color:#666'>TASK</th>
            <th style='padding:8px 12px;text-align:left;font-size:12px;color:#666'>ASSIGNEE</th>
            <th style='padding:8px 12px;text-align:left;font-size:12px;color:#666'>DUE DATE</th>
            <th style='padding:8px 12px;text-align:left;font-size:12px;color:#666'>PRIORITY</th>
          </tr>{rows}</table>"""

    return f"""<!DOCTYPE html><html><body style='margin:0;padding:20px;background:#f0f2f5'>
    <div style='font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;max-width:680px;margin:auto'>
      <div style='background:#1a1a2e;padding:28px 32px;border-radius:14px 14px 0 0'>
        <h1 style='color:#fff;margin:0;font-size:22px'>Task<span style='color:#6c63ff'>Flow</span> Daily Briefing</h1>
        <p style='color:#aaa;margin:8px 0 0;font-size:14px'>{today_fmt}</p>
      </div>
      <div style='background:#fff;padding:32px;border:1px solid #e0e0e0;border-top:none;border-radius:0 0 14px 14px'>

        <div style='background:#fff5f5;border-left:4px solid #e74c3c;padding:20px;border-radius:8px;margin-bottom:20px'>
          <h2 style='margin:0 0 14px;color:#e74c3c;font-size:15px'>🔴 Overdue — {len(overdue)} task{"s" if len(overdue)!=1 else ""}</h2>
          {task_rows(overdue, today)}
        </div>

        <div style='background:#fffdf0;border-left:4px solid #f39c12;padding:20px;border-radius:8px;margin-bottom:20px'>
          <h2 style='margin:0 0 14px;color:#856404;font-size:15px'>🟡 Due Today — {len(due_today)} task{"s" if len(due_today)!=1 else ""}</h2>
          {task_rows(due_today, today)}
        </div>

        <div style='background:#f0f4ff;border-left:4px solid #6c63ff;padding:20px;border-radius:8px'>
          <h2 style='margin:0 0 14px;color:#3730a3;font-size:15px'>🔵 Due This Week — {len(upcoming)} task{"s" if len(upcoming)!=1 else ""}</h2>
          {task_rows(upcoming, today)}
        </div>

        <p style='margin-top:28px;color:#bbb;font-size:12px;text-align:center'>
          Sent automatically by TaskFlow every morning at 8:00 AM
        </p>
      </div>
    </div></body></html>"""

def send_email(subject, html, force=False):
    if not GMAIL_PASS:
        print("No Gmail password set — skipping email")
        return False, "No Gmail password configured"
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = GMAIL_USER
        msg["To"]      = NOTIFY_EMAIL
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_USER, GMAIL_PASS)
            server.sendmail(GMAIL_USER, NOTIFY_EMAIL, msg.as_string())
        print(f"Email sent to {NOTIFY_EMAIL}: {subject}")
        return True, "Email sent successfully"
    except Exception as e:
        print(f"Email error: {e}")
        return False, str(e)

def get_task_summary():
    conn, db_type = get_db()
    p = ph(db_type)
    today = date.today().isoformat()
    try:
        cur = conn.cursor()
        if db_type == "pg":
            cur.execute(f"SELECT * FROM tasks WHERE due_date < {p} AND status!='Done' ORDER BY due_date ASC", (today,))
            overdue = fetchall(cur, db_type)
            cur.execute(f"SELECT * FROM tasks WHERE due_date = {p} AND status!='Done' ORDER BY assignee ASC", (today,))
            due_today = fetchall(cur, db_type)
            cur.execute(f"SELECT * FROM tasks WHERE due_date > {p} AND due_date <= ({p}::date + INTERVAL '7 days')::text AND status!='Done' ORDER BY due_date ASC", (today, today))
        else:
            cur.execute(f"SELECT * FROM tasks WHERE due_date < {p} AND status!='Done' ORDER BY due_date ASC", (today,))
            overdue = fetchall(cur, db_type)
            cur.execute(f"SELECT * FROM tasks WHERE due_date = {p} AND status!='Done' ORDER BY assignee ASC", (today,))
            due_today = fetchall(cur, db_type)
            cur.execute(f"SELECT * FROM tasks WHERE due_date > {p} AND due_date <= date({p},'+7 days') AND status!='Done' ORDER BY due_date ASC", (today, today))
        upcoming = fetchall(cur, db_type)
        for lst in [overdue, due_today, upcoming]:
            for t in lst:
                t["due_date"] = str(t["due_date"])[:10]
        return overdue, due_today, upcoming, today
    finally:
        conn.close()

@app.route("/api/send-test-email", methods=["POST"])
def send_test_email():
    try:
        overdue, due_today, upcoming, today = get_task_summary()
        today_fmt = date.today().strftime("%A, %B %d %Y")
        html = build_email_html(overdue, due_today, upcoming, today)
        ok, msg = send_email(f"[TEST] TaskFlow Daily Briefing — {today_fmt}", html, force=True)
        return jsonify({"ok": ok, "message": msg})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500

def send_scheduled_email():
    overdue, due_today, upcoming, today = get_task_summary()
    today_fmt = date.today().strftime("%A, %B %d %Y")
    html = build_email_html(overdue, due_today, upcoming, today)
    send_email(f"TaskFlow Daily Briefing — {today_fmt}", html)

def email_scheduler():
    """Runs in background. Sends email at 8 AM in user's timezone."""
    sent_today = None
    while True:
        try:
            utc_now = datetime.now(timezone.utc)
            offset  = timedelta(hours=TZ_OFFSET)
            local_now = utc_now + offset
            if local_now.hour == 8 and local_now.minute < 5:
                today_str = local_now.strftime("%Y-%m-%d")
                if sent_today != today_str:
                    send_scheduled_email()
                    sent_today = today_str
        except Exception as e:
            print(f"Scheduler error: {e}")
        time.sleep(60)

# ── Startup ───────────────────────────────────────────────────────────────────

with app.app_context():
    try:
        init_db()
        print("Database initialized successfully")
    except Exception as e:
        print(f"DB init error: {e}")

if GMAIL_PASS:
    t = threading.Thread(target=email_scheduler, daemon=True)
    t.start()
    print("Email scheduler started (sends at 8 AM IST daily)")

if __name__ == "__main__":
    print("\nTask Tracker running at: http://localhost:5000\n")
    app.run(debug=False, port=5000)
