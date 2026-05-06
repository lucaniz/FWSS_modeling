#!/usr/bin/env python3
"""
FWSS model refit — runs daily via GitHub Actions at 09:00 UTC.
Updates ALL gas constants in calculator.html, capacity.html, extreme_modeling.html:
  - provePossession: PP_A, PP_B (log2 model)
  - addPieces: ADD_MODEL_ALPHA, ADD_MODEL_BETA (linear model), K_stuck
  - nextProvingPeriod: NPP
  - createDataSet: CR0 (no CDN), CR1 (CDN)
  - terminateService: TR0 (no CDN), TR1 (CDN)
"""
import re, sys
from datetime import datetime, timezone

try:
    import requests, numpy as np
    from scipy.optimize import curve_fit
    from scipy.stats import linregress
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install",
                           "requests", "numpy", "scipy", "--quiet"])
    import requests, numpy as np
    from scipy.optimize import curve_fit
    from scipy.stats import linregress

FOC_URL  = "https://foc-observer.va.gg/sql"
TIPSET_B = 24.5e9

def foc(sql):
    r = requests.post(FOC_URL,
                      json={"sql": sql, "network": "mainnet"},
                      timeout=30)
    r.raise_for_status()
    return r.json()["rows"]

# ── 1. provePossession model ──────────────────────────────────────────────────
print("=== provePossession model ===")
rows = foc("""
    SELECT
        n_pieces AS pieces,
        COUNT(*) AS n,
        ROUND(AVG(gas_used)) AS avg_gas
    FROM (
        SELECT
            p.gas_used,
            (SELECT COUNT(*)
             FROM fwss_piece_added fp
             WHERE fp.data_set_id = p.set_id) AS n_pieces
        FROM pdp_possession_proven p
        WHERE p.gas_used > 0
          AND p.set_id IN (SELECT DISTINCT data_set_id FROM fwss_piece_added)
    ) t
    WHERE n_pieces > 0
    GROUP BY n_pieces
    HAVING COUNT(*) >= 3
    ORDER BY n_pieces
""")

if not rows or len(rows) < 5:
    rows = foc("""
        SELECT
            piece_count AS pieces,
            cnt AS n,
            avg_gas
        FROM (
            SELECT set_id, COUNT(*) AS cnt, ROUND(AVG(gas_used)) AS avg_gas
            FROM pdp_possession_proven
            WHERE gas_used > 0
            GROUP BY set_id
            HAVING COUNT(*) >= 3
        ) proven
        JOIN (
            SELECT set_id, COUNT(DISTINCT piece_id) AS piece_count
            FROM pdp_pieces_added
            GROUP BY set_id
        ) pieces USING (set_id)
        WHERE piece_count > 0
        ORDER BY piece_count
    """)

if rows and len(rows) >= 5:
    X = np.array([float(r["pieces"]) for r in rows])
    Y = np.array([float(r["avg_gas"]) for r in rows])
    def log2_model(x, a, b): return a + b * np.log2(np.maximum(x, 1))
    popt, _ = curve_fit(log2_model, X, Y, p0=[158e6, 8e6])
    pp_alpha, pp_beta = popt
    pp_pred = log2_model(X, *popt)
    r2  = float(1 - np.sum((Y - pp_pred)**2) / np.sum((Y - np.mean(Y))**2))
    mae = float(np.mean(np.abs(Y - pp_pred)))
    n   = len(rows)
    print(f"  gas = {pp_alpha/1e6:.2f}M + {pp_beta/1e6:.3f}M x log2(N), R2={r2:.4f}, MAE={mae/1e6:.1f}M, n={n}")
else:
    pp_alpha, pp_beta, r2, mae, n = 158.67e6, 8.485e6, 0.9551, 4.8e6, 670
    print("  Using fallback values")

# ── 2. addPieces model ────────────────────────────────────────────────────────
print("\n=== addPieces model ===")
add_rows = foc("""
    SELECT piece_count AS k, COUNT(*) AS n, ROUND(AVG(gas_used)) AS avg_gas
    FROM pdp_pieces_added
    WHERE gas_used > 0
    GROUP BY piece_count
    HAVING COUNT(*) >= 10
    ORDER BY piece_count
""")

