"""UZEX qualification prototype: Gemini -> validated read-only SQLite -> evidence.

Run: GEMINI_API_KEY=... python app.py
"""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import urllib.request
import urllib.error
import zipfile
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import uvicorn

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / 'uzex_spot_data'
ARCHIVE = ROOT / 'uzex_spot_data.zip'
DB = ROOT / 'uzex.sqlite3'
BUILD_LOCK = threading.Lock()

TABLES = {
    'deals': (
        'deal_id', 'deal_date', 'deal_time', 'auction_type', 'quantity', 'amount',
        'starting_price', 'contract_id', 'product_name', 'lot', 'unit', 'region',
        'contract_type', 'contract_form', 'status', 'seller_inn', 'buyer_inn',
        'seller_broker_code', 'buyer_broker_code', 'payment_date', 'delivery_date',
        'delivered_quantity'
    ),
    'contracts': (
        'contract_id', 'product_name', 'brand', 'product_size', 'grade',
        'product_type', 'product_class', 'hs_code', 'product_group', 'category',
        'industry', 'unit', 'lot', 'currency', 'base_price',
        'deposit_percent', 'delivery_basis', 'region', 'warehouse_address',
        'auction_type', 'contract_type', 'contract_form', 'payment_days',
        'delivery_days', 'packaging', 'seller_inn', 'registration_date', 'valid_until'
    ),
    'participants': ('inn', 'name', 'region', 'business_type', 'role', 'vat_payer'),
    'brokers': ('broker_code', 'name', 'inn', 'region'),
    'usd_rates': ('rate_date', 'uzs_per_usd')
}

INTEGER_FIELDS = {'deal_id', 'quantity', 'contract_id', 'lot', 'delivered_quantity', 'payment_days', 'delivery_days'}
FLOAT_FIELDS = {'amount', 'starting_price', 'base_price', 'uzs_per_usd'}
DATE_FIELDS = {'deal_date','payment_date','delivery_date','registration_date','valid_until','rate_date'}

SCHEMAS = {
 'deals': '''CREATE TABLE deals (
 deal_id INTEGER PRIMARY KEY, deal_date TEXT, deal_time TEXT, auction_type TEXT,
 quantity INTEGER, amount REAL, starting_price REAL, contract_id INTEGER,
 product_name TEXT, lot INTEGER, unit TEXT, region TEXT, contract_type TEXT,
 contract_form TEXT, status TEXT, seller_inn TEXT, buyer_inn TEXT,
 seller_broker_code TEXT, buyer_broker_code TEXT, payment_date TEXT,
 delivery_date TEXT, delivered_quantity INTEGER)''',
 'contracts': '''CREATE TABLE contracts (
 contract_id INTEGER PRIMARY KEY, product_name TEXT, brand TEXT, product_size TEXT,
 grade TEXT, product_type TEXT, product_class TEXT, hs_code TEXT,
 product_group TEXT, category TEXT, industry TEXT, unit TEXT, lot INTEGER,
 currency TEXT, base_price REAL, deposit_percent TEXT, delivery_basis TEXT,
 region TEXT, warehouse_address TEXT, auction_type TEXT,
 contract_type TEXT, contract_form TEXT, payment_days INTEGER, delivery_days INTEGER,
 packaging TEXT, seller_inn TEXT, registration_date TEXT, valid_until TEXT)''',
 'participants': '''CREATE TABLE participants (
 inn TEXT PRIMARY KEY, name TEXT, region TEXT, business_type TEXT, role TEXT, vat_payer TEXT)''',
 'brokers': '''CREATE TABLE brokers (broker_code TEXT PRIMARY KEY, name TEXT, inn TEXT, region TEXT)''',
 'usd_rates': '''CREATE TABLE usd_rates (rate_date TEXT PRIMARY KEY, uzs_per_usd REAL)'''
}

INDEX_SQL = [
 'CREATE INDEX idx_deals_date ON deals(deal_date)',
 'CREATE INDEX idx_deals_contract ON deals(contract_id)',
 'CREATE INDEX idx_deals_seller ON deals(seller_inn)',
 'CREATE INDEX idx_deals_buyer ON deals(buyer_inn)',
 'CREATE INDEX idx_deals_status ON deals(status)',
 'CREATE INDEX idx_deals_region ON deals(region)',
 'CREATE INDEX idx_deals_brokers ON deals(seller_broker_code, buyer_broker_code)',
 'CREATE INDEX idx_contract_cat ON contracts(category)',
]


