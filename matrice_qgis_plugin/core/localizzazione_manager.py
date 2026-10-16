"""
Localizzazione dell'intervento (punto) e sua area (sezione successiva su
richiesta esplicita): Matrice registra per il cantiere solo un punto
(cantiere.lat/lng), non un'area vera e propria -- il campo
cantiere.area_geom_wkt e' testo semplice (WKT), non una colonna
PostGIS/geometry. Per questo qui non si usano layer PostGIS nativi (come
in core/layer_manager.py) ma due layer "memory" di QGIS del tutto separati,
ciascuno con la propria spunta nel pannello "Livelli del cantiere" (key
"localizzazione_cantiere" e "area_intervento"):

- "Localizzazione di cantiere": un punto di sola lettura, costruito da
  cantiere.lat/lng, solo per vedere dov'e' l'intervento in mappa.
- "Area di cantiere (disegno)": un poligono disegnabile a mano con gli
  strumenti di editing normali di QGIS (Attiva editing -> Aggiungi
  elemento poligono), precaricato con l'area gia' salvata su Matrice se
  presente. Il salvataggio verso Matrice NON e' automatico (a differenza
  dei layer PostGIS veri e propri, sincronizzati ad ogni commit): va
  confermato esplicitamente dal pulsante dedicato nel pannello Matrice,
  stesso principio di "nessuna scrittura senza conferma esplicita" gia'
  seguito per l'importazione GPS.

I due layer non hanno nessun legame tra loro (due QgsVectorLayer distinti,
due voci distinte nel pannello): un operatore puo' mostrare l'uno senza
l'altro, e nascondere l'uno non tocca mai l'altro.
"""

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsVectorLayer,
)
from qgis.PyQt import sip

NOME_LAYER_PUNTO = "Localizzazione di cantiere"
NOME_LAYER_AREA = "Area di cantiere (disegno)"
NOME_LAYER_BUFFER = "Area buffer indagine"


def _layer_presente(layer):
    """
    True se `layer` esiste ancora nel progetto QGIS corrente.

    Un operatore puo' rimuovere un layer a mano dal pannello Livelli di
    QGIS (non solo togliendo la spunta dal pannello Matrice): l'oggetto
    C++ sottostante viene distrutto, ma il wrapper Python resta nei
    dizionari _layer_*_per_cantiere qui sotto finche' non si richiama
    questa funzione. Senza `sip.isdeleted()`, chiamare layer.id() su un
    wrapper il cui oggetto C++ e' gia' stato eliminato lancia
    RuntimeError invece di poter essere trattato come "non piu'
    presente" (bug corretto: mandava in errore ogni funzione di questo
    modulo che tocca il layer dopo una rimozione manuale).
    """
    return layer is not None and not sip.isdeleted(layer) and QgsProject.instance().mapLayer(layer.id()) is not None


def calcola_buffer_wkt(area_wkt, distanza_m, segmenti=16):
    """
    Buffer di `distanza_m` metri attorno al poligono `area_wkt` (WKT,
    EPSG:4326). Stessa tecnica di riproiezione locale azimutale
    equidistante di core/rettangolo_progettazione.py
    crea_geometria_rettangolo (CRS +proj=aeqd centrato sul centroide del
    poligono), qui applicata a un poligono qualunque invece che a un
    rettangolo costruito da zero. Ritorna WKT in EPSG:4326.
    """
    geometria = QgsGeometry.fromWkt(area_wkt)
    if geometria.isNull():
        raise ValueError("Area di cantiere non valida: impossibile calcolare il buffer.")
    centro = geometria.centroid().asPoint()
    crs_metrico = QgsCoordinateReferenceSystem.fromProj(
        f"+proj=aeqd +lat_0={centro.y()} +lon_0={centro.x()} +x_0=0 +y_0=0 +ellps=WGS84 +units=m +no_defs"
    )
    crs_wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
    verso_metrico = QgsCoordinateTransform(crs_wgs84, crs_metrico, QgsProject.instance())
    verso_wgs84 = QgsCoordinateTransform(crs_metrico, crs_wgs84, QgsProject.instance())
    geometria_metrica = QgsGeometry(geometria)
    geometria_metrica.transform(verso_metrico)
    buffer_metrico = geometria_metrica.buffer(distanza_m, segmenti)
    buffer_metrico.transform(verso_wgs84)
    return buffer_metrico.asWkt()


