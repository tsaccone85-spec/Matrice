"""
Rinvenimento di survey (flusso Progettazione): un punto creato con un
click sulla mappa (core/rettangolo_progettazione.py per trincea/saggio e'
il mirror -- qui niente rotazione/dimensioni, solo un punto). La
geometria scritta in v_survey_ritrovamento_qgis passa dal trigger
INSTEAD OF INSERT (migrazione 0070) che la traduce in lat/lng sulla
tabella reale.
"""

from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsFeature, QgsGeometry, QgsProject
from qgis.gui import QgsMapTool


def crea_feature_ritrovamento(layer, punto_wgs84, attributi):
    """Inserisce un punto in `layer` (editabile, PostGIS) su `punto_wgs84` (QgsPointXY EPSG:4326)."""
    feature = QgsFeature(layer.fields())
    feature.setGeometry(QgsGeometry.fromPointXY(punto_wgs84))
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


class PosizionaPuntoMapTool(QgsMapTool):
    def __init__(self, canvas, layer, attributi, on_creato, on_annullato=None):
        super().__init__(canvas)
        self.canvas = canvas
        self.layer = layer
        self.attributi = attributi
        self.on_creato = on_creato
        self.on_annullato = on_annullato

    def canvasReleaseEvent(self, event):
        punto_canvas = self.toMapCoordinates(event.pos())
        crs_canvas = self.canvas.mapSettings().destinationCrs()
        crs_wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        trasforma = QgsCoordinateTransform(crs_canvas, crs_wgs84, QgsProject.instance())
        punto_wgs84 = trasforma.transform(punto_canvas)
        ok = crea_feature_ritrovamento(self.layer, punto_wgs84, self.attributi)
        self.canvas.unsetMapTool(self)
        self.on_creato(ok)

    def keyPressEvent(self, event):
        from qgis.PyQt.QtCore import Qt
        if event.key() == Qt.Key_Escape:
            self.canvas.unsetMapTool(self)
            if self.on_annullato:
                self.on_annullato()
