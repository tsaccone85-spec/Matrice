"""
Export punti di progettazione (trincee, saggi, carotaggi) in CSV per
l'importazione su ricevitori GNSS (Stonex, Topcon) e il successivo
stakeout sul campo.

Formato: PNEZD (Punto, Nord, Est, Quota, Descrizione) con riga di
intestazione -- lo standard de facto riconosciuto dai controller dei due
brand (MAGNET Field di Topcon, Cube-a/SurvCE di Stonex), verificato non
avere un'unica convenzione fissata sull'ordine Nord/Est tra i due: la
riga di intestazione serve proprio a questo, l'operatore mappa le
colonne nella finestra di import del controller (entrambi i software la
supportano) invece di doverne indovinare l'ordine a priori.

Coordinate nel sistema di riferimento metrico personale dell'utente
loggato (core/sistema_riferimento.py, lo stesso su cui si digitalizza in
QGIS), non WGS84: un ricevitore GNSS per lo stakeout si aspetta
Nord/Est in un sistema piano, non lat/lon in gradi.

Quota: assente per trincee/saggi (nessuna colonna Z su quelle tabelle,
poligoni digitalizzati in piano, vedi trincea_progettazione/
saggio_progettazione) -- riportata vuota, non azzerata: un controller
dovrebbe capire "misura sul campo" invece di stakeout a una quota 0 che
non significa nulla. Per i carotaggi si usa invece quota_slm se gia'
nota.
"""

import csv

from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject

# key in LAYER_SOURCES -> (prefisso nome punto, campo del nome/numero, codice)
_LAYER_EXPORT = {
    "trincea_progettazione": ("nome", "trincea"),
    "saggio_progettazione": ("nome", "saggio"),
}


def _vertici_poligono(geometria):
    """
    Vertici del primo anello esterno di un poligono, SENZA il vertice di
    chiusura (identico al primo -- vedi core/rettangolo_progettazione.py
    crea_geometria_rettangolo, che lo aggiunge sempre per chiudere l'anello).
    """
    poligono = geometria.asPolygon()
    if not poligono:
        return []
    anello = poligono[0]
    if len(anello) > 1 and anello[0] == anello[-1]:
        anello = anello[:-1]
    return anello


def genera_righe(cantiere_id, layer_manager, epsg_destinazione):
    """
    Righe (Punto, Nord, Est, Quota, Descrizione) per tutti i vertici di
    trincee/saggi e i punti dei carotaggi del cantiere aperto, riproiettate
    da EPSG:4326 (sistema di archiviazione di Matrice, sezione 5) a
    `epsg_destinazione` (il sistema di riferimento personale dell'utente).
    """
    crs_wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
    crs_destinazione = QgsCoordinateReferenceSystem(f"EPSG:{epsg_destinazione}")
    trasforma = QgsCoordinateTransform(crs_wgs84, crs_destinazione, QgsProject.instance())

    layers = layer_manager.current_layers(cantiere_id)
    righe = []

    for chiave, (campo_nome, codice) in _LAYER_EXPORT.items():
        layer = layers.get(chiave)
        if layer is None:
            continue
        for feature in layer.getFeatures():
            geometria = feature.geometry()
            if geometria is None or geometria.isEmpty():
                continue
            nome_base = feature[campo_nome] or codice.capitalize()
            for indice, vertice in enumerate(_vertici_poligono(geometria), start=1):
                punto = trasforma.transform(vertice)
                righe.append({
                    "punto": f"{nome_base}-V{indice}",
                    "nord": punto.y(),
                    "est": punto.x(),
                    "quota": "",
                    "descrizione": codice,
                })

    layer_carotaggio = layers.get("carotaggio")
    if layer_carotaggio is not None:
        for feature in layer_carotaggio.getFeatures():
            geometria = feature.geometry()
            if geometria is None or geometria.isEmpty():
                continue
            punto = trasforma.transform(geometria.asPoint())
            quota_slm = feature["quota_slm"] if "quota_slm" in feature.fields().names() else None
            righe.append({
                "punto": f"Carotaggio-{feature['numero_carotaggio']}",
                "nord": punto.y(),
                "est": punto.x(),
                "quota": quota_slm if quota_slm not in (None, "") else "",
                "descrizione": "carotaggio",
            })

    return righe


def scrivi_csv(path, righe):
    """
    Scrive `righe` (vedi genera_righe) in CSV PNEZD con intestazione,
    virgola come separatore (riconosciuto da entrambi i controller, a
    differenza del punto e virgola usato da Excel in locale italiana).
    """
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Punto", "Nord", "Est", "Quota", "Descrizione"])
        for riga in righe:
            writer.writerow([riga["punto"], riga["nord"], riga["est"], riga["quota"], riga["descrizione"]])
