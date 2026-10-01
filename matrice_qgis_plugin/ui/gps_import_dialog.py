"""
Dialogo "Importa rilievo GPS" (sezione 4, passo 3): l'operatore sceglie un
file di esportazione del ricevitore GPS differenziale (CSV, GPX, SHP o
DXF), vede subito quali punti sono stati abbinati a una US e quali no, e
scrive su Matrice solo dopo aver premuto "Conferma" -- mai in automatico
al solo apertura/analisi del file (sezione 4: nessuna scrittura senza
conferma esplicita).

Il formato del file e' riconosciuto dall'estensione, cosi' l'operatore non
deve saperlo indicare a mano; il "campo con il codice del punto" si chiede
solo per SHP/DXF (per CSV e GPX e' fisso: colonna codice/point_id per il
CSV, campo "name" del waypoint per il GPX -- core/gps_import.py), con un
valore proposto gia' compilato che nella maggior parte dei casi va bene
cosi'.
"""

import os

from qgis.PyQt import uic
from qgis.PyQt.QtWidgets import QDialog, QFileDialog, QListWidgetItem, QTableWidgetItem

from ..core.gps_import import ENTITA_CAROTAGGIO, ENTITA_COLONNA, ENTITA_SEZIONE, ENTITA_US

FORM_CLASS, _ = uic.loadUiType(os.path.join(os.path.dirname(__file__), "gps_import_dialog.ui"))

FORMATO_PER_ESTENSIONE = {".csv": "csv", ".gpx": "gpx", ".shp": "shp", ".dxf": "dxf"}
CAMPO_CODICE_DEFAULT = {"shp": "codice", "dxf": "Text"}
ETICHETTA_FORMATO = {"csv": "CSV", "gpx": "GPX (waypoint)", "shp": "Shapefile", "dxf": "DXF"}

# Etichetta mostrata nel menu "Abbinare i punti a" -> valore di `entita`
# passato a core/gps_import.py. Colonne stratigrafiche, carotaggi e
# sezioni/prospetti NON hanno nessun legame con una US: il codice del
# punto e' il loro numero proprio, non il numero US (vedi
# gps_import._codici_cantiere).
OPZIONI_ENTITA = (
    ("US (rilievo vettoriale / quote)", ENTITA_US),
    ("Colonna stratigrafica", ENTITA_COLONNA),
    ("Carotaggio", ENTITA_CAROTAGGIO),
    ("Sezione/prospetto", ENTITA_SEZIONE),
)


