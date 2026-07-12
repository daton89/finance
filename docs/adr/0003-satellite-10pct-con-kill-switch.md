# ADR-0003 — Satellite momentum al 10% con kill-switch

Data: 2026-07-12
Stato: accettato

## Contesto

Dopo la chiusura dello swing fund (ADR-0001) l'utente ha proposto una struttura 70/30 (70% ETF core, 30% strategie swing). Il backtest non giustifica il 30%: nessuna strategia testata batte buy&hold, e il portafoglio è in piena dismissione della concentrazione stock (ADR-0002). È però legittimo mantenere un motore momentum come budget di apprendimento, se dimensionato in modo che la sua sottoperformance attesa non intacchi gli obiettivi.

## Decisione

**Satellite al 10% del portafoglio** (non 30%), con queste regole:

- **Contenuto**: solo strategie sistematiche validate da `backtest.py`. Prima candidata: `ema-cross` (EMA10>EMA20, exit EMA20, stop -7%) su ETF settoriali SMH/QQQ — l'unica variante robusta sia su stock che su ETF (profilo: rendimento < buy&hold, drawdown dimezzato).
- **Niente stock singoli.** Niente strategie discrezionali.
- **Partenza**: solo a transizione MU completata (backstop 2026-09-15).
- **Kill-switch** (deciso ora, a mente fredda): il satellite confluisce nel core (CSPX) se (a) drawdown ≥ -15% dal picco del satellite, oppure (b) sottoperformance vs CSPX ≥ 10 punti su 12 mesi rolling. Nessuna deroga.
- **Target allocation aggiornata**: CSPX 45%, EMIM 15%, WSML 10%, IGLN 10%, IUHC 5%, SATELLITE 10%, cassa 5%.

## Alternative considerate

- **30% swingy**: rifiutato — triplica l'esposizione a un approccio che il backtest ha appena bocciato, e ricrea il problema di concentrazione in smontaggio.
- **0% (solo core)**: più pulito, ma rinuncia al valore di apprendimento del trading sistematico che l'utente vuole mantenere.

## Conseguenze

- `swing_signals.py` verrà riconvertito a regole ema-cross su SMH/QQQ quando il satellite parte (non prima).
- Il risk agent deve trattare il satellite come bucket separato: drawdown dal picco e confronto rolling vs CSPX sono i due input del kill-switch.
- Se il kill-switch scatta, l'esperimento momentum è chiuso — riaprirlo richiede un nuovo ADR.