def _convert(field: str, value: str) -> Any:
    value = value.strip()
    if not value:
        return None
    if field in DATE_FIELDS:
        try:
            dd, mm, yyyy = value.split('.')
            return f'{yyyy}-{mm}-{dd}'
        except ValueError:
            return value
    if field in INTEGER_FIELDS:
        return int(value.replace(' ', '').replace('\u00a0', ''))
    if field in FLOAT_FIELDS:
        return float(value.replace(' ', '').replace('\u00a0', ''))
    return value


def build_database() -> None:
    if DB.exists() and DB.stat().st_size > 1024 * 1024:
        return
    with BUILD_LOCK:
        if DB.exists() and DB.stat().st_size > 1024 * 1024:
            return
        if not DATA_DIR.is_dir():
            if not ARCHIVE.is_file():
                raise RuntimeError('Missing uzex_spot_data.zip and uzex_spot_data folder')
            with zipfile.ZipFile(ARCHIVE) as z:
                for item in z.infolist():
                    p = (ROOT / item.filename).resolve()
                    if not p.is_relative_to(ROOT):
                        raise RuntimeError('Unsafe archive entry')
                z.extractall(ROOT)
        temp = DB.with_suffix('.building')
        temp.unlink(missing_ok=True)
        conn = sqlite3.connect(temp)
        try:
            conn.execute('PRAGMA journal_mode=OFF')
            conn.execute('PRAGMA synchronous=OFF')
            for sql in SCHEMAS.values():
                conn.execute(sql)
            for table, names in TABLES.items():
                paths = sorted(DATA_DIR.glob('deals_*.csv')) if table == 'deals' else [DATA_DIR / (table + '.csv')]
                for path in paths:
                    with path.open('r', encoding='utf-8-sig', newline='') as f:
                        reader = csv.reader(f)
                        next(reader)
                        insert = f'INSERT INTO {table} VALUES ({",".join("?" for _ in names)})'
                        batch=[]
                        for row in reader:
                            if len(row) != len(names):
                                raise ValueError(f'{path.name}: expected {len(names)}, got {len(row)}')
                            batch.append(tuple(_convert(field, value) for field, value in zip(names, row)))
                            if len(batch) >= 5000:
                                conn.executemany(insert, batch)
                                batch=[]
                        if batch:
                            conn.executemany(insert, batch)
            for sql in INDEX_SQL:
                conn.execute(sql)
            conn.commit()
            deals = conn.execute('SELECT COUNT(*) FROM deals').fetchone()[0]
            if deals != 500000:
                raise RuntimeError(f'Expected 500000 deals, found {deals}')
            conn.close()
            temp.replace(DB)
        except BaseException:
            conn.close()
            temp.unlink(missing_ok=True)
            raise


