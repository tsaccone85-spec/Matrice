"""
Trincee e Saggi del flusso Progettazione Archeologica: rettangoli
(larghezza/lunghezza/profondita' + rotazione) creati dal menu "Progettazione"
della toolbar (matrice_plugin.py), non disegnati a mano vertice per vertice.

La geometria si costruisce in una proiezione azimutale equidistante locale
centrata sul punto cliccato (+proj=aeqd): da' metri esatti in qualunque punto
del mondo senza dover scegliere un fuso UTM, a differenza di una proiezione
fissa. Il rettangolo nasce con rotazione 0; per ruotarlo dopo l'inserimento
si usa lo strumento nativo QGIS "Ruota elementi" (richiamabile anche dal
nostro menu, vedi matrice_plugin.py), che scrive la rotazione sui vertici
--- `angolo_rotazione_gradi` va poi ricalcolato da quei vertici (lo stesso
principio di core/point_position_sync.py per la quota, qui per l'angolo).
"""

import math

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
)
from qgis.gui import QgsMapTool


def crea_geometria_rettangolo(centro_wgs84, larghezza_m, lunghezza_m, angolo_gradi=0):
    """
    Rettangolo larghezza_m x lunghezza_m, centrato su `centro_wgs84`
    (QgsPointXY in EPSG:4326), ruotato di `angolo_gradi` (antiorario),
    restituito come QgsGeometry in EPSG:4326.
    """
    crs_metrico = QgsCoordinateReferenceSystem.fromProj(
        f"+proj=aeqd +lat_0={centro_wgs84.y()} +lon_0={centro_wgs84.x()} "
        "+x_0=0 +y_0=0 +ellps=WGS84 +units=m +no_defs"
    )
    crs_wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
    trasforma_in_wgs84 = QgsCoordinateTransform(crs_metrico, crs_wgs84, QgsProject.instance())

    mezza_larghezza = larghezza_m / 2
    mezza_lunghezza = lunghezza_m / 2
    angolo_rad = math.radians(angolo_gradi)
    cos_a, sin_a = math.cos(angolo_rad), math.sin(angolo_rad)

    def ruota(dx, dy):
        return (dx * cos_a - dy * sin_a, dx * sin_a + dy * cos_a)

    # Nel CRS metrico il centro del rettangolo e' sempre (0, 0) per
    # costruzione (lat_0/lon_0 = centro cliccato).
    angoli_locali = [
        ruota(-mezza_larghezza, -mezza_lunghezza),
        ruota(mezza_larghezza, -mezza_lunghezza),
        ruota(mezza_larghezza, mezza_lunghezza),
        ruota(-mezza_larghezza, mezza_lunghezza),
    ]
    angoli_locali.append(angoli_locali[0])  # chiude l'anello

    vertici_wgs84 = [trasforma_in_wgs84.transform(QgsPointXY(dx, dy)) for dx, dy in angoli_locali]
    return QgsGeometry.fromPolygonXY([vertici_wgs84])


def crea_feature_rettangolo(layer, centro_wgs84, larghezza_m, lunghezza_m, attributi):
    """
    Inserisce nel layer (editabile, PostGIS) un nuovo rettangolo centrato su
    `centro_wgs84`, rotazione 0 (si ruota dopo con lo strumento nativo
    QGIS). `attributi`: dict nome_campo -> valore, solo i campi presenti nel
    layer vengono scritti. Ritorna True/False (esito di addFeature).
    """
    geometria = crea_geometria_rettangolo(centro_wgs84, larghezza_m, lunghezza_m, angolo_gradi=0)
    feature = QgsFeature(layer.fields())
    feature.setGeometry(geometria)
    for campo, valore in attributi.items():
        indice = layer.fields().indexOf(campo)
        if indice >= 0:
            feature.setAttribute(indice, valore)

    era_editabile = layer.isEditable()
    if not era_editabile:
        layer.startEditing()
    ok = layer.addFeature(feature)
    if ok and not era_editabile:
        layer.commitChanges()
    return ok


class PosizionaRettangoloMapTool(QgsMapTool):
    """
    Strumento "un click per posizionare": il prossimo click sulla canvas
    crea il rettangolo (dimensioni/attributi gia' raccolti dal dialog) nel
    punto cliccato, poi disattiva se stesso. Niente drag-and-drop: posizione
    precisa si ottiene spostando il vertice/l'elemento dopo con gli
    strumenti nativi QGIS (Sposta elemento(i), Ruota elementi).
    """

    def __init__(self, canvas, layer, larghezza_m, lunghezza_m, attributi, on_creato, on_annullato=None):
        super().__init__(canvas)
        self.canvas = canvas
        self.layer = layer
        self.larghezza_m = larghezza_m
        self.lunghezza_m = lunghezza_m
        self.attributi = attributi
        self.on_creato = on_creato
        self.on_annullato = on_annullato

    def canvasReleaseEvent(self, event):
        punto_canvas = self.toMapCoordinates(event.pos())
        crs_canvas = self.canvas.mapSettings().destinationCrs()
        crs_wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        trasforma = QgsCoordinateTransform(crs_canvas, crs_wgs84, QgsProject.instance())
        centro_wgs84 = trasforma.transform(punto_canvas)

        ok = crea_feature_rettangolo(self.layer, centro_wgs84, self.larghezza_m, self.lunghezza_m, self.attributi)
        self.canvas.unsetMapTool(self)
        self.on_creato(ok)

    def keyPressEvent(self, event):
        from qgis.PyQt.QtCore import Qt
        if event.key() == Qt.Key_Escape:
            self.canvas.unsetMapTool(self)
            if self.on_annullato:
                self.on_annullato()
