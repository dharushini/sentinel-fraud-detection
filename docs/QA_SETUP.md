# Natural-language Q&A setup (`/qa`)

The "Ask about this transaction" panel on any flagged case is a real LLM call
(via [Groq](https://console.groq.com), which has a free tier) — not a canned
response. It answers strictly from that one case's real decision, rules, and
feature data; the rest of Sentinel runs fully offline without this feature,
so treat it as optional polish, not a dependency.

## One-time setup

1. Create a free account at [console.groq.com](https://console.groq.com) (no
   credit card needed for the free tier).
2. Create an API key: **API Keys → Create API Key**. Copy it — Groq only
   shows it once.
3. In the project folder, create a file named `.env` (already gitignored —
   never commit it) with:

   ```
   GROQ_API_KEY=your-key-here
   ```

4. Before starting the server, load it into the shell:

   **macOS / Linux:**
   ```
   export $(cat .env | xargs)
   uvicorn sentinel.main:app --host 127.0.0.1 --port 8000
   ```

   **Windows (Command Prompt):**
   ```
   for /f "delims== tokens=1,2" %a in (.env) do set %a=%b
   uvicorn sentinel.main:app --host 127.0.0.1 --port 8000
   ```

   Or just set it once for the session before the usual `uvicorn` command:
   `set GROQ_API_KEY=your-key-here` (Windows) / `export GROQ_API_KEY=your-key-here` (macOS/Linux).

## Without a key

The rest of the dashboard works exactly the same. Clicking "Ask" without
`GROQ_API_KEY` set returns a clear, honest error in the panel — never a fake
or canned answer — telling you the key isn't configured.

## Talking to judges about it

This is a genuine LLM integration, but a narrow, grounded one: the model is
given the case's exact decision, risk score, rule hits, and feature
attributions as its only source of fact, and is explicitly instructed to say
"I don't know" rather than invent anything outside that data. It's
retrieval-grounded Q&A over real pipeline output, not a general chatbot with
opinions about fraud — worth saying plainly if asked, since it's a stronger
and more honest claim than "an AI that understands fraud."
