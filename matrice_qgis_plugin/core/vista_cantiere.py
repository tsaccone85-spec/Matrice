"""
Vista della mappa sul cantiere e conversione sicura dei click in WGS84.

Perche' esiste (bug segnalato 2026-10-17): aprendo la tavola di un cantiere
"Progettazione" quasi tutti i livelli sono vuoti, quindi QGIS non ha nessuna
estensione su cui centrarsi e resta alla vista precedente -- tipicamente il
mondo intero (sfondo satellitare/CTR). Con il progetto in un sistema di
riferimento locale (es. EPSG:25832, UTM 32N) gli angoli di una vista cosi'
ampia cadono fuori dal dominio di validita' della proiezione: QGIS scrive nel
registro "Trasformazione inversa (da EPSG:25832 a EPSG:4326) ... Point
outside of projection domain" e un click per posizionare una trincea/un
saggio/un carotaggio produceva coordinate senza senso (o un errore).

Questo modulo:
- `punto_canvas_in_wgs84`: converte un punto della canvas in EPSG:4326 senza
  mai lanciare eccezioni (ritorna None se il punto e' fuori dominio);
- `vista_canvas_valida`: dice se la vista corrente e' abbastanza locale da
  poterci posizionare qualcosa;
- `estensione_cantiere_wgs84` / `zoom_su_estensione_wgs84`: calcolano e
  applicano l'ingrandimento sul cantiere (punto di localizzazione, area
  disegnata, livelli con dati).

Tutto e' non bloccante: ogni errore si traduce in "nessuno zoom", mai in
un'eccezione verso l'operatore.
"""

import math

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
)

# Mezza ampiezza (in gradi, ~330 m) della vista attorno al solo punto di
# localizzazione, quando il cantiere non ha ne' area ne' livelli con dati.
MARGINE_PUNTO_GRADI = 0.003
# Oltre questa ampiezza (in gradi, ~200 km) la vista non e' piu' "locale":
# posizionarci sopra una trincea di pochi metri non ha senso.
AMPIEZZA_MASSIMA_VISTA_GRADI = 2.0


def _crs_wgs84():
    return QgsCoordinateReferenceSystem("EPSG:4326")


def punto_canvas_in_wgs84(canvas, punto_canvas):
    """
    Converte `punto_canvas` (QgsPointXY nel CRS della canvas) in EPSG:4326.
    Ritorna None -- mai un'eccezione -- se il punto cade fuori dal dominio
    della proiezione o il risultato non e' un'angolo geografico valido.
    """
    try:
        crs_canvas = canvas.mapSettings().destinationCrs()
        trasforma = QgsCoordinateTransform(crs_canvas, _crs_wgs84(), QgsProject.instance())
        punto = trasforma.transform(punto_canvas)
    except Exception:
        return None
    if not (math.isfinite(punto.x()) and math.isfinite(punto.y())):
        return None
    if not (-180 <= punto.x() <= 180 and -90 <= punto.y() <= 90):
        return None
    return punto


def vista_canvas_valida(canvas):
    """
    True se centro e angoli della vista corrente si convertono in WGS84 e la
    vista copre un'area locale (meno di AMPIEZZA_MASSIMA_VISTA_GRADI).
    """
    extent = canvas.extent()
    if extent.isEmpty() or extent.isNull():
        return False
    punti = [
        QgsPointXY(extent.xMinimum(), extent.yMinimum()),
        QgsPointXY(extent.xMaximum(), extent.yMinimum()),
        QgsPointXY(extent.xMaximum(), extent.yMaximum()),
        QgsPointXY(extent.xMinimum(), extent.yMaximum()),
        extent.center(),
    ]
    convertiti = [punto_canvas_in_wgs84(canvas, p) for p in punti]
    if any(p is None for p in convertiti):
        return False
    lon = [p.x() for p in convertiti]
    lat = [p.y() for p in convertiti]
    return (max(lon) - min(lon)) <= AMPIEZZA_MASSIMA_VISTA_GRADI and (max(lat) - min(lat)) <= AMPIEZZA_MASSIMA_VISTA_GRADI


def _estensione_valida(rect):
    return (
        rect is not None
        and not rect.isNull()
        and all(math.isfinite(v) for v in (rect.xMinimum(), rect.xMaximum(), rect.yMinimum(), rect.yMaximum()))
    )


def estensione_cantiere_wgs84(layers_con_dati, localizzazione):
    """
    Estensione (QgsRectangle, EPSG:4326) che racchiude tutto cio' che il
    cantiere ha gia' in mappa, oppure None se non c'e' nulla di
    georeferenziato.

    layers_con_dati: iterabile di QgsVectorLayer (EPSG:4326) gia' in mappa e
    con almeno una riga. localizzazione: dict di
    MatriceApi.get_localizzazione_cantiere (lat, lng, area_geom_wkt) o None.
    """
    rettangoli = []

    if localizzazione:
        lat, lng = localizzazione.get("lat"), localizzazione.get("lng")
        if lat is not None and lng is not None:
            rettangoli.append(
                QgsRectangle(
                    lng - MARGINE_PUNTO_GRADI, lat - MARGINE_PUNTO_GRADI,
                    lng + MARGINE_PUNTO_GRADI, lat + MARGINE_PUNTO_GRADI,
                )
            )
        wkt = localizzazione.get("area_geom_wkt")
        if wkt:
            geometria = QgsGeometry.fromWkt(wkt)
            if geometria is not None and not geometria.isNull():
                rettangoli.append(geometria.boundingBox())

    for layer in layers_con_dati:
        try:
            rettangoli.append(layer.extent())
        except Exception:
            continue

    rettangoli = [r for r in rettangoli if _estensione_valida(r)]
    if not rettangoli:
        return None

    unione = QgsRectangle(rettangoli[0])
    for r in rettangoli[1:]:
        unione.combineExtentWith(r)

    # Un solo punto (nessuna area): allarga comunque la vista.
    if unione.width() < 1e-6 or unione.height() < 1e-6:
        c = unione.center()
        unione = QgsRectangle(
            c.x() - MARGINE_PUNTO_GRADI, c.y() - MARGINE_PUNTO_GRADI,
            c.x() + MARGINE_PUNTO_GRADI, c.y() + MARGINE_PUNTO_GRADI,
        )
    return unione


def zoom_su_estensione_wgs84(canvas, estensione_wgs84, scala_margine=1.2):
    """
    Porta la canvas su `estensione_wgs84` (EPSG:4326). Ritorna True se
    l'ingrandimento e' stato applicato, False in qualunque altro caso (mai
    eccezioni).
    """
    try:
        crs_canvas = canvas.mapSettings().destinationCrs()
        trasforma = QgsCoordinateTransform(_crs_wgs84(), crs_canvas, QgsProject.instance())
        estensione_canvas = trasforma.transformBoundingBox(estensione_wgs84)
        if not _estensione_valida(estensione_canvas):
            return False
        estensione_canvas.scale(scala_margine)
        canvas.setExtent(estensione_canvas)
        canvas.refresh()
        return True
    except Exception:
        return False
