"""
Sincronizzazione e salvataggio automatico verso Matrice (sezione 5).

Non esiste un pulsante "Sincronizza": il plugin intercetta l'evento nativo
di QGIS `committed` (fine sessione di editing di un layer) e ne instrada
il risultato verso Matrice invece che su file locale. Ogni scrittura
genera una riga in us_storico_modifiche per la tracciabilita' (sezione 5).

Questo handler copre i layer con geometria PostGIS vera e propria
(rilievo_geometria, rilievo_quota, tabelle reali dietro v_rilievo_geometria_qgis).
I layer puntuali con lat/lng (carotaggio, colonna_stratigrafica,
sezione_prospetto) usano invece core/point_position_sync.py, perche' li'
la regola di business (quota manuale vs. ricalcolo da DEM) e' diversa.
"""

import datetime

# Valori ammessi da us_storico_modifiche_tabella_check (verificato in produzione).
TABELLE_CON_STORICO = frozenset((
    "us", "rilievo_geometria", "rilievo_quota", "sepoltura",
    "carotaggio", "colonna_stratigrafica", "sezione_prospetto",
    # Layer Progettazione (migrazione 0085)
    "trincea_progettazione", "saggio_progettazione", "carotaggio_progettazione",
    "survey_ur", "survey_ritrovamento", "buffer_progettazione",
    "progettazione_cropmark", "progettazione_vincolo",
))

