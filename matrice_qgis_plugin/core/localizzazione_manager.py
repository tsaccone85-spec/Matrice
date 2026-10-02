"""
Localizzazione dell'intervento (punto) e sua area (sezione successiva su
richiesta esplicita): Matrice registra per il cantiere solo un punto
(cantiere.lat/lng), non un'area vera e propria -- il campo
cantiere.area_geom_wkt e' testo semplice (WKT), non una colonna
PostGIS/geometry. Per questo qui non si usano layer PostGIS nativi (come
in core/layer_manager.py) ma semplici layer "memory" di QGIS:

- "Localizzazione intervento": un punto di sola lettura, costruito da
  cantiere.lat/lng, solo per vedere dov'e' l'intervento in mappa.
- "Area intervento (disegno)": un poligono disegnabile a mano con gli
  strumenti di editing normali di QGIS (Attiva editing -> Aggiungi
  elemento poligono), precaricato con l'area gia' salvata su Matrice se
  presente. Il salvataggio verso Matrice NON e' automatico (a differenza
  dei layer PostGIS veri e propri, sincronizzati ad ogni commit): va
  confermato esplicitamente dal pulsante dedicato nel pannello Matrice,
  stesso principio di "nessuna scrittura senza conferma esplicita" gia'
  seguito per l'importazione GPS. Il tratto e' impostato a tratto-punto
  (convenzione cartografica per il limite dell'area di indagine).
- "Sezioni (disegno)": layer di linee disegnabile a mano allo stesso
  modo, per tracciare in mappa dove sono state fatte le sezioni/prospetti.
  Matrice non ha una colonna geometry dedicata per le sezioni (solo il
  punto di presa in sezione_prospetto, vedi core/layer_manager.py): per
  questo, a differenza dell'area, qui non c'e' alcun salvataggio verso
  Matrice, e' solo disegno/visualizzazione locale in QGIS. Il tratto e'
  tratteggiato (convenzione cartografica per le linee di sezione).
"""

from qgis.core import (
    QgsFeature,
    QgsFillSymbol,
    QgsGeometry,
    QgsLineSymbol,
    QgsPointXY,
    QgsProject,
    QgsSingleSymbolRenderer,
    QgsVectorLayer,
)

NOME_LAYER_PUNTO = "Localizzazione intervento"
NOME_LAYER_AREA = "Area intervento (disegno)"
NOME_LAYER_SEZIONI = "Sezioni (disegno)"


def _imposta_stile_area(layer):
    """Contorno tratto-punto, senza riempimento, per il limite dell'area di indagine."""
    simbolo = QgsFillSymbol.createSimple(
        {"style": "no", "outline_style": "dash dot", "outline_width": "0.6", "outline_color": "0,0,0,255"}
    )
    layer.setRenderer(QgsSingleSymbolRenderer(simbolo))


def _imposta_stile_sezioni(layer):
    """Tratto tratteggiato per le linee di sezione/prospetto."""
    simbolo = QgsLineSymbol.createSimple({"line_style": "dash", "line_width": "0.6", "line_color": "0,0,0,255"})
    layer.setRenderer(QgsSingleSymbolRenderer(simbolo))


class LocalizzazioneManager:
    def __init__(self):
        self._layer_area_per_cantiere = {}
        self._layer_sezioni_per_cantiere = {}

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
        _imposta_stile_area(layer)
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

    def layer_area_esiste(self, cantiere_id):
        """True se il layer di disegno e' gia' stato creato e risulta ancora nel progetto."""
        layer = self._layer_area_per_cantiere.get(cantiere_id)
        return layer is not None and QgsProject.instance().mapLayer(layer.id()) is not None

    def is_area_visibile(self, cantiere_id):
        """
        Stato della spunta di visibilita' del layer nel pannello Livelli di
        QGIS (non se il layer esiste: un layer presente ma con la spunta
        tolta conta come non visibile).
        """
        layer = self._layer_area_per_cantiere.get(cantiere_id)
        if layer is None or QgsProject.instance().mapLayer(layer.id()) is None:
            return False
        nodo = QgsProject.instance().layerTreeRoot().findLayer(layer.id())
        return nodo is not None and nodo.isVisible()

    def set_area_visibile(self, cantiere_id, visibile):
        """
        Spunta/toglie la visibilita' del layer di disegno dal pannello
        Livelli di QGIS -- MAI lo rimuove dal progetto (a differenza di
        LayerManager.hide_layer per i layer PostGIS): un'area disegnata ma
        non ancora salvata su Matrice andrebbe persa se il layer venisse
        distrutto solo per nasconderlo.
        """
        layer = self._layer_area_per_cantiere.get(cantiere_id)
        if layer is None or QgsProject.instance().mapLayer(layer.id()) is None:
            return
        nodo = QgsProject.instance().layerTreeRoot().findLayer(layer.id())
        if nodo is not None:
            nodo.setItemVisibilityChecked(visibile)

    def layer_sezioni(self, cantiere_id, nome_cantiere):
        """
        Ritorna il layer "Sezioni (disegno)" del cantiere, creandolo se non
        esiste ancora -- un solo layer per cantiere per sessione QGIS,
        riusato se gia' aperto (non si perde il disegno in corso se si
        riapre la stessa tavola). A differenza di layer_area(), non c'e'
        nulla da precaricare da Matrice: e' solo disegno locale.
        """
        layer_esistente = self._layer_sezioni_per_cantiere.get(cantiere_id)
        if layer_esistente is not None and QgsProject.instance().mapLayer(layer_esistente.id()) is not None:
            return layer_esistente

        nome = f"{NOME_LAYER_SEZIONI} - {nome_cantiere}"
        layer = QgsVectorLayer("LineString?crs=EPSG:4326", nome, "memory")
        _imposta_stile_sezioni(layer)
        QgsProject.instance().addMapLayer(layer)
        self._layer_sezioni_per_cantiere[cantiere_id] = layer
        return layer

    def layer_sezioni_esiste(self, cantiere_id):
        """True se il layer di disegno delle sezioni e' gia' stato creato e risulta ancora nel progetto."""
        layer = self._layer_sezioni_per_cantiere.get(cantiere_id)
        return layer is not None and QgsProject.instance().mapLayer(layer.id()) is not None

    def is_sezioni_visibile(self, cantiere_id):
        """Stato della spunta di visibilita' del layer sezioni nel pannello Livelli di QGIS."""
        layer = self._layer_sezioni_per_cantiere.get(cantiere_id)
        if layer is None or QgsProject.instance().mapLayer(layer.id()) is None:
            return False
        nodo = QgsProject.instance().layerTreeRoot().findLayer(layer.id())
        return nodo is not None and nodo.isVisible()

    def set_sezioni_visibile(self, cantiere_id, visibile):
        """
        Spunta/toglie la visibilita' del layer sezioni dal pannello Livelli
        di QGIS -- MAI lo rimuove dal progetto, stesso principio di
        set_area_visibile: un disegno in corso non va perso solo per
        nasconderlo.
        """
        layer = self._layer_sezioni_per_cantiere.get(cantiere_id)
        if layer is None or QgsProject.instance().mapLayer(layer.id()) is None:
            return
        nodo = QgsProject.instance().layerTreeRoot().findLayer(layer.id())
        if nodo is not None:
            nodo.setItemVisibilityChecked(visibile)

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
