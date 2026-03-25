#!/usr/bin/env python3
"""
Agent: reads Google Sheets, generates Czech SPD payment QR codes,
outputs a static HTML page to docs/index.html
"""

import csv
import io
import json
import os
import re
import shutil
from pathlib import Path

import qrcode
import requests

SPREADSHEET_ID = "15dbO9aipE8n4KSD34QdvW8VYlHiOVtd3tqYi_TDetFk"
API_KEY = os.environ.get("GOOGLE_API_KEY", "")
DOCS_DIR = Path("docs")
QR_DIR = DOCS_DIR / "qr"

SKIP_HEADERS = {"v bal.", "czk", "cena za ks.", "kusu", "-", "sumy"}


def get_visible_sheets():
    """Return list of visible sheets: [{name, gid}, ...]"""
    url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}"
        f"?fields=sheets.properties&key={API_KEY}"
    )
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    sheets = []
    for sheet in r.json().get("sheets", []):
        props = sheet["properties"]
        if not props.get("hidden", False):
            sheets.append({"name": props["title"], "gid": props["sheetId"]})
    return sheets


def get_sheet_csv(gid):
    """Fetch sheet as list of rows via CSV export."""
    url = (
        f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}"
        f"/export?format=csv&gid={gid}"
    )
    r = requests.get(url, allow_redirects=True, timeout=30)
    r.raise_for_status()
    return list(csv.reader(io.StringIO(r.text)))


def czk_to_iban(account_str):
    """Convert Czech account '1234567/0100' (or 'prefix-1234567/0100') to IBAN."""
    m = re.search(r"((\d+)-)?(\d+)/(\d+)", account_str.strip())
    if not m:
        return None
    prefix = (m.group(2) or "0").zfill(6)
    number = m.group(3).zfill(10)
    bank = m.group(4).zfill(4)
    bban = bank + prefix + number          # 20 digits
    # Append CZ00 converted: C=12, Z=35
    num_str = bban + "123500"
    remainder = int(num_str) % 97
    check = str(98 - remainder).zfill(2)
    return f"CZ{check}{bban}"


def make_spd_string(iban, amount_czk, recipient_name):
    """Return Czech SPD QR payload string."""
    amount = f"{float(amount_czk):.2f}"
    msg = recipient_name[:60]
    return f"SPD*1.0*ACC:{iban}*AM:{amount}*CC:CZK*MSG:{msg}*"


def save_qr(spd_string, filepath):
    """Generate and save QR code PNG."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=4,
    )
    qr.add_data(spd_string)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    img.save(filepath)


def _extract_amounts(header_row, sums_row):
    """Extract {name: amount} from header and sums rows."""
    amounts = {}
    for col_idx, name in enumerate(header_row):
        name = name.strip()
        if not name:
            continue
        if name.lower() in SKIP_HEADERS:
            continue
        if "." in name and "/" not in name:
            continue
        if col_idx >= len(sums_row):
            continue
        amount_str = sums_row[col_idx].strip().replace(",", ".").replace(" ", "")
        try:
            amount = float(amount_str)
        except ValueError:
            continue
        if amount > 0:
            amounts[name] = amount
    return amounts


def parse_sheet(rows):
    """
    Parse a sheet and return one of:
      {'account': '...', 'total': N, 'amounts': {...}}   — full data, QR codes can be generated
      {'account': None,  'total': N, 'amounts': {...}}   — sums found, but no account number
      {'error': 'no_sums'}                               — cannot find any sums at all
    """
    # --- Strategy 1: look for a "platit na" row (search all columns) ---
    platit_idx = None
    for i, row in enumerate(rows):
        if row and any("platit" in cell.lower() for cell in row):
            platit_idx = i
            break

    if platit_idx is not None and platit_idx > 0:
        platit_text = " ".join(rows[platit_idx])
        account_match = re.search(r"(\d[\d-]*/\d+)", platit_text)
        account = account_match.group(1) if account_match else None

        sums_row = rows[platit_idx - 1]
        header_row = rows[0]
        amounts = _extract_amounts(header_row, sums_row)

        total = None
        for cell in sums_row:
            try:
                val = float(cell.replace(",", ".").replace(" ", ""))
                if val > 0:
                    total = val
                    break
            except ValueError:
                continue

        if total is None or not amounts:
            return {"error": "no_sums"}

        return {"account": account, "total": total, "amounts": amounts}

    # --- Strategy 2: look for a "Sumy" section header ---
    sumy_idx = None
    for i, row in enumerate(rows):
        if row and row[0].strip().lower() == "sumy":
            sumy_idx = i
            break

    if sumy_idx is None:
        return {"error": "no_sums"}

    header_row = rows[sumy_idx]

    # Sums row = last non-empty row after the Sumy header
    sums_row = None
    for row in reversed(rows[sumy_idx + 1:]):
        if any(c.strip() for c in row):
            sums_row = row
            break

    if sums_row is None:
        return {"error": "no_sums"}

    amounts = _extract_amounts(header_row, sums_row)

    total = None
    for cell in sums_row:
        try:
            val = float(cell.replace(",", ".").replace(" ", ""))
            if val > 0:
                total = val
                break
        except ValueError:
            continue

    if total is None or not amounts:
        return {"error": "no_sums"}

    return {"account": None, "total": total, "amounts": amounts}


def generate_html(sheets_data):
    """Generate the full index.html content."""
    sections = []
    for sheet in sheets_data:
        name = sheet["name"]
        data = sheet["data"]

        if data.get("error") == "no_sums":
            sections.append(f"""
  <section>
    <h2>{name}</h2>
    <p class="warning">&#x26A0;&#xFE0F; Vidím tab, nevidím sumy.</p>
  </section>""")
            continue

        amounts = data.get("amounts", {})
        if not amounts:
            continue

        account = data.get("account")
        total = data.get("total")

        cards = []
        for person, amount in sorted(amounts.items()):
            amount_fmt = f"{int(amount):,}".replace(",", "\u00a0")
            if account:
                qr_file = f"qr/{sheet['qr_prefix']}_{person}.png"
                cards.append(f"""
        <div class="card">
          <div class="person">{person}</div>
          <div class="amount">{amount_fmt} Kč</div>
          <img src="{qr_file}" alt="QR platba {person}" />
        </div>""")
            else:
                cards.append(f"""
        <div class="card">
          <div class="person">{person}</div>
          <div class="amount">{amount_fmt} Kč</div>
          <div class="no-account">&#x26A0;&#xFE0F; Nebylo nalezeno číslo účtu kam platit.</div>
        </div>""")

        account_line = (
            f"Platit na: <strong>{account}</strong> &nbsp;|&nbsp; Celkem: <strong>{int(total):,} Kč</strong>"
            if account
            else f"&#x26A0;&#xFE0F; Číslo účtu nenalezeno &nbsp;|&nbsp; Celkem: <strong>{int(total):,} Kč</strong>"
        )

        sections.append(f"""
  <section>
    <h2>{name}</h2>
    <p class="account">{account_line}</p>
    <div class="cards">{"".join(cards)}
    </div>
  </section>""")

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%d. %m. %Y %H:%M UTC")

    return f"""<!DOCTYPE html>
