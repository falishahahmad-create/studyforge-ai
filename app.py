"""StudyForge AI - turn study materials into interactive learning (Streamlit + Groq)."""
import json
import random
import time
from datetime import date, timedelta

import pandas as pd
import streamlit as st

import ai
import db
import parsing

st.set_page_config(page_title="StudyForge AI", page_icon="📚", layout="wide")


@st.cache_resource
def _init_db():
    db.init()
    return True


_init_db()

NAV = ["Dashboard", "Library", "Summaries", "Flashcards", "Practice quiz", "Ask your notes", "Focus mode"]


# ------------------------------------------------------------------ auth
def auth_configured():
    try:
        return "auth" in st.secrets and "client_id" in st.secrets["auth"]
    except Exception:
        return False


def current_user():
    if auth_configured():
        if not st.user.is_logged_in:
            st.title("📚 StudyForge AI")
            st.write("Turn your notes and PDFs into summaries, flashcards and practice quizzes.")
            st.button("Sign in with Google", on_click=st.login, type="primary")
            st.stop()
        return st.user.email, getattr(st.user, "name", None) or st.user.email
    if str(ai.get_secret("ALLOW_GUEST", "")).lower() == "true":
        st.sidebar.warning("Guest mode: anyone can type any name. Use Google sign-in for real accounts.")
        name = st.sidebar.text_input("Guest name")
        if not name.strip():
            st.title("📚 StudyForge AI")
            st.info("Enter a guest name in the sidebar to begin.")
            st.stop()
        return "guest:" + name.strip().lower(), name.strip()
    st.title("📚 StudyForge AI")
    st.error("Sign-in is not configured yet. Add the [auth] secrets (see README), "
             "or set ALLOW_GUEST = \"true\" for a quick demo.")
    st.stop()


user, display_name = current_user()

st.session_state.setdefault("nav", "Dashboard")


def go(page):
    st.session_state["nav"] = page


with st.sidebar:
    st.header("📚 StudyForge AI")
    st.caption(f"Signed in as {display_name}")
    st.radio("Navigate", NAV, key="nav")
    st.toggle("Larger text", key="big")
    if auth_configured():
        st.button("Sign out", on_click=st.logout)

CSS = "<style>:focus-visible{outline:3px solid #f59e0b!important;outline-offset:2px}"
if st.session_state.get("big"):
    CSS += ".stApp p,.stApp li,.stApp label,.stApp span{font-size:1.2rem}"
if st.session_state["nav"] == "Focus mode":
    CSS += ('[data-testid="stSidebar"],[data-testid="stHeader"],[data-testid="stSidebarCollapsedControl"],'
            '[data-testid="collapsedControl"]{display:none!important}'
            ".block-container{max-width:760px;padding-top:2.5rem}")
st.markdown(CSS + "</style>", unsafe_allow_html=True)


# ------------------------------------------------------------------ helpers
def ref_text(page, has_pages):
    if not page:
        return ""
    return f"{'Page' if has_pages else 'Section'} {page}"


def track(kind):
    """Log study time since the last action (capped so idle breaks don't count)."""
    now = time.time()
    last = st.session_state.get("last_t")
    secs = min(now - last, 180) if last else 20
    st.session_state["last_t"] = now
    db.log_activity(user, kind, secs)


def pick_doc(key, allow_all=False):
    docs = db.list_documents(user)
    if not docs:
        st.info("Upload a document in the Library first.")
        st.button("Go to Library", on_click=go, args=("Library",))
        st.stop()
    labels = {d["id"]: d["title"] for d in docs}
    options = ([0] if allow_all else []) + list(labels)
    labels[0] = "All documents"
    return st.selectbox("Document", options, format_func=labels.get, key=key)


def page_scope(doc_id, key):
    doc = db.get_document(user, doc_id)
    pages = db.get_pages(user, doc_id)
    label = "Page" if doc["has_pages"] else "Section"
    if len(pages) > 1:
        nums = [p for p, _ in pages]
        lo, hi = st.select_slider(f"{label} range to use", options=nums,
                                  value=(nums[0], nums[min(len(nums) - 1, 7)]), key=key)
        pages = [x for x in pages if lo <= x[0] <= hi]
    st.caption(f"Up to ~{ai.MAX_CONTEXT_CHARS // 1000}k characters of the selected range are sent to the AI.")
    return pages, label


def ai_call(fn, *args):
    try:
        return fn(*args)
    except Exception as e:
        st.error(f"AI request failed: {e}")
        return None


