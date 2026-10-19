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

from .vista_cantiere import punto_canvas_in_wgs84


# Dettaglio dell'ultimo commit fallito, mostrato da matrice_plugin.py.
ULTIMO_ERRORE_COMMIT = {"testo": ""}


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
        # Esito del commit NON ignorabile: se il database rifiuta l'insert
        # (RLS, vincoli, trigger) l'elemento non esiste, anche se addFeature
        # in locale e' riuscito.
        ok = layer.commitChanges()
        if not ok:
            ULTIMO_ERRORE_COMMIT["testo"] = "; ".join(layer.commitErrors())
            layer.rollBack()
    return ok


class PosizionaRettangoloMapTool(QgsMapTool):
    """
    Strumento "clicca per posizionare" CONTINUO: ogni click sulla canvas crea
    un rettangolo (dimensioni/attributi gia' raccolti dal dialog) centrato nel
    punto cliccato, e lo strumento resta attivo per posizionarne altri
    identici, sempre sullo stesso layer. Si termina con Esc o tasto destro.
    Dal secondo elemento in poi il nome riceve un numero progressivo
    ("Trincea 2", "Trincea 3"...) per non avere elementi omonimi.
    Posizione precisa o rotazione si sistemano dopo con gli strumenti
    nativi QGIS (Sposta/Ruota elementi, vedi menu "Progettazione").
    """

    def __init__(self, canvas, layer, larghezza_m, lunghezza_m, attributi, on_creato,
                 on_annullato=None, on_errore=None, on_terminato=None):
        super().__init__(canvas)
        self.canvas = canvas
        self.layer = layer
        self.larghezza_m = larghezza_m
        self.lunghezza_m = lunghezza_m
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

    def canvasReleaseEvent(self, event):
        from qgis.PyQt.QtCore import Qt
        if event.button() == Qt.RightButton:
            self._termina()
            return
        punto_canvas = self.toMapCoordinates(event.pos())
        centro_wgs84 = punto_canvas_in_wgs84(self.canvas, punto_canvas)
        if centro_wgs84 is None:
            # Punto fuori dal dominio della proiezione (vista troppo ampia,
            # tipicamente il mondo intero): lo strumento resta attivo cosi'
            # l'operatore puo' ingrandire sul cantiere e cliccare di nuovo.
            if self.on_errore:
                self.on_errore()
            return

        attributi = dict(self.attributi)
        if self.creati >= 1 and attributi.get("nome"):
            attributi["nome"] = f"{self.attributi['nome']} {self.creati + 1}"
        ok = crea_feature_rettangolo(self.layer, centro_wgs84, self.larghezza_m, self.lunghezza_m, attributi)
        if ok:
            self.creati += 1
        self.on_creato(ok)

    def keyPressEvent(self, event):
        from qgis.PyQt.QtCore import Qt
        if event.key() == Qt.Key_Escape:
            self._termina()
