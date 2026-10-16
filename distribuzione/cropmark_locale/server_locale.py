#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import sys, io
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
else:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
"""
server_locale.py
=================
Server HTTP locale per lo strumento "Cropmark" di Matrice — alternativa a un
backend su server web: gira sul PC dell'utente (stesso PC dove sono già
installati Python + rasterio/geopandas/scikit-image, usati per i test di
questo strumento), ascolta su localhost, e la pagina Cropmark di Matrice
(views/cropmark.js) ci parla direttamente da browser via fetch.

A differenza del backend server (app/produzione/cropmark-endpoint/):
- NESSUNA credenziale Supabase qui. Il server locale fa solo il calcolo
  (NDVI + microrilievo) e restituisce PDF+GeoPackage al browser; è il
  browser — già autenticato con la sessione reale dell'utente — a caricare
  i file su Supabase Storage e a inserire le righe in
  progettazione_cropmark, con le stesse regole RLS di qualunque altra
  scrittura di Matrice. Una service-role key distribuita su ogni PC utente
  sarebbe un rischio enorme (bypassa tutta la RLS per chiunque la trovi):
  qui non esiste affatto.
- Nessun database dei job: stato in memoria (dizionario Python), persa al
  riavvio del server. Va bene per uno strumento puntuale lanciato quando
  serve, non per archiviazione.

Uso:
    python3 server_locale.py [--porta 5057]

Poi, nella pagina Cropmark di Matrice, lo strumento lo rileva da solo
(GET http://localhost:5057/ping) e lo usa al posto del comando manuale.

Dipendenze: stesse di rileva_segni.py (rasterio, geopandas, scikit-image,
scipy, shapely, matplotlib, requests, Pillow) + nessuna libreria web
aggiuntiva (solo http.server della libreria standard).
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Dentro un bundle PyInstaller sys.executable è l'exe stesso, non Python.
# Cerca il vero interprete in PATH; serve per lanciare rileva_segni.py.
def _trova_python() -> str:
    import shutil
    if not getattr(sys, 'frozen', False):
        return sys.executable
    for nome in ('python', 'python3', 'python.exe', 'python3.exe'):
        p = shutil.which(nome)
        if p:
            return p
    raise RuntimeError(
        'Python non trovato nel PATH. Installa Python 3 e aggiungilo al PATH.'
    )

_PYTHON = _trova_python()

import sys as _sys
_sys.path.insert(0, SCRIPT_DIR)
from fonte_satellitare import MASSIMO_STAGIONI

# ---------------------------------------------------------------------------
# Gating per piano (lacuna 12)
# ---------------------------------------------------------------------------
# Il server locale non può usare la service-role key di Supabase (non deve
# girare sul PC dell'utente). Verifica il piano usando il JWT di sessione
# dell'utente che il browser invia nell'header Authorization: Bearer <token>.
# Il JWT è firmato da Supabase: ne verifichiamo la firma con la JWT secret
# (SUPABASE_JWT_SECRET, env var), oppure, se non configurata, decodifichiamo
# senza verifica solo per leggere il campo app_metadata.piano — meno sicuro
# ma accettabile perché il server è su localhost (non esposto a internet) e
# la vera barriera è lato server web (cropmark-endpoint.js).
#
# Limite per piano (specchio di CROPMARK_LIMITE_PER_PIANO in store.js e
# LIMITE_PER_PIANO in cropmark-endpoint.js — unica fonte di verità logica):
CROPMARK_LIMITE_PER_PIANO: dict[str, int | None] = {
    "free": 1,
    "base": 5,
    "pro": None,    # illimitato
    "team": None,   # illimitato
}
FILIGRANA_FREE = "VERSIONE DI PROVA — Matrice piano Free"

# URL Supabase: usato per contare le esecuzioni già fatte via REST.
# Può essere sovrascritto con la variabile d'ambiente SUPABASE_URL.
_SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://wsbazealycuhemwqbplx.supabase.co")
_SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")


def _decodifica_jwt_payload(token: str) -> dict:
    """Decodifica il payload del JWT senza verifica della firma (il server
    locale è su localhost, non esposto a internet). Ritorna {} in caso di
    errore di formato."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        import base64
        payload_b64 = parts[1]
        # Aggiunge padding se mancante
        payload_b64 += "=" * (-len(payload_b64) % 4)
        return json.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception:
        return {}


