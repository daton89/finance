# ADR-0001 — Chiusura dello Swing Fund

Data: 2026-07-12
Stato: accettato

## Contesto

Il fondo swing (10k€, MU/AMD 50/50, regole RSI35-cross + exit EMA10, target +10%/mese) è stato backtestato su 5 anni (`packages/scripts/backtest.py`):

- Regole live: -0.5% vs +838% buy&hold; 6 trade in 5 anni; 0/1234 finestre mensili raggiungono +10%.
- Migliore variante su MU/AMD (entry-rsi50, +378%) crolla su ETF (SMH/QQQ: +39.6%) → overfitting.
- Migliore variante robusta (ema-cross) non batte buy&hold su nessun universo; riduce solo il drawdown.

Nel frattempo la direzione strategica del portafoglio è diventata ETF-only (Transizione ETF).

## Decisione

Chiudere lo Swing Fund. Il capitale confluisce nella Transizione ETF (acquisto posizioni target). Niente conversione a momentum su ETF.

## Alternative considerate

- **Convertire a ema-cross su SMH/QQQ**: profilo rischio decente (maxDD -16% vs -40% B&H) ma rendimento inferiore a buy&hold — incoerente con l'obiettivo di semplificazione, e mantiene costi di manutenzione (cron, monitoraggio, esecuzione manuale).
- **Congelare e ridecidere**: rimanda senza nuove informazioni attese.

## Conseguenze

- Il target "+10%/mese" è ufficialmente abbandonato.
- Cron "Swing Momentum Signal" (14:30) da disabilitare; `swing_signals.py` e `backtest.py` restano come strumenti di analisi non operativi.
- La skill Hermes `finance-agent-team` va aggiornata (descrive ancora il fondo swing come attivo).