SCHEMA_PROMPT = '''DATABASE: SQLite. IMPORTANT: All data are fictional and supplied by contest.
The following tables have normalized, ENGLISH names; values remain Russian/Uzbek strings.

deals(deal_id INTEGER, deal_date TEXT YYYY-MM-DD, deal_time TEXT HH:MM:SS,
 auction_type TEXT, quantity INTEGER [NUMBER OF LOTS], amount REAL [IN CONTRACT CURRENCY],
 starting_price REAL [PRICE PER UNIT], contract_id INTEGER, product_name TEXT [FULL SELLER TITLE],
 lot INTEGER [UNITS PER LOT], unit TEXT, region TEXT,
 contract_type TEXT [Внутренний/Экспортный], contract_form TEXT [Спот/Форвард/Фьючерс],
 status TEXT, seller_inn TEXT, buyer_inn TEXT, seller_broker_code TEXT,
 buyer_broker_code TEXT, payment_date TEXT YYYY-MM-DD, delivery_date TEXT YYYY-MM-DD,
 delivered_quantity INTEGER [LOT COUNT, not units])
contracts(contract_id INTEGER, product_name TEXT [SHORT PRODUCT], brand TEXT,
 product_size TEXT, grade TEXT, product_type TEXT, product_class TEXT,
 hs_code TEXT, product_group TEXT, category TEXT, industry TEXT, unit TEXT,
 lot INTEGER, currency TEXT [Узбекский сум / Доллар США],
 base_price REAL [PER UNIT], deposit_percent TEXT, delivery_basis TEXT,
 packaging TEXT, region TEXT, warehouse_address TEXT, auction_type TEXT,
 contract_type TEXT, contract_form TEXT, payment_days INTEGER,
 delivery_days INTEGER, seller_inn TEXT, registration_date TEXT YYYY-MM-DD,
 valid_until TEXT YYYY-MM-DD)
participants(inn TEXT PRIMARY KEY [9-digit string], name TEXT, region TEXT,
 business_type TEXT, role TEXT [Продавец/Покупатель], vat_payer TEXT)
brokers(broker_code TEXT PRIMARY KEY, name TEXT, inn TEXT, region TEXT)
usd_rates(rate_date TEXT YYYY-MM-DD, uzs_per_usd REAL)

JOIN: deals.contract_id=contracts.contract_id; deals.seller_inn or buyer_inn=participants.inn;
 deals.seller_broker_code or buyer_broker_code=brokers.broker_code;
 deals.deal_date=usd_rates.rate_date.
Correct rules:
* Actual traded units = deals.quantity * deals.lot (NOT just quantity).
* Unit price = deals.amount / NULLIF(deals.quantity * deals.lot, 0).
* Currency comes from contracts.currency. For sum of UZS values across mixed contracts,
  use CASE WHEN contracts.currency='Доллар США' THEN deals.amount * usd_rates.uzs_per_usd ELSE deals.amount END and join rates.
* Deal status Аннулирован(Не оплачен) is a canceled/unrealized deal; exclude if question
  requests EXECUTED or completed trades. Исполнен = fully executed; Исполнен(Частично)
  partially executed; Оплачен = paid but NOT delivered. If question says "all deals" include all statuses.
* Different sellers spell same commodity differently. Prefer contracts.category/product_group/hs_code for categories,
  and explain ambiguity rather than promise complete matches for loose brand/name searches.
* Deals are 2022-01-03 to 2026-10-30; no data beyond. All statuses as at 2026-10-30.
* Do not invent a company name, code, record, sum, event, future prediction, or missing data.
* A particular name/INN must be looked up in participants; never invent or hardcode it.
* Question asking the actual current real UZEX outside this fictional snapshot is unsupported.
* Sql output must use English schema names above and real Russian value literals.
* For 2026 October use deal_date >= '2026-10-01' AND deal_date < '2026-11-01'.
* Only SQLite syntax supported. Prefer exact IDs/INNs and joins. No imaginary columns.
* Avoid grouping mixed currencies without converting. For prices across unlike product units do not average.
* For seller/buyer company JOIN participants with explicit aliases.
* To rank distinct top entities, GROUP BY ID/INN and ORDER BY the computed metric DESC.
'''

SYSTEM = '''You are a STRICT, bilingual (Uzbek/Russian) natural-language-to-SQL planner for UZEX hackathon.
Produce exactly ONE JSON object with keys:
{"status":"query" | "unsupported" | "ambiguous", "sql":"...", "language":"ru" | "uz", "reason":"short user-language explanation"}
Never write prose or Markdown outside JSON.
You do NOT answer factual questions yourself. Your sole task is preparing a SQL SELECT to query the supplied database.
If asked about data absent from the database, set status unsupported or ambiguous, sql="".
If question can be answered, status query and safe SQLite SELECT (or WITH...SELECT).
Do not guess missing filter details; where a vague commodity is named, use LIKE and do not assert completeness.
Only use database tables listed. Do not use PRAGMA, attach, external files, network, code execution, SQL comments, multiple statements, DDL, INSERT, UPDATE, DELETE, DROP, TRIGGER, or unauthorized database functions.
SELECT at most 20 columns and at most 30 result rows; for long lists use LIMIT 30 and ORDER BY relevant measure.
ALWAYS alias computed expressions descriptively, ideally using letters/numbers/underscores.
Use the user's language for short reason, but do not put any numeric or company claims into reason.
Do not claim zero results without a query. If no matching records, the application will handle this.
Question underconstrained? Return ambiguous and ask for specificity.
Avoid recursion and computationally expensive cross joins.

''' + SCHEMA_PROMPT

ALLOWED_SQL_FUNCS = {
    'count','sum','avg','min','max','total','round','abs','lower','upper','trim','ltrim',
    'rtrim','replace','substr','substring','length','coalesce','ifnull','nullif',
    'date','time','datetime','strftime','cast','iif','like','glob','printf',
    'row_number','rank','dense_rank','julianday','typeof','max','min','group_concat'
}