# ------------------------------------------------------------------ pages
def dashboard():
    st.title(f"Welcome back, {display_name.split()[0] if display_name else 'learner'} 👋")
    o = db.overview(user)
    acc = (o["correct"] / o["attempts"] * 100) if o["attempts"] else None
    c = st.columns(4)
    c[0].metric("Documents", o["docs"])
    c[1].metric("Cards due now", o["due"], help=f"{o['cards']} cards in total")
    c[2].metric("Quiz accuracy", f"{acc:.0f}%" if acc is not None else "-")
    c[3].metric("Minutes this week", f"{o['minutes_week']:.0f}")
    if o["due"]:
        st.button(f"Start focused review ({o['due']} due)", type="primary", on_click=go, args=("Focus mode",))
    elif not o["docs"]:
        st.info("Start by uploading a PDF or pasting notes in the Library.")

    st.subheader("Study time (last 14 days)")
    rows = {r["day"]: r["minutes"] for r in db.minutes_by_day(user)}
    days = [(date.today() - timedelta(days=i)).isoformat() for i in range(13, -1, -1)]
    st.bar_chart(pd.DataFrame({"Minutes": [rows.get(d, 0) for d in days]}, index=days))

    st.subheader("Topics that need more practice")
    quiz, cards = db.topic_stats(user)
    df = pd.DataFrame(quiz or [], columns=["topic", "attempts", "correct"])
    cf = pd.DataFrame(cards or [], columns=["topic", "lapses"])
    merged = df.merge(cf, on="topic", how="outer").fillna(0)
    if merged.empty:
        st.caption("Practice a few questions or cards and your weak spots will appear here.")
        return
    merged["accuracy %"] = (merged["correct"] / merged["attempts"].replace(0, pd.NA) * 100).round(0)
    merged = merged.sort_values(["accuracy %", "lapses"], ascending=[True, False], na_position="last")
    weak = merged[(merged["accuracy %"] < 70) | (merged["lapses"] >= 2)]
    st.dataframe(weak if not weak.empty else merged.head(5), use_container_width=True, hide_index=True)


def library():
    st.title("Library")
    folders = db.list_folders(user)
    fmap = {0: "(No folder)"} | {f["id"]: f["name"] for f in folders}

    st.subheader("Add material")
    t1, t2, t3 = st.tabs(["Upload files", "Paste notes", "New folder"])
    with t1:
        target = st.selectbox("Save to folder", list(fmap), format_func=fmap.get, key="up_folder")
        files = st.file_uploader(f"PDF, TXT or MD (max {parsing.MAX_MB} MB each)",
                                 type=["pdf", "txt", "md"], accept_multiple_files=True)
        if st.button("Process files", disabled=not files, type="primary"):
            for f in files:
                try:
                    with st.spinner(f"Reading {f.name}..."):
                        pages, has_pages = parsing.parse_upload(f.name, f.getvalue())
                        db.add_document(user, target or None, f.name, pages, has_pages)
                    st.success(f"{f.name}: {len(pages)} {'pages' if has_pages else 'sections'} extracted.")
                except parsing.ParseError as e:
                    st.error(f"{f.name}: {e}")
    with t2:
        with st.form("notes", clear_on_submit=True):
            title = st.text_input("Title")
            body = st.text_area("Paste your notes", height=200)
            folder = st.selectbox("Folder", list(fmap), format_func=fmap.get, key="note_folder")
            if st.form_submit_button("Save notes"):
                try:
                    pages, has_pages = parsing.parse_text(body)
                    db.add_document(user, folder or None, (title.strip() or "Untitled notes")[:100], pages, has_pages)
                    st.success("Notes saved.")
                except parsing.ParseError as e:
                    st.error(str(e))
    with t3:
        with st.form("folder", clear_on_submit=True):
            name = st.text_input("Folder name")
            if st.form_submit_button("Create folder") and name.strip():
                db.create_folder(user, name.strip()[:60])
                st.rerun()

    st.divider()
    q = st.text_input("Search inside your documents", placeholder="e.g. photosynthesis")
    if q.strip():
        hits = db.search(user, q.strip())
        st.caption(f"{len(hits)} result(s)")
        for h in hits:
            i = max(h["text"].lower().find(q.strip().lower()), 0)
            snippet = h["text"][max(0, i - 80): i + 180].replace("\n", " ")
            st.markdown(f"**{h['title']}** · {ref_text(h['page'], h['has_pages'])}")
            st.caption(f"...{snippet}...")
        return

    opts = [-1] + list(fmap)
    labels = {-1: "All documents"} | fmap
    flt = st.selectbox("Show", opts, format_func=labels.get)
    docs = db.list_documents(user, flt)
    if not docs:
        st.info("Nothing here yet.")
    for d in docs:
        with st.expander(f"📄 {d['title']}  ({d['n_pages']} {'pages' if d['has_pages'] else 'sections'})"):
            preview = db.get_pages(user, d["id"])[0][1][:400]
            st.caption(preview + "...")
            if st.button("Delete document", key=f"del{d['id']}"):
                db.delete_document(user, d["id"])
                st.rerun()


