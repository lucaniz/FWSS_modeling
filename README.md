# FWSS Gas Modeling

Gas cost calculators and network capacity tools for the [FilecoinWarmStorageService (FWSS)](https://github.com/FilOzone/filecoin-services) protocol on Filecoin mainnet.

Live: **[lucaniz.github.io/FWSS_modeling](https://lucaniz.github.io/FWSS_modeling/)**

---

## Tools

### [Gas Calculator](https://lucaniz.github.io/FWSS_modeling/calculator.html)
Per-operation cost breakdown with live data from FOC Observer. Input BaseFee, GasPremium, GasFeeCap and FIL/USD price to compute the cost of every FWSS operation — provePossession, nextProvingPeriod, addPieces (K=1..310), createDataSet, terminateService, removePieces. Includes:
- Proving floor price (monthly cost per dataset)
- Batching table (observed mainnet values K=1..10, model extrapolation K>10)
- Historical EGP from real FWSS transactions (24h / 30d / 90d)
- FIP-0115 toggle (BaseFee/premium split sensitivity)
- CDN toggle (+45M gas on createDataSet, +120M on terminateService)

### [Capacity](https://lucaniz.github.io/FWSS_modeling/capacity.html)
Network headroom analysis — how many active FWSS datasets fit within Filecoin's daily gas budget. Live dataset count from FOC Observer, 5 background gas scenarios (Today / Low / Dec 2024 / Peak / Custom), user-configurable pieces-per-dataset and batch parameters.

### [Extreme Scenarios](https://lucaniz.github.io/FWSS_modeling/extreme_modeling.html)
Code-derived threshold analysis. Sidebar controls for every gas dimension (background load, active datasets, pieces/dataset, addPieces batch size K, removal queue, CDN fraction, lifecycle events). Shows dual bars (daily budget vs tipset budget), metrics cards, and a dynamic findings panel that flags CRITICAL / HIGH / MEDIUM conditions with exact threshold derivations from contract code.

---

## Gas model

All costs use the correct effective gas price formula from [Lotus source](https://github.com/filecoin-project/lotus/blob/master/chain/types/message.go):

```
EGP = BaseFee + min(GasPremium, GasFeeCap − BaseFee)
cost in FIL = GasUsed × EGP / 1e18
```

Today on Filecoin mainnet: BaseFee ≈ 100 attoFIL, GasPremium ≈ 250k attoFIL — the SP tip dominates (~99.9% of cost). The old calculator used only BaseFee and was off by ~2,500×.

### provePossession
Logarithmic model fitted on real mainnet data:
```
gas = PP_ALPHA + PP_BETA × log₂(N)
```
where N = number of pieces in the dataset. Currently PP_ALPHA ≈ 158.67M, PP_BETA ≈ 8.485M (R²=0.9551).

### addPieces
Linear model fitted on real mainnet data (K=1..10 observed):
```
gas = ADD_MODEL_ALPHA + ADD_MODEL_BETA × K
```
Currently ADD_MODEL_ALPHA ≈ 228.72M, ADD_MODEL_BETA ≈ 78.20M (R²=0.9996). **K > 310: tx exceeds tipset gas budget → stuck forever.**

### Other operations (30d mainnet avg, post FWSS v1.2.0)

| Operation | Gas |
|---|---|
| nextProvingPeriod | ~153M |
| createDataSet (no CDN) | ~1,123M |
| createDataSet (CDN) | ~1,168M |
| terminateService (no CDN) | ~137M |
| terminateService (CDN) | ~257M |
| removePieces ×1 | ~443M |

> ⚠️ **FWSS v1.2.0 upgrade (23 Mar 2026):** createDataSet gas increased ~38% (796M → 1,123M), nextProvingPeriod +23% (124M → 153M). All values above reflect the post-upgrade era.

---

## Auto-refit

Gas model constants update automatically every day at **09:00 UTC** via GitHub Actions (`update_model.yml`). The script (`scripts/refit_model.py`):

1. Queries FOC Observer mainnet for real transaction data
2. Fits the provePossession log₂ model via OLS
3. Fits the addPieces linear model via `linregress` on all K with ≥10 mainnet observations
4. Fetches 30d averages for NPP, createDataSet, terminateService
5. Patches all constants (`PP_ALPHA`, `PP_BETA`, `ADD_MODEL_ALPHA`, `ADD_MODEL_BETA`, `NPP`, `CR0`, `CR1`, `TR0`, `TR1`) in all three HTML files and commits

If a new K value accumulates ≥10 mainnet observations (e.g. K=11), it is automatically included in the next refit. The stuck threshold K>310 is recomputed from the fitted model on every run.

Clicking **Refresh** in any tool also fetches live values from FOC Observer directly in the browser, updating constants without waiting for the next daily refit.

---

## Repo structure

```
.
├── index.html                      # Landing page
├── calculator.html                 # Gas calculator
├── capacity.html                   # Capacity tool
├── extreme_modeling.html           # Extreme scenarios
├── scripts/
│   └── refit_model.py              # Daily refit script
└── .github/workflows/
    ├── update_model.yml            # Daily refit (cron 09:00 UTC)
    ├── write_calculator.yml        # Manual deploy: calculator.html
    ├── write_capacity.yml          # Manual deploy: capacity.html
    ├── write_extreme_modeling.yml  # Manual deploy: extreme_modeling.html
    └── write_index.yml             # Manual deploy: index.html
```

---

## Data source

All gas data from **[FOC Observer](https://foc-observer.va.gg)** — Filecoin mainnet, indexed from PDPVerifier and FWSS contract events. REST API at `https://foc-observer.va.gg/sql` (CORS enabled, no auth required).

---