if add_rows and len(add_rows) >= 2:
    K = np.array([float(r["k"])       for r in add_rows])
    G = np.array([float(r["avg_gas"]) for r in add_rows])
    slope, intercept, ar, _, _ = linregress(K, G)
    add_alpha = float(intercept)
    add_beta  = float(slope)
    add_r2    = float(ar**2)
    add_mae   = float(np.mean(np.abs(G - (add_alpha + add_beta * K))))
    k_max_obs = int(K.max())
    k_stuck   = int((TIPSET_B - add_alpha) / add_beta)
    print(f"  gas = {add_alpha/1e6:.2f}M + {add_beta/1e6:.2f}M x K, R2={add_r2:.4f}, MAE={add_mae/1e6:.1f}M")
    print(f"  K range: 1..{k_max_obs}, stuck at K>{k_stuck}")
else:
    add_alpha, add_beta, add_r2, k_max_obs, k_stuck = 228.72e6, 78.20e6, 0.9996, 10, 310
    print("  Using fallback values")

# ── 3. NPP, createDataSet, terminateService (30d avg) ────────────────────────
print("\n=== Other operations (30d avg, EGP-based) ===")
op_rows = foc("""
    SELECT op, ROUND(AVG(gas_used)) AS avg_gas, COUNT(*) AS n FROM (
      SELECT 'NPP'      AS op, gas_used FROM pdp_next_proving_period
        WHERE timestamp >= EXTRACT(EPOCH FROM NOW()) - 86400*30
      UNION ALL
      SELECT 'CR0', gas_used FROM fwss_data_set_created
        WHERE with_cdn=false AND timestamp >= EXTRACT(EPOCH FROM NOW()) - 86400*30
      UNION ALL
      SELECT 'CR1', gas_used FROM fwss_data_set_created
        WHERE with_cdn=true AND timestamp >= EXTRACT(EPOCH FROM NOW()) - 86400*30
      UNION ALL
      SELECT 'TR0', t.gas_used FROM fwss_service_terminated t
        JOIN fwss_data_set_created d ON d.data_set_id=t.data_set_id
        WHERE d.with_cdn=false AND t.timestamp >= EXTRACT(EPOCH FROM NOW()) - 86400*90
      UNION ALL
      SELECT 'TR1', t.gas_used FROM fwss_service_terminated t
        JOIN fwss_data_set_created d ON d.data_set_id=t.data_set_id
        WHERE d.with_cdn=true AND t.timestamp >= EXTRACT(EPOCH FROM NOW()) - 86400*90
    ) t GROUP BY op ORDER BY op
""")

# Defaults (post v1.2.0)
ops = {'NPP': 153e6, 'CR0': 1123e6, 'CR1': 1168e6, 'TR0': 137e6, 'TR1': 257e6}
MIN_N = {'NPP': 100, 'CR0': 10, 'CR1': 5, 'TR0': 20, 'TR1': 5}

for r in op_rows:
    key = r['op']
    gas = float(r['avg_gas']) if r['avg_gas'] else None
    n_obs = int(r['n'])
    if gas and n_obs >= MIN_N.get(key, 5):
        ops[key] = gas
        print(f"  {key}: {gas/1e6:.0f}M (n={n_obs})")
    else:
        print(f"  {key}: fallback {ops[key]/1e6:.0f}M (only {n_obs} obs, need {MIN_N.get(key,5)})")

npp  = ops['NPP']
cr0  = ops['CR0']
cr1  = ops['CR1']
tr0  = ops['TR0']
tr1  = ops['TR1']

today = datetime.now(timezone.utc).strftime("%-d %b %Y")

