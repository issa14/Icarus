"""
Script de diagnostic isolé — teste la connexion Binance Futures Demo
en dehors du bot, avec logs verbeux pour voir l'URL EXACTE appelée.

Usage:
    python3 diag_binance_demo.py TA_CLE_API TON_SECRET
"""
import sys
import ccxt

if len(sys.argv) != 3:
    print("Usage: python3 diag_binance_demo.py <api_key> <api_secret>")
    sys.exit(1)

api_key, api_secret = sys.argv[1], sys.argv[2]

exchange = ccxt.binance({
    "apiKey": api_key,
    "secret": api_secret,
    "enableRateLimit": True,
    "options": {"defaultType": "future"},
    "verbose": True,  # <-- affiche l'URL exacte de chaque requête
})

# Même logique de substitution que le bot
api_urls = exchange.urls["api"]
for key, url in list(api_urls.items()):
    if "fapi.binance.com" in url:
        api_urls[key] = url.replace("fapi.binance.com", "demo-fapi.binance.com")
    elif "dapi.binance.com" in url:
        api_urls[key] = url.replace("dapi.binance.com", "demo-dapi.binance.com")

print("\n--- URLs après substitution ---")
for k, v in exchange.urls["api"].items():
    if "fapi" in k or "dapi" in k:
        print(f"  {k}: {v}")

print("\n--- Test 1: endpoint PUBLIC (pas besoin de clé) ---")
try:
    server_time = exchange.fetch_time()
    print(f"OK — server time: {server_time}")
except Exception as e:
    print(f"ÉCHEC endpoint public: {e}")
    print("=> Le problème est réseau/DNS, pas les clés. Vérifie que demo-fapi.binance.com est joignable.")
    sys.exit(1)

print("\n--- Test 2: endpoint PRIVÉ (fetch_balance, nécessite les clés) ---")
try:
    balance = exchange.fetch_balance(params={"type": "future"})
    print("OK — clés valides sur demo-fapi.binance.com !")
    print("USDT balance:", balance.get("USDT"))
except Exception as e:
    print(f"ÉCHEC endpoint privé: {e}")
    print("=> Regarde l'URL affichée juste au-dessus (verbose) pour voir où la requête a VRAIMENT été envoyée.")
