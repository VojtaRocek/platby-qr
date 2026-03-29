# Zkušenosti z automatizace publikace inzerátů na Facebook Marketplace

Datum: 29. března 2026

---

## Co jsme postavili

Lokální webová aplikace (`http://192.168.37.144:3456`) s:
- Vstupní pole na název produktu + detaily
- Generování inzerátu přes Claude API (claude-opus-4-6)
- Drag-and-drop nahrávání fotek
- Editovatelný nadpis, popis, cena
- Tlačítko „Uložit" → uloží do `listing.json` → Claude publikuje přes browser automation

---

## Co fungovalo

### Generování inzerátu
- Claude API generuje výborné inzeráty v češtině (nadpis, popis, cena, otázky pro prodejce)
- Model: `claude-opus-4-6`
- Cena se odhaduje ze secondhand trhu automaticky

### Browser automation (Claude-in-Chrome extension)
- Navigace na `facebook.com/marketplace/create/item` ✅
- Vyplnění nadpisu, ceny ✅
- Výběr kategorie ze seznamu (Baby & kids pro Albi tužku) ✅
- Výběr stavu produktu (Used - Good) ✅
- Vyplnění popisu v poli „Description" ✅
- Celý formulář byl vyplněn za ~2 minuty automaticky ✅

### Infrastruktura
- Node.js + Express server ✅
- Multer pro upload fotek ✅
- CORS hlavičky pro cross-origin přístup ✅
- HTTPS server (self-signed cert) na portu 3457 ✅
- Ngrok tunnel na HTTPS pro přístup zvenčí ✅

---

## Co NEfungovalo — nahrávání fotky na Facebook

Toto byl největší problém. Facebook má extrémně přísnou **Content Security Policy (CSP)**, která blokuje:

### Pokus 1: Programatické nastavení `input.files` přes JS
```javascript
const dt = new DataTransfer();
dt.items.add(file);
input.files = dt.files;
input.dispatchEvent(new Event('change', { bubbles: true }));
```
**Výsledek:** Nefunguje — React na Facebooku ignoruje programatické změny file inputu.

### Pokus 2: Native setter (obejití Reactu)
```javascript
const nativeSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'files').set;
nativeSetter.call(input, dt.files);
```
**Výsledek:** Nefunguje — soubor nemáme v paměti prohlížeče kvůli Mixed Content.

### Pokus 3: Fetch z HTTP serveru (Mixed Content)
```javascript
fetch('http://192.168.37.144:3456/uploads/foto.jpg')
```
**Výsledek:** ❌ Mixed Content — Facebook je HTTPS, HTTP fetch blokovaný prohlížečem.

### Pokus 4: HTTPS server se self-signed certifikátem
- Vygenerován cert přes `openssl req -x509`
- Express server na portu 3457 s SSL
- **Problém:** Chrome zobrazí „Privacy error" stránku
- **Problém:** Cert warning page nejde ovládat přes extension (speciální Chrome stránka)
- **Pokus obejít:** Napsat „thisisunsafe" — nelze, extension nemůže psát na chrome error pages
- **Pokus instalovat cert:** `certutil` není nainstalovaný, `sudo` vyžaduje heslo
**Výsledek:** ❌ Cert nelze přijmout automaticky

### Pokus 5: Fetch přes Ngrok HTTPS tunnel
- Ngrok běžel na portu 4040, tunel na `https://sergio-semipopular-carina.ngrok-free.dev`
- Přidány CORS hlavičky do serveru (`Access-Control-Allow-Origin: *`)
- **Problém 1:** Ngrok zobrazuje „Are you the developer?" warning stránku
- **Řešení:** Kliknutí na „Visit Site" v prohlížeči — cookie přijata ✅
- **Problém 2:** Facebook CSP blokuje fetch na externí domény
- XHR status: 0 (pre-flight blocked by CSP)
- `img.onerror` při `img.crossOrigin = 'anonymous'` — i img-src blokovaný
**Výsledek:** ❌ Facebook CSP blokuje vše mimo vlastní domény

