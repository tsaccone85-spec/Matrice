"""
Aggiunta automatica delle mappe di sfondo (satellite, CTR regionale) alla
sessione QGIS corrente, cosi' l'operatore non deve configurarle a mano ad
ogni apertura del pannello Matrice.

Il satellite usa il provider "wms" di QGIS con uri "type=xyz": e' il modo
standard per aggiungere un servizio di tile XYZ via codice (stesso
risultato di Browser -> XYZ Tiles in QGIS), non e' un vero WMS.

Gli sfondi non vengono mai duplicati: se un layer con lo stesso nome e'
gia' nel progetto (aggiunto da questo plugin in precedenza, o anche a mano
dall'operatore), non viene ricreato.
"""

from qgis.core import QgsProject, QgsRasterLayer

SATELLITE_NAME = "Satellite (Esri World Imagery)"
SATELLITE_URI = (
    "type=xyz&url=https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/"
    "MapServer/tile/%7Bz%7D/%7By%7D/%7Bx%7D&zmax=19&zmin=0"
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
