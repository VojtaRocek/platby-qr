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
    # MSG is limited to 60 chars, strip diacritics-safe truncation
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


def parse_sheet(rows):
    """
    Parse a sheet and return:
      {
        'header': [name1, name2, ...],   # person names
        'amounts': {name: amount, ...},  # only non-zero
        'account': '1234567/0100',
        'total': 22740,
      }
    or None if the sheet doesn't match the expected format.
    """
    # Find the "platit na" row — contains "platit" (case-insensitive)
    platit_idx = None
    for i, row in enumerate(rows):
        if row and "platit" in row[0].lower():
            platit_idx = i
            break

    if platit_idx is None or platit_idx == 0:
        return None

    # Extract account number from "platit na" row
    platit_text = " ".join(rows[platit_idx])
    account_match = re.search(r"(\d[\d-]*/\d+)", platit_text)
    if not account_match:
        return None
    account = account_match.group(1)

    # Sums row is immediately above the "platit na" row
    sums_row = rows[platit_idx - 1]

    # Header row is always row 0
    header_row = rows[0]

    # Total = first non-empty numeric cell in sums_row
    total = None
    for cell in sums_row:
        try:
            val = float(cell.replace(",", ".").replace(" ", ""))
            if val > 0:
                total = val
                break
        except ValueError:
            continue

    if total is None:
        return None

    # Find which columns have person names (non-empty, non-numeric in header)
    # and match amounts in sums_row
    amounts = {}
    for col_idx, name in enumerate(header_row):
        name = name.strip()
        if not name:
            continue
        # Skip known non-person header labels
        if name.lower() in {"v bal.", "czk", "cena za ks.", "kusu", "-", "sumy"}:
            continue
        # Skip cells that look like URLs or sheet metadata
        if "." in name and "/" not in name:
            continue
        # Get the corresponding amount from sums_row
        if col_idx >= len(sums_row):
            continue
        amount_str = sums_row[col_idx].strip().replace(",", ".").replace(" ", "")
        try:
            amount = float(amount_str)
        except ValueError:
            continue
        if amount > 0:
            amounts[name] = amount

    return {
        "account": account,
        "total": total,
        "amounts": amounts,
    }


def generate_html(sheets_data):
    """Generate the full index.html content."""
    sections = []
    for sheet in sheets_data:
        name = sheet["name"]
        data = sheet["data"]
        if not data or not data["amounts"]:
            continue

        cards = []
        for person, amount in sorted(data["amounts"].items()):
            qr_file = f"qr/{sheet['qr_prefix']}_{person}.png"
            amount_fmt = f"{int(amount):,}".replace(",", "\u00a0")  # non-breaking space
            cards.append(
                f"""
        <div class="card">
          <div class="person">{person}</div>
          <div class="amount">{amount_fmt} Kč</div>
          <img src="{qr_file}" alt="QR platba {person}" />
        </div>"""
            )

        sections.append(
            f"""
  <section>
    <h2>{name}</h2>
    <p class="account">Platit na: <strong>{data['account']}</strong> &nbsp;|&nbsp; Celkem: <strong>{int(data['total']):,} Kč</strong></p>
    <div class="cards">{"".join(cards)}
    </div>
  </section>"""
        )

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
    .cards {{ display: flex; flex-wrap: wrap; gap: 1.2rem; }}
    .card {{ background: #fafafa; border: 1px solid #eee; border-radius: 10px;
             padding: 1rem; text-align: center; min-width: 160px; }}
    .person {{ font-weight: 600; font-size: 1.05rem; margin-bottom: 0.3rem; }}
    .amount {{ font-size: 1.4rem; font-weight: 700; color: #1a6b3c; margin-bottom: 0.8rem; }}
    .card img {{ width: 160px; height: 160px; display: block; margin: 0 auto; }}
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

    # Prepare output dirs
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
            continue

        data = parse_sheet(rows)
        if not data:
            print(f"  Skipping — could not parse (no 'platit na' row?)")
            continue

        iban = czk_to_iban(data["account"])
        if not iban:
            print(f"  Skipping — could not convert account '{data['account']}' to IBAN")
            continue

        print(f"  Account: {data['account']} → IBAN: {iban}")
        print(f"  Total: {data['total']} | Persons: {list(data['amounts'].keys())}")

        # Generate QR codes
        safe_name = re.sub(r"[^\w]", "_", sheet["name"])
        for person, amount in data["amounts"].items():
            spd = make_spd_string(iban, amount, person)
            safe_person = re.sub(r"[^\w]", "_", person)
            qr_path = QR_DIR / f"{safe_name}_{safe_person}.png"
            save_qr(spd, qr_path)
            print(f"  QR: {qr_path} ({spd[:60]}...)")

        sheets_data.append({
            "name": sheet["name"],
            "data": data,
            "qr_prefix": safe_name,
        })

    # Generate HTML
    html = generate_html(sheets_data)
    (DOCS_DIR / "index.html").write_text(html, encoding="utf-8")
    print(f"\nGenerated docs/index.html with {len(sheets_data)} sections")


if __name__ == "__main__":
    main()
