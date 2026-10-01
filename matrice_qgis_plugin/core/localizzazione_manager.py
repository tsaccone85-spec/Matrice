"""
Localizzazione dell'intervento (punto) e sua area (sezione successiva su
richiesta esplicita): Matrice registra per il cantiere solo un punto
(cantiere.lat/lng), non un'area vera e propria -- il campo
cantiere.area_geom_wkt e' testo semplice (WKT), non una colonna
PostGIS/geometry. Per questo qui non si usano layer PostGIS nativi (come
in core/layer_manager.py) ma due semplici layer "memory" di QGIS:

- "Localizzazione intervento": un punto di sola lettura, costruito da
  cantiere.lat/lng, solo per vedere dov'e' l'intervento in mappa.
- "Area intervento (disegno)": un poligono disegnabile a mano con gli
  strumenti di editing normali di QGIS (Attiva editing -> Aggiungi
  elemento poligono), precaricato con l'area gia' salvata su Matrice se
  presente. Il salvataggio verso Matrice NON e' automatico (a differenza
  dei layer PostGIS veri e propri, sincronizzati ad ogni commit): va
  confermato esplicitamente dal pulsante dedicato nel pannello Matrice,
  stesso principio di "nessuna scrittura senza conferma esplicita" gia'
  seguito per l'importazione GPS.
"""

from qgis.core import QgsFeature, QgsGeometry, QgsPointXY, QgsProject, QgsVectorLayer

NOME_LAYER_PUNTO = "Localizzazione intervento"
NOME_LAYER_AREA = "Area intervento (disegno)"


class LocalizzazioneManager:
    def __init__(self):
        self._layer_area_per_cantiere = {}

    def mostra_punto(self, cantiere_id, nome_cantiere, lat, lng):
        """Punto di sola lettura dalla posizione registrata su Matrice (cantiere.lat/lng)."""
        if lat is None or lng is None:
            return  # cantiere senza posizione registrata, niente da mostrare
        nome = f"{NOME_LAYER_PUNTO} - {nome_cantiere}"
        project = QgsProject.instance()
        if project.mapLayersByName(nome):
            return  # gia' presente, non duplicare
        layer = QgsVectorLayer("Point?crs=EPSG:4326", nome, "memory")
        feature = QgsFeature()
        feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(lng, lat)))
        layer.dataProvider().addFeature(feature)
        layer.updateExtents()
        project.addMapLayer(layer)

    def layer_area(self, cantiere_id, nome_cantiere, wkt_esistente):
        """
        Ritorna il layer "Area intervento (disegno)" del cantiere, creandolo
        se non esiste ancora -- precaricato con l'area gia' presente su
        Matrice (cantiere.area_geom_wkt), se c'e'. Un solo layer per
        cantiere per sessione QGIS, riusato se gia' aperto (non si perde
        il disegno in corso se si riapre la stessa tavola).
        """
        layer_esistente = self._layer_area_per_cantiere.get(cantiere_id)
        if layer_esistente is not None and QgsProject.instance().mapLayer(layer_esistente.id()) is not None:
            return layer_esistente

        nome = f"{NOME_LAYER_AREA} - {nome_cantiere}"
        layer = QgsVectorLayer("Polygon?crs=EPSG:4326", nome, "memory")
        if wkt_esistente:
            geometria = QgsGeometry.fromWkt(wkt_esistente)
            if not geometria.isNull():
                feature = QgsFeature()
                feature.setGeometry(geometria)
                layer.dataProvider().addFeature(feature)
                layer.updateExtents()
        QgsProject.instance().addMapLayer(layer)
        self._layer_area_per_cantiere[cantiere_id] = layer
        return layer

    def wkt_corrente(self, cantiere_id):
        """
        WKT della (prima) geometria presente nel layer di disegno del
        cantiere, o None se il layer non esiste piu' o non ha ancora
        nessuna area disegnata.
        """
        layer = self._layer_area_per_cantiere.get(cantiere_id)
        if layer is None or QgsProject.instance().mapLayer(layer.id()) is None:
            return None
        feature = next(layer.getFeatures(), None)
        if feature is None or feature.geometry().isNull():
            return None
        return feature.geometry().asWkt()
