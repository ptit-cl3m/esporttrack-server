"""
Petit serveur "relais" pour TrackZone.

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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# La clé est lue depuis une variable d'environnement PANDASCORE_TOKEN.
# - En local : le fichier demarrer.bat la définit avant de lancer ce script.
# - En ligne (Render) : elle se règle dans le dashboard Render (onglet "Environment"),
#   jamais écrite en clair ici, pour que la clé ne se retrouve pas sur GitHub.
PANDASCORE_TOKEN = os.environ.get("PANDASCORE_TOKEN", "")

# Clé Cito API (source secondaire, utilisée uniquement pour Fortnite pour l'instant).
# Même principe que PANDASCORE_TOKEN : réglée dans Render, jamais écrite ici en clair.
CITO_API_KEY = os.environ.get("CITO_API_KEY", "")

# Render impose son propre port via la variable d'environnement PORT.
# En local, on retombe sur 5050 comme avant.
PORT = int(os.environ.get("PORT", 5050))
CACHE_SECONDS = 120  # on ne rappelle pas PandaScore à chaque clic, on garde les résultats 2 minutes
_cache = {"data": None, "timestamp": 0}

# Cache à part et beaucoup plus long pour Cito : leur offre gratuite est limitée à
# 500 appels PAR MOIS (pas par jour), donc on espace fort les rappels (2h) pour ne
# jamais s'approcher de la limite même si l'appli est utilisée toute la journée.
CITO_CACHE_SECONDS = 2 * 60 * 60
_cito_cache = {"data": None, "timestamp": 0}


# Slugs PandaScore des jeux suivis par l'appli (doit rester synchronisé avec le tableau
# GAMES dans index.html). Utilisés pour aller chercher les matchs jeu par jeu plutôt que
# tous en vrac : sinon les jeux à fort volume (CS2, LoL, Valorant) remplissaient toute la
# fenêtre de pages récupérées et poussaient les jeux plus rares (Rocket League, MLBB...)
# hors de portée, qui semblaient alors "vides" alors qu'ils avaient bien des matchs.
GAME_SLUGS = [
    "league-of-legends", "cs-go", "valorant", "cod-mw",
    "dota-2", "ow", "teamfight-tactics", "rocket-league", "mlbb",
]


def fetch_matches_for(endpoint, max_pages, videogame_slug=None):
    """Va chercher plusieurs pages de matchs pour un statut donné ("upcoming" ou "running")
    chez PandaScore, éventuellement filtrées sur un seul jeu via son slug."""
    all_matches = []
    for page in range(1, max_pages + 1):
        url = (
            f"https://api.pandascore.co/matches/{endpoint}"
            f"?token={PANDASCORE_TOKEN}&per_page=100&page={page}&sort=begin_at"
        )
        if videogame_slug:
            url += f"&filter[videogame]={videogame_slug}"
        with urllib.request.urlopen(url, timeout=15) as response:
            page_data = json.loads(response.read().decode("utf-8"))
        if not page_data:
            break
        all_matches.extend(page_data)
    return all_matches


def fetch_upcoming_matches():
    """Récupère les matchs à venir ET les matchs actuellement en direct.

    Deux stratégies combinées, car le filtre par jeu de PandaScore (filter[videogame])
    ne répond pas de façon fiable pour tous les jeux (marche pour mlbb/ow/dota-2/valorant/
    cs-go/lol, mais renvoie vide pour cod-mw/teamfight-tactics/rocket-league sans erreur) :
    1. Un appel filtré par jeu pour chaque jeu suivi (rapide, garantit les jeux où ça marche).
    2. Un appel global plus profond (10 pages = jusqu'à 1000 matchs tous jeux confondus) en
       filet de sécurité, pour les jeux dont le filtre échoue silencieusement.
    Les deux résultats sont fusionnés et dédoublonnés par id de match plus loin dans le code."""
    running = fetch_matches_for("running", max_pages=2)  # peu de matchs en direct à un instant T, tous jeux confondus

    per_game = []
    for slug in GAME_SLUGS:
        per_game.extend(fetch_matches_for("upcoming", max_pages=1, videogame_slug=slug))

    global_fallback = fetch_matches_for("upcoming", max_pages=10)  # filet de sécurité, jusqu'à 1000 matchs

    return running + per_game + global_fallback


def fetch_fortnite_live_tournaments():
    """Récupère les tournois Fortnite actuellement en direct chez Cito.
    Contrairement à PandaScore, Fortnite compétitif n'a pas de "match Équipe A vs
    Équipe B" — ce sont des tournois battle royale avec plein de joueurs/duos classés
    par points sur une fenêtre de temps. On ne remonte que les tournois en direct
    (pas d'historique ni de détail des participants, pour économiser le quota)."""
    url = "https://api.citoapi.com/api/v1/fortnite/tournaments/live?includeLeaderboard=false"
    req = urllib.request.Request(url, headers={"x-api-key": CITO_API_KEY})
    with urllib.request.urlopen(req, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))
    raw = payload.get("data", [])

    # On uniformise les champs ici plutôt que côté appli : les noms exacts renvoyés
    # par Cito peuvent varier légèrement selon l'endpoint, mieux vaut absorber ça
    # une seule fois côté serveur plutôt que dans le JS.
    normalized = []
    for t in raw:
        normalized.append({
            "id": t.get("id") or t.get("tournamentId") or t.get("eventWindowId") or t.get("eventId"),
            "name": t.get("name") or "Tournoi Fortnite",
            "regions": t.get("regions") or [],
            "startTime": t.get("startTime"),
            "endTime": t.get("endTime"),
            "isLive": t.get("isLive", True),
        })
    return normalized


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
        elif self.path.startswith("/api/fortnite-tournaments"):
            if not CITO_API_KEY:
                self._send_json({"error": "CITO_API_KEY manquant (variable d'environnement non définie)."}, status=500)
                return
            now = time.time()
            if _cito_cache["data"] is None or (now - _cito_cache["timestamp"]) > CITO_CACHE_SECONDS:
                try:
                    _cito_cache["data"] = fetch_fortnite_live_tournaments()
                    _cito_cache["timestamp"] = now
                except Exception as e:
                    self._send_json({"error": str(e)}, status=500)
                    return
            self._send_json(_cito_cache["data"])
        else:
            self._send_json({"error": "Route inconnue"}, status=404)

    def log_message(self, format, *args):
        pass  # évite d'encombrer la console avec chaque requête


if __name__ == "__main__":
    print(f"Serveur relais démarré sur le port {PORT} : /api/matches")
    print("Laisse cette fenêtre ouverte pendant que tu utilises l'application (en local).")
    # 0.0.0.0 : nécessaire pour que Render puisse router le trafic vers ce serveur.
    # Fonctionne aussi très bien en local.
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
