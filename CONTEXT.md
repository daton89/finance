# CONTEXT — Glossario del dominio

## Termini

**Portafoglio (Portfolio)**
L'insieme delle posizioni detenute su Scalable Capital, tracciate in `packages/portfolio.json`. Comprende ETF, stock e cassa.

**Transizione ETF (ETF Transition)**
Il processo pianificato di migrazione del portafoglio da mix stock+ETF a solo ETF (più cassa). Misurata come % del valore di portafoglio nelle posizioni target. Il ricavato di ogni vendita si reinveste lo stesso giorno nell'ETF target più sottopesato (rotazione equity→equity, niente cash parcheggiata). Stato: in corso.

**Target Allocation**
La composizione obiettivo del portafoglio a fine transizione, definita in `packages/config/target_allocation.json`. Ogni posizione detenuta ma assente dalla target ha implicitamente target 0%.

**Core**
Il mattone principale della Target Allocation: CSPX (iShares Core S&P 500), 45%. Scelto rispetto a Scalable MSCI AC World (in dismissione, overlap ~60%) perché non esiste un PAC attivo e i mattoni separati (CSPX+EMIM+WSML) danno controllo sui pesi con TER inferiore.

**Satellite**
Bucket del 10% per momentum sistematico su ETF settoriali (SMH/QQQ), attivo solo a transizione MU completata. Solo strategie validate da backtest, mai stock singoli. Soggetto a kill-switch: -15% dal picco o -10pp vs CSPX su 12 mesi → confluisce nel core. Vedi ADR-0003.

**Swing Fund** *(chiuso — vedi ADR-0001)*
Ex fondo sperimentale da 10.000€ per swing trading su MU/AMD con target +10%/mese. Chiuso a luglio 2026: il capitale confluisce nella Transizione ETF. Il tooling (`swing_signals.py`, `backtest.py`) resta nel repo come strumento di analisi, non operativo.

**Banda di ribilanciamento (Rebalance Band)**
Deviazione massima tollerata di una posizione dal suo target: 5 punti percentuali assoluti. Sotto banda non si interviene. I versamenti nuovi vanno sempre sull'ETF più sottopesato (importo variabile, nessun PAC). Ribilanciamento con vendita solo a banda superata.

**Vendita sulla forza (Strength Sell)**
Vendita di una tranche di uno stock in dismissione quando RSI(14) > 60. Genera un alert; l'esecuzione è manuale. Vedi ADR-0002.

**Backstop**
Data limite oltre la quale uno stock in dismissione si vende comunque, anche senza segnale di forza. MU: 2026-09-15 (pre-earnings). Altri stock: fine ottobre 2026. Vedi ADR-0002.

**Abbinamento fiscale (Tax Pairing)**
La vendita nello stesso anno fiscale di posizioni in plusvalenza e in minusvalenza per compensarle. Include stock ed ETC (entrambi "redditi diversi"); esclude gli ETF (le loro plusvalenze sono "redditi di capitale", non compensabili). ISLN (ETC argento, in dismissione) partecipa quindi al pairing con gli stock. Suggerito da `portfolio_manager.py transition`, da verificare col commercialista.

**Dismissione (Divestment)**
L'uscita da una posizione con target 0%. Stock: segnale+backstop (ADR-0002). ISLN: nello stesso anno fiscale degli stock, per il pairing. Scalable AC World: subito, con la prima tranche. IGLN (sovrappeso): nessuna vendita — si diluisce con la crescita del denominatore.