<html lang="cs">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Platební QR kódy</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: system-ui, sans-serif; background: #f5f5f5; color: #222; padding: 2rem 1rem; }}
    h1 {{ font-size: 1.6rem; margin-bottom: 0.3rem; }}
    .updated {{ color: #888; font-size: 0.85rem; margin-bottom: 2.5rem; }}
    section {{ background: white; border-radius: 12px; padding: 1.5rem; margin-bottom: 2rem;
               box-shadow: 0 1px 4px rgba(0,0,0,.1); }}
    h2 {{ font-size: 1.25rem; margin-bottom: 0.5rem; color: #1a1a1a; }}
    .account {{ font-size: 0.9rem; color: #555; margin-bottom: 1.2rem; }}
    .warning {{ color: #b45309; font-size: 0.95rem; }}
    .cards {{ display: flex; flex-wrap: wrap; gap: 1.2rem; }}
    .card {{ background: #fafafa; border: 1px solid #eee; border-radius: 10px;
             padding: 1rem; text-align: center; min-width: 160px; }}
    .person {{ font-weight: 600; font-size: 1.05rem; margin-bottom: 0.3rem; }}
    .amount {{ font-size: 1.4rem; font-weight: 700; color: #1a6b3c; margin-bottom: 0.8rem; }}
    .card img {{ width: 160px; height: 160px; display: block; margin: 0 auto; }}
    .no-account {{ font-size: 0.8rem; color: #b45309; margin-top: 0.4rem; max-width: 160px; }}
  </style>
</head>
<body>
  <h1>Platební QR kódy</h1>
  <p class="updated">Aktualizováno: {now}</p>
  {"".join(sections)}
</body>
</html>
"""


def main():
    if not API_KEY:
        raise SystemExit("GOOGLE_API_KEY environment variable not set")

    shutil.rmtree(QR_DIR, ignore_errors=True)
    QR_DIR.mkdir(parents=True, exist_ok=True)

    visible_sheets = get_visible_sheets()
    print(f"Visible sheets: {[s['name'] for s in visible_sheets]}")

    sheets_data = []
    for sheet in visible_sheets:
        print(f"Processing: {sheet['name']}")
        try:
            rows = get_sheet_csv(sheet["gid"])
        except Exception as e:
            print(f"  ERROR fetching CSV: {e}")
            sheets_data.append({"name": sheet["name"], "data": {"error": "no_sums"}, "qr_prefix": ""})
            continue

        data = parse_sheet(rows)
        safe_name = re.sub(r"[^\w]", "_", sheet["name"])

        if data.get("error") == "no_sums":
            print(f"  No sums found — will show warning in HTML")
            sheets_data.append({"name": sheet["name"], "data": data, "qr_prefix": safe_name})
            continue

        account = data.get("account")
        if account:
            iban = czk_to_iban(account)
            if not iban:
                print(f"  Invalid account '{account}' — treating as missing")
                data["account"] = None
                iban = None
        else:
            iban = None

        print(f"  Account: {account or 'NOT FOUND'} | Total: {data['total']} | Persons: {list(data['amounts'].keys())}")

        if iban:
            for person, amount in data["amounts"].items():
                spd = make_spd_string(iban, amount, person)
                safe_person = re.sub(r"[^\w]", "_", person)
                qr_path = QR_DIR / f"{safe_name}_{safe_person}.png"
                save_qr(spd, qr_path)
                print(f"  QR: {qr_path}")

        sheets_data.append({"name": sheet["name"], "data": data, "qr_prefix": safe_name})

    html = generate_html(sheets_data)
    (DOCS_DIR / "index.html").write_text(html, encoding="utf-8")
    print(f"\nGenerated docs/index.html with {len(sheets_data)} sections")


if __name__ == "__main__":
    main()