def summaries():
    st.title("Summaries")
    doc_id = pick_doc("sum_doc")
    pages, label = page_scope(doc_id, "sum_scope")
    if st.button("Generate summary", type="primary"):
        with st.spinner("Summarizing..."):
            s = ai_call(ai.summarize, pages, label)
        if s:
            db.save_summary(user, doc_id, s)
    s = db.get_summary(user, doc_id)
    if not s:
        return
    md = []
    for t in s["topics"]:
        st.subheader(t["heading"])
        for p in t["points"]:
            st.markdown(f"- {p}")
        refs = ", ".join(f"{s['label']} {p}" for p in t["pages"])
        if refs:
            st.caption(f"Source: {refs}")
        md.append(f"## {t['heading']}\n" + "\n".join(f"- {p}" for p in t["points"]) + (f"\n\n*Source: {refs}*" if refs else ""))
    st.download_button("Download summary (.md)", "\n\n".join(md), file_name="summary.md")


def review_ui(doc_id=0, focus=False):
    cards = db.due_cards(user, doc_id)
    if not cards:
        st.success("🎉 No cards are due. Come back later or create more cards.")
        return
    c = cards[0]
    st.caption(f"{len(cards)} card(s) due · Topic: {c['topic'] or 'General'}")
    with st.container(border=True):
        st.markdown("**Question**")
        st.subheader(c["question"])
        shown = st.session_state.get("show_answer") == c["id"]
        if shown:
            st.markdown("**Answer**")
            st.write(c["answer"])
            r = ref_text(c["page"], c["has_pages"])
            st.caption(f"Source: {c['doc_title']}" + (f", {r}" if r else ""))
    if not shown:
        if st.button("Show answer", type="primary", use_container_width=True, key="showans"):
            st.session_state["show_answer"] = c["id"]
            st.rerun()
        return
    cols = st.columns(4)
    for col, (label, q) in zip(cols, [("Again", 1), ("Hard", 3), ("Good", 4), ("Easy", 5)]):
        if col.button(label, key=f"rate{q}", use_container_width=True):
            db.review_card(user, c["id"], q)
            track("flashcards")
            st.session_state["show_answer"] = None
            st.rerun()


def flashcards():
    st.title("Flashcards")
    t1, t2 = st.tabs(["Review due cards", "Create cards"])
    with t1:
        doc_id = pick_doc("rev_doc", allow_all=True)
        review_ui(doc_id)
        st.button("Distraction-free mode", on_click=go, args=("Focus mode",))
    with t2:
        doc_id = pick_doc("card_doc")
        pages, label = page_scope(doc_id, "card_scope")
        n = st.slider("Number of cards", 3, 15, 8)
        if st.button("Generate flashcards", type="primary"):
            with st.spinner("Creating cards..."):
                cards = ai_call(ai.generate_cards, pages, label, n)
            if cards:
                db.add_cards(user, doc_id, cards)
                st.success(f"Added {len(cards)} cards. They are due for review now.")