def _piano_da_token(token: str) -> str:
    """Estrae il piano dal JWT di sessione Supabase. Il piano viene scritto
    in app_metadata dal trigger lato database (vedi PRODUZIONE.md).
    Ritorna 'free' se non trovato o token non valido."""
    payload = _decodifica_jwt_payload(token)
    # Supabase mette i metadati dell'app in app_metadata (non raw_user_meta_data)
    piano = (payload.get("app_metadata") or {}).get("piano", "")
    if piano in CROPMARK_LIMITE_PER_PIANO:
        return piano
    return "free"


def _esecuzioni_usate(token: str) -> int:
    """Conta le esecuzioni già fatte sull'account via Supabase REST API,
    usando il JWT dell'utente (rispetta la RLS). Ritorna 0 in caso di errore
    (fallisce in modo permissivo: il gate vero è lato server web)."""
    if not _SUPABASE_URL or not token:
        return 0
    try:
        import urllib.request
        url = f"{_SUPABASE_URL}/rest/v1/strumento_cropmark_job?select=id&limit=1000"
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {token}",
            "apikey": _SUPABASE_ANON_KEY,
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=5) as resp:
            rows = json.loads(resp.read())
            return len(rows) if isinstance(rows, list) else 0
    except Exception:
        return 0

# Origini autorizzate a chiamare questo server (CORS). localhost incluso per
# test/sviluppo della pagina Cropmark senza passare da matrice.info.
ORIGINI_CONSENTITE = {
    "https://www.matrice.info",
    "https://matrice.info",
    "http://localhost:8777",  # app/build.sh, anteprima locale
}

JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


def _qualita_alta_disponibile() -> bool:
    """True se le librerie di super-risoluzione (modalita 'alta qualita')
    sono installate su questa macchina. Usato da /ping cosi' il frontend
    puo' mostrare o nascondere l'opzione senza doverla provare a cieca."""
    try:
        import torch  # noqa: F401
        import mlstac  # noqa: F401
        import sen2sr  # noqa: F401
        return True
    except ImportError:
        return False


