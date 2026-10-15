import os, io, json
import httpx
import pandas as pd

ROOT = os.environ.get("ROOT_URL", "")
LOCAL_FILE = os.path.join(os.path.dirname(__file__), "ledger.ndjson")

# Fallback only if the rates endpoint cannot be read ("USD value of one unit of each currency")
FALLBACK_USD_PER_UNIT = {"USD": 1.0, "EUR": 1.13, "INR": 0.01134}


def fetch(url):
    last = None
    for _ in range(3):
        try:
            r = httpx.get(url, timeout=30)
            r.raise_for_status()
            return r
        except Exception as e:
            last = e
    raise last


def parse_ndjson(text):
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def load_rows():
    """Primary: the 'export' link (one JSON object per line). Fallback: local snapshot file."""
    try:
        links = fetch(ROOT).json()["links"]
        rows = parse_ndjson(fetch(links["export"]).text)
        if rows:
            return rows, links
    except Exception as e:
        print("API load failed, using local snapshot:", e)
    with open(LOCAL_FILE, encoding="utf-8") as f:
        return parse_ndjson(f.read()), None


def load_rates(links):
    try:
        return {k.upper(): float(v) for k, v in fetch(links["rates"]).json()["usd_per_unit"].items()}
    except Exception as e:
        print("Rates load failed, using fallback:", e)
        return FALLBACK_USD_PER_UNIT


def load_all():
    rows, links = load_rows()
    rates = load_rates(links) if links else FALLBACK_USD_PER_UNIT
    df = pd.DataFrame(rows)

    # Timestamps come in mixed formats ("...Z" and "...+05:30"): parse everything as UTC-aware.
    df["updated_at"] = pd.to_datetime(df["updated_at"], utc=True, format="ISO8601")
    df["created_at"] = pd.to_datetime(df["created_at"], utc=True, format="ISO8601")

    # 1) An order id can appear more than once: the latest updated_at is the current version.
    df = df.sort_values("updated_at").drop_duplicates("id", keep="last").reset_index(drop=True)

    # 2) Business dates are in India time.
    ist = df["created_at"].dt.tz_convert("Asia/Kolkata")
    df["month"] = ist.dt.strftime("%Y-%m")
    df["date"] = ist.dt.strftime("%Y-%m-%d")

    # 3) Money in USD (unrounded per row; round only the final answer).
    df["currency"] = df["currency"].str.upper()
    df["amount_usd"] = df["amount"] * df["currency"].map(rates)
    df["status"] = df["status"].str.strip().str.lower()

    df = df.drop(columns=["created_at", "updated_at"])
    print(f"Loaded {len(df)} current orders; rates={rates}")
    return df
