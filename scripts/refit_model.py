#!/usr/bin/env python3
"""
FWSS model refit — runs daily via GitHub Actions.
Updates provePossession model (PP_ALPHA, PP_BETA) and
addPieces linear model (ADD_MODEL_ALPHA, ADD_MODEL_BETA, K_stuck)
in all HTML files.
"""
import json, re, sys, math
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
    # Fallback: simpler per-dataset average approach
    rows = foc("""
        SELECT
            piece_count AS pieces,
            cnt AS n,
            avg_gas
        FROM (
            SELECT
                set_id,
                COUNT(*) AS cnt,
                ROUND(AVG(gas_used)) AS avg_gas
            FROM pdp_possession_proven
            WHERE gas_used > 0
            GROUP BY set_id
            HAVING COUNT(*) >= 3
        ) proven
        JOIN (
            SELECT set_id,
                   COUNT(DISTINCT piece_id) AS piece_count
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
    ss_res  = np.sum((Y - pp_pred)**2)
    ss_tot  = np.sum((Y - np.mean(Y))**2)
    r2  = float(1 - ss_res/ss_tot)
    mae = float(np.mean(np.abs(Y - pp_pred)))
    n   = len(rows)
    print(f"  gas = {pp_alpha/1e6:.2f}M + {pp_beta/1e6:.3f}M x log2(N)")
    print(f"  R2 = {r2:.4f}  MAE = {mae/1e6:.1f}M  datasets = {n}")
else:
    pp_alpha, pp_beta, r2, mae, n = 158.67e6, 8.485e6, 0.9551, 4.8e6, 670
    print("  Using fallback values (insufficient data from FOC)")

# ── 2. addPieces model ────────────────────────────────────────────────────────
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

if add_rows and len(add_rows) >= 2:
    K  = np.array([float(r["k"])       for r in add_rows])
    G  = np.array([float(r["avg_gas"]) for r in add_rows])
    slope, intercept, ar, _, _ = linregress(K, G)
    add_alpha = float(intercept)
    add_beta  = float(slope)
    add_r2    = float(ar**2)
    add_mae   = float(np.mean(np.abs(G - (add_alpha + add_beta * K))))
    k_max_obs = int(K.max())
    k_stuck   = int((TIPSET_B - add_alpha) / add_beta)
    print(f"  gas = {add_alpha/1e6:.2f}M + {add_beta/1e6:.2f}M x K")
    print(f"  R2 = {add_r2:.4f}  MAE = {add_mae/1e6:.1f}M")
    print(f"  Observed K: 1..{k_max_obs}  Stuck at K > {k_stuck}")
else:
    add_alpha, add_beta, add_r2, k_max_obs = 228.72e6, 78.20e6, 0.9996, 9
    k_stuck = 310
    print("  Using fallback values (insufficient data)")

today = datetime.now(timezone.utc).strftime("%-d %b %Y")

# ── 3. Patch HTML files ───────────────────────────────────────────────────────
def patch(path):
    try:
        c = open(path).read()
    except FileNotFoundError:
        print(f"  SKIP {path} (not found)")
        return

    # PP model
    c = re.sub(r'MODEL_ALPHA\s*=\s*[\d.e+]+',   f'MODEL_ALPHA = {pp_alpha:.2f}', c)
    c = re.sub(r'PP_ALPHA\s*=\s*[\d.e+]+',      f'PP_ALPHA = {pp_alpha:.2f}', c)
    c = re.sub(r'MODEL_BETA\s*=\s*[\d.e+]+',    f'MODEL_BETA = {pp_beta:.3f}', c)
    c = re.sub(r'PP_BETA\s*=\s*[\d.e+]+',       f'PP_BETA = {pp_beta:.3f}', c)

    # addPieces model
    c = re.sub(r'ADD_MODEL_ALPHA\s*=\s*[\d.e+]+', f'ADD_MODEL_ALPHA = {add_alpha:.2f}', c)
    c = re.sub(r'ADD_MODEL_BETA\s*=\s*[\d.e+]+',  f'ADD_MODEL_BETA = {add_beta:.2f}', c)

    # Stuck threshold
    c = re.sub(r'K>\d+: stuck',          f'K>{k_stuck}: stuck', c)
    c = re.sub(r'K>\d+: tx exceeds',     f'K>{k_stuck}: tx exceeds', c)
    c = re.sub(r'Beyond x\d+: tx',       f'Beyond x{k_stuck}: tx', c)

    # Human-readable formulas
    c = re.sub(r'\d+\.\d+M \+ \d+\.\d+M x K',
               f'{add_alpha/1e6:.2f}M + {add_beta/1e6:.2f}M x K', c)
    c = re.sub(r'\d+\.\d+M \+ \d+\.\d+M x log2.N.',
               f'{pp_alpha/1e6:.2f}M + {pp_beta/1e6:.3f}M x log2(N)', c)

    # Stats
    c = re.sub(r'R2\s*=\s*0\.\d+',        f'R2 = {r2:.4f}', c)
    c = re.sub(r'MAE\s*=\s*[\d.]+M gas',  f'MAE = {mae/1e6:.1f}M gas', c)
    c = re.sub(r'\d+ real mainnet datasets', f'{n} real mainnet datasets', c)
    c = re.sub(r'updated \d{1,2} \w+ \d{4}', f'updated {today}', c)

    open(path, 'w').write(c)
    print(f"  Patched {path}")

print("\n=== Patching HTML files ===")
for f in ["calculator.html", "capacity.html", "extreme_modeling.html"]:
    patch(f)

print(f"\nDone — PP: {pp_alpha/1e6:.2f}M + {pp_beta/1e6:.3f}M*log2(N)")
print(f"       Add: {add_alpha/1e6:.2f}M + {add_beta/1e6:.2f}M*K, K_stuck={k_stuck}")
