"""
Aggiunta automatica delle mappe di sfondo (satellite, Catasto, CTR
regionale) alla sessione QGIS corrente, cosi' l'operatore non deve
configurarle a mano ad ogni apertura del pannello Matrice.

Il satellite usa il provider "wms" di QGIS con uri "type=xyz": e' il modo
standard per aggiungere un servizio di tile XYZ via codice (stesso
risultato di Browser -> XYZ Tiles in QGIS), non e' un vero WMS.

NOTA sul satellite Google: su richiesta esplicita, usa i tile di Google
Maps (mt0.google.com/vt/lyrs=s) senza passare dalla Google Maps Platform
(nessuna API key, nessuna fatturazione) -- e' la stessa pratica diffusa in
tutta la comunita' QGIS, ma NON e' un uso autorizzato dai Termini di
Servizio di Google: Google puo' bloccare o rallentare l'accesso senza
preavviso. Se in futuro lo sfondo smette di caricarsi, e' la prima cosa da
sospettare.

Catasto e CTR regionale sono invece veri servizi WMS di enti pubblici
(Agenzia delle Entrate, Regioni). Gli URL qui sotto vengono dalla ricerca
sui portali cartografici ufficiali (ottobre 2026): l'ambiente in cui e'
stato scritto questo codice non puo' raggiungere direttamente i domini
regionali/.gov.it per verificarli con una GetCapabilities (stesso limite
incontrato con l'Emilia-Romagna in una sessione precedente), quindi NON
sono garantiti uno per uno. Per questo il layer giusto (nome + sistema di
riferimento) viene scoperto automaticamente interrogando il servizio *da
dentro QGIS* (che ha un accesso reale a Internet) ogni volta che serve: se
il servizio non risponde, risponde con un formato inatteso, o non espone
nessun layer riconoscibile, la funzione ritorna senza fare nulla -- mai un
errore bloccante solo per uno sfondo opzionale, resta semplicemente il
satellite. Mancano le Regioni per cui la ricerca non ha restituito un
endpoint WMS diretto (solo un portale/visualizzatore web): se un operatore
lavora li', vale la stessa cosa.

Gli sfondi non vengono mai duplicati: se un layer con lo stesso nome e'
gia' nel progetto (aggiunto da questo plugin in precedenza, o anche a mano
dall'operatore), non viene ricreato.
"""

import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

from qgis.core import QgsProject, QgsRasterLayer

SATELLITE_NAME = "Satellite (Google)"
# "mt0" e' uno dei sottodomini tile di Google (mt0-mt3, intercambiabili);
# il formato "type=xyz" di QGIS non ruota automaticamente tra piu'
# sottodomini, quindi se ne usa semplicemente uno fisso -- stesso pattern
# diffuso in tutta la comunita' QGIS per questo stesso scopo.
# {x}/{y}/{z} e i separatori = e & dei parametri nidificati dell'URL Google
# sono percent-encoded (%7Bx%7D, %3D, %26).
SATELLITE_URI = (
    "type=xyz&url=https://mt0.google.com/vt/lyrs%3Ds%26x%3D%7Bx%7D%26y%3D%7By%7D%26z%3D%7Bz%7D"
    "&zmax=20&zmin=0"
)

CATASTO_NAME = "Catasto (Agenzia delle Entrate)"
CATASTO_WMS_BASE_URL = "https://wms.cartografia.agenziaentrate.gov.it/inspire/wms/ows01.php"
CATASTO_PAROLA_CHIAVE = "cadastral"  # layer INSPIRE standard: CP.CadastralParcel

# Endpoint WMS di base per Regione (solo l'URL del servizio, il layer e il
# CRS si scoprono a runtime -- vedi nota del modulo). Chiave normalizzata
# in minuscolo, confrontata con cantiere.regione dopo strip()+lower().
# Mancano le Regioni/Province per cui la ricerca non ha dato un endpoint
# WMS diretto (solo pagine portale): Valle d'Aosta, Liguria, Veneto,
# Toscana, Marche, Umbria, Abruzzo, Molise, Basilicata, Calabria, Sicilia,
# Sardegna. Trentino-Alto Adige copre qui solo la Provincia di Bolzano
# (quella di Trento non ha restituito un endpoint diretto).
CTR_WMS_BASE_URL_PER_REGIONE = {
    "piemonte": "https://geomap.reteunitaria.piemonte.it/mapproxy/service",
    "lombardia": "http://www.cartografia.servizirl.it/arcgis/services/wms/ctr_wms/MapServer/WMSServer",
    "trentino-alto adige": "http://sdi.provincia.bz.it/geoserver/ows",
    "friuli-venezia giulia": "http://serviziogc.regione.fvg.it:8080/geoserver/wms",
    "emilia-romagna": "http://servizigis.regione.emilia-romagna.it/wms/ctr25",
    "lazio": "http://cartografia.regione.lazio.it/ecwp/ecw_wms.dll",
    "campania": "https://geomaps.regione.campania.it/federated/services/DG5009/CTR2020_v2/MapServer/WMSServer",
    "puglia": "http://cartografia.sit.puglia.it/wmsconnector/com.esri.wms.Esrimap/WMS_CTR",
}
CTR_PAROLA_CHIAVE = "ctr"

