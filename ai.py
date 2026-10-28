"""Groq-powered generation with grounding checks (no invented pages or quotes)."""
import json
import os
import random
import re

import streamlit as st

MAX_CONTEXT_CHARS = 12000  # keeps requests inside Groq's free-tier token limits


def get_secret(name, default=""):
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass
    return os.environ.get(name, default)


def _client():
    from groq import Groq
    key = get_secret("GROQ_API_KEY")
    if not key:
        raise RuntimeError("GROQ_API_KEY is missing. Add it in Streamlit Settings > Secrets.")
    return Groq(api_key=key)


def _chat_json(messages, temperature=0.2, max_tokens=2500):
    model = get_secret("GROQ_MODEL", "llama-3.3-70b-versatile")
    resp = _client().chat.completions.create(
        model=model, messages=messages, temperature=temperature, max_tokens=max_tokens,
        response_format={"type": "json_object"})
    try:
        return json.loads(resp.choices[0].message.content)
    except (json.JSONDecodeError, TypeError):
        raise RuntimeError("The AI returned an unreadable answer. Please try again.")


RULES = (
    "You are a careful study assistant. The source material is inside <source> tags and each part "
    "is tagged like [Page 3] or [Section 3].\n"
    "Rules:\n"
    "- Facts attributed to the document must come ONLY from the source.\n"
    "- Never invent quotations, statistics, citations or page numbers. Page values must be numbers "
    "copied from the tags; use null when unsure.\n"
    "- General knowledge that is not in the source goes ONLY in 'background' fields, kept short.\n"
    "- Respond with a single valid JSON object and nothing else (JSON only)."
)


def build_context(pages, label, max_chars=MAX_CONTEXT_CHARS):
    parts, used, included = [], 0, set()
    for n, text in pages:
        room = max_chars - used
        if room <= 200:
            break
        chunk = text[:room]
        parts.append(f"[{label} {n}]\n{chunk}\n")
        used += len(chunk) + 12
        included.add(n)
    return "\n".join(parts), included


def _page(value, valid):
    try:
        v = int(value)
    except (TypeError, ValueError):
        return None
    return v if v in valid else None


def _s(x, limit=700):
    return str(x).strip()[:limit] if x else ""


def summarize(pages, label):
    ctx, valid = build_context(pages, label)
    data = _chat_json([
        {"role": "system", "content": RULES},
        {"role": "user", "content":
            f"<source>\n{ctx}\n</source>\nSummarize the source by topic and heading. Return JSON: "
            '{"topics":[{"heading":"...","points":["short bullet","..."],"pages":[numbers]}]}. '
            "Use 3-8 topics, 2-5 bullets each."}])
    topics = []
    for t in data.get("topics", []):
        pts = [_s(p, 400) for p in t.get("points", []) if p]
        if t.get("heading") and pts:
            refs = sorted({p for p in (_page(x, valid) for x in t.get("pages", [])) if p})
            topics.append({"heading": _s(t["heading"], 120), "points": pts, "pages": refs})
    if not topics:
        raise RuntimeError("Could not build a summary from this text.")
    return {"label": label, "topics": topics}


def generate_cards(pages, label, n=8):
    ctx, valid = build_context(pages, label)
    data = _chat_json([
        {"role": "system", "content": RULES},
        {"role": "user", "content":
            f"<source>\n{ctx}\n</source>\nCreate {n} flashcards testing the key ideas. Return JSON: "
            '{"cards":[{"topic":"short topic","question":"...","answer":"...","page":number or null}]}. '
            "Answers must be supported by the source and concise."}], temperature=0.3)
    cards = []
    for c in data.get("cards", []):
        if c.get("question") and c.get("answer"):
            cards.append({"topic": _s(c.get("topic"), 80) or "General", "question": _s(c["question"]),
                          "answer": _s(c["answer"]), "page": _page(c.get("page"), valid)})
    if not cards:
        raise RuntimeError("No flashcards could be created. Try a different range.")
    return cards