class SyncHandler:
    def __init__(self, connection, session):
        self.connection = connection
        self.session = session
        self._watched_layers = []

    def watch_layer(self, layer, table_name=None):
        """
        Collega i segnali di editing del layer alla scrittura su Matrice.

        `table_name` e' la tabella reale su cui scrivere (rilievo_geometria
        o rilievo_quota): la vista v_rilievo_geometria_qgis e' di sola
        lettura per i campi extra (numero_us, definizione, fase, ...), le
        scritture vanno sulla tabella base. Se omesso, si usa il nome del
        layer (comportamento di fallback per i layer che coincidono gia'
        con la tabella, es. rilievo_quota).
        """
        table = table_name or layer.name()
        layer.committedGeometriesChanges.connect(
            lambda layer_id, changed: self._on_geometries_changed(layer, table, changed)
        )
        layer.committedAttributeValuesChanges.connect(
            lambda layer_id, changed: self._on_attributes_changed(layer, table, changed)
        )
        layer.committedFeaturesAdded.connect(
            lambda layer_id, added: self._on_features_added(layer, table, added)
        )
        self._watched_layers.append(layer)

    def disconnect_all(self):
        for layer in self._watched_layers:
            try:
                layer.committedGeometriesChanges.disconnect()
                layer.committedAttributeValuesChanges.disconnect()
                layer.committedFeaturesAdded.disconnect()
            except (RuntimeError, TypeError):
                pass  # layer gia' rimosso dal progetto
        self._watched_layers = []

    # ------------------------------------------------------------------
    # Handler dei segnali QGIS -> scrittura su Matrice + us_storico_modifiche
    # ------------------------------------------------------------------
    def _on_geometries_changed(self, layer, table, changed_geometries):
        key_field = "id_geom" if table == "rilievo_geometria" else "id"
        for fid, geometry in changed_geometries.items():
            feature = layer.getFeature(fid)
            id_record = feature[key_field] if key_field in feature.fields().names() else fid
            wkt = self._wkt_4326(layer, geometry)

            valore_precedente = self._read_current_geom(table, key_field, id_record)
            self.connection.execute(
                f"UPDATE {table} SET geom = ST_GeomFromText(%s, 4326) WHERE {key_field} = %s;",
                (wkt, id_record),
            )
            self._log_modifica(table, id_record, "geometria", valore_precedente, wkt, "aggiornamento")

    @staticmethod
    def _wkt_4326(layer, geometry):
        """
        WKT della geometria in EPSG:4326, come si salva su Matrice. Il layer
        «Rilievo vettoriale» puo' essere nel sistema dei dati di scavo (UTM...):
        va riproiettato prima della scrittura, altrimenti si scriverebbero
        metri come gradi.
        """
        from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsGeometry, QgsProject
        crs_layer = layer.crs()
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        if not crs_layer.isValid() or crs_layer == wgs84:
            return geometry.asWkt()
        copia = QgsGeometry(geometry)
        if copia.transform(QgsCoordinateTransform(crs_layer, wgs84, QgsProject.instance())) != 0:
            raise ValueError("Impossibile riproiettare la geometria in EPSG:4326: modifica non salvata.")
        return copia.asWkt()

    def _on_attributes_changed(self, layer, table, changed_values):
        key_field = "id_geom" if table == "rilievo_geometria" else "id"
        for fid, changes in changed_values.items():
            feature = layer.getFeature(fid)
            id_record = feature[key_field] if key_field in feature.fields().names() else fid
            for field_index, new_value in changes.items():
                # QgsUnsetAttributeValue: campo non toccato (QGIS >= 3.28 lo
                # passa nelle modifiche multiple), psycopg2 non lo sa adattare.
                if type(new_value).__name__ == "QgsUnsetAttributeValue":
                    continue
                field_name = layer.fields().at(field_index).name()
                # I campi "di vista" (numero_us, definizione, fase, periodo,
                # sepoltura_id, numero_tomba) sono di sola lettura: arrivano
                # dal join in v_rilievo_geometria_qgis e non esistono sulla
                # tabella rilievo_geometria. Si scrivono solo i campi propri.
                self.connection.execute(
                    f"UPDATE {table} SET {field_name} = %s WHERE {key_field} = %s;",
                    (new_value, id_record),
                )
                self._log_modifica(table, id_record, field_name, None, new_value, "aggiornamento")

    def _on_features_added(self, layer, table, added_features):
        """
        Passo 4 (join geometria-dato): una geometria disegnata ex novo richiede
        sempre una US di appartenenza gia' selezionata in UI prima del commit
        (validazione lato dock widget, non qui) -- qui si assume gia' presente
        nell'attributo us_id della feature.
        """
        # Layer senza campo us_id (es. Progettazione, Aree): niente
        # vincolo di appartenenza, si registra solo la creazione.
        ha_us_id = layer.fields().indexOf("us_id") >= 0
        for feature in added_features:
            if ha_us_id and not feature["us_id"]:
                raise ValueError(
                    "Geometria senza US di appartenenza: selezionare la US prima di salvare."
                )
            # record_id e' uuid: feature.id() e' solo l'fid intero del provider
            # (DatatypeMismatch), serve il valore della chiave del record.
            id_record = self._uuid_record(layer, feature, "id_geom" if table == "rilievo_geometria" else "id")
            if id_record is None:
                continue  # chiave non ancora nota (generata dal DB): niente riga di storico
            self._log_modifica(table, id_record, "creazione", None, "nuova geometria", "creazione")

    @staticmethod
    def _uuid_record(layer, feature, key_field):
        """Valore uuid (str) della chiave del record, o None se assente/non valido."""
        import uuid
        if layer.fields().indexOf(key_field) < 0:
            return None
        valore = feature[key_field]
        try:
            return str(uuid.UUID(str(valore)))
        except (ValueError, AttributeError, TypeError):
            return None

    # ------------------------------------------------------------------
    def _read_current_geom(self, table, key_field, id_record):
        """
        Legge la geometria come si trova su Matrice PRIMA dell'update, per
        popolare correttamente valore_precedente in us_storico_modifiche
        (sezione 5): senza questa lettura il log riporterebbe sempre
        valore_precedente=NULL, perdendo la tracciabilita' delle modifiche.
        """
        righe = self.connection.execute_returning(
            f"SELECT ST_AsText(geom) AS geom_wkt FROM {table} WHERE {key_field} = %s;",
            (id_record,),
        )
        if not righe:
            return None
        return righe[0]["geom_wkt"]

    def _log_modifica(self, tabella, record_id, campo, valore_precedente, valore_nuovo, tipo_operazione):
        if tabella not in TABELLE_CON_STORICO:
            return  # fuori dal check us_storico_modifiche_tabella_check (es. layer Progettazione)
        self.connection.execute(
            """
            INSERT INTO us_storico_modifiche
                (cantiere_id, tabella, record_id, utente_id, campo, valore_precedente, valore_nuovo, tipo_operazione)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
            """,
            (
                self.session.cantiere_corrente_id,
                tabella,
                record_id,
                self.session.user_id,
                campo,
                valore_precedente,
                valore_nuovo,
                tipo_operazione,
            ),
        )
