# Platební QR kódy

Webová stránka, která automaticky generuje česká platební QR kódy ze sdíleného Google Sheets a publikuje je na GitHub Pages. Každý den se aktualizuje sama.

**Živá stránka:** https://vojtarocek.github.io/platby-qr/

---

## Co to dělá

1. GitHub Actions každý den o půlnoci (UTC) spustí Python skript
2. Skript přečte všechny **viditelné** taby ze spreadsheeetu přes Google Sheets API
3. Pro každou osobu a každý tab vygeneruje QR kód ve formátu **Czech SPD** (standard pro české bankovní platby)
4. Výsledek uloží jako statickou HTML stránku do `docs/index.html` a commitne zpět do repozitáře
5. GitHub Pages stránku automaticky publikuje

---

## Jak to technicky funguje

### Čtení dat ze spreadsheetů

Skript používá **dvě strategie** pro parsování tabů, protože různé taby mají různou strukturu:

#### Strategie 1 — řádek „Platit na" (např. tab Marada)
- Skript hledá řádek, který v libovolném sloupci obsahuje slovo „platit" (case-insensitive)
- Řádek **nad** ním obsahuje sumy pro jednotlivé osoby
- Ze samotného řádku „Platit na" se regexem vytáhne číslo účtu ve formátu `1234567/0100` nebo `prefix-číslo/kód`
- Celková suma = první kladné číslo v řádku se sumami

#### Strategie 2 — sekce „Sumy" (např. taby Olejari, Bidule)
- Skript hledá řádek, jehož první buňka je přesně „Sumy" (case-insensitive)
- Tento řádek slouží jako **hlavička** se jmény osob
- Poslední neprázdný řádek pod ním obsahuje sumy pro jednotlivé osoby
- Číslo účtu v těchto tabech **není uvedeno** → zobrazí se varování
- Celková suma v těchto tabech **není v spreadsheeetu** → zobrazí se „nevím"

### Formát QR kódu (Czech SPD)

Každý QR kód je ve formátu:
```
SPD*1.0*ACC:{IBAN}*AM:{částka}*CC:CZK*MSG:{jméno osoby}*
```

Číslo účtu ve formátu `1234567/0100` se před použitím převede na IBAN (`CZ...`).

### Co se zobrazí při různých stavech

| Situace | Co se zobrazí |
|---|---|
| Vše OK (účet + sumy + osoby) | QR kód pro každou osobu |
| Nalezeny sumy, ale chybí číslo účtu | Částky bez QR kódu + varování |
| Celková suma není v tabulce | „Celkem: nevím" |
| Nenalezeny žádné sumy | „Vidím tab, nevidím sumy" |

---

## Proč jsou credentials v GitHub Secrets

### `GOOGLE_API_KEY`
Klíč pro Google Sheets API. Bez něj nelze zjistit, které taby jsou viditelné (hidden vs. visible) — to vyžaduje autentizovaný volání Sheets API v4. Samotná data (CSV export) jsou veřejně dostupná, ale seznam tabů nikoli.

Pokud by byl klíč veřejný v kódu, kdokoli by ho mohl zneužít pro volání Google API na účet vlastníka projektu (kvóty, případně poplatky).

### `SPREADSHEET_ID`
ID spreadsheettu je součástí URL. Samotné ho znát nestačí pro zápis (spreadsheet je sdílený pro čtení), ale:
- Zbytečně neukazujeme přístupový bod k datům o platbách skupiny lidí
- Jednoduché udržet kód generický a přepoužitelný pro jiné spreadsheets

---

## Soubory v repozitáři

```
platby-qr/
├── generate_qr.py          # Hlavní skript — čte sheets, generuje QR + HTML
├── requirements.txt        # Python závislosti (qrcode, requests, pillow)
├── .github/
│   └── workflows/
│       └── update.yml      # GitHub Actions workflow
└── docs/
    ├── index.html          # Generovaná stránka (GitHub Pages)
    └── qr/                 # Generované PNG QR kódy
        └── *.png
```

> `docs/` je gitignorovaný z `.gitignore` — ne, čeká se na to, že Actions ho vygeneruje a commitne

---

## GitHub Actions workflow

Soubor `.github/workflows/update.yml`:

- **Spouštění:** každý den v 00:00 UTC (cron) + manuálně přes tlačítko „Run workflow"
- **Co dělá:**
  1. Checkout repozitáře
  2. Nainstaluje Python závislosti z `requirements.txt`
  3. Spustí `generate_qr.py` (s env proměnnými `GOOGLE_API_KEY` a `SPREADSHEET_ID` ze secrets)
  4. Commitne změněné soubory v `docs/` zpět do `main` větve

---

## Přístupová práva (Fine-grained PAT)

Pro commit zpět do repozitáře workflow používá GitHub token. Je nakonfigurovaný jako **fine-grained personal access token** s minimálními právy:

| Oprávnění | Úroveň | Důvod |
|---|---|---|
| Contents | Read & write | Čtení kódu + commit vygenerovaných souborů |
| Workflows | Read & write | Správa workflow souborů |
| Metadata | Read-only | Povinné pro všechny fine-grained tokeny |

Token platí do **23. června 2026** a je omezený **pouze na tento repozitář** (`VojtaRocek/platby-qr`). Pokud někdo token získá, nemůže přistoupit k žádnému jinému repozitáři ani k nastavení účtu.

---

## Struktura spreadsheettu

Pro správné fungování musí každý viditelný tab splňovat jedno z:

**Formát A (Marada):**
- Řádek 1: jména osob jako záhlaví sloupců
- Řádek se sumami: celková suma + sumy pro každou osobu
- Řádek „Platit na XXXX/YYYY": text obsahující `platit` a číslo účtu

**Formát B (Olejari, Bidule):**
- Řádek se záhlavím „Sumy": slovo „Sumy" v prvním sloupci, pak jména osob
- Řádky pod ním: jednotlivé položky s částkami pro každou osobu
- Poslední neprázdný řádek: součty za každou osobu
- Číslo účtu: není potřeba (nebo ho lze přidat řádkem „Platit na")

---

## Lokální spuštění

```bash
pip install -r requirements.txt
export GOOGLE_API_KEY=tvuj_klic
export SPREADSHEET_ID=id_spreadsheetu
python generate_qr.py
# → vygeneruje docs/index.html a docs/qr/*.png
```
