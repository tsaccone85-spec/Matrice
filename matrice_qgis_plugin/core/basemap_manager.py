"""
Aggiunta automatica delle mappe di sfondo (satellite, CTR regionale) alla
sessione QGIS corrente, cosi' l'operatore non deve configurarle a mano ad
ogni apertura del pannello Matrice.

Il satellite usa il provider "wms" di QGIS con uri "type=xyz": e' il modo
standard per aggiungere un servizio di tile XYZ via codice (stesso
risultato di Browser -> XYZ Tiles in QGIS), non e' un vero WMS.

NOTA sul satellite Google: su richiesta esplicita, usa i tile di Google
Maps (mt0-3.google.com/vt/lyrs=s) senza passare dalla Google Maps
Platform (nessuna API key, nessuna fatturazione) -- e' la stessa pratica
diffusa in tutta la comunita' QGIS, ma NON e' un uso autorizzato dai
Termini di Servizio di Google: a differenza di Esri World Imagery (servizio
pubblico pensato per questo), Google puo' bloccare o rallentare l'accesso
senza preavviso. Se in futuro lo sfondo smette di caricarsi, e' la prima
cosa da sospettare.

Gli sfondi non vengono mai duplicati: se un layer con lo stesso nome e'
gia' nel progetto (aggiunto da questo plugin in precedenza, o anche a mano
dall'operatore), non viene ricreato.
"""

from qgis.core import QgsProject, QgsRasterLayer

SATELLITE_NAME = "Satellite (Google)"
# "mt0" e' uno dei sottodomini tile di Google (mt0-mt3, intercambiabili);
# il formato "type=xyz" di QGIS non ruota automaticamente tra piu'
# sottodomini, quindi se ne usa semplicemente uno fisso -- stesso pattern
# diffuso in tutta la comunita' QGIS per questo stesso scopo.
# {x}/{y}/{z} e i separatori = e & dei parametri nidificati dell'URL Google
# sono percent-encoded (%7Bx%7D, %3D, %26), stessa convenzione dell'URI
# Esri usato in precedenza qui.
SATELLITE_URI = (
    "type=xyz&url=https://mt0.google.com/vt/lyrs%3Ds%26x%3D%7Bx%7D%26y%3D%7By%7D%26z%3D%7Bz%7D"
    "&zmax=20&zmin=0"
)

# CTR Emilia-Romagna: da completare con l'URI esatto una volta nota la
# stringa sorgente generata da QGIS stesso (nome layer/CRS verificati a
# mano via Browser -> WMS/WMTS, non indovinati -- un nome layer sbagliato
# farebbe fallire il raster in silenzio, come successo con rilievo_geometria).
CTR_EMILIA_ROMAGNA_NAME = "CTR Emilia-Romagna"
CTR_EMILIA_ROMAGNA_URI = None


class BasemapManager:
    """Aggiunge gli sfondi una sola volta per sessione QGIS (non duplica se gia' presenti)."""

    def ensure_default_basemaps(self):
        self.ensure_satellite()
        self.ensure_ctr()

    def ensure_satellite(self):
        self._ensure_raster_layer(SATELLITE_NAME, SATELLITE_URI, "wms")

    def ensure_ctr(self):
        if CTR_EMILIA_ROMAGNA_URI is None:
            return  # non ancora configurato, vedi nota sopra
        self._ensure_raster_layer(CTR_EMILIA_ROMAGNA_NAME, CTR_EMILIA_ROMAGNA_URI, "wms")

    def _ensure_raster_layer(self, name, uri, provider):
        project = QgsProject.instance()
        if project.mapLayersByName(name):
            return  # gia' presente, non duplicare
        layer = QgsRasterLayer(uri, name, provider)
        if layer.isValid():
            project.addMapLayer(layer)
