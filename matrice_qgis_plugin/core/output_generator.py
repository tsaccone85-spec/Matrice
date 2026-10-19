"""
Generazione dei Print Layout: rilievo complessivo per fasi, rilievo per
singola fase, rilievo di dettaglio 1:20 (sezioni 7 e 8 -- Iterazione 3 e 4
della roadmap).
"""

from qgis.core import QgsLayoutExporter, QgsPrintLayout, QgsProject

# Soglie indicative di scelta automatica della scala (sezione 7), in metri
# di dimensione massima del cantiere. Configurabili.
SCALE_THRESHOLDS = [
    (20, 50),    # < 20 m -> 1:50
    (50, 100),   # 20-50 m -> 1:100
    (float("inf"), 200),  # > 50 m -> 1:200
]

# Sequenza di scale da provare per la Tavola 2, dalla piu' dettagliata alla
# piu' larga: si usa la prima che fa entrare il contenuto in un unico A0.
SCALE_SEQUENCE_TAVOLA2 = [1000, 1200, 1500, 2000]

# Formato A0 in millimetri (orientamento verticale; per il confronto si
# considerano entrambi gli orientamenti, dato che il layout puo' essere
# ruotato in orizzontale).
FOGLIO_A0_MM = (841, 1189)


class OutputGenerator:
    def __init__(self, connection):
        self.connection = connection

    def suggest_scale(self, extent):
        """Sezione 7: scala automatica in base all'ingombro del rilievo."""
        dimensione_max = max(extent.width(), extent.height())
        for soglia, scala in SCALE_THRESHOLDS:
            if dimensione_max < soglia:
                return scala
        return SCALE_THRESHOLDS[-1][1]

    def entra_in_foglio(self, extent, scala, dimensione_foglio_mm=FOGLIO_A0_MM):
        """
        True se il contenuto dell'extent (in metri), stampato alla scala
        indicata (1:scala), entra in un unico foglio di dimensione_foglio_mm,
        in uno qualsiasi dei due orientamenti (verticale/orizzontale).
        """
        larghezza_mm = extent.width() * 1000 / scala
        altezza_mm = extent.height() * 1000 / scala
        foglio_a, foglio_b = dimensione_foglio_mm
        entra_verticale = larghezza_mm <= foglio_a and altezza_mm <= foglio_b
        entra_orizzontale = larghezza_mm <= foglio_b and altezza_mm <= foglio_a
        return entra_verticale or entra_orizzontale

    def find_scala_tavola2(self, extent, scale_sequence=None, dimensione_foglio_mm=FOGLIO_A0_MM):
        """
        Tavola 2: trova la prima scala della sequenza (dalla piu' dettagliata
        alla piu' larga) che fa entrare l'extent in un unico foglio A0.
        Se nessuna scala della sequenza basta, ritorna l'ultima (la piu'
        larga) come fallback, perche' e' comunque la stampa piu' compatta
        disponibile.
        """
        sequenza = scale_sequence or SCALE_SEQUENCE_TAVOLA2
        for scala in sequenza:
            if self.entra_in_foglio(extent, scala, dimensione_foglio_mm):
                return scala
        return sequenza[-1]

    def generate_rilievo_per_fasi(self, layers, fasi_selezionate, scala=None, singola_fase=False):
        """
        Genera il layout "rilievo complessivo per fasi" (tutte le fasi
        selezionate in un'unica tavola) o, se singola_fase=True, una tavola
        per ciascuna fase con le altre US in grigio di contesto (sezione 7).
        """
        project = QgsProject.instance()
        layouts = []

        # `fase` e' un campo testo libero sulla scheda US (non un id):
        # fasi_selezionate/fasi_da_generare contengono nomi di fase, non
        # identificativi numerici.
        fasi_da_generare = fasi_selezionate if singola_fase else [None]
        for fase_nome in fasi_da_generare:
            layout = QgsPrintLayout(project)
            layout.initializeDefaults()
            layout.setName(self._layout_name(fase_nome))

            # TODO: aggiungere QgsLayoutItemMap collegato all'extent del
            # cantiere, applicare la scala (calcolata o quella passata),
            # aggiungere il riquadro di ingombro e l'elenco US a margine
            # (elenco generato da query, non da compilazione manuale).
            self._add_map_item(layout, layers, scala)
            self._add_ingombro_e_elenco(layout, layers, fase_nome)

            project.layoutManager().addLayout(layout)
            layouts.append(layout)

        return layouts

    def generate_rilievo_dettaglio_1_20(self, layers, us_attive):
        """
        Sezione 8: rilievo 1:20 con quote e caratterizzazioni QGIS. Mostra
        solo le quote delle US attive, salvo forzatura manuale dell'operatore.
        """
        layout = QgsPrintLayout(QgsProject.instance())
        layout.initializeDefaults()
        layout.setName("Rilievo di dettaglio 1_20")

        quota_layer = layers.get("rilievo_quota")
        if quota_layer is not None:
            # id sono uuid: subset string quotato, non elenco di interi.
            ids_attive = ",".join(f"'{u['id']}'" for u in us_attive) or "'00000000-0000-0000-0000-000000000000'"
            quota_layer.setSubsetString(f"us_id IN ({ids_attive})")

        self._add_map_item(layout, layers, scala=20)
        QgsProject.instance().layoutManager().addLayout(layout)
        return layout

    def export_pdf(self, layout, output_path):
        exporter = QgsLayoutExporter(layout)
        settings = QgsLayoutExporter.PdfExportSettings()
        result = exporter.exportToPdf(output_path, settings)
        if result != QgsLayoutExporter.Success:
            raise RuntimeError(f"Esportazione PDF non riuscita (codice {result}).")

    # ------------------------------------------------------------------
    def _layout_name(self, fase_nome):
        return f"Rilievo fase {fase_nome}" if fase_nome else "Rilievo complessivo per fasi"

    def _add_map_item(self, layout, layers, scala):
        # TODO: QgsLayoutItemMap(layout), impostare extent, layer visibili
        # e scala (layout.referenceMap().setScale(scala) o equivalente).
        pass

    def _add_ingombro_e_elenco(self, layout, layers, fase_nome):
        # TODO: QgsLayoutItemLabel/QgsLayoutItemHtml per il riquadro di
        # ingombro (bounding box + coordinate) e l'elenco tabellare delle
        # US rappresentate (numero_us, fase, definizione), generato dal
        # contenuto del layer -- mai da compilazione manuale (sezione 7).
        pass
