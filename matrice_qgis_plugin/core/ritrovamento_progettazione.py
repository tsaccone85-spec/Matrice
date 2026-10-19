"""
Rinvenimento di survey (flusso Progettazione): un punto creato con un
click sulla mappa (core/rettangolo_progettazione.py per trincea/saggio e'
il mirror -- qui niente rotazione/dimensioni, solo un punto). La
geometria scritta in v_survey_ritrovamento_qgis passa dal trigger
INSTEAD OF INSERT (migrazione 0070) che la traduce in lat/lng sulla
tabella reale.
"""

from qgis.core import QgsFeature, QgsGeometry, QgsProject
from qgis.gui import QgsMapTool

from .vista_cantiere import punto_canvas_in_wgs84


# Dettaglio dell'ultimo commit fallito, mostrato da matrice_plugin.py.
ULTIMO_ERRORE_COMMIT = {"testo": ""}


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
        # Esito del commit NON ignorabile: se il database rifiuta l'insert
        # (RLS, vincoli, trigger) l'elemento non esiste, anche se addFeature
        # in locale e' riuscito.
        ok = layer.commitChanges()
        if not ok:
            ULTIMO_ERRORE_COMMIT["testo"] = "; ".join(layer.commitErrors())
            layer.rollBack()
    return ok


class PosizionaPuntoMapTool(QgsMapTool):
    """
    Strumento "clicca per posizionare" CONTINUO per punti (rinvenimento,
    carotaggio): ogni click crea un punto con gli stessi attributi sul layer
    scelto e lo strumento resta attivo per posizionarne altri. Si termina con
    Esc o tasto destro. Numerazioni progressive (numero_carotaggio,
    numero_progressivo) le assegna il trigger del database a ogni inserimento.
    """

    def __init__(self, canvas, layer, attributi, on_creato, on_annullato=None, on_errore=None, on_terminato=None):
        super().__init__(canvas)
        self.canvas = canvas
        self.layer = layer
        self._layer_id = layer.id()
        self.attributi = attributi
        self.on_creato = on_creato
        self.on_annullato = on_annullato
        self.on_errore = on_errore
        self.on_terminato = on_terminato
        self.creati = 0

    def _termina(self):
        self.canvas.unsetMapTool(self)
        if self.on_terminato:
            self.on_terminato(self.creati)
        elif self.on_annullato:
            self.on_annullato()

    def _layer_valido(self):
        """
        Il layer puo' essere stato ricreato/rimosso dopo l'attivazione dello
        strumento (ricarica del progetto, toggle Progettazione): riallinea
        `self.layer` al layer vivo con lo stesso id. False se non esiste piu'.
        """
        from qgis.PyQt import sip
        if not sip.isdeleted(self.layer):
            return True
        vivo = QgsProject.instance().mapLayer(self._layer_id)
        if vivo is None:
            return False
        self.layer = vivo
        return True

    def canvasReleaseEvent(self, event):
        from qgis.PyQt.QtCore import Qt
        if event.button() == Qt.RightButton:
            self._termina()
            return
        if not self._layer_valido():
            if self.on_errore:
                self.on_errore()
            self._termina()
            return
        punto_canvas = self.toMapCoordinates(event.pos())
        punto_wgs84 = punto_canvas_in_wgs84(self.canvas, punto_canvas)
        if punto_wgs84 is None:
            # Punto fuori dal dominio della proiezione (vista troppo ampia):
            # lo strumento resta attivo per riprovare dopo aver ingrandito.
            if self.on_errore:
                self.on_errore()
            return
        ok = crea_feature_ritrovamento(self.layer, punto_wgs84, dict(self.attributi))
        if ok:
            self.creati += 1
        self.on_creato(ok)

    def keyPressEvent(self, event):
        from qgis.PyQt.QtCore import Qt
        if event.key() == Qt.Key_Escape:
            self._termina()
