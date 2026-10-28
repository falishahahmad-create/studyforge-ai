"""SQLite storage for StudyForge AI. Every query is scoped to the signed-in user."""
import json
import os
import sqlite3
import time
from contextlib import contextmanager

DB_PATH = os.environ.get("STUDYFORGE_DB", "studyforge.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS folders(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT NOT NULL, name TEXT NOT NULL,
  UNIQUE(user, name));
CREATE TABLE IF NOT EXISTS documents(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT NOT NULL, folder_id INTEGER,
  title TEXT NOT NULL, has_pages INTEGER NOT NULL DEFAULT 1, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS pages(
  doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  page INTEGER NOT NULL, text TEXT NOT NULL, PRIMARY KEY(doc_id, page));
CREATE TABLE IF NOT EXISTS summaries(
  doc_id INTEGER PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
  data TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS cards(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT NOT NULL,
  doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  topic TEXT, question TEXT NOT NULL, answer TEXT NOT NULL, page INTEGER,
  ease REAL NOT NULL DEFAULT 2.5, interval REAL NOT NULL DEFAULT 0,
  reps INTEGER NOT NULL DEFAULT 0, lapses INTEGER NOT NULL DEFAULT 0,
  due REAL NOT NULL);
CREATE TABLE IF NOT EXISTS questions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT NOT NULL,
  doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  topic TEXT, qtype TEXT NOT NULL, question TEXT NOT NULL, options TEXT,
  answer TEXT NOT NULL, explanation TEXT, background TEXT, page INTEGER);
CREATE TABLE IF NOT EXISTS attempts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT NOT NULL, question_id INTEGER,
  topic TEXT, correct INTEGER NOT NULL, ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS activity(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT NOT NULL, kind TEXT NOT NULL,
  seconds REAL NOT NULL, ts REAL NOT NULL);
"""


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init():
    with conn() as c:
        c.executescript(SCHEMA)


def _rows(cur):
    return [dict(r) for r in cur.fetchall()]


# ---------- folders & documents ----------
def create_folder(user, name):
    with conn() as c:
        c.execute("INSERT OR IGNORE INTO folders(user,name) VALUES(?,?)", (user, name))


def list_folders(user):
    with conn() as c:
        return _rows(c.execute("SELECT * FROM folders WHERE user=? ORDER BY name", (user,)))


def add_document(user, folder_id, title, pages, has_pages):
    with conn() as c:
        cur = c.execute(
            "INSERT INTO documents(user,folder_id,title,has_pages,created) VALUES(?,?,?,?,?)",
            (user, folder_id, title, int(has_pages), time.time()),
        )
        doc_id = cur.lastrowid
        c.executemany("INSERT INTO pages(doc_id,page,text) VALUES(?,?,?)",
                      [(doc_id, p, t) for p, t in pages])
        return doc_id


def list_documents(user, folder_id=-1):
    """folder_id: -1 = all, 0 = unfiled, otherwise a folder id."""
    sql = ("SELECT d.*, (SELECT COUNT(*) FROM pages p WHERE p.doc_id=d.id) AS n_pages "
           "FROM documents d WHERE d.user=?")
    args = [user]
    if folder_id == 0:
        sql += " AND d.folder_id IS NULL"
    elif folder_id > 0:
        sql += " AND d.folder_id=?"
        args.append(folder_id)
    with conn() as c:
        return _rows(c.execute(sql + " ORDER BY d.created DESC", args))


def get_document(user, doc_id):
    with conn() as c:
        r = c.execute("SELECT * FROM documents WHERE id=? AND user=?", (doc_id, user)).fetchone()
        return dict(r) if r else None


def get_pages(user, doc_id):
    with conn() as c:
        cur = c.execute(
            "SELECT p.page, p.text FROM pages p JOIN documents d ON d.id=p.doc_id "
            "WHERE d.id=? AND d.user=? ORDER BY p.page", (doc_id, user))
        return [(r["page"], r["text"]) for r in cur.fetchall()]


def delete_document(user, doc_id):
    with conn() as c:
        c.execute("DELETE FROM documents WHERE id=? AND user=?", (doc_id, user))


def search(user, term, limit=30):
    like = "%" + term.replace("%", "").replace("_", "") + "%"
    with conn() as c:
        return _rows(c.execute(
            "SELECT d.id AS doc_id, d.title, d.has_pages, p.page, p.text FROM pages p "
            "JOIN documents d ON d.id=p.doc_id WHERE d.user=? AND p.text LIKE ? "
            "ORDER BY d.title, p.page LIMIT ?", (user, like, limit)))


# ---------- summaries ----------
def save_summary(user, doc_id, data):
    if not get_document(user, doc_id):
        return
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO summaries(doc_id,data,created) VALUES(?,?,?)",
                  (doc_id, json.dumps(data), time.time()))


def get_summary(user, doc_id):
    if not get_document(user, doc_id):
        return None
    with conn() as c:
        r = c.execute("SELECT data FROM summaries WHERE doc_id=?", (doc_id,)).fetchone()
        return json.loads(r["data"]) if r else None


# ---------- flashcards (SM-2 spaced repetition) ----------
def add_cards(user, doc_id, cards):
    if not get_document(user, doc_id):
        return
    now = time.time()
    with conn() as c:
        c.executemany(
            "INSERT INTO cards(user,doc_id,topic,question,answer,page,due) VALUES(?,?,?,?,?,?,?)",
            [(user, doc_id, k.get("topic"), k["question"], k["answer"], k.get("page"), now) for k in cards])


def due_cards(user, doc_id=0, limit=50):
    sql = ("SELECT c.*, d.title AS doc_title, d.has_pages FROM cards c "
           "JOIN documents d ON d.id=c.doc_id WHERE c.user=? AND c.due<=?")
    args = [user, time.time()]
    if doc_id:
        sql += " AND c.doc_id=?"
        args.append(doc_id)
    with conn() as c:
        return _rows(c.execute(sql + " ORDER BY c.due LIMIT ?", args + [limit]))


def review_card(user, card_id, quality):
    """quality: 1=Again, 3=Hard, 4=Good, 5=Easy (SM-2 scale)."""
    with conn() as c:
        r = c.execute("SELECT * FROM cards WHERE id=? AND user=?", (card_id, user)).fetchone()
        if not r:
            return
        ease, interval, reps, lapses = r["ease"], r["interval"], r["reps"], r["lapses"]
        now = time.time()
        if quality < 3:
            reps, interval, lapses = 0, 0.0, lapses + 1
            due = now + 600  # see it again in 10 minutes
        else:
            reps += 1
            if reps == 1:
                interval = 1.0
            elif reps == 2:
                interval = 6.0
            else:
                interval = round(interval * ease, 1)
            if quality == 3:
                interval = max(1.0, round(interval * 0.8, 1))
            due = now + interval * 86400
        ease = max(1.3, ease + 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
        c.execute("UPDATE cards SET ease=?,interval=?,reps=?,lapses=?,due=? WHERE id=?",
                  (ease, interval, reps, lapses, due, card_id))


# ---------- questions & attempts ----------
def add_questions(user, doc_id, qs):
    if not get_document(user, doc_id):
        return
    with conn() as c:
        c.executemany(
            "INSERT INTO questions(user,doc_id,topic,qtype,question,options,answer,explanation,background,page) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            [(user, doc_id, q.get("topic"), q["type"], q["question"],
              json.dumps(q.get("options") or []), q["answer"], q.get("explanation"),
              q.get("background"), q.get("page")) for q in qs])


def list_questions(user, doc_id):
    with conn() as c:
        return _rows(c.execute(
            "SELECT q.*, d.title AS doc_title, d.has_pages FROM questions q "
            "JOIN documents d ON d.id=q.doc_id WHERE q.user=? AND q.doc_id=?", (user, doc_id)))


def record_attempt(user, question_id, topic, correct):
    with conn() as c:
        c.execute("INSERT INTO attempts(user,question_id,topic,correct,ts) VALUES(?,?,?,?,?)",
                  (user, question_id, topic or "General", int(correct), time.time()))


# ---------- activity & stats ----------
def log_activity(user, kind, seconds):
    with conn() as c:
        c.execute("INSERT INTO activity(user,kind,seconds,ts) VALUES(?,?,?,?)",
                  (user, kind, float(seconds), time.time()))


def overview(user):
    week = time.time() - 7 * 86400
    with conn() as c:
        one = lambda sql, *a: c.execute(sql, a).fetchone()[0] or 0
        return {
            "docs": one("SELECT COUNT(*) FROM documents WHERE user=?", user),
            "cards": one("SELECT COUNT(*) FROM cards WHERE user=?", user),
            "due": one("SELECT COUNT(*) FROM cards WHERE user=? AND due<=?", user, time.time()),
            "attempts": one("SELECT COUNT(*) FROM attempts WHERE user=?", user),
            "correct": one("SELECT SUM(correct) FROM attempts WHERE user=?", user),
            "minutes_week": one("SELECT SUM(seconds) FROM activity WHERE user=? AND ts>=?", user, week) / 60.0,
        }


def minutes_by_day(user, days=14):
    since = time.time() - days * 86400
    with conn() as c:
        return _rows(c.execute(
            "SELECT date(ts,'unixepoch') AS day, SUM(seconds)/60.0 AS minutes FROM activity "
            "WHERE user=? AND ts>=? GROUP BY day", (user, since)))


def topic_stats(user):
    with conn() as c:
        quiz = _rows(c.execute(
            "SELECT topic, COUNT(*) AS attempts, SUM(correct) AS correct FROM attempts "
            "WHERE user=? GROUP BY topic", (user,)))
        cards = _rows(c.execute(
            "SELECT COALESCE(topic,'General') AS topic, SUM(lapses) AS lapses FROM cards "
            "WHERE user=? GROUP BY topic", (user,)))
    return quiz, cards
