"""
Petit serveur "relais" pour EsportTrack.

Pourquoi ce fichier existe : PandaScore interdit qu'une page web appelle son API
directement depuis le navigateur (protection CORS). Ce serveur tourne sur ton
ordinateur, va chercher les données à ta place avec ta clé, et les repasse à
l'application. Ta clé ne quitte jamais ce fichier.

Comment l'utiliser :
1. Assure-toi d'avoir Python installé (tape "python --version" dans un terminal).
2. Lance ce fichier : python proxy_server.py
3. Laisse la fenêtre ouverte, puis ouvre index.html dans ton navigateur.
"""

import json
import os
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

# La clé est lue depuis une variable d'environnement PANDASCORE_TOKEN.
# - En local : le fichier demarrer.bat la définit avant de lancer ce script.
# - En ligne (Render) : elle se règle dans le dashboard Render (onglet "Environment"),
#   jamais écrite en clair ici, pour que la clé ne se retrouve pas sur GitHub.
PANDASCORE_TOKEN = os.environ.get("PANDASCORE_TOKEN", "")

# Render impose son propre port via la variable d'environnement PORT.
# En local, on retombe sur 5050 comme avant.
PORT = int(os.environ.get("PORT", 5050))
CACHE_SECONDS = 120  # on ne rappelle pas PandaScore à chaque clic, on garde les résultats 2 minutes
_cache = {"data": None, "timestamp": 0}


def fetch_upcoming_matches():
    """Va chercher plusieurs pages de matchs à venir (tous jeux confondus) chez PandaScore."""
    all_matches = []
    for page in range(1, 5):  # 4 pages x 100 = jusqu'à 400 matchs à venir
        url = (
            "https://api.pandascore.co/matches/upcoming"
            f"?token={PANDASCORE_TOKEN}&per_page=100&page={page}&sort=begin_at"
        )
        with urllib.request.urlopen(url, timeout=15) as response:
            page_data = json.loads(response.read().decode("utf-8"))
        if not page_data:
            break
        all_matches.extend(page_data)
    return all_matches


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.end_headers()

    def do_GET(self):
        if self.path.startswith("/api/matches"):
            if not PANDASCORE_TOKEN:
                self._send_json({"error": "PANDASCORE_TOKEN manquant (variable d'environnement non définie)."}, status=500)
                return
            now = time.time()
            if _cache["data"] is None or (now - _cache["timestamp"]) > CACHE_SECONDS:
                try:
                    _cache["data"] = fetch_upcoming_matches()
                    _cache["timestamp"] = now
                except Exception as e:
                    self._send_json({"error": str(e)}, status=500)
                    return
            self._send_json(_cache["data"])
        else:
            self._send_json({"error": "Route inconnue"}, status=404)

    def log_message(self, format, *args):
        pass  # évite d'encombrer la console avec chaque requête


if __name__ == "__main__":
    print(f"Serveur relais démarré sur le port {PORT} : /api/matches")
    print("Laisse cette fenêtre ouverte pendant que tu utilises l'application (en local).")
    # 0.0.0.0 : nécessaire pour que Render puisse router le trafic vers ce serveur.
    # Fonctionne aussi très bien en local.
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
