"""
Importazione file GPS differenziale e abbinamento punto-US (sezione 4,
passo 3 -- Iterazione 2 della roadmap, sezione 11).

Formati supportati: CSV con coordinate, GPX (waypoint), SHP e DXF di
post-processamento. GPX/SHP/DXF sono letti con il driver OGR gia'
integrato in QGIS (nessuna libreria aggiuntiva da installare): lo stesso
principio usato per i layer PostGIS in core/layer_manager.py, applicato
qui a file locali invece che al database.
"""

import csv


class GpsImportResult:
    def __init__(self):
        self.matches = []      # [{"codice_punto", "id_us", "geometry"}]
        self.unmatched = []    # punti del file senza una US corrispondente
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
        # `matrice_api` + `session`: lettura dell'elenco US filtrato per
        # l'utente loggato, via PostgREST (core/matrice_api.py), non con
        # una query diretta.
        self.connection = connection
        self.matrice_api = matrice_api
        self.session = session

    # ------------------------------------------------------------------
    # CSV -- coordinate gia' in formato tabellare (colonne x/y + codice)
    # ------------------------------------------------------------------
    def import_csv(self, file_path, cantiere_id):
        """
        Legge un CSV di esportazione del ricevitore GPS differenziale e
        propone un abbinamento punto-US basato sul codice/etichetta del
        punto rilevato. La scrittura su Matrice avviene solo dopo conferma
        manuale dell'operatore (sezione 4).
        """
        result = GpsImportResult()
        us_by_codice = self._us_codici_cantiere(cantiere_id)

        with open(file_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                codice_punto = row.get("codice") or row.get("point_id")
                x, y = float(row["x"]), float(row["y"])
                id_us = us_by_codice.get(codice_punto)
                entry = {"codice_punto": codice_punto, "x": x, "y": y, "id_us": id_us}
                if id_us:
                    result.matches.append(entry)
                else:
                    result.unmatched.append(entry)
        return result

    # ------------------------------------------------------------------
    # GPX -- waypoint del ricevitore GPS (etichetta nel campo "name")
    # ------------------------------------------------------------------
    def import_gpx(self, file_path, cantiere_id, campo_codice="name"):
        """
        Legge i waypoint di un file GPX (sublayer "waypoints" del driver
        OGR): ogni waypoint ha un'etichetta nel campo `name`, di norma
        gia' coincidente con il codice/numero della US rilevata sul
        campo, secondo la convenzione di etichettatura del cantiere.
        """
        from qgis.core import QgsVectorLayer

        layer = QgsVectorLayer(f"{file_path}|layername=waypoints", "gps_import_gpx", "ogr")
        if not layer.isValid():
            raise RuntimeError(
                "Impossibile leggere il file GPX: verificare che contenga dei waypoint "
                "(punti), non solo tracce o percorsi."
            )
        return self._import_da_layer(layer, cantiere_id, campo_codice)

    # ------------------------------------------------------------------
    # SHP -- shapefile di post-processamento del rilievo differenziale
    # ------------------------------------------------------------------
    def import_shp(self, file_path, cantiere_id, campo_codice="codice"):
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
        return self._import_da_layer(layer, cantiere_id, campo_codice)

    # ------------------------------------------------------------------
    # DXF -- disegno di post-processamento (punti/blocchi con etichetta)
    # ------------------------------------------------------------------
    def import_dxf(self, file_path, cantiere_id, campo_codice="Text"):
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
        return self._import_da_layer(layer, cantiere_id, campo_codice)

    # ------------------------------------------------------------------
    def _import_da_layer(self, layer, cantiere_id, campo_codice):
        """
        Logica comune a GPX/SHP/DXF una volta aperti come layer QGIS
        (via OGR): per il CSV non serve, perche' non e' un formato
        geometrico e le coordinate sono gia' colonne numeriche.
        """
        result = GpsImportResult()
        us_by_codice = self._us_codici_cantiere(cantiere_id)
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
            id_us = us_by_codice.get(codice_punto) if codice_punto else None
            entry = {"codice_punto": codice_punto, "x": punto.x(), "y": punto.y(), "id_us": id_us}
            if id_us:
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
    def _us_codici_cantiere(self, cantiere_id):
        """
        Mappa il codice punto del rilievo GPS (etichetta di campagna)
        all'id della US in Matrice. La corrispondenza si basa sul numero
        US come da convenzione di etichettatura standard del cantiere; se
        il cantiere ne usa una diversa, l'abbinamento automatico produce
        piu' "unmatched" e l'operatore corregge a mano prima di
        confermare (sezione 4).
        """
        rows = self.matrice_api.list_us(cantiere_id, self.session.access_token, solo_attive=False)
        return {str(row["numero_us"]): row["id"] for row in rows}

    def apply_matches(self, matches, table="rilievo_geometria"):
        """
        Scrive su Matrice solo i punti confermati dall'operatore. Un match
        del GPS differenziale corrisponde sempre a un nuovo punto quotato o
        a un aggiornamento della geometria puntuale gia' associata alla US
        (sezione 4, passo 3): qui si aggiorna la geometria della riga
        rilievo_geometria di tipo punto gia' legata a quella US.
        """
        for match in matches:
            self.connection.execute(
                f"""
                UPDATE {table}
                SET geom = ST_SetSRID(ST_MakePoint(%s, %s), 4326)
                WHERE us_id = %s AND tipo_geometria = 'punto';
                """,
                (match["x"], match["y"], match["id_us"]),
            )
