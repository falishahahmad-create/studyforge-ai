# studyforge-ai
AI-powered interactive learning platform
# StudyForge AI

Upload PDFs or notes and get summaries, flashcards, practice quizzes, spaced-repetition review,
progress tracking and a chat assistant grounded in your own material.

**Stack:** Streamlit (UI) · Groq API (AI) · SQLite (database) · Google sign-in (authentication) · pypdf (PDF parsing)

## Files

| File | Purpose |
|---|---|
| `app.py` | The whole interface (dashboard, library, summaries, flashcards, quiz, chat, focus mode) |
| `ai.py` | Groq calls plus checks that stop invented page numbers and quotes |
| `db.py` | Database tables and spaced-repetition (SM-2) logic |
| `parsing.py` | Upload validation and PDF/text extraction |
| `requirements.txt` | Python packages Streamlit installs |
| `secrets_example.toml` | Template for your private keys (paste into Streamlit Secrets, never commit real keys) |
| `.gitignore` | Keeps the database and secrets out of GitHub |

## How it keeps the AI honest

- Page/section numbers are accepted only if they match a tag that was actually sent to the model.
- Quotes in chat answers are shown only if they are found word-for-word in the document text.
- Answers are split into **From your document** and **Background knowledge (not from your document)**.
- Plain text notes have no real pages, so they are labelled "Section", never "Page".

## Deploy (no VS Code needed)

1. **Groq key:** go to https://console.groq.com/keys, create an API key and copy it.
2. **GitHub:** create a new repository, click *Add file > Upload files*, drag in every file from this
   folder (`app.py`, `ai.py`, `db.py`, `parsing.py`, `requirements.txt`, `README.md`, `.gitignore`,
   `secrets_example.toml`) and commit. Do not upload a real `secrets.toml`.
3. **Streamlit Cloud:** go to https://share.streamlit.io, *Create app*, pick your repo, branch `main`,
   main file `app.py`. Open *Advanced settings* and choose a custom app URL
   (for example `studyforge-yourname`). Paste the secrets (below) into the Secrets box and deploy.
4. **Google sign-in:** in https://console.cloud.google.com create a project, then
   *APIs & Services > OAuth consent screen* (External, app name, your email; add yourself as a test user),
   then *Credentials > Create credentials > OAuth client ID > Web application*. Under
   *Authorized redirect URIs* add `https://YOUR-APP-NAME.streamlit.app/oauth2callback`.
   Copy the client ID and secret into the `[auth]` block of your secrets.

Quick demo without Google: use only `GROQ_API_KEY` and `ALLOW_GUEST = "true"` in secrets.
Guest mode is not secure, so switch to Google sign-in for real use.

## Important limitation

Streamlit Cloud's disk is temporary. The SQLite file (`studyforge.db`) can be wiped when the app
restarts or is redeployed. For permanent progress, move the database to a free hosted Postgres
(Supabase or Neon).

## Limits

PDF max 10 MB and 300 pages; scanned PDFs (images only) are rejected because they need OCR.
Each AI request uses up to about 12k characters of the page range you select, to stay inside
Groq's free limits.