def validate_sql(sql: str) -> str:
    if not isinstance(sql, str):
        raise ValueError('SQL must be a string')
    sql = sql.strip()
    if sql.endswith(';'):
        sql = sql[:-1].strip()
    if not sql or len(sql) > 7000:
        raise ValueError('SQL missing or too long')
    if not re.match(r'^\s*(SELECT|WITH)\s', sql, re.I):
        raise ValueError('Only SELECT queries are permitted')
    # Prevent injection of extra statements; semicolons inside quoted strings are not needed.
    if ';' in sql or re.search(r'--|/\*|\*/', sql):
        raise ValueError('SQL comments and multiple statements forbidden')
    return sql


def read_query(sql: str) -> tuple[list[str], list[tuple[Any, ...]]]:
    sql = validate_sql(sql)
    conn = sqlite3.connect(f'file:{DB.as_posix()}?mode=ro', uri=True, timeout=8)
    try:
        conn.execute('PRAGMA query_only=ON')
        tables = set(TABLES)
        def auth(action: int, arg1: str | None, arg2: str | None, _db: str, _trigger: str):
            if action == sqlite3.SQLITE_SELECT:
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_READ:
                return sqlite3.SQLITE_OK if arg1 in tables else sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_FUNCTION:
                return sqlite3.SQLITE_OK if (arg2 or arg1 or '').lower() in ALLOWED_SQL_FUNCS else sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_DENY
        t0 = time.monotonic()
        conn.set_progress_handler(lambda: 1 if time.monotonic()-t0 > 14 else 0, 20000)
        conn.set_authorizer(auth)
        # Outer limit prevents accidental huge results, even if model omitted LIMIT.
        cur = conn.execute(f'SELECT * FROM ({sql}) AS verified_result LIMIT 31')
        rows = cur.fetchmany(31)
        cols = [c[0] for c in cur.description]
        return cols, rows
    finally:
        conn.close()