TIMEOUT_SECONDS = 6


def _tag_locale(elemento):
    """Nome del tag XML senza namespace (variabile da server a server/versione WMS)."""
    return elemento.tag.rsplit("}", 1)[-1]


def _scopri_layer_wms(url_base, parola_chiave, timeout=TIMEOUT_SECONDS):
    """
    Interroga il servizio con GetCapabilities e ritorna (nome_layer, crs)
    del primo layer il cui nome/titolo contiene `parola_chiave`, o del
    primo layer disponibile se nessuno corrisponde. None se il servizio
    non risponde, non e' un WMS valido, o non espone nessun layer -- mai
    un'eccezione, il chiamante decide di mostrare il satellite al suo posto.
    """
    separatore = "&" if "?" in url_base else "?"
    url_capabilities = f"{url_base}{separatore}SERVICE=WMS&REQUEST=GetCapabilities"
    try:
        with urllib.request.urlopen(url_capabilities, timeout=timeout) as response:
            contenuto = response.read()
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None

    try:
        radice = ET.fromstring(contenuto)
    except ET.ParseError:
        return None

    layer_scelto = None
    primo_layer = None
    for elemento in radice.iter():
        if _tag_locale(elemento) != "Layer":
            continue
        nome_elem = next((f for f in elemento if _tag_locale(f) == "Name"), None)
        if nome_elem is None or not nome_elem.text:
            continue
        if primo_layer is None:
            primo_layer = elemento
        titolo_elem = next((f for f in elemento if _tag_locale(f) == "Title"), None)
        testo_confronto = f"{nome_elem.text} {titolo_elem.text if titolo_elem is not None else ''}".lower()
        if parola_chiave in testo_confronto and layer_scelto is None:
            layer_scelto = elemento

    elemento = layer_scelto or primo_layer
    if elemento is None:
        return None

    nome = next(f.text for f in elemento if _tag_locale(f) == "Name")
    crs_disponibili = [f.text for f in elemento if _tag_locale(f) in ("CRS", "SRS") and f.text]
    crs_scelto = (
        next((c for c in crs_disponibili if c in ("EPSG:4326", "CRS:84")), None)
        or next((c for c in crs_disponibili if c == "EPSG:3857"), None)
        or (crs_disponibili[0] if crs_disponibili else "EPSG:4326")
    )
    return nome, crs_scelto


class BasemapManager:
    """Aggiunge gli sfondi una sola volta per sessione QGIS (non duplica se gia' presenti)."""

    def ensure_default_basemaps(self):
        """Sfondi nazionali, indipendenti dal cantiere: chiamati una volta dopo il login."""
        self.ensure_satellite()
        self.ensure_catasto()

    def ensure_satellite(self):
        self._ensure_raster_layer(SATELLITE_NAME, SATELLITE_URI, "wms")

    def ensure_catasto(self):
        self._ensure_wms_scoperto(CATASTO_NAME, CATASTO_WMS_BASE_URL, CATASTO_PAROLA_CHIAVE)

    def ensure_ctr_per_regione(self, regione):
        """
        Sfondo CTR della Regione del cantiere appena aperto (sezione 4,
        passo 1): chiamato da handle_open_tavola con cantiere.regione.
        Se la Regione non e' in elenco, o il servizio non risponde/non ha
        un layer riconoscibile, non fa nulla -- resta il satellite gia'
        garantito da ensure_default_basemaps.
        """
        if not regione:
            return
        url_base = CTR_WMS_BASE_URL_PER_REGIONE.get(regione.strip().lower())
        if not url_base:
            return
        self._ensure_wms_scoperto(f"CTR - {regione}", url_base, CTR_PAROLA_CHIAVE)

    def _ensure_wms_scoperto(self, nome, url_base, parola_chiave):
        project = QgsProject.instance()
        if project.mapLayersByName(nome):
            return  # gia' presente, non duplicare (ne' re-interrogare il servizio)
        scoperta = _scopri_layer_wms(url_base, parola_chiave)
        if scoperta is None:
            return
        nome_layer, crs = scoperta
        uri = f"layers={nome_layer}&styles=&format=image/png&crs={crs}&url={url_base}"
        self._ensure_raster_layer(nome, uri, "wms")

    def _ensure_raster_layer(self, name, uri, provider):
        project = QgsProject.instance()
        if project.mapLayersByName(name):
            return  # gia' presente, non duplicare
        layer = QgsRasterLayer(uri, name, provider)
        if layer.isValid():
            project.addMapLayer(layer)
