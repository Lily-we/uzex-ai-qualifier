# UZEX AI Qualification Agent

Evidence-first agent that translates Uzbek/Russian questions into read-only SQLite queries over the **500,000 fictional UZEX deals** supplied for the 2026 qualification round. Answers contain only database outputs and show the executed SQL. Missing data and unsafe SQL result in refusal rather than hallucinations.

## Contents
- `app.py`: Gradio UI, Gemini NL->SQL planner, safety authorizer, SQLite loader, and SQL evidence display.
- `uzex_spot_data.zip`: unchanged competition-provided synthetic data, including original bilingual README.
- `requirements.txt`: runtime dependencies.
- `render.yaml`: Render web service configuration.

## Local run

1. Install Python 3.11+.
2. `pip install -r requirements.txt`
3. Set an environment variable `GEMINI_API_KEY` in your local terminal (do NOT paste it into source code or commit it).
4. `python app.py`
5. Open `http://localhost:7860`.

On first launch the app extracts the ZIP, converts 500,000 deals plus reference files into an indexed SQLite database, and caches it. Later starts reuse the cached DB when persistent storage exists.

## Render deployment

1. Create a **private** or public GitHub repository for this folder; include `uzex_spot_data.zip` (26 MB), `app.py`, `requirements.txt`, and `render.yaml`. Do not include `.env`, API keys, or `.sqlite3`.
2. In Render, select New -> Blueprint (or New -> Web Service), and connect that repository. It recognizes `render.yaml`.
3. Under the Render service's Environment settings, add `GEMINI_API_KEY` as a secret environment variable. Get the key in Google AI Studio; never expose it to browsers or Git.
4. Confirm the public URL accepts a question without signing in. Leave the app online for the judges.

If Render's free tier is unavailable or the instance sleeps, use an always-on hosting plan suitable for testing at 18:00. A cold start must import the CSVs and may be slow.

## Safety and correctness
- Synthetic data only. Not authoritative for real-world UZEX trading.
- The model plans SQL but does not author the numerical answer.
- SQLite is opened in **read-only** mode with an SQL authorizer and runtime limit.
- A SQL failure, planner failure, or missing row never produces guessed numbers.
- Convert USD deal amounts using the historical date-matched `usd_rates` rate when comparing or summing UZS across mixed currencies.
- Separate lot counts and physical units (`quantity * lot`); canceled transactions must not be treated as completed delivery.

## Limitations to disclose
- Free-form commodity spellings vary; exact matches can be incomplete.
- Data ends 2026-10-30 and reports status as of that date.
- SQL can return a count of zero for an unrecognized named entity; check entity existence before interpreting this as a meaningful absence.
- Answers are raw verified SQL-result tables, not polished generated prose.

## Competition submission description (English)
Bilingual Gemini-powered query interpreter over an indexed SQLite database of 500,000 fictional UZEX trades and related contracts, participants, brokers, and historical FX rates. Gemini generates read-only SQL, a restricted SQL validator executes it, and the interface renders database results with SQL evidence. Unsupported questions and failed queries are refused rather than filled with invented facts. The SQL logic accounts for contract currency, USD conversion at the transaction date, lot size, deal status, and contract/participant joins.