def quiz():
    st.title("Practice quiz")
    doc_id = pick_doc("quiz_doc")
    existing = db.list_questions(user, doc_id)
    with st.expander("Generate new questions", expanded=not existing):
        pages, label = page_scope(doc_id, "quiz_scope")
        n = st.slider("Number of questions", 3, 10, 6)
        if st.button("Generate questions", type="primary"):
            with st.spinner("Writing questions..."):
                qs = ai_call(ai.generate_quiz, pages, label, n)
            if qs:
                db.add_questions(user, doc_id, qs)
                st.success(f"Added {len(qs)} questions.")
                st.rerun()
    if not existing:
        return
    st.write(f"{len(existing)} questions saved for this document.")
    if st.button("Start practice session"):
        ids = [q["id"] for q in existing]
        random.shuffle(ids)
        st.session_state["quiz"] = {"doc": doc_id, "ids": ids, "i": 0, "score": 0, "graded": False, "revealed": False}
        st.rerun()
    qz = st.session_state.get("quiz")
    if not qz or qz["doc"] != doc_id:
        return
    qmap = {q["id"]: q for q in existing}
    ids = [i for i in qz["ids"] if i in qmap]
    if qz["i"] >= len(ids):
        st.success(f"Session finished: {qz['score']} / {len(ids)} correct.")
        return
    q = qmap[ids[qz["i"]]]
    st.progress(qz["i"] / len(ids), text=f"Question {qz['i'] + 1} of {len(ids)}")
    st.markdown(f"**{q['question']}**")

    def grade(correct):
        db.record_attempt(user, q["id"], q["topic"], correct)
        track("quiz")
        qz["graded"] = True
        qz["score"] += int(correct)
        qz["last_correct"] = correct

    if q["qtype"] == "mcq":
        choice = st.radio("Choose one", json.loads(q["options"]), index=None,
                          key=f"mc{q['id']}", disabled=qz["graded"], label_visibility="collapsed")
        if not qz["graded"] and st.button("Check answer", disabled=choice is None):
            grade(choice == q["answer"])
            st.rerun()
    else:
        st.text_area("Your answer (write it out first)", key=f"sa{q['id']}", disabled=qz["revealed"])
        if not qz["revealed"] and st.button("Reveal model answer"):
            qz["revealed"] = True
            st.rerun()
        if qz["revealed"]:
            st.info(f"Model answer: {q['answer']}")
            if not qz["graded"]:
                c1, c2 = st.columns(2)
                if c1.button("I got it right", use_container_width=True):
                    grade(True)
                    st.rerun()
                if c2.button("I missed it", use_container_width=True):
                    grade(False)
                    st.rerun()

    if qz["graded"]:
        if q["qtype"] == "mcq":
            (st.success if qz["last_correct"] else st.error)(
                "Correct!" if qz["last_correct"] else f"Not quite. Correct answer: {q['answer']}")
        if q["explanation"]:
            st.write(f"**Explanation (from your material):** {q['explanation']}")
        r = ref_text(q["page"], q["has_pages"])
        if r:
            st.caption(f"Source: {q['doc_title']}, {r}")
        if q["background"]:
            st.caption(f"Background knowledge (not from your document): {q['background']}")
        if st.button("Next question", type="primary"):
            qz["i"] += 1
            qz["graded"] = qz["revealed"] = False
            st.rerun()


def chat():
    st.title("Ask your notes")
    doc_id = pick_doc("chat_doc")
    doc = db.get_document(user, doc_id)
    label = "Page" if doc["has_pages"] else "Section"
    hist = st.session_state.setdefault("chat", {}).setdefault(doc_id, [])
    for m in hist:
        with st.chat_message(m["role"]):
            render_msg(m, label)
    question = st.chat_input("Ask a question about this document")
    if not question:
        return
    hist.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.write(question)
    with st.chat_message("assistant"):
        with st.spinner("Searching your document..."):
            res = ai_call(ai.ask, question, db.get_pages(user, doc_id), label, hist[:-1])
        if res:
            raw = res["answer"] or res["background"] or "Not found in the document."
            msg = {"role": "assistant", "content": raw, "raw": raw, "res": res}
            hist.append(msg)
            render_msg(msg, label)
            track("chat")
        else:
            hist.pop()


def render_msg(m, label):
    if m["role"] == "user":
        st.write(m["content"])
        return
    r = m["res"]
    if r["in_document"]:
        st.markdown("**From your document**")
        st.write(r["answer"])
        if r["pages"]:
            st.caption("Found in: " + ", ".join(f"{label} {p}" for p in r["pages"]))
        if r["quote"]:
            st.markdown(f"> {r['quote']}")
            st.caption("Quote checked against the document text.")
    else:
        st.warning("I could not find this in your document.")
    if r["background"]:
        st.markdown("**Background knowledge (not from your document)**")
        st.write(r["background"])


def focus():
    st.title("Focus mode")
    review_ui(0, focus=True)
    st.button("Exit focus mode", on_click=go, args=("Dashboard",))


{"Dashboard": dashboard, "Library": library, "Summaries": summaries, "Flashcards": flashcards,
 "Practice quiz": quiz, "Ask your notes": chat, "Focus mode": focus}[st.session_state["nav"]]()
