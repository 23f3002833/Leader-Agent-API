import os, re, time
import pandas as pd
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI
from data import load_all

client = OpenAI(
    api_key=os.environ.get("LLM_API_KEY", "missing"),
    base_url=os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1"),
)
MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")

DF = load_all()  # once, at startup

SCHEMA = f"""`df` has {len(DF)} rows, one per order (already de-duplicated). Columns:
- id (e.g. ORD-13895), customer (e.g. C1084), region, product, qty, unit_price, amount
- currency (USD/EUR/INR) - `amount` and `unit_price` are in that currency
- amount_usd - `amount` converted to USD (USE THIS for any money answer)
- status: one of {sorted(DF.status.unique())}
- month: 'YYYY-MM' and date: 'YYYY-MM-DD', both already in India time (Asia/Kolkata)
Regions: {sorted(DF.region.unique())}
Products: {sorted(DF["product"].unique())}
Dates range: {DF.date.min()} to {DF.date.max()}
Sample rows:
{DF.head(3).to_string()}"""

SYSTEM = f"""You answer questions about Acme Appliances' order ledger by writing pandas code.

{SCHEMA}

Business rules:
- REVENUE = sum of amount_usd where status == 'paid'. 'refunded' and 'void' are NOT revenue.
- REFUNDS = rows where status == 'refunded'. Refund amount = sum of amount_usd on those rows;
  number of refunds = count of those rows.
- Refund rate (if asked) = refunded orders / all orders, unless the question says otherwise.
- Order count / "how many orders" = count rows matching the filter (say 'paid' only if asked about paid/revenue).
- Filter time ONLY with `month` ('2026-03') or `date` ('2026-03-15'). For ranges, compare these strings.
- Match region/product/customer exactly using the spellings listed above (fix casing/typos in the question).
- Product names must be returned exactly as in the data. Customers are ids like 'C1084'.
- For "top/best/highest" questions return just the name/id (a string); for "top N" return a list.
- Money answers: a plain number in USD (no $ sign). Counts: an integer.

Output rules:
- Write ONLY Python code. No explanations, no markdown fences.
- `df` and `pd` already exist; never load data.
- Put the final answer in a variable named `result`.

Example
Q: What was the total revenue in USD from the North region in March 2026?
A:
x = df[(df.status == 'paid') & (df.region == 'North') & (df.month == '2026-03')]
result = float(x.amount_usd.sum())
"""


def ask_llm(messages, timeout=8):
    r = client.chat.completions.create(model=MODEL, messages=messages, temperature=0, timeout=timeout)
    code = r.choices[0].message.content.strip()
    return re.sub(r"^```(?:python)?\s*|\s*```$", "", code, flags=re.M).strip()


def run(code):
    scope = {"df": DF.copy(), "pd": pd}
    exec(code, scope)
    return scope["result"]


def clean(x):
    if isinstance(x, pd.DataFrame):
        return [clean(r) for r in x.to_dict("records")]
    if isinstance(x, pd.Series):
        return [clean(i) for i in x.tolist()]
    if isinstance(x, dict):
        return {str(k): clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, set)):
        return [clean(i) for i in x]
    if hasattr(x, "item"):
        x = x.item()
    if isinstance(x, float):
        return None if x != x else round(x + 0.0, 2)
    if isinstance(x, pd.Timestamp):
        return str(x)
    return x


app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/")
def health():
    return {"status": "ok", "rows": len(DF)}


@app.post("/")
@app.post("/answer")
@app.post("/{path:path}")
async def answer(req: Request):
    started = time.time()
    question = (await req.json())["question"]
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}]
    try:
        code = ask_llm(msgs)
        try:
            return {"answer": clean(run(code))}
        except Exception as e:
            if time.time() - started > 8:
                raise
            msgs += [{"role": "assistant", "content": code},
                     {"role": "user", "content": f"That code failed with: {e}. Return corrected code only."}]
            return {"answer": clean(run(ask_llm(msgs, timeout=5)))}
    except Exception as e:
        return {"answer": None, "error": str(e)}