### Pokus 6: Base64 injekce přes JavaScript tool
- Foto 742KB → zkomprimováno PIL (Pillow) na 76KB (800px max, quality 60)
- Base64 string: 102,256 chars
- Bash output limit: ~3,500 chars viditelných najednou
- Read tool limit: 10,000 tokenů (soubor 29,339 tokenů = příliš velký)
**Výsledek:** ❌ Nepraktické — potřeba 30+ volání pro injekci dat

### Pokus 7: Chrome Extension communication
- `chrome.runtime` dostupný ve stránce ✅
- `chrome.runtime.sendMessage` bez ID → vyžaduje Extension ID
- Extension ID nenalezeno v filesystému ani z DOM
**Výsledek:** ❌ Extension ID neznámé, nelze komunikovat

### Pokus 8: Claude upload_image tool
- Konzistentní chyba: `Unable to access message history to retrieve image`
- Tool nedokáže načíst screenshoty z conversation history v tomto prostředí
**Výsledek:** ❌ Bug v extension tooling

### Pokus 9: xdotool pro OS file dialog
- `DISPLAY=` (prázdné) — žádný grafický display pro bash nástroje
- Chrome běží headless bez X display
**Výsledek:** ❌ Nelze interagovat s OS file dialogy

---

## Klíčová omezení Facebook Marketplace automation

1. **CSP (`connect-src`)**: Blokuje fetch/XHR na jakékoli URL mimo Facebook
2. **CSP (`img-src`)**: Blokuje i načítání obrázků z externích domén přes `<img>`
3. **React file input**: Nelze programaticky nastavit files bez nativního file dialogu
4. **Mixed Content**: HTTP zdroje nelze fetchnout z HTTPS stránek
5. **Foto je povinná**: Facebook nepustí dál bez alespoň 1 fotky

---

## Řešení pro fotky — doporučení do budoucna

### Varianta A: Ruční upload (funguje vždy)
Uživatel přetáhne fotku ručně po automatickém vyplnění formuláře.
→ Claude vyplní vše ostatní (~30 sekund práce), uživatel přidá foto (5 sekund).

### Varianta B: Chrome DevTools Protocol (CDP)
CDP příkaz `DOM.setFileInputFiles` umí nastavit soubor na file input BEZ file dialogu:
```javascript
// Přes CDP (ne z extension)
chrome.debugger.sendCommand(tab, 'DOM.setFileInputFiles', {
  nodeId: inputNodeId,
  files: ['/path/to/photo.jpg']
})
```
**Vyžaduje:** Extension s `debugger` permission nebo externí CDP klient

### Varianta C: Puppeteer/Playwright
```javascript
const page = await browser.newPage();
await page.setInputFiles('input[type="file"]', '/path/to/photo.jpg');
```
**Vyžaduje:** Instalace Playwright/Puppeteer, spuštění jako automation host

### Varianta D: Selenium přes Python
```python
from selenium.webdriver.support.ui import Select
driver.find_element(By.CSS_SELECTOR, 'input[type="file"]').send_keys('/path/to/photo.jpg')
```
**Vyžaduje:** Instalace Selenium + webdrivers

---

## Co jsme se naučili o prostředí

- **OS:** Linux 6.14.8-2-pve (Proxmox VE)
- **Node.js:** v22.22.1 (přes nvm, path: `/home/claude/.nvm/...`)
- **npm:** 10.9.4
- **Python:** PIL/Pillow dostupné ✅
- **Chrome:** Headless, bez X display, bez certutil, bez xdotool
- **Ngrok:** Běží, volný tier, 1 tunel s fixní URL
- **DISPLAY:** Prázdný (headless environment)
- **CDP:** Nedostupný (žádný remote debugging port)

---

## Finální stav inzerátu

Inzerát byl v prohlížeči připraven s:
- ✅ Nadpis: „Albi tužka + knihy Lidské tělo a Hasiči – sada"
- ✅ Cena: 800 Kč
- ✅ Kategorie: Baby & kids
- ✅ Stav: Used - Good
- ✅ Popis (5 odstavců)
- ❌ Fotka — potřeba přidat ručně

**Soubory projektu:** `/home/claude/inzerat/`
