"""
Tavola di stampa "Localizzazione area di cantiere" (sezione 7), a scala
fissa 1:200: un layout di stampa per cantiere, creato una sola volta dal
modello fornito dall'operatore (config/layouts/localizzazione_1_200.qpt,
senza riquadro mappa -- lo si aggiunge qui) e poi sempre riusato, mai
ricreato da zero.

Questo e' il punto chiave per renderlo uguale per tutti gli utenti del
plugin, ma personalizzabile da ciascuno: il layout, una volta creato, vive
nel progetto QGIS corrente (Progetto -> Layout di stampa) come un layout
qualunque. Un operatore puo' aprirlo e aggiungere il proprio logo o altre
annotazioni; quella modifica resta li' anche alle stampe successive, perche'
qui si ricicla sempre lo stesso layout (cercato per nome) invece di
ricaricare il modello. Solo il riquadro mappa (estensione/scala/layer) e il
nome del cantiere nel titolo vengono aggiornati automaticamente a ogni
stampa -- il resto (titolo fisso, barra di scala, freccia nord, ed eventuali
aggiunte dell'operatore) non viene mai toccato.
"""

from qgis.core import (
    QgsLayoutExporter,
    QgsLayoutItemLabel,
    QgsLayoutItemMap,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsPointXY,
    QgsPrintLayout,
    QgsProject,
    QgsReadWriteContext,
    QgsRectangle,
    QgsUnitTypes,
)
from qgis.PyQt.QtXml import QDomDocument

SCALA_LOCALIZZAZIONE = 200

# Proprieta' personalizzata usata per ritrovare il riquadro mappa creato da
# questo modulo tra gli item del layout (il modello non ne contiene uno: va
# cercato per questa proprieta', non per tipo, perche' l'operatore potrebbe
# avere aggiunto altre mappe a mano nel frattempo).
PROPRIETA_MAP_ITEM = "matrice_map_localizzazione"


class PrintLayoutManager:
    def __init__(self, template_path):
        self.template_path = template_path

    def _nome_layout(self, nome_cantiere):
        return f"Localizzazione area di cantiere - {nome_cantiere}"

    def get_or_create_layout(self, nome_cantiere):
        """
        Ritorna il layout di stampa del cantiere, creandolo dal modello solo
        se non esiste ancora nel progetto corrente -- un layout gia'
        esistente (anche se personalizzato dall'operatore) non viene mai
        ricreato ne' sovrascritto.
        """
        project = QgsProject.instance()
        manager = project.layoutManager()
        nome = self._nome_layout(nome_cantiere)
        layout_esistente = manager.layoutByName(nome)
        if layout_esistente is not None:
            return layout_esistente

        with open(self.template_path, encoding="utf-8") as f:
            documento = QDomDocument()
            documento.setContent(f.read())

        layout = QgsPrintLayout(project)
        layout.loadFromTemplate(documento, QgsReadWriteContext())
        layout.setName(nome)
        manager.addLayout(layout)
        return layout

    def _map_item(self, layout):
        """
        Riquadro mappa del layout: creato una sola volta (il modello fornito
        non ne contiene uno) e riusato alle stampe successive, a tutta
        pagina e dietro agli altri elementi (titolo, barra di scala, freccia
        nord restano sempre visibili sopra).
        """
        for item in layout.items():
            if isinstance(item, QgsLayoutItemMap) and item.customProperty(PROPRIETA_MAP_ITEM):
                return item

        map_item = QgsLayoutItemMap(layout)
        layout.addLayoutItem(map_item)
        dimensione_pagina = layout.pageCollection().page(0).pageSize()
        map_item.attemptMove(QgsLayoutPoint(0, 0, QgsUnitTypes.LayoutMillimeters))
        map_item.attemptResize(
            QgsLayoutSize(dimensione_pagina.width(), dimensione_pagina.height(), QgsUnitTypes.LayoutMillimeters)
        )
        map_item.setZValue(-1)
        map_item.setCustomProperty(PROPRIETA_MAP_ITEM, True)
        return map_item

    def aggiorna_mappa(self, layout, layers, centro_lat, centro_lng):
        """
        Centra la mappa sul punto/area di localizzazione del cantiere a
        scala fissa 1:200 (sezione 7), mostrando solo i layer passati
        (sfondo + localizzazione/area del cantiere), non l'intero progetto.
        """
        map_item = self._map_item(layout)
        map_item.setLayers(layers)
        map_item.setCrs(QgsProject.instance().crs())

        centro = QgsPointXY(centro_lng, centro_lat)
        estensione_minima = QgsRectangle(centro, centro)
        estensione_minima.grow(0.0001)  # non nulla: setScale ricalcola la vera estensione dal centro
        map_item.setExtent(estensione_minima)
        map_item.setScale(SCALA_LOCALIZZAZIONE)
        map_item.refresh()

    def aggiorna_titolo(self, layout, nome_cantiere):
        """Nome del cantiere nel titolo del modello, sopra la scritta fissa "Scala 1:200"."""
        for item in layout.items():
            if isinstance(item, QgsLayoutItemLabel) and "Scala 1:200" in item.text():
                item.setText(f"{nome_cantiere}\nLocalizzazione area di cantiere\nScala 1:200")
                return

    def esporta_pdf(self, layout, percorso_pdf):
        esportatore = QgsLayoutExporter(layout)
        impostazioni = QgsLayoutExporter.PdfExportSettings()
        risultato = esportatore.exportToPdf(percorso_pdf, impostazioni)
        if risultato != QgsLayoutExporter.ExportResult.Success:
            raise RuntimeError("Esportazione PDF non riuscita: verificare che il percorso scelto sia scrivibile.")
