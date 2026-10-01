"""
Importazione file GPS differenziale e abbinamento punto-elemento (sezione
4, passo 3 -- Iterazione 2 della roadmap, sezione 11).

Formati supportati: CSV con coordinate, GPX (waypoint), SHP e DXF di
post-processamento. GPX/SHP/DXF sono letti con il driver OGR gia'
integrato in QGIS (nessuna libreria aggiuntiva da installare): lo stesso
principio usato per i layer PostGIS in core/layer_manager.py, applicato
qui a file locali invece che al database.

A cosa si abbina un punto dipende da `entita` (scelta dall'operatore nel
dialogo, sezione 4): per i punti naturalmente legati a una unita'
stratigrafica (rilievo vettoriale/quote), il codice del punto e' il
numero US. Colonne stratigrafiche e carotaggi NON hanno nessun legame con
una US: ogni punto va abbinato alla singola colonna/carotaggio con lo
stesso numero, e se quella colonna/quel carotaggio aveva gia' una
posizione, l'importazione la sostituisce (QGIS sovrascrive Matrice).
"""

import csv

ENTITA_US = "us"
ENTITA_COLONNA = "colonna_stratigrafica"
ENTITA_CAROTAGGIO = "carotaggio"


class GpsImportResult:
    def __init__(self, entita=ENTITA_US):
        self.entita = entita
        self.matches = []      # [{"codice_punto", "x", "y", "id_target"}]
        self.unmatched = []    # punti del file senza una corrispondenza
        # Valorizzato solo da _import_da_layer (GPX/SHP/DXF) quando il
        # campo indicato per il codice punto non esiste nel file: aiuta
        # l'operatore a capire perche' tutti i punti sono "unmatched".
        self.campo_codice_non_trovato = None