def query_plan(question: str) -> dict[str, Any]:
    """Call Gemini via the public REST API. Only SQL proposals, never factual answers."""
    model = os.getenv('GEMINI_MODEL', 'gemini-3.5-flash-lite')
    if not re.fullmatch(r'[a-zA-Z0-9._-]+', model):
        raise ValueError('Invalid Gemini model name')
    payload = {
        'systemInstruction': {'parts': [{'text': SYSTEM}]},
        'contents': [{'role': 'user', 'parts': [{'text': question}]}],
        'generationConfig': {
            'maxOutputTokens': 2500,
            'responseMimeType': 'application/json',
            'thinkingConfig': {'thinkingLevel': 'minimal'},
        },
    }
    request = urllib.request.Request(
        f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',
        data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'x-goog-api-key': os.environ['GEMINI_API_KEY'],
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(request, timeout=35) as response:
            content = json.load(response)
    except urllib.error.HTTPError as error:
        # Keep credentials and request data out of server logs.
        try:
            err_body = json.loads(error.read(4096).decode('utf-8', 'replace'))
            provider_status = str(err_body.get('error', {}).get('status', 'UNKNOWN'))[:40]
        except Exception:
            provider_status = 'UNKNOWN'
        raise RuntimeError(f'Gemini API HTTP {error.code} ({provider_status})') from None
    candidates = content.get('candidates') or []
    if not candidates:
        raise ValueError('Gemini returned no SQL proposal')
    parts = candidates[0].get('content', {}).get('parts') or []
    response_text = ''.join(str(p.get('text', '')) for p in parts if not p.get('thought')).strip()
    if not response_text:
        raise ValueError('Gemini did not return structured SQL')
    plan = json.loads(response_text)
    if not isinstance(plan, dict) or plan.get('status') not in ('query','unsupported','ambiguous'):
        raise ValueError('Unexpected model planning response')
    return plan


def human_val(v: Any) -> str:
    if v is None:
        return '—'
    if isinstance(v, float):
        return f'{v:,.2f}'.replace(',', ' ') if abs(v) >= 1000 else str(round(v, 4))
    if isinstance(v, int):
        return f'{v:,}'.replace(',', ' ')
    return str(v)


def create_response(question: str, history: list | None = None) -> str:
    if not question or not question.strip():
        return 'Please ask a question about the exchange registry. / Birja reyestri haqida savol bering.'
    if len(question) > 2000:
        return 'Question too long. / Savol juda uzun.'
    if not os.getenv('GEMINI_API_KEY'):
        return ('**Sozlash talab etiladi.** Serverda Gemini API kaliti o‘rnatilmagan. / '
                '**Требуется настройка.** На сервере отсутствует ключ Gemini API.')
    try:
        build_database()
        plan = query_plan(question)
        uz = plan.get('language') == 'uz'
        if plan['status'] != 'query':
            title = 'Maʼlumot yetarli emas' if uz else 'Недостаточно данных'
            return f'**{title}.** {plan.get("reason") or ("Savolga aniq javob topib bo‘lmaydi." if uz else "По имеющимся данным точный ответ невозможен.")}'
        for attempt in range(2):
            try:
                sql = validate_sql(plan.get('sql', ''))
                cols, rows = read_query(sql)
                break
            except (sqlite3.Error, ValueError) as query_error:
                if attempt:
                    raise
                repair = (
                    'Fix this SQL plan for the exact original question. Never fabricate a result. '
                    'Return a single JSON plan with a read-only SQLite SELECT, or unsupported. '
                    f'Original question: {question}\n'
                    f'Failed SQL: {str(plan.get("sql", ""))[:3000]}\n'
                    f'Failure: {str(query_error)[:300]}'
                )
                plan = query_plan(repair)
                if plan['status'] != 'query':
                    raise ValueError('The SQL plan could not be safely repaired')
        if not rows or all(v is None for row in rows for v in row):
            return ('**Maʼlumotda javob topilmadi.** Berilgan shartlarga mos yozuv yo‘q.' if uz
                    else '**В предоставленных данных ответ не найден.** Совпадающих записей нет.')
        if len(rows)==1 and len(cols)==1 and isinstance(rows[0][0], int) and rows[0][0] == 0:
            # This is a valid exact count, but we state what the query established, not an invented item.
            pass
        n = min(len(rows), 30)
        heading='**Reyestr asosida hisoblangan javob**' if uz else '**Результат, вычисленный по реестру**'
        lines=[heading,'', '| ' + ' | '.join(c.replace('|','/') for c in cols) + ' |',
                      '| ' + ' | '.join('---' for _ in cols) + ' |']
        for row in rows[:n]:
            lines.append('| ' + ' | '.join(human_val(v).replace('|','/') for v in row) + ' |')
        if len(rows)>30:
            lines.append('\n' + ('Ko‘rsatilgan birinchi 30 qator.' if uz else 'Показаны первые 30 строк.'))
        lines.extend(['','**' + ('Dalil — bajarilgan SQL so‘rovi' if uz else 'Основание — выполненный SQL-запрос') + '**',
                      '```sql',sql,'```',
                      ('Manba: UZEX sintetik bitimlar reyestri, 2022–2026.' if uz
                       else 'Источник: синтетический реестр сделок UZEX, 2022–2026.')])
        return '\n'.join(lines)
    except sqlite3.Error as ex:
        print(f'Database query blocked or failed: {type(ex).__name__}: {ex}',flush=True)
        return ('**Tekshirilgan javob mavjud emas.** So‘rovni xavfsiz bajarib bo‘lmadi.'
                if re.search('[oʻgʻ]',question.lower()) else
                '**Не удалось получить проверенный ответ.** Безопасно выполнить запрос не получилось.')
    except Exception as ex:
        print(f'Query failure: {type(ex).__name__}: {ex}',flush=True)
        return ('**Tasdiqlangan javob mavjud emas.** Maʼlumotlar asosida tekshirilgan natija olinmadi.'
                if re.search(r'[oʻgʻ]|yil|bitim|qaysi|nechta', question.lower()) else
                '**Не удалось получить проверенный ответ.** Система не выдала неподтверждённых данных.')


class AskRequest(BaseModel):
    question: str


api = FastAPI(title='UZEX AI', docs_url=None, redoc_url=None)


@api.get('/', response_class=HTMLResponse)
def home() -> str:
    return (ROOT / 'frontend.html').read_text(encoding='utf-8')


@api.get('/health')
def health() -> dict[str, Any]:
    return {
        'ready': DB.is_file() and DB.stat().st_size > 1024 * 1024,
        'gemini_key_present': bool(os.getenv('GEMINI_API_KEY')),
        'gemini_model': os.getenv('GEMINI_MODEL', 'gemini-3.5-flash-lite'),
    }


@api.post('/api/ask')
def ask(req: AskRequest) -> dict[str, str]:
    return {'answer': create_response(req.question)}


def main() -> None:
    build_database()
    uvicorn.run(api, host='0.0.0.0', port=int(os.getenv('PORT','7860')), log_level='info')


if __name__ == '__main__':
    main()