class LocalizzazioneManager:
    def __init__(self):
        self._layer_punto_per_cantiere = {}
        self._layer_area_per_cantiere = {}
        self._layer_buffer_per_cantiere = {}
        # WKT con cui layer_area() e' stato creato per ciascun cantiere:
        # confrontato con wkt_corrente() in remove_cantiere() per sapere se
        # l'operatore ha disegnato/modificato l'area senza ancora salvarla
        # su Matrice -- non si vuole scartarla in silenzio.
        self._wkt_iniziale_per_cantiere = {}

    # ------------------------------------------------------------------
    # "Localizzazione di cantiere" (punto, sola lettura)
    # ------------------------------------------------------------------
    def layer_punto(self, cantiere_id, nome_cantiere, lat, lng):
        """
        Ritorna il layer puntuale del cantiere, creandolo se non esiste
        ancora -- un solo layer per cantiere per sessione QGIS, riusato se
        gia' aperto. Senza lat/lng registrati su Matrice il layer viene
        comunque creato, ma vuoto (nessun punto da mostrare).
        """
        layer_esistente = self._layer_punto_per_cantiere.get(cantiere_id)
        if _layer_presente(layer_esistente):
            return layer_esistente

        nome = f"{NOME_LAYER_PUNTO} - {nome_cantiere}"
        layer = QgsVectorLayer("Point?crs=EPSG:4326", nome, "memory")
        if lat is not None and lng is not None:
            feature = QgsFeature()
            feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(lng, lat)))
            layer.dataProvider().addFeature(feature)
            layer.updateExtents()
        QgsProject.instance().addMapLayer(layer)
        self._layer_punto_per_cantiere[cantiere_id] = layer
        return layer

    def layer_punto_esiste(self, cantiere_id):
        """True se il layer del punto e' gia' stato creato e risulta ancora nel progetto."""
        layer = self._layer_punto_per_cantiere.get(cantiere_id)
        return _layer_presente(layer)

    def is_punto_visibile(self, cantiere_id):
        layer = self._layer_punto_per_cantiere.get(cantiere_id)
        if not _layer_presente(layer):
            return False
        nodo = QgsProject.instance().layerTreeRoot().findLayer(layer.id())
        return nodo is not None and nodo.isVisible()

    def set_punto_visibile(self, cantiere_id, visibile):
        """Spunta/toglie la visibilita' del punto -- MAI lo rimuove dal progetto (vedi set_area_visibile)."""
        layer = self._layer_punto_per_cantiere.get(cantiere_id)
        if not _layer_presente(layer):
            return
        nodo = QgsProject.instance().layerTreeRoot().findLayer(layer.id())
        if nodo is not None:
            nodo.setItemVisibilityChecked(visibile)

    # ------------------------------------------------------------------
    # "Area di cantiere (disegno)" (poligono, disegnabile)
    # ------------------------------------------------------------------
    def layer_area(self, cantiere_id, nome_cantiere, wkt_esistente):
        """
        Ritorna il layer "Area di cantiere (disegno)" del cantiere,
        creandolo se non esiste ancora -- precaricato con l'area gia'
        presente su Matrice (cantiere.area_geom_wkt), se c'e'. Un solo
        layer per cantiere per sessione QGIS, riusato se gia' aperto (non si
        perde il disegno in corso se si riapre la stessa tavola).
        """
        layer_esistente = self._layer_area_per_cantiere.get(cantiere_id)
        if _layer_presente(layer_esistente):
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
        self._wkt_iniziale_per_cantiere[cantiere_id] = wkt_esistente
        return layer

    def layer_area_esiste(self, cantiere_id):
        """True se il layer di disegno e' gia' stato creato e risulta ancora nel progetto."""
        layer = self._layer_area_per_cantiere.get(cantiere_id)
        return _layer_presente(layer)

    def is_area_visibile(self, cantiere_id):
        """
        Stato della spunta di visibilita' del layer nel pannello Livelli di
        QGIS (non se il layer esiste: un layer presente ma con la spunta
        tolta conta come non visibile).
        """
        layer = self._layer_area_per_cantiere.get(cantiere_id)
        if not _layer_presente(layer):
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
        if not _layer_presente(layer):
            return
        nodo = QgsProject.instance().layerTreeRoot().findLayer(layer.id())
        if nodo is not None:
            nodo.setItemVisibilityChecked(visibile)

    # ------------------------------------------------------------------
    # "Area buffer indagine" (poligono, sola lettura -- rigenerato ad ogni
    # creazione, mai disegnato a mano: a differenza di layer_area, qui il
    # layer precedente viene sempre rimosso e ricreato da capo, stesso
    # schema di layer_punto).
    # ------------------------------------------------------------------
    def layer_buffer(self, cantiere_id, nome_cantiere, wkt_buffer):
        layer_esistente = self._layer_buffer_per_cantiere.get(cantiere_id)
        if _layer_presente(layer_esistente):
            QgsProject.instance().removeMapLayer(layer_esistente.id())
        nome = f"{NOME_LAYER_BUFFER} - {nome_cantiere}"
        layer = QgsVectorLayer("Polygon?crs=EPSG:4326", nome, "memory")
        if wkt_buffer:
            geometria = QgsGeometry.fromWkt(wkt_buffer)
            if not geometria.isNull():
                feature = QgsFeature()
                feature.setGeometry(geometria)
                layer.dataProvider().addFeature(feature)
                layer.updateExtents()
        QgsProject.instance().addMapLayer(layer)
        self._layer_buffer_per_cantiere[cantiere_id] = layer
        return layer

    def wkt_corrente(self, cantiere_id):
        """
        WKT della (prima) geometria presente nel layer di disegno del
        cantiere, o None se il layer non esiste piu' o non ha ancora
        nessuna area disegnata.
        """
        layer = self._layer_area_per_cantiere.get(cantiere_id)
        if not _layer_presente(layer):
            return None
        feature = next(layer.getFeatures(), None)
        if feature is None or feature.geometry().isNull():
            return None
        return feature.geometry().asWkt()

    def remove_cantiere(self, cantiere_id):
        """
        Rimuove dal progetto QGIS i layer di localizzazione/area/buffer del
        cantiere indicato (passaggio a un altro cantiere) -- a differenza di
        set_punto_visibile/set_area_visibile, qui la rimozione e' voluta, non
        solo un cambio di visibilita'.

        Ritorna True se l'area aveva un disegno diverso da quello con cui il
        layer era stato creato (quindi non ancora salvato su Matrice): il
        chiamante decide se avvisare l'operatore prima di scartarlo.
        """
        wkt_iniziale = self._wkt_iniziale_per_cantiere.pop(cantiere_id, None)
        area_non_salvata = self.layer_area_esiste(cantiere_id) and self.wkt_corrente(cantiere_id) != wkt_iniziale

        layer_punto = self._layer_punto_per_cantiere.pop(cantiere_id, None)
        if _layer_presente(layer_punto):
            QgsProject.instance().removeMapLayer(layer_punto.id())

        layer_area = self._layer_area_per_cantiere.pop(cantiere_id, None)
        if _layer_presente(layer_area):
            QgsProject.instance().removeMapLayer(layer_area.id())

        layer_buffer = self._layer_buffer_per_cantiere.pop(cantiere_id, None)
        if _layer_presente(layer_buffer):
            QgsProject.instance().removeMapLayer(layer_buffer.id())

        return area_non_salvata

    def wkt_da_file(self, path):
        """
        Legge la prima geometria di un file vettoriale (shapefile/GeoPackage/
        KML) e la ritorna come WKT in EPSG:4326, per l'import manuale
        dell'area di cantiere (sezione 4 della spec). Ogni altro formato o
        geometria supportato da OGR funziona per lo stesso motivo per cui
        core/gps_import.py apre file GPX/GeoJSON: QgsVectorLayer li legge
        tutti con lo stesso codice, il provider cambia da solo in base
        all'estensione.
        """
        layer = QgsVectorLayer(path, "import_area", "ogr")
        if not layer.isValid() or layer.featureCount() == 0:
            raise ValueError(f"Impossibile leggere una geometria da '{path}'.")
        feature = next(layer.getFeatures())
        geom = feature.geometry()
        if geom is None or geom.isNull() or geom.isEmpty():
            raise ValueError(f"Impossibile leggere una geometria da '{path}'.")
        if layer.crs().authid() != "EPSG:4326":
            geom.transform(
                QgsCoordinateTransform(
                    layer.crs(), QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance()
                )
            )
        return geom.asWkt()