def generate_quiz(pages, label, n=6):
    ctx, valid = build_context(pages, label)
    data = _chat_json([
        {"role": "system", "content": RULES},
        {"role": "user", "content":
            f"<source>\n{ctx}\n</source>\nWrite {n} practice questions (about 60% multiple-choice, "
            "40% short-answer). Return JSON: "
            '{"questions":[{"type":"mcq" or "short","topic":"...","question":"...",'
            '"options":["4 options for mcq, empty list for short"],'
            '"answer":"mcq: the exact text of the correct option; short: a model answer",'
            '"explanation":"why, grounded in the source","background":"optional extra context not in the source, or empty",'
            '"page":number or null}]}.'}], temperature=0.4, max_tokens=3000)
    out = []
    for q in data.get("questions", []):
        qtype = q.get("type")
        if not q.get("question") or not q.get("answer") or qtype not in ("mcq", "short"):
            continue
        item = {"type": qtype, "topic": _s(q.get("topic"), 80) or "General",
                "question": _s(q["question"]), "answer": _s(q["answer"]),
                "explanation": _s(q.get("explanation")), "background": _s(q.get("background")),
                "page": _page(q.get("page"), valid), "options": []}
        if qtype == "mcq":
            opts = list(dict.fromkeys(_s(o, 300) for o in q.get("options", []) if o))
            if len(opts) < 3:
                continue
            if item["answer"] not in opts:
                m = re.match(r"^\(?([A-Da-d])[\).:]?$", item["answer"])
                if m and ord(m.group(1).upper()) - 65 < len(opts):
                    item["answer"] = opts[ord(m.group(1).upper()) - 65]
                else:
                    continue
            random.shuffle(opts)
            item["options"] = opts
        out.append(item)
    if not out:
        raise RuntimeError("No valid questions were produced. Try again or pick another range.")
    return out


STOP = {"the", "and", "what", "which", "that", "this", "with", "from", "have", "does", "about",
        "how", "why", "when", "who", "are", "was", "were", "for", "into", "your", "can", "you"}


def retrieve(question, pages, k=5):
    words = set(re.findall(r"[a-z0-9]{3,}", question.lower())) - STOP
    scored = [(sum(t.lower().count(w) for w in words), n, t) for n, t in pages]
    scored.sort(key=lambda x: (-x[0], x[1]))
    top = [(n, t) for s, n, t in scored[:k] if s > 0] or [(n, t) for _, n, t in scored[:k]]
    return sorted(top)


def _norm(s):
    return re.sub(r"\s+", " ", s).lower().strip(" \"'“”")


def ask(question, pages, label, history):
    top = retrieve(question, pages)
    ctx, valid = build_context(top, label)
    msgs = [{"role": "system", "content":
             RULES + "\nAnswer the user's question. Return JSON: "
             '{"in_document":true or false,"answer":"what the source says, in your own words",'
             '"pages":[numbers],"quote":"a short exact quote (max 25 words) copied character-for-character '
             'from the source, or null","background":"extra general knowledge beyond the source, or empty"}. '
             "If the source does not contain the answer set in_document to false and leave answer empty."}]
    for m in history[-6:]:
        msgs.append({"role": m["role"], "content": m["content"] if m["role"] == "user" else m.get("raw", "")})
    msgs.append({"role": "user", "content": f"<source>\n{ctx}\n</source>\nQuestion: {question}"})
    data = _chat_json(msgs, temperature=0.2, max_tokens=1200)

    pgs = sorted({p for p in (_page(x, valid) for x in data.get("pages", [])) if p})
    quote, verified = _s(data.get("quote"), 400) or None, False
    if quote:
        haystack = " ".join(_norm(t) for n, t in top if not pgs or n in pgs)
        verified = _norm(quote) in haystack
        if not verified:
            quote = None  # never show a quote we cannot find in the source
    in_doc = bool(data.get("in_document")) and bool(_s(data.get("answer")))
    return {"in_document": in_doc, "answer": _s(data.get("answer"), 2500) if in_doc else "",
            "pages": pgs if in_doc else [], "quote": quote if in_doc else None,
            "background": _s(data.get("background"), 1500)}