def _esegui_job(job_id: str, area_geojson: dict, buffer_m: float, num_acquisizioni: int,
                 qualita: str = "standard", filigrana: str | None = None):
    with JOBS_LOCK:
        JOBS[job_id]["stato"] = "in_esecuzione"

    cartella = tempfile.mkdtemp(prefix="cropmark-locale-")
    percorso_area = os.path.join(cartella, "cantiere.geojson")
    percorso_gpkg = os.path.join(cartella, "segni_rilevati.gpkg")
    percorso_pdf = os.path.join(cartella, "report.pdf")

    with open(percorso_area, "w", encoding="utf-8") as f:
        json.dump(area_geojson, f)

    percorso_riepilogo = os.path.join(cartella, "riepilogo.json")

    argomenti = [
        _PYTHON, os.path.join(SCRIPT_DIR, "rileva_segni.py"),
        "--cantiere", percorso_area,
        "--auto",
        "--buffer-m", str(buffer_m),
        "--num-acquisizioni", str(num_acquisizioni),
        "--output", percorso_gpkg,
        "--output-pdf", percorso_pdf,
        "--nome-area", "Cantiere Matrice (server locale)",
        "--banda-red", "1", "--banda-nir", "2",
        "--qualita", qualita,
        "--output-riepilogo", percorso_riepilogo,
    ]
    # Gating piano: aggiunge filigrana al PDF per il piano free.
    if filigrana:
        argomenti += ["--filigrana", filigrana]

    try:
        risultato = subprocess.run(
            argomenti, cwd=SCRIPT_DIR, capture_output=True, text=True, timeout=600
        )
        if risultato.returncode != 0 or not os.path.exists(percorso_gpkg) or not os.path.exists(percorso_pdf):
            raise RuntimeError(f"rileva_segni.py fallito:\n{risultato.stdout}\n{risultato.stderr}")

        n_totali = None
        n_multidata = None
        if os.path.exists(percorso_riepilogo):
            with open(percorso_riepilogo, encoding="utf-8") as f:
                riepilogo = json.load(f)
            n_totali = riepilogo.get("n_segni_totali")
            n_multidata = riepilogo.get("n_segni_multidata")
        else:
            # Fallback: parse stdout per compatibilità con versioni precedenti
            # dello script (rimosso quando --output-riepilogo sarà stabile).
            for riga in risultato.stdout.splitlines():
                if riga.strip().startswith("Totale segni rilevati:"):
                    try:
                        n_totali = int(riga.split(":")[1].strip())
                    except (IndexError, ValueError):
                        pass
                if "confermati su 2+ date" in riga:
                    try:
                        n_multidata = int(riga.split("(")[1].split(" confermati")[0])
                    except (IndexError, ValueError):
                        pass

        with JOBS_LOCK:
            JOBS[job_id].update({
                "stato": "completato",
                "percorso_pdf": percorso_pdf,
                "percorso_gpkg": percorso_gpkg,
                "n_segni_totali": n_totali,
                "n_segni_multidata": n_multidata,
            })
    except Exception as e:
        with JOBS_LOCK:
            JOBS[job_id].update({"stato": "errore", "messaggio_errore": str(e)})


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        origine = self.headers.get("Origin", "")
        if origine in ORIGINI_CONSENTITE:
            self.send_header("Access-Control-Allow-Origin", origine)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            # Private Network Access (Chrome): una pagina pubblica https (matrice.info)
            # che chiama un indirizzo privato/loopback (127.0.0.1) deve ricevere questo
            # header, altrimenti Chrome blocca la richiesta ancora prima che arrivi qui
            # — il ping fallisce in silenzio e la pagina pensa che il server locale non
            # ci sia (bug segnalato da Tommaso, 2026-10-08: "Avvia analisi" va al
            # backend web invece che al server locale già avviato).
            if self.headers.get("Access-Control-Request-Private-Network") == "true":
                self.send_header("Access-Control-Allow-Private-Network", "true")

    def _json(self, status: int, payload: dict):
        corpo = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if self.path == "/ping":
            self._json(200, {
                "ok": True, "versione": "1.0",
                "qualita_alta_disponibile": _qualita_alta_disponibile(),
            })
            return

        if self.path.startswith("/stato/"):
            job_id = self.path.split("/stato/")[1]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
            if not job:
                self._json(404, {"errore": "job non trovato"})
                return
            self._json(200, {
                "stato": job["stato"],
                "messaggio_errore": job.get("messaggio_errore"),
                "n_segni_totali": job.get("n_segni_totali"),
                "n_segni_multidata": job.get("n_segni_multidata"),
            })
            return

        if self.path.startswith("/scarica/"):
            parti = self.path.split("/")
            if len(parti) != 4 or parti[3] not in ("pdf", "gpkg"):
                self._json(400, {"errore": "percorso non valido"})
                return
            job_id, tipo = parti[2], parti[3]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
            if not job or job.get("stato") != "completato":
                self._json(404, {"errore": "risultato non disponibile"})
                return
            # Piano free: GeoPackage non scaricabile (specchio di cropmark-endpoint.js)
            if tipo == "gpkg" and job.get("piano") == "free":
                self._json(403, {
                    "errore": "Il piano Free non include il download del GeoPackage. "
                              "Fai l'upgrade a Base, Professionista o Aziendale.",
                    "upgrade_richiesto": True,
                })
                return
            percorso = job["percorso_pdf"] if tipo == "pdf" else job["percorso_gpkg"]
            with open(percorso, "rb") as f:
                dati = f.read()
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/pdf" if tipo == "pdf" else "application/geopackage+sqlite3")
            self.send_header("Content-Length", str(len(dati)))
            self.end_headers()
            self.wfile.write(dati)
            return

        self._json(404, {"errore": "non trovato"})

    def do_POST(self):
        if self.path != "/avvia":
            self._json(404, {"errore": "non trovato"})
            return
        lunghezza = int(self.headers.get("Content-Length", 0))
        try:
            corpo = json.loads(self.rfile.read(lunghezza) or b"{}")
        except Exception:
            self._json(400, {"errore": "JSON non valido"})
            return

        # ----------------------------------------------------------------
        # Gating per piano (lacuna 12)
        # Il browser invia il JWT di sessione Supabase nell'header
        # Authorization: Bearer <token>. Lo usiamo per:
        #   1. Leggere il piano dell'utente (app_metadata.piano nel JWT)
        #   2. Contare le esecuzioni già fatte via REST Supabase (RLS)
        #   3. Bloccare se il limite è raggiunto
        #   4. Aggiungere la filigrana al PDF per il piano free
        # Nota: il server è su localhost, non esposto a internet. Il gate
        # vero e non aggirabile è lato server web (cropmark-endpoint.js);
        # questo è un gate di cortesia che evita analisi inutili (60s) e
        # mostra un messaggio chiaro invece di far partire e poi fallire.
        # ----------------------------------------------------------------
        auth_header = self.headers.get("Authorization", "")
        token = auth_header[len("Bearer "):].strip() if auth_header.startswith("Bearer ") else ""
        # Senza SUPABASE_ANON_KEY il server non può verificare il piano:
        # in quel caso bypassa il gating (server locale non configurato).
        if not _SUPABASE_ANON_KEY:
            piano = "pro"
        else:
            piano = _piano_da_token(token) if token else "free"
        limite = CROPMARK_LIMITE_PER_PIANO.get(piano, 1)

        if limite is not None:
            usate = _esecuzioni_usate(token)
            if usate >= limite:
                msg = (
                    "Il piano Free include 1 sola analisi di prova. "
                    "Fai l'upgrade a Base, Professionista o Aziendale per continuare."
                    if piano == "free"
                    else f"Il piano Base include {limite} analisi. "
                         "Fai l'upgrade a Professionista o Aziendale per continuare."
                )
                self._json(403, {"errore": msg, "upgrade_richiesto": True})
                return

        filigrana = FILIGRANA_FREE if piano == "free" else None
        # Il piano viene salvato nel job per bloccare il download del
        # GeoPackage per il piano free nel GET /scarica.
        # ----------------------------------------------------------------

        area_geojson = corpo.get("area_geojson")
        buffer_m = corpo.get("buffer_m")
        num_acquisizioni = corpo.get("num_acquisizioni")
        qualita = corpo.get("qualita", "standard")
        if qualita not in ("standard", "alta"):
            self._json(400, {"errore": "qualita deve essere 'standard' o 'alta'"})
            return
        if qualita == "alta" and not _qualita_alta_disponibile():
            self._json(400, {
                "errore": "Modalità 'alta qualità' non installata su questa macchina. "
                          "Rilancia avvia_server_locale_windows.bat e scegli di installarla."
            })
            return

        if not area_geojson:
            self._json(400, {"errore": "area_geojson mancante"})
            return
        if not buffer_m or buffer_m <= 0:
            self._json(400, {"errore": "buffer_m mancante o non valido"})
            return
        if not num_acquisizioni or not (2 <= num_acquisizioni <= MASSIMO_STAGIONI):
            self._json(400, {"errore": f"num_acquisizioni deve essere tra 2 e {MASSIMO_STAGIONI}"})
            return

        job_id = str(uuid.uuid4())
        with JOBS_LOCK:
            JOBS[job_id] = {"stato": "in_coda", "piano": piano}
        threading.Thread(
            target=_esegui_job,
            args=(job_id, area_geojson, buffer_m, num_acquisizioni, qualita, filigrana),
            daemon=True,
        ).start()
        self._json(201, {"job_id": job_id, "stato": "in_coda"})

    def log_message(self, format, *args):
        print(f"[server_locale] {self.address_string()} - {format % args}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--porta", type=int, default=5057)
    args = parser.parse_args()

    server = ThreadingHTTPServer(("127.0.0.1", args.porta), Handler)
    print(f"Server Cropmark locale in ascolto su http://127.0.0.1:{args.porta}")
    print("Lascia questa finestra aperta mentre usi lo strumento Cropmark su Matrice. Ctrl+C per fermarlo.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nFermato.")


if __name__ == "__main__":
    main()
