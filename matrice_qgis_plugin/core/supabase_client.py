"""
Client REST minimale verso Supabase (GoTrue per l'autenticazione,
PostgREST per le letture filtrate per utente -- sezione 1/2 della
specifica).

Questo e' il "servizio intermedio" della decisione presa in sezione 1:
non un microservizio scritto per il plugin, ma il livello REST che
Supabase mette gia' a disposizione (lo stesso usato dall'app web di
Matrice), con autenticazione e permessi (Row Level Security) applicati
dal database ad ogni richiesta -- non dal plugin.

Nessuna dipendenza esterna oltre alla libreria standard: `urllib` e'
sempre disponibile nell'ambiente Python di QGIS, a differenza di
`requests`, che andrebbe installato a parte come psycopg2-binary.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_TIMEOUT_SECONDS = 10


class SupabaseAuthError(Exception):
    """Credenziali non valide (risposta 400/401 di Supabase Auth)."""


class SupabaseRequestError(Exception):
    """Richiesta REST rifiutata da Supabase (es. permessi insufficienti)."""


class SupabaseRestClient:
    """Wrapper sottile su GoTrue (/auth/v1) e PostgREST (/rest/v1)."""

    def __init__(self, base_url, anon_key, timeout=DEFAULT_TIMEOUT_SECONDS):
        self.base_url = base_url.rstrip("/")
        self.anon_key = anon_key
        self.timeout = timeout

    # ------------------------------------------------------------------
    # GoTrue -- autenticazione (sezione 2)
    # ------------------------------------------------------------------
    def sign_in_with_password(self, email, password):
        """
        Login utente/password, stesso meccanismo dell'app web di Matrice.
        Ritorna il dizionario di risposta di Supabase Auth:
        {"access_token", "refresh_token", "expires_in", "user": {"id", "email", ...}}.
        """
        url = f"{self.base_url}/auth/v1/token?grant_type=password"
        try:
            return self._request("POST", url, body={"email": email, "password": password})
        except _HttpError as exc:
            if exc.status in (400, 401):
                raise SupabaseAuthError("Credenziali non valide.") from exc
            raise

    def refresh_session(self, refresh_token):
        """Rinnovo silenzioso del token (sezione 2), senza richiedere un nuovo login."""
        url = f"{self.base_url}/auth/v1/token?grant_type=refresh_token"
        try:
            return self._request("POST", url, body={"refresh_token": refresh_token})
        except _HttpError as exc:
            if exc.status in (400, 401):
                raise SupabaseAuthError("Sessione scaduta, e' necessario un nuovo accesso.") from exc
            raise

    # ------------------------------------------------------------------
    # PostgREST -- letture filtrate per utente tramite Row Level Security
    # ------------------------------------------------------------------
    def select(self, table, access_token, params=None):
        """
        GET su /rest/v1/<table> con il token dell'utente loggato: le righe
        restituite sono gia' filtrate lato database dalle policy di Row
        Level Security (es. has_cantiere_access), non dal plugin.
        """
        query = urllib.parse.urlencode(params or {}, safe=",.():*")
        url = f"{self.base_url}/rest/v1/{table}"
        if query:
            url += f"?{query}"
        try:
            return self._request("GET", url, access_token=access_token)
        except _HttpError as exc:
            raise SupabaseRequestError(self._messaggio_semplice(exc)) from exc

    def insert(self, table, fields, access_token):
        """
        POST su /rest/v1/<table> con il token dell'utente loggato: come
        select()/update(), e' la Row Level Security del database a decidere
        se l'inserimento e' permesso, non il plugin. Ritorna la riga creata
        (es. serve l'id del cantiere appena creato, vedi
        matrice_plugin._handle_nuovo_cantiere).
        """
        url = f"{self.base_url}/rest/v1/{table}"
        try:
            rows = self._request(
                "POST", url, body=fields, access_token=access_token, prefer="return=representation"
            )
        except _HttpError as exc:
            raise SupabaseRequestError(self._messaggio_semplice(exc)) from exc
        return rows[0] if rows else None

    # ------------------------------------------------------------------
    # Storage -- bucket privati (foto-scavo, documenti, geopackage): le
    # colonne foto_*_url/file_url nelle tabelle NON sono URL completi, sono
    # percorsi relativi dentro il bucket (verificato direttamente su
    # Supabase, sezione 3 -- vedi matrice_api.scarica_foto). Essendo bucket
    # privati, un link diretto non funziona: serve l'endpoint autenticato
    # con il token dell'utente, stessa Row Level Security delle tabelle
    # (policy su storage.objects basata su has_cantiere_access).
    # ------------------------------------------------------------------
    def download_object(self, bucket, path, access_token):
        """
        Scarica un file da Storage. Ritorna (dati, None) se riuscito, o
        (None, messaggio_errore) altrimenti -- il messaggio e' diagnostico
        (per capire PERCHE' una foto non si vede: permessi, percorso
        sbagliato, rete), non va mostrato cosi' com'e' in un contesto non
        tecnico, ma e' utile in fase di verifica.
        """
        url = f"{self.base_url}/storage/v1/object/{bucket}/{path}"
        headers = {"apikey": self.anon_key, "Authorization": f"Bearer {access_token or self.anon_key}"}
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.read(), None
        except urllib.error.HTTPError as exc:
            corpo = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            return None, f"HTTP {exc.code}: {corpo[:200]}"
        except urllib.error.URLError as exc:
            return None, f"Rete: {exc.reason}"

    def update(self, table, id_column, record_id, fields, access_token):
        """
        PATCH su /rest/v1/<table>?<id_column>=eq.<record_id> con il token
        dell'utente loggato: come select(), e' la Row Level Security del
        database (has_cantiere_access_editor) a decidere se la scrittura e'
        permessa, non il plugin. Ritorna la riga aggiornata.
        """
        url = f"{self.base_url}/rest/v1/{table}?{id_column}=eq.{record_id}"
        try:
            rows = self._request(
                "PATCH", url, body=fields, access_token=access_token, prefer="return=representation"
            )
        except _HttpError as exc:
            raise SupabaseRequestError(self._messaggio_semplice(exc)) from exc
        return rows[0] if rows else None

    # ------------------------------------------------------------------
    def _request(self, method, url, body=None, access_token=None, prefer=None):
        headers = {
            "apikey": self.anon_key,
            "Authorization": f"Bearer {access_token or self.anon_key}",
            "Content-Type": "application/json",
        }
        if prefer:
            headers["Prefer"] = prefer
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            message = raw.decode("utf-8", errors="replace") if raw else str(exc)
            raise _HttpError(exc.code, message) from exc
        except urllib.error.URLError as exc:
            raise ConnectionError(f"Impossibile raggiungere Matrice: {exc.reason}") from exc

    def _messaggio_semplice(self, exc):
        """Mai un errore tecnico grezzo mostrato all'operatore (principio generale, sezione 2/8)."""
        if exc.status in (401, 403):
            return "Non si hanno i permessi per vedere questi dati su Matrice."
        return "Impossibile leggere i dati da Matrice in questo momento. Riprovare."


class _HttpError(Exception):
    """Errore interno: risposta HTTP non-2xx, con codice e corpo del messaggio."""

    def __init__(self, status, message):
        self.status = status
        self.message = message
        super().__init__(message)