class GpsImporter:
    def __init__(self, connection, matrice_api, session):
        # `connection`: connessione diretta (core/db_connector.py), usata
        # solo per scrivere i punti confermati (apply_matches) -- stesso
        # limite tecnico del provider "postgres" di QGIS gia' documentato
        # in db_connector.py.
        # `matrice_api` + `session`: lettura degli elenchi codice/numero
        # filtrati per l'utente loggato, via PostgREST (core/matrice_api.py),
        # non con una query diretta.
        self.connection = connection
        self.matrice_api = matrice_api
        self.session = session

    # ------------------------------------------------------------------
    # CSV -- coordinate gia' in formato tabellare (colonne x/y + codice)
    # ------------------------------------------------------------------
    def import_csv(self, file_path, cantiere_id, entita=ENTITA_US):
        """
        Legge un CSV di esportazione del ricevitore GPS differenziale e
        propone un abbinamento basato sul codice/etichetta del punto
        rilevato. La scrittura su Matrice avviene solo dopo conferma
        manuale dell'operatore (sezione 4).
        """
        result = GpsImportResult(entita=entita)
        codici = self._codici_cantiere(cantiere_id, entita)

        with open(file_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                codice_punto = row.get("codice") or row.get("point_id")
                x, y = float(row["x"]), float(row["y"])
                id_target = codici.get(codice_punto)
                entry = {"codice_punto": codice_punto, "x": x, "y": y, "id_target": id_target}
                if id_target:
                    result.matches.append(entry)
                else:
                    result.unmatched.append(entry)
        return result

    # ------------------------------------------------------------------
    # GPX -- waypoint del ricevitore GPS (etichetta nel campo "name")
    # ------------------------------------------------------------------
    def import_gpx(self, file_path, cantiere_id, campo_codice="name", entita=ENTITA_US):
        """
        Legge i waypoint di un file GPX (sublayer "waypoints" del driver
        OGR): ogni waypoint ha un'etichetta nel campo `name`, di norma
        gia' coincidente con il codice/numero dell'elemento rilevato sul
        campo, secondo la convenzione di etichettatura del cantiere.
        """
        from qgis.core import QgsVectorLayer

        layer = QgsVectorLayer(f"{file_path}|layername=waypoints", "gps_import_gpx", "ogr")
        if not layer.isValid():
            raise RuntimeError(
                "Impossibile leggere il file GPX: verificare che contenga dei waypoint "
                "(punti), non solo tracce o percorsi."
            )
        return self._import_da_layer(layer, cantiere_id, campo_codice, entita=entita)

    # ------------------------------------------------------------------
    # SHP -- shapefile di post-processamento del rilievo differenziale
    # ------------------------------------------------------------------
    def import_shp(self, file_path, cantiere_id, campo_codice="codice", entita=ENTITA_US):
        """
        Legge uno shapefile puntuale post-processato. `campo_codice` e' il
        nome della colonna dello shapefile che contiene l'etichetta del
        punto (varia da un ricevitore/software all'altro: l'operatore la
        indica al momento dell'importazione, sezione 4).
        """
        from qgis.core import QgsVectorLayer

        layer = QgsVectorLayer(file_path, "gps_import_shp", "ogr")
        if not layer.isValid():
            raise RuntimeError(
                "Impossibile leggere il file SHP: verificare che il percorso sia corretto "
                "e che tutti i file collegati (.dbf, .shx, .prj) siano nella stessa cartella."
            )
        return self._import_da_layer(layer, cantiere_id, campo_codice, entita=entita)

    # ------------------------------------------------------------------
    # DXF -- disegno di post-processamento (punti/blocchi con etichetta)
    # ------------------------------------------------------------------
    def import_dxf(self, file_path, cantiere_id, campo_codice="Text", entita=ENTITA_US):
        """
        Legge le entita' puntuali di un file DXF (sublayer "entities" del
        driver OGR). Il campo con l'etichetta del punto dipende da come e'
        stato esportato il DXF (spesso "Text" per un testo associato al
        punto, a volte "Layer"): l'operatore lo indica al momento
        dell'importazione, come per lo SHP.
        """
        from qgis.core import QgsVectorLayer

        layer = QgsVectorLayer(f"{file_path}|layername=entities", "gps_import_dxf", "ogr")
        if not layer.isValid():
            raise RuntimeError("Impossibile leggere il file DXF.")
        return self._import_da_layer(layer, cantiere_id, campo_codice, entita=entita)

    # ------------------------------------------------------------------
    def _import_da_layer(self, layer, cantiere_id, campo_codice, entita=ENTITA_US):
        """
        Logica comune a GPX/SHP/DXF una volta aperti come layer QGIS
        (via OGR): per il CSV non serve, perche' non e' un formato
        geometrico e le coordinate sono gia' colonne numeriche.
        """
        result = GpsImportResult(entita=entita)
        codici = self._codici_cantiere(cantiere_id, entita)
        nomi_campi = layer.fields().names()
        campo_presente = campo_codice in nomi_campi

        for feature in layer.getFeatures():
            geometria = feature.geometry()
            if geometria is None or geometria.isEmpty():
                continue
            # centroid() funziona anche per una geometria gia' puntuale
            # (ritorna il punto stesso), evitando di distinguere caso per
            # caso il tipo di geometria restituito dai vari driver OGR.
            punto = geometria.centroid().asPoint()
            codice_punto = str(feature[campo_codice]) if campo_presente and feature[campo_codice] else None
            id_target = codici.get(codice_punto) if codice_punto else None
            entry = {"codice_punto": codice_punto, "x": punto.x(), "y": punto.y(), "id_target": id_target}
            if id_target:
                result.matches.append(entry)
            else:
                result.unmatched.append(entry)

        if not campo_presente:
            # Non un errore bloccante: tutti i punti finiscono in
            # "unmatched" e l'operatore lo capisce dall'elenco senza
            # abbinamenti, ma vale la pena segnalarlo esplicitamente.
            result.campo_codice_non_trovato = campo_codice
        return result

    # ------------------------------------------------------------------
    def _codici_cantiere(self, cantiere_id, entita):
        """
        Mappa il codice/numero del punto rilevato sul campo all'id della
        riga Matrice a cui abbinarlo. Il significato del "codice" dipende
        da `entita`: per i punti legati a una US e' il numero US (per
        convenzione di etichettatura di cantiere: se il cantiere ne usa
        una diversa, l'abbinamento automatico produce piu' "unmatched" e
        l'operatore corregge a mano prima di confermare); per colonne
        stratigrafiche e carotaggi e' invece il loro numero proprio --
        non esiste nessun legame con una US per questi due casi.
        """
        token = self.session.access_token
        if entita == ENTITA_COLONNA:
            righe = self.matrice_api.list_colonne_stratigrafiche(cantiere_id, token)
            return {str(r["numero_colonna"]): r["id"] for r in righe}
        if entita == ENTITA_CAROTAGGIO:
            righe = self.matrice_api.list_carotaggi(cantiere_id, token)
            return {str(r["numero_carotaggio"]): r["id"] for r in righe}
        righe = self.matrice_api.list_us(cantiere_id, token, solo_attive=False)
        return {str(r["numero_us"]): r["id"] for r in righe}

    def apply_matches(self, matches, entita=ENTITA_US):
        """
        Scrive su Matrice solo i punti confermati dall'operatore (sezione 4).

        - entita="us": aggiorna la geometria del punto di rilievo
          vettoriale gia' legato a quella US (percorso storico, per i
          punti naturalmente associati a una unita' stratigrafica).
        - entita="colonna_stratigrafica"/"carotaggio": sovrascrive
          lat/lng della singola colonna/carotaggio con lo stesso numero.
          Non c'e' nessun legame con una US: se la colonna/il carotaggio
          aveva gia' una posizione, viene sostituita.
        """
        tabella_punto_diretto = {ENTITA_COLONNA: "colonna_stratigrafica", ENTITA_CAROTAGGIO: "carotaggio"}.get(
            entita
        )
        for match in matches:
            if tabella_punto_diretto:
                self.connection.execute(
                    f"UPDATE {tabella_punto_diretto} SET lat = %s, lng = %s WHERE id = %s;",
                    (match["y"], match["x"], match["id_target"]),
                )
            else:
                self.connection.execute(
                    """
                    UPDATE rilievo_geometria
                    SET geom = ST_SetSRID(ST_MakePoint(%s, %s), 4326)
                    WHERE us_id = %s AND tipo_geometria = 'punto';
                    """,
                    (match["x"], match["y"], match["id_target"]),
                )