class GpsImportDialog(QDialog, FORM_CLASS):
    def __init__(self, parent, gps_importer, cantiere_id):
        super().__init__(parent)
        self.setupUi(self)
        self.gps_importer = gps_importer
        self.cantiere_id = cantiere_id
        self._risultato = None

        for etichetta, valore in OPZIONI_ENTITA:
            self.entitaCombo.addItem(etichetta, valore)
        self.entitaCombo.currentIndexChanged.connect(self._on_entita_cambiata)
        self._on_entita_cambiata(0)

        self.browseButton.clicked.connect(self._on_browse)
        self.analyzeButton.clicked.connect(self._on_analyze)
        self.confirmButton.clicked.connect(self._on_confirm)
        self.buttonBox.rejected.connect(self.reject)

        self.confirmButton.setEnabled(False)
        self.campoCodiceEdit.setEnabled(False)
        self.statusLabel.setText("")

    # ------------------------------------------------------------------
    def _on_entita_cambiata(self, _indice):
        """Cambiare il tipo di abbinamento invalida un'eventuale analisi gia' fatta."""
        etichetta, _valore = OPZIONI_ENTITA[self.entitaCombo.currentIndex()]
        self.matchesLabel.setText(f"Punti abbinati a: {etichetta}")
        self._risultato = None
        self.confirmButton.setEnabled(False)
        self.matchesTable.setRowCount(0)
        self.unmatchedListWidget.clear()
        self._set_status("", ok=None)

    def _on_browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Seleziona il file del rilievo GPS",
            "",
            "File rilievo GPS (*.csv *.gpx *.shp *.dxf);;Tutti i file (*)",
        )
        if not path:
            return

        formato = self._formato_da_percorso(path)
        self.fileEdit.setText(path)
        self.formatoLabel.setText(ETICHETTA_FORMATO.get(formato, "Non riconosciuto"))
        self.campoCodiceEdit.setEnabled(formato in ("shp", "dxf"))
        self.campoCodiceEdit.setText(CAMPO_CODICE_DEFAULT.get(formato, ""))

        self._risultato = None
        self.confirmButton.setEnabled(False)
        self.matchesTable.setRowCount(0)
        self.unmatchedListWidget.clear()
        self._set_status("", ok=None)

    def _on_analyze(self):
        path = self.fileEdit.text().strip()
        if not path:
            self._set_status("Selezionare prima un file.", ok=False)
            return

        formato = self._formato_da_percorso(path)
        if formato is None:
            self._set_status("Formato file non riconosciuto: usare CSV, GPX, SHP o DXF.", ok=False)
            return

        self._set_status("Lettura del file in corso...", ok=None)
        try:
            risultato = self._analizza(path, formato)
        except Exception as exc:
            self._risultato = None
            self.confirmButton.setEnabled(False)
            self.matchesTable.setRowCount(0)
            self.unmatchedListWidget.clear()
            self._set_status(self._messaggio_semplice(exc), ok=False)
            return

        self._risultato = risultato
        self._popola_risultati(risultato)

        if risultato.campo_codice_non_trovato:
            self._set_status(
                f"Il campo '{risultato.campo_codice_non_trovato}' non esiste in questo file: "
                "controllare il nome del campo che contiene il codice del punto, sopra.",
                ok=False,
            )
            self.confirmButton.setEnabled(False)
            return

        n_match = len(risultato.matches)
        n_unmatched = len(risultato.unmatched)
        if n_match == 0:
            self._set_status(
                f"Nessun punto abbinato (letti {n_match + n_unmatched} punti dal file).", ok=False
            )
        else:
            self._set_status(f"{n_match} punti abbinati, {n_unmatched} senza corrispondenza.", ok=True)
        self.confirmButton.setEnabled(n_match > 0)

    def _on_confirm(self):
        if not self._risultato or not self._risultato.matches:
            return
        try:
            self.gps_importer.apply_matches(self._risultato.matches, entita=self._risultato.entita)
        except Exception as exc:
            self._set_status(self._messaggio_semplice(exc), ok=False)
            return

        self._set_status(
            f"Scritti su Matrice {len(self._risultato.matches)} punti. Si puo' chiudere questa finestra.",
            ok=True,
        )
        self.confirmButton.setEnabled(False)

    # ------------------------------------------------------------------
    def _analizza(self, path, formato):
        campo_codice = self.campoCodiceEdit.text().strip() or CAMPO_CODICE_DEFAULT.get(formato)
        entita = self.entitaCombo.currentData()
        if formato == "csv":
            return self.gps_importer.import_csv(path, self.cantiere_id, entita=entita)
        if formato == "gpx":
            return self.gps_importer.import_gpx(path, self.cantiere_id, entita=entita)
        if formato == "shp":
            return self.gps_importer.import_shp(path, self.cantiere_id, campo_codice=campo_codice, entita=entita)
        return self.gps_importer.import_dxf(path, self.cantiere_id, campo_codice=campo_codice, entita=entita)

    def _popola_risultati(self, risultato):
        self.matchesTable.setRowCount(0)
        for riga in risultato.matches:
            posizione = self.matchesTable.rowCount()
            self.matchesTable.insertRow(posizione)
            self.matchesTable.setItem(posizione, 0, QTableWidgetItem(str(riga.get("codice_punto") or "")))
            self.matchesTable.setItem(posizione, 1, QTableWidgetItem(f"{riga['x']:.3f}"))
            self.matchesTable.setItem(posizione, 2, QTableWidgetItem(f"{riga['y']:.3f}"))
            quota = riga.get("quota")
            self.matchesTable.setItem(posizione, 3, QTableWidgetItem(f"{quota:.3f}" if quota is not None else "--"))

        self.unmatchedListWidget.clear()
        for riga in risultato.unmatched:
            etichetta = riga.get("codice_punto") or "(punto senza codice)"
            self.unmatchedListWidget.addItem(QListWidgetItem(etichetta))

    def _formato_da_percorso(self, path):
        estensione = os.path.splitext(path)[1].lower()
        return FORMATO_PER_ESTENSIONE.get(estensione)

    def _messaggio_semplice(self, exc):
        """
        Mai un errore tecnico grezzo mostrato all'operatore (principio
        generale del plugin -- sezione 2/8 della specifica). RuntimeError
        arriva gia' in linguaggio semplice da core/gps_import.py; per il
        resto si distinguono solo i casi piu' comuni.
        """
        if isinstance(exc, RuntimeError):
            return str(exc)
        if isinstance(exc, FileNotFoundError):
            return "File non trovato: verificare il percorso indicato."
        if isinstance(exc, (KeyError, ValueError)):
            return "Il file non e' nel formato atteso: controllare le colonne o i campi richiesti."
        return "Impossibile leggere il file. Verificare che sia nel formato corretto e riprovare."

    def _set_status(self, message, ok):
        colore = "#1b7f3a" if ok is True else ("#5f6368" if ok is None else "#b3261e")
        self.statusLabel.setStyleSheet(f"color: {colore};")
        self.statusLabel.setText(message)
