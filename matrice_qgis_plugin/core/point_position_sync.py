"""
Sincronizzazione posizione di carotaggi, colonne stratigrafiche e
sezioni/prospetto (tabelle con lat/lng/quota_slm dirette, non PostGIS puro
come rilievo_geometria/rilievo_quota).

Regola sulla quota quando l'operatore sposta il punto in QGIS -- SOLO per
carotaggi e colonne stratigrafiche (DEM_TABLES qui sotto):
- se ha ANCHE modificato manualmente quota_slm nella stessa sessione di
  editing, quel valore vince e non viene toccato;
- altrimenti la quota va ricalcolata dal DEM del cantiere (core/dem_sampler.py)
  nella nuova posizione, non lasciata invariata.

Le sezioni/prospetto (sezione_prospetto) NON rientrano in questa regola: la
quota di una sezione non rappresenta il piano di campagna nel punto (e'
tipicamente una quota di riferimento del rilievo del prospetto), quindi non
ha senso ricalcolarla dal DEM quando il punto viene spostato in pianta.
Per sezione_prospetto lo spostamento aggiorna solo lat/lng: quota_slm resta
sempre quella gia' presente, salvo modifica manuale esplicita dell'operatore
nello stesso commit (nel qual caso viene comunque scritta).

Le tre tabelle sono caricate in QGIS tramite le viste aggiornabili
v_carotaggio_qgis / v_colonna_stratigrafica_qgis / v_sezione_prospetto_qgis:
un trigger INSTEAD OF UPDATE lato database traduce gia' la modifica della
geometria in una scrittura su lat/lng sulla tabella reale (vedi migrazione
"viste_punto_qgis_carotaggio_colonna_sezione"). Questo modulo si occupa SOLO
della quota: interviene dopo il commit per decidere se ricalcolarla dal DEM
(solo per le due tabelle di DEM_TABLES).
"""

from .dem_sampler import DemNotAvailableError

# Tabella reale -> vista QGIS caricata in QGIS.
POINT_TABLES = {
    "carotaggio": "v_carotaggio_qgis",
    "colonna_stratigrafica": "v_colonna_stratigrafica_qgis",
    "sezione_prospetto": "v_sezione_prospetto_qgis",
}

# Solo queste due tabelle ricalcolano la quota dal DEM quando il punto viene
# spostato senza che l'operatore abbia impostato manualmente quota_slm.
# sezione_prospetto ne resta fuori (vedi docstring del modulo).
DEM_TABLES = {"carotaggio", "colonna_stratigrafica"}


class PointPositionSyncHandler:
    def __init__(self, connection, dem_sampler, session, on_warning=None):
        self.connection = connection
        self.dem_sampler = dem_sampler
        self.session = session
        self.on_warning = on_warning or (lambda message: None)
        self._pending_geom = {}   # layer -> {fid: QgsGeometry}
        self._pending_attrs = {}  # layer -> {fid: {field_index: value}}
        self._watched = []

    def watch(self, layer, table_name, cantiere_id):
        """Collega i segnali di editing del layer puntuale (view) alla logica di sync."""
        self._pending_geom[layer] = {}
        self._pending_attrs[layer] = {}

        layer.committedGeometriesChanges.connect(
            lambda layer_id, changed: self._pending_geom[layer].update(changed)
        )
        layer.committedAttributeValuesChanges.connect(
            lambda layer_id, changed: self._pending_attrs[layer].update(changed)
        )
        layer.afterCommitChanges.connect(
            lambda: self._process_commit(layer, table_name, cantiere_id)
        )
        self._watched.append(layer)

    def disconnect_all(self):
        for layer in self._watched:
            try:
                layer.committedGeometriesChanges.disconnect()
                layer.committedAttributeValuesChanges.disconnect()
                layer.afterCommitChanges.disconnect()
            except (RuntimeError, TypeError):
                pass
        self._watched = []
        self._pending_geom = {}
        self._pending_attrs = {}

    # ------------------------------------------------------------------
    def _process_commit(self, layer, table_name, cantiere_id):
        geom_changes = self._pending_geom.get(layer, {})
        attr_changes = self._pending_attrs.get(layer, {})
        if not geom_changes:
            self._pending_geom[layer] = {}
            self._pending_attrs[layer] = {}
            return

        quota_field_index = layer.fields().indexOf("quota_slm")
        ricalcola_da_dem = table_name in DEM_TABLES
        dem_url = self._dem_url_for_cantiere(cantiere_id) if ricalcola_da_dem else None

        for fid, geometry in geom_changes.items():
            point = geometry.asPoint()
            lat, lng = point.y(), point.x()

            feature_attr_changes = attr_changes.get(fid, {})
            quota_manuale = quota_field_index in feature_attr_changes

            if quota_manuale:
                nuova_quota = feature_attr_changes[quota_field_index]
                self._write_position(table_name, layer, fid, lat, lng, nuova_quota)
                continue

            if not ricalcola_da_dem:
                # sezione_prospetto: si aggiorna solo la posizione, la
                # quota_slm esistente non viene toccata (nessun DEM coinvolto).
                self._write_position(table_name, layer, fid, lat, lng, quota=None)
                continue

            try:
                nuova_quota = self.dem_sampler.sample_elevation(dem_url, lat, lng)
            except DemNotAvailableError as exc:
                # Sezione 8/5: mai un errore tecnico grezzo -- si scrive la
                # nuova posizione comunque, la quota resta quella precedente
                # e l'operatore viene avvisato in linguaggio semplice.
                self.on_warning(str(exc))
                self._write_position(table_name, layer, fid, lat, lng, quota=None)
                continue

            self._write_position(table_name, layer, fid, lat, lng, nuova_quota)

        self._pending_geom[layer] = {}
        self._pending_attrs[layer] = {}

    def _dem_url_for_cantiere(self, cantiere_id):
        rows = self.connection.execute_returning(
            "SELECT dem_url FROM cantiere WHERE id = %s;", (cantiere_id,)
        )
        return rows[0]["dem_url"] if rows else None

    def _write_position(self, table_name, layer, fid, lat, lng, quota):
        record_id = self._id_for_feature(layer, fid)

        if quota is not None:
            self.connection.execute(
                f"UPDATE {table_name} SET lat = %s, lng = %s, quota_slm = %s WHERE id = %s;",
                (lat, lng, quota, record_id),
            )
            campo, valore_nuovo = "lat/lng/quota_slm", f"{lat},{lng},{quota}"
        else:
            self.connection.execute(
                f"UPDATE {table_name} SET lat = %s, lng = %s WHERE id = %s;",
                (lat, lng, record_id),
            )
            campo, valore_nuovo = "lat/lng", f"{lat},{lng}"

        self._log_modifica(table_name, record_id, campo, valore_nuovo)

    def _id_for_feature(self, layer, fid):
        feature = layer.getFeature(fid)
        return feature["id"]

    def _log_modifica(self, tabella, record_id, campo, valore_nuovo):
        self.connection.execute(
            """
            INSERT INTO us_storico_modifiche
                (cantiere_id, tabella, record_id, utente_id, campo, valore_nuovo, tipo_operazione)
            VALUES (%s, %s, %s, %s, %s, %s, 'aggiornamento');
            """,
            (self.session.cantiere_corrente_id, tabella, record_id, self.session.user_id, campo, valore_nuovo),
        )
