"""
Anteprime foto nella tabella attributi / form dei layer (2026-10-27).

Le colonne foto_*_url / file_url dei layer contengono solo il percorso relativo
(`<cantiere_id>/<id>.jpg`) del file su R2: QGIS non sa aprirlo da solo. Qui le
foto vengono scaricate in una cartella temporanea locale (stesso percorso
relativo) e il campo viene presentato con il widget "Allegato" di QGIS
(ExternalResource, visualizzatore Immagine) con radice = quella cartella: cosi'
il valore resta il percorso relativo (nessuna scrittura diversa su Matrice) e
QGIS mostra l'anteprima.

Il download gira in un QgsTask (thread separato): in thread principale si
leggono solo i percorsi, nel thread si usa solo `urllib` (matrice_api).
"""

import os
import tempfile

from qgis.core import Qgis, QgsApplication, QgsEditorWidgetSetup, QgsMessageLog, QgsTask

CARTELLA_CACHE = os.path.join(tempfile.gettempdir(), "matrice_foto_cache")
ESTENSIONI_OK = (".jpg", ".jpeg", ".png", ".webp")


def _setup_widget():
    # ExternalResource: RelativeStorage 2 = relativo a DefaultRoot; DocumentViewer 1 = immagine.
    return QgsEditorWidgetSetup("ExternalResource", {
        "DefaultRoot": CARTELLA_CACHE,
        "RelativeStorage": 2,
        "DocumentViewer": 1,
        "DocumentViewerHeight": 160,
        "DocumentViewerWidth": 160,
        "FileWidget": False,
        "FileWidgetButton": False,
        "StorageMode": 0,
    })


def applica_widget_foto(layer, campi_foto):
    """Imposta il widget Allegato (anteprima immagine) sui campi foto del layer."""
    for nome in campi_foto:
        indice = layer.fields().indexFromName(nome)
        if indice != -1:
            layer.setEditorWidgetSetup(indice, _setup_widget())


def _percorso_locale(relativo):
    return os.path.join(CARTELLA_CACHE, *relativo.split("/"))


def precarica_foto(layer, campi_foto, matrice_api, access_token):
    """
    Scarica in cache le foto referenziate dal layer che non ci sono ancora e,
    a fine download, ridisegna il layer (le anteprime compaiono da sole).
    """
    indici = [layer.fields().indexFromName(c) for c in campi_foto]
    indici = [i for i in indici if i != -1]
    if not indici:
        return
    percorsi = set()
    for feature in layer.getFeatures():
        for i in indici:
            valore = feature[i]
            if isinstance(valore, str) and valore.lower().endswith(ESTENSIONI_OK) and ".." not in valore:
                if not os.path.exists(_percorso_locale(valore)):
                    percorsi.add(valore)
    if not percorsi:
        return

    errori = []

    def _scarica(task):
        for n, rel in enumerate(sorted(percorsi)):
            if task.isCanceled():
                return False
            dati, errore = matrice_api.scarica_foto(rel, access_token)
            if dati is None:
                errori.append(f"{rel}: {errore}")
                continue
            destinazione = _percorso_locale(rel)
            os.makedirs(os.path.dirname(destinazione), exist_ok=True)
            with open(destinazione, "wb") as f:
                f.write(dati)
            task.setProgress(100.0 * (n + 1) / len(percorsi))
        return True

    def _fine(eccezione, risultato=None):
        # Thread principale: si ridisegna soltanto (il layer puo' essere gia' stato rimosso).
        try:
            layer.triggerRepaint()
            layer.dataProvider().reloadData()
        except (RuntimeError, AttributeError):
            pass
        for riga in errori:
            QgsMessageLog.logMessage(f"Foto non scaricata - {riga}", "Matrice", Qgis.Warning)

    task = QgsTask.fromFunction("Matrice: scarico anteprime foto", _scarica, on_finished=_fine)
    QgsApplication.taskManager().addTask(task)
