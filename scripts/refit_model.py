#!/usr/bin/env python3
"""
FWSS model refit script — runs daily via GitHub Actions
Updates:
  1. provePossession logarithmic model: gas = PP_ALPHA + PP_BETA * log2(pieces)
  2. addPieces linear model: gas = ADD_ALPHA + ADD_BETA * K
     - Uses all observed K values (currently K=1..10 on mainnet)
     - If new K values appear (K>10), they are automatically included
     - Model coefficients and stuck threshold updated in all HTML files
"""
import json, re, sys, math
from datetime import datetime, timezone

try:
    import requests
    import numpy as np
    from scipy.optimize import curve_fit
    from scipy.stats import linregress
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install",
                           "requests", "numpy", "scipy", "--quiet"])
    import requests
    import numpy as np
    from scipy.optimize import curve_fit
    from scipy.stats import linregress

FOC_URL  = "https://foc-observer.va.gg/sql"
TIPSET_B = 24.5e9  # gas units per tipset budget

def foc(sql):
    r = requests.post(FOC_URL,
                      json={"sql": sql, "network": "mainnet"},
                      timeout=30)
    r.raise_for_status()
    return r.json()["rows"]

# ── 1. Fit provePossession model ──────────────────────────────────────────────
print("=== provePossession model ===")
rows = foc("""
    SELECT
        COUNT(p.set_id) AS n,
        ROUND(AVG(p.gas_used)) AS avg_gas,
        f.piece_count_at_proof AS pieces
    FROM pdp_possession_proven p
    JOIN (
        SELECT
            set_id,
            COUNT(DISTINCT piece_id) AS piece_count_at_proof
        FROM fwss_piece_added
        GROUP BY set_id
    ) f ON f.set_id = p.set_id
    WHERE p.gas_used > 0
    GROUP BY f.piece_count_at_proof
    HAVING COUNT(p.set_id) >= 3
    ORDER BY f.piece_count_at_proof
""")

if len(rows) < 5:
    # fallback simpler query
    rows = foc("""
        SELECT
            piece_count AS pieces,
            COUNT(*) AS n,
            ROUND(AVG(gas_used)) AS avg_gas
        FROM (
            SELECT
                p.gas_used,
                (SELECT COUNT(*) FROM fwss_piece_added fp
                 WHERE fp.data_set_id = p.set_id) AS piece_count
            FROM pdp_possession_proven p
            WHERE p.gas_used > 0
        ) t
        WHERE piece_count > 0
        GROUP BY piece_count
        HAVING COUNT(*) >= 3
        ORDER BY piece_count
    """)

if rows:
    X = np.array([float(r["pieces"]) for r in rows])
    Y = np.array([float(r["avg_gas"]) for r in rows])
    def log2_model(x, a, b): return a + b * np.log2(np.maximum(x, 1))
    popt, _ = curve_fit(log2_model, X, Y, p0=[158e6, 8e6])
    pp_alpha, pp_beta = popt
    pp_pred = log2_model(X, *popt)
    ss_res = np.sum((Y - pp_pred)**2)
    ss_tot = np.sum((Y - np.mean(Y))**2)
    r2  = 1 - ss_res/ss_tot
    mae = np.mean(np.abs(Y - pp_pred))
    n   = len(rows)
    print(f"  gas = {pp_alpha/1e6:.2f}M + {pp_beta/1e6:.3f}M × log2(N)")
    print(f"  R² = {r2:.4f}  MAE = {mae/1e6:.1f}M  N = {n} datasets")
else:
    pp_alpha, pp_beta, r2, mae, n = 158.67e6, 8.485e6, 0.9551, 4.8e6, 670
    print("  Using fallback values (no data)")

# ── 2. Fit addPieces model ────────────────────────────────────────────────────
print("\n=== addPieces model ===")
add_rows = foc("""
    SELECT
        piece_count AS k,
        COUNT(*) AS n,
        ROUND(AVG(gas_used)) AS avg_gas
    FROM pdp_pieces_added
    WHERE gas_used > 0
    GROUP BY piece_count
    HAVING COUNT(*) >= 10
    ORDER BY piece_count
""")

