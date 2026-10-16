"""
Sincronizza le credenziali OAuth Copernicus Data Space (lette dal registro
Windows HKCU\\Environment, impostate una tantum dai programmi esterni
"carte-locale"/"cropmark-locale", vedi i loro .bat di avvio) su Matrice,
cosi' l'operatore le inserisce una sola volta invece di doverle ripetere
in ogni strumento.

Scrittura "best effort", mai bloccante per il login (sezione 2): se le
variabili non ci sono (strumenti esterni mai configurati, o un sistema non
Windows), o se la scrittura su Matrice fallisce per qualunque motivo
(tabella non ancora creata, permessi), questo modulo non fa nulla e non
solleva mai un'eccezione verso il chiamante.

ATTENZIONE -- richiede una tabella dedicata su Supabase, NON presente in
questo repository (va creata con una migrazione sul progetto Matrice, da
chi gestisce il database):

    create table utente_credenziali_copernicus (
        utente_id uuid primary key references utente(id) on delete cascade,
        copernicus_client_id text,
        copernicus_client_secret text,
        aggiornato_il timestamptz not null default now()
    );
    alter table utente_credenziali_copernicus enable row level security;
    create policy "solo il proprietario" on utente_credenziali_copernicus
        for all using (utente_id = auth.uid()) with check (utente_id = auth.uid());

Il client_secret resta comunque un segreto OAuth: la policy sopra è
l'unica cosa che impedisce a un altro utente autenticato di leggerlo
tramite PostgREST -- senza quella policy la tabella sarebbe leggibile da
chiunque abbia un token valido, qualunque esso sia.
"""

import sys


def _leggi_credenziali_registro():
    """
    (COPERNICUS_CLIENT_ID, COPERNICUS_CLIENT_SECRET) da HKCU\\Environment,
    o (None, None) se non impostate o se non si e' su Windows -- le stesse
    variabili lette dai .bat di carte-locale/cropmark-locale, mai scritte
    da questo plugin.
    """
    if sys.platform != "win32":
        return None, None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as chiave:
            def _leggi(nome):
                try:
                    valore, _ = winreg.QueryValueEx(chiave, nome)
                    return valore.strip() or None
                except FileNotFoundError:
                    return None
            return _leggi("COPERNICUS_CLIENT_ID"), _leggi("COPERNICUS_CLIENT_SECRET")
    except Exception:
        return None, None


def sincronizza_su_matrice(session):
    """
    Da richiamare dopo un login riuscito (matrice_plugin._dopo_login_riuscito):
    se l'operatore ha gia' configurato Copernicus per carte-locale/
    cropmark-locale, le stesse credenziali vengono salvate anche su Matrice
    (tabella dedicata, sola lettura/scrittura per l'utente stesso via RLS
    -- vedi il commento in testa al file), cosi' da inserirle una volta
    sola invece che per ogni strumento esterno.
    """
    client_id, client_secret = _leggi_credenziali_registro()
    if not client_id or not client_secret:
        return
    try:
        session.rest_client().insert(
            "utente_credenziali_copernicus",
            {
                "utente_id": session.user_id,
                "copernicus_client_id": client_id,
                "copernicus_client_secret": client_secret,
            },
            session.access_token,
            prefer="resolution=merge-duplicates,return=minimal",
        )
    except Exception:
        # Mai bloccante: la tabella potrebbe non esistere ancora su questo
        # ambiente, o l'operatore potrebbe non avere ancora i permessi --
        # in ogni caso il login prosegue comunque.
        pass