# ── 4. Patch HTML files ───────────────────────────────────────────────────────
def patch(path):
    try:
        c = open(path).read()
    except FileNotFoundError:
        print(f"  SKIP {path} (not found)")
        return

    # PP model (multiple variable name variants across files)
    c = re.sub(r'PP_A\s*=\s*[\d.e+]+',         f'PP_A = {pp_alpha:.2f}', c)
    c = re.sub(r'PP_B\s*=\s*[\d.e+]+',         f'PP_B = {pp_beta:.3f}', c)
    c = re.sub(r'PP_ALPHA\s*=\s*[\d.e+]+',     f'PP_ALPHA = {pp_alpha:.2f}', c)
    c = re.sub(r'PP_BETA\s*=\s*[\d.e+]+',      f'PP_BETA = {pp_beta:.3f}', c)
    c = re.sub(r'MODEL_ALPHA\s*=\s*[\d.e+]+',  f'MODEL_ALPHA = {pp_alpha:.2f}', c)
    c = re.sub(r'MODEL_BETA\s*=\s*[\d.e+]+',   f'MODEL_BETA = {pp_beta:.3f}', c)

    # addPieces model
    c = re.sub(r'ADD_MODEL_ALPHA\s*=\s*[\d.e+]+', f'ADD_MODEL_ALPHA = {add_alpha:.2f}', c)
    c = re.sub(r'ADD_MODEL_BETA\s*=\s*[\d.e+]+',  f'ADD_MODEL_BETA = {add_beta:.2f}', c)

    # NPP (multiple variable name variants)
    c = re.sub(r'const NPP\s*=\s*[\d.e+]+',  f'const NPP = {npp:.2e}', c)
    c = re.sub(r'NPP_GAS\s*=\s*[\d.e+]+',    f'NPP_GAS = {npp:.2e}', c)
    c = re.sub(r'NPP_BASE\s*=\s*[\d.e+]+',   f'NPP_BASE = {npp:.2e}', c)
    c = re.sub(r'NPP_LIVE\s*=\s*[\d.e+]+',   f'NPP_LIVE = {npp:.2e}', c)

    # createDataSet
    c = re.sub(r'const CR0\s*=\s*[\d.e+]+',  f'const CR0 = {cr0:.2e}', c)
    c = re.sub(r'const CR1\s*=\s*[\d.e+]+',  f'const CR1 = {cr1:.2e}', c)
    c = re.sub(r'CREATE_NO_CDN\s*=\s*[\d.e+]+', f'CREATE_NO_CDN = {cr0:.2e}', c)
    c = re.sub(r'CREATE_CDN\s*=\s*[\d.e+]+',    f'CREATE_CDN = {cr1:.2e}', c)

    # terminateService
    c = re.sub(r'const TR0\s*=\s*[\d.e+]+',  f'const TR0 = {tr0:.2e}', c)
    c = re.sub(r'const TR1\s*=\s*[\d.e+]+',  f'const TR1 = {tr1:.2e}', c)
    c = re.sub(r'TERM_NO_CDN\s*=\s*[\d.e+]+',   f'TERM_NO_CDN = {tr0:.2e}', c)
    c = re.sub(r'TERM_CDN\s*=\s*[\d.e+]+',      f'TERM_CDN = {tr1:.2e}', c)

    # Human-readable text
    c = re.sub(r'\d+\.\d+M \+ \d+\.\d+M x K',
               f'{add_alpha/1e6:.2f}M + {add_beta/1e6:.2f}M x K', c)
    c = re.sub(r'\d+\.\d+M \+ \d+\.\d+M x log2.N.',
               f'{pp_alpha/1e6:.2f}M + {pp_beta/1e6:.3f}M x log2(N)', c)

    # Stats
    c = re.sub(r'R2\s*=\s*0\.\d+',          f'R2 = {r2:.4f}', c)
    c = re.sub(r'MAE\s*=\s*[\d.]+M gas',    f'MAE = {mae/1e6:.1f}M gas', c)
    c = re.sub(r'\d+ real mainnet datasets', f'{n} real mainnet datasets', c)
    c = re.sub(r'updated \d{1,2} \w+ \d{4}', f'updated {today}', c)

    open(path, 'w').write(c)
    print(f"  Patched {path}")

print("\n=== Patching HTML files ===")
for f in ["calculator.html", "capacity.html", "extreme_modeling.html"]:
    patch(f)

print(f"\nDone.")
print(f"  PP:   {pp_alpha/1e6:.2f}M + {pp_beta/1e6:.3f}M*log2(N)")
print(f"  Add:  {add_alpha/1e6:.2f}M + {add_beta/1e6:.2f}M*K, K_stuck={k_stuck}")
print(f"  NPP:  {npp/1e6:.0f}M")
print(f"  CR0:  {cr0/1e6:.0f}M  CR1: {cr1/1e6:.0f}M")
print(f"  TR0:  {tr0/1e6:.0f}M  TR1: {tr1/1e6:.0f}M")