if len(add_rows) >= 2:
    K = np.array([float(r["k"]) for r in add_rows])
    G = np.array([float(r["avg_gas"]) for r in add_rows])
    slope, intercept, ar2, _, _ = linregress(K, G)
    add_alpha = intercept
    add_beta  = slope
    add_mae   = np.mean(np.abs(G - (add_alpha + add_beta * K)))
    add_r2    = ar2**2
    k_max_obs = int(K.max())
    # Stuck threshold: largest K where gas < tipset budget
    k_stuck = int((TIPSET_B - add_alpha) / add_beta)
    print(f"  gas = {add_alpha/1e6:.2f}M + {add_beta/1e6:.2f}M × K")
    print(f"  R² = {add_r2:.4f}  MAE = {add_mae/1e6:.1f}M")
    print(f"  Observed K range: 1..{k_max_obs}  Stuck at K > {k_stuck}")
    observed_k = {int(r["k"]): int(r["avg_gas"]) for r in add_rows}
else:
    add_alpha, add_beta, add_r2, k_max_obs = 228.72e6, 78.20e6, 0.9996, 9
    k_stuck   = 310
    observed_k = {}
    print("  Using fallback values (insufficient data)")

today = datetime.now(timezone.utc).strftime("%-d %b %Y")

# ── 3. Patch HTML files ───────────────────────────────────────────────────────
def patch_file(path, pp_alpha, pp_beta, r2, mae, n, add_alpha, add_beta, k_stuck, obs_k, today):
    with open(path) as f:
        c = f.read()

    # provePossession model constants
    c = re.sub(r'MODEL_ALPHA\s*=\s*[\d.e]+',
               f'MODEL_ALPHA = {pp_alpha:.2f}', c)
    c = re.sub(r'PP_ALPHA\s*=\s*[\d.e]+',
               f'PP_ALPHA = {pp_alpha:.2f}', c)
    c = re.sub(r'MODEL_BETA\s*=\s*[\d.e]+',
               f'MODEL_BETA = {pp_beta:.3f}', c)
    c = re.sub(r'PP_BETA\s*=\s*[\d.e]+',
               f'PP_BETA = {pp_beta:.3f}', c)

    # addPieces model constants
    c = re.sub(r'ADD_MODEL_ALPHA\s*=\s*[\d.e]+',
               f'ADD_MODEL_ALPHA = {add_alpha:.2f}', c)
    c = re.sub(r'ADD_MODEL_BETA\s*=\s*[\d.e]+',
               f'ADD_MODEL_BETA = {add_beta:.2f}', c)

    # Stuck threshold
    c = re.sub(r'K>\d+: stuck', f'K>{k_stuck}: stuck', c)
    c = re.sub(r'K>\d+: tx exceeds', f'K>{k_stuck}: tx exceeds', c)
    c = re.sub(r'Beyond ×\d+: tx exceeds', f'Beyond ×{k_stuck}: tx exceeds', c)

    # Human-readable model formula in text
    c = re.sub(r'\d+\.\d+M \+ \d+\.\d+M × K',
               f'{add_alpha/1e6:.2f}M + {add_beta/1e6:.2f}M × K', c)
    c = re.sub(r'\d+\.\d+M \+ \d+\.\d+M × log₂\(N\)',
               f'{pp_alpha/1e6:.2f}M + {pp_beta/1e6:.3f}M × log₂(N)', c)

    # Fit stats
    c = re.sub(r'R²\s*=\s*0\.\d+', f'R² = {r2:.4f}', c)
    c = re.sub(r'MAE\s*=\s*[\d.]+M gas', f'MAE = {mae/1e6:.1f}M gas', c)
    c = re.sub(r'\d+ real mainnet datasets', f'{n} real mainnet datasets', c)

    # Update date stamp
    c = re.sub(r'updated \d{1,2} \w+ \d{4}', f'updated {today}', c)

    with open(path, 'w') as f:
        f.write(c)
    print(f"  Patched: {path}")

html_files = [
    "calculator.html",
    "capacity.html",
    "extreme_modeling.html",
]

print("\n=== Patching HTML files ===")
for fname in html_files:
    try:
        patch_file(fname, pp_alpha, pp_beta, r2, mae, n,
                   add_alpha, add_beta, k_stuck, observed_k, today)
    except FileNotFoundError:
        print(f"  Skipping {fname} (not found)")

print("\nDone.")
print(f"PP:  {pp_alpha/1e6:.2f}M + {pp_beta/1e6:.3f}M × log2(N), R²={r2:.4f}")
print(f"Add: {add_alpha/1e6:.2f}M + {add_beta/1e6:.2f}M × K, K_stuck={k_stuck}")
