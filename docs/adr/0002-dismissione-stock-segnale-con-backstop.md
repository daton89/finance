# ADR-0002 — Dismissione stock: vendita su segnale con backstop a data fissa

Data: 2026-07-12
Stato: accettato

## Contesto

La Transizione ETF richiede di vendere tutte le posizioni stock (MU 38.5% del portafoglio, AMD, WDC, MRVL, SanDisk, Comfort Systems). Due approcci possibili: calendario fisso (tranche a date prestabilite) o vendita discrezionale "sulla forza". Il calendario elimina il market timing ma l'utente preferisce sfruttare i rimbalzi; il discrezionale puro degenera in "non vendere mai" e lascia il rischio di concentrazione aperto a tempo indeterminato — incluso il rischio earnings (MU riporta il 2026-09-23).

## Decisione

Vendita su segnale CON backstop obbligatorio:

- **Segnale ("forza")**: RSI(14) > 60 sul titolo → si vende una tranche (~1/3 della posizione per MU, intera posizione per i titoli minori). Il segnale genera un alert; l'esecuzione resta manuale.
- **Backstop MU**: qualunque residuo si vende entro il **2026-09-15** (prima degli earnings del 23/9), a prescindere dal prezzo.
- **Backstop altri stock**: entro fine ottobre 2026, con abbinamento fiscale gain/loss nello stesso anno fiscale (vedi Tax Pairing in CONTEXT.md).

## Alternative considerate

- **Calendario fisso (3 tranche mensili)**: più disciplinato, rifiutato per preferenza a sfruttare la forza di breve.
- **Discrezionale puro senza backstop**: rifiutato — non monitorabile, rischio di paralisi decisionale con 38% del portafoglio su un singolo titolo semiconduttori.

## Conseguenze

- Serve un condition-checker che monitori RSI(14) dei titoli in dismissione e i giorni residui al backstop, con alert Telegram.
- Se il segnale non scatta mai, la transizione si completa comunque entro ottobre 2026.
- Le vendite possono avvenire a prezzi peggiori del massimo di periodo: accettato per design.
