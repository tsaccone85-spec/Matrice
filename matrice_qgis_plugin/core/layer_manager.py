"""
Caricamento dei layer da Matrice come layer PostGIS nativi di QGIS,
filtrati sul cantiere corrente (sezione 4, passo 1).

Nessun file temporaneo: QgsVectorLayer punta direttamente al database
(tabelle o viste), quindi ogni sessione di editing legge e scrive
direttamente su Matrice.

Le geometrie vere e proprie (US) passano dalla vista v_rilievo_geometria_qgis
(gia' unita a fase/definizione/sepoltura, sezione 3). Carotaggi, colonne
stratigrafiche e sezioni/prospetto -- tabelle con lat/lng dirette, non
geometria PostGIS -- passano dalle rispettive viste aggiornabili, che
espongono un campo `geom` calcolato e traducono le modifiche di posizione
in scritture su lat/lng tramite trigger lato database.
"""

from qgis.core import Qgis, QgsDefaultValue, QgsEditorWidgetSetup, QgsFieldConstraints, QgsProject, QgsVectorLayer
from qgis.PyQt import sip


def _layer_presente(layer):
    """
    True se `layer` esiste ancora nel progetto QGIS corrente.

    Un operatore puo' rimuovere un layer a mano dal pannello Livelli di
    QGIS (non solo togliendo la spunta dal pannello Matrice): l'oggetto
    C++ sottostante viene distrutto, ma il wrapper Python resta in
    _layers_by_cantiere finche' non si richiama questa funzione. Senza
    `sip.isdeleted()`, chiamare layer.id() su un wrapper il cui oggetto
    C++ e' gia' stato eliminato lancia RuntimeError invece di poter
    essere trattato come "non piu' presente" (stesso bug corretto in
    core/localizzazione_manager.py).
    """
    return layer is not None and not sip.isdeleted(layer) and QgsProject.instance().mapLayer(layer.id()) is not None

# view/tabella -> colonna geometria, chiave primaria, nome visualizzato,
# tipo WKB e SRID (sezione 4, passo 1).
#
# wkb_type/srid sono dichiarati qui esplicitamente e passati a
# db_connector.postgis_uri(): senza, QGIS deve dedurli campionando i dati,
# il che fallisce a subset SQL vuoto (nessuna riga per il cantiere
# selezionato, o tabella non ancora popolata) -- il provider non ha nulla
# da campionare e segna il layer non valido.
# Tipo confermato direttamente dal catalogo PostGIS (geometry_columns:
# "punto", srid 4326) il 2026-10-01 -- NON e' geometria mista nonostante
# config/styles/US.qml contenga anche simboli fill/line: quei livelli sono
# probabilmente simboli annidati/non attivi per questo layer, non categorie
# rilevanti per rilievo_geometria.
# Tutte le geometrie di Matrice sono in WGS84 (core/sync_handler.py,
# core/gps_import.py, core/dem_sampler.py usano tutti EPSG:4326).
#
# campi_nascosti: colonne uuid tecniche (chiavi, riferimenti ad altre
# tabelle) che non interessano in tabella attributi/identifica elemento --
# cantiere_id e' escluso ovunque perche' ridondante (un layer corrisponde
# sempre a un solo cantiere alla volta, gia' nel titolo del layer, non
# serve ripeterlo su ogni riga). espressione_display e' cosa mostra QGIS
# come "nome" di ogni elemento (identifica, etichette) al posto dell'id.
LAYER_SOURCES = {
    "rilievo_geometria": {
        "source": "v_rilievo_geometria_qgis",
        "geometry_column": "geom",
        "key_column": "id_geom",
        "display_name": "Rilievo vettoriale",
        "wkb_type": Qgis.WkbType.Point,
        "srid": 4326,
        "campi_nascosti": ("id_geom", "cantiere_id", "us_id", "operatore_id", "sepoltura_id"),
        "espressione_display": "'US ' || \"numero_us\"",
    },
    "rilievo_quota": {
        "source": "rilievo_quota",
        "geometry_column": "geom",
        "key_column": "id",
        "display_name": "Quote",
        "wkb_type": Qgis.WkbType.Point,
        "srid": 4326,
        "campi_nascosti": ("id", "cantiere_id", "us_id", "operatore_id", "created_by"),
        "espressione_display": "\"tipo_quota\" || ' ' || \"valore_quota\"",
    },
    "carotaggio": {
        "source": "v_carotaggio_qgis",
        "geometry_column": "geom",
        "key_column": "id",
        "display_name": "Carotaggi",
        "wkb_type": Qgis.WkbType.Point,
        "srid": 4326,
        # stratigrafia: jsonb (log carotaggio a livelli), illeggibile come
        # testo grezzo in tabella attributi -- tolto su richiesta esplicita,
        # non e' un id tecnico come gli altri campi qui.
        "campi_nascosti": ("id", "cantiere_id", "created_by", "stratigrafia"),
        "campi_foto": ("foto_1_url", "foto_2_url", "foto_3_url"),
        "espressione_display": "'Carotaggio ' || \"numero_carotaggio\"",
    },
    "colonna_stratigrafica": {
        "source": "v_colonna_stratigrafica_qgis",
        "geometry_column": "geom",
        "key_column": "id",
        "display_name": "Colonne stratigrafiche",
        "wkb_type": Qgis.WkbType.Point,
        "srid": 4326,
        "campi_nascosti": ("id", "cantiere_id", "created_by", "stratigrafia", "storage_provider", "disegno_tavola"),
        "campi_foto": ("foto_1_url", "foto_2_url", "foto_3_url"),
        "espressione_display": "'Colonna ' || \"numero_colonna\"",
    },
    "sezione_prospetto": {
        "source": "v_sezione_prospetto_qgis",
        "geometry_column": "geom",
        "key_column": "id",
        "display_name": "Sezioni e prospetti",
        "wkb_type": Qgis.WkbType.Point,
        "srid": 4326,
        "campi_nascosti": ("id", "cantiere_id", "origine_ocr_id"),
        "campi_foto": ("file_url",),
        "espressione_display": "'Sezione ' || \"numero_sezione\"",
    },
    # Candidati di segni archeologici (crop/soil/shadow mark) rilevati
    # automaticamente da strumento esterno (segni_archeologici/rileva_segni.py,
    # NDVI multitemporale + microrilievo DTM), caricati su Matrice per
    # revisione in QGIS. `geom` qui e' una vera colonna PostGIS (poligono),
    # non sincronizzata da lat/lng come colonna_stratigrafica/carotaggio.
    "progettazione_cropmark": {
        "source": "v_progettazione_cropmark_qgis",
        "geometry_column": "geom",
        "key_column": "id",
        "display_name": "Progettazione - cropmark",
        "wkb_type": Qgis.WkbType.MultiPolygon,
        "srid": 4326,
        "campi_nascosti": ("id", "cantiere_id", "created_by"),
        "espressione_display": "\"tipo\" || ' (' || round(\"area_mq\") || ' mq)'",
    },
    # Poligoni dei vincoli paesaggistici SITAP (MiC) intersecanti l'area di
    # cantiere, salvati dal pannello "Vincoli paesaggistici" dopo l'analisi
    # WFS+Turf lato client (app/js/tool-vincoli.js, 2026-10-13). Solo gli
    # areali (art. 136/142): lineari/puntuali restano solo a video, mai
    # persistiti qui.
    "progettazione_vincolo": {
        "source": "v_progettazione_vincolo_qgis",
        "geometry_column": "geom",
        "key_column": "id",
        "display_name": "Progettazione - vincoli",
        "wkb_type": Qgis.WkbType.MultiPolygon,
        "srid": 4326,
        "campi_nascosti": ("id", "cantiere_id", "created_by", "attributi"),
        "espressione_display": "coalesce(\"nome\", \"codice_norma\", 'Vincolo')",
    },
    # Confini dei comuni ISTAT toccati dall'area (strumento «Confini comunali»,
    # migrazione 0078): solo contorno, per non coprire il resto.
    "progettazione_confine_comunale": {
        "source": "v_progettazione_confine_comunale_qgis",
        "geometry_column": "geom",
        "key_column": "id",
        "display_name": "Progettazione - confini comunali",
        "wkb_type": Qgis.WkbType.MultiPolygon,
        "srid": 4326,
        "campi_nascosti": ("id", "cantiere_id", "created_by", "cod_prov", "cod_reg"),
        "espressione_display": "coalesce(\"nome\", \"pro_com_t\")",
        "stile": {"riempimento": None, "contorno": "#6a1b9a"},
    },
    # Trincee e Saggi del flusso Progettazione Archeologica (migrazione
    # 0068_trincea_saggio_progettazione.sql): rettangoli creati dal menu
    # "Progettazione" della toolbar (core/rettangolo_progettazione.py), non
    # disegnati a mano. Due tabelle separate: stessa forma (rettangolo +
    # rotazione) ma campi/tempistiche diversi (vedi la migrazione).
    "trincea_progettazione": {
        "source": "v_trincea_progettazione_qgis",
        "geometry_column": "geom",
        "key_column": "id",
        "display_name": "Progettazione - Trincee",
        "wkb_type": Qgis.WkbType.Polygon,
        "srid": 4326,
        "campi_nascosti": ("id", "cantiere_id", "created_by"),
        "espressione_display": "\"nome\"",
        "stile": {"riempimento": "#e53935", "contorno": "#e53935"},
    },
    "saggio_progettazione": {
        "source": "v_saggio_progettazione_qgis",
        "geometry_column": "geom",
        "key_column": "id",
        "display_name": "Progettazione - Saggi",
        "wkb_type": Qgis.WkbType.Polygon,
        "srid": 4326,
        "campi_nascosti": ("id", "cantiere_id", "created_by"),
        "espressione_display": "\"nome\"",
        "stile": {"riempimento": "#1e66f5", "contorno": "#1e66f5"},
    },
    "survey_ur": {
        "source": "v_survey_ur_qgis",
        "geometry_column": "geom",
        "key_column": "id",
        "display_name": "Progettazione - Unità di Ricognizione",
        "wkb_type": Qgis.WkbType.Polygon,
        "srid": 4326,
        # In QGIS si chiede solo il codice UR; l'area si calcola in automatico
        # (trigger survey_ur_area, migrazione 0083), tutto il resto si compila
        # sul campo dall'app.
        "campi_nascosti": (
            "id", "cantiere_id", "created_by", "data_ricognizione", "operatori",
            "durata_minuti", "condizioni_meteo", "grado_visibilita", "uso_suolo",
            "densita_manufatti", "tipo_manufatti", "periodo_prevalente", "esito",
            "note", "created_at",
        ),
        "campi_sola_lettura": ("area_mq",),
        # Le viste non espongono il not null della tabella: senza vincolo nel
        # form QGIS salva anche con codice_ur vuoto e il DB rifiuta la riga.
        "campi_obbligatori": ("codice_ur",),
        "espressione_display": "\"codice_ur\"",
        # Contorno UR sempre nero, interno trasparente.
        "stile": {"riempimento": None, "contorno": "#000000"},
    },
    # Carotaggi PREVISTI dalla Progettazione: tabella propria
    # (carotaggio_progettazione), distinta da "carotaggio" che appartiene al
    # flusso "Lettura carotaggi" e non va mai mescolata con questa.
    "carotaggio_progettazione": {
        "source": "v_carotaggio_progettazione_qgis",
        "geometry_column": "geom",
        "key_column": "id",
        "display_name": "Progettazione - Carotaggi",
        "wkb_type": Qgis.WkbType.Point,
        "srid": 4326,
        "campi_nascosti": ("id", "cantiere_id", "created_by"),
        "espressione_display": "\"nome\"",
        "stile": {"riempimento": "#fb8c00", "contorno": "#e65100"},
    },
    # Buffer attorno all'area di indagine (migrazione 0082): un poligono per
    # cantiere, generato da "Crea buffer area di indagine" e modificabile.
    "buffer_progettazione": {
        "source": "v_buffer_progettazione_qgis",
        "geometry_column": "geom",
        "key_column": "id",
        "display_name": "Progettazione - Buffer",
        "wkb_type": Qgis.WkbType.Polygon,
        "srid": 4326,
        "campi_nascosti": ("id", "cantiere_id", "created_by"),
        "espressione_display": "'Buffer ' || coalesce(to_string(\"distanza_m\"), '')",
    },
    # ---- VPA (Verifica preventiva): viste 0100_vpa_qgis.sql --------------------
    # La VPA salva la geometria come WKT testuale; le viste la espongono come
    # geometry PostGIS e riscrivono su vpa_progetto / vpa_sito con trigger
    # INSTEAD OF. Compaiono solo nei cantieri VPA (`solo_vpa`). Ricognizioni e
    # Unita' di ricognizione riusano i layer survey_ur / survey_ritrovamento
    # della Progettazione (vedi CHIAVI_RICOGNIZIONE_VPA).
    "vpa_progetto": {
        "source": "v_vpa_progetto_qgis",
        "geometry_column": "geom",
        "key_column": "id",
        "display_name": "VPA - Area di progetto (MOPR)",
        "wkb_type": Qgis.WkbType.MultiPolygon,
        "srid": 4326,
        "solo_vpa": True,
        "campi_nascosti": ("id", "cantiere_id", "created_at"),
        "campi_sola_lettura": ("id_progett",),
        "espressione_display": "coalesce(\"ogn\", \"id_progett\", 'Area di progetto')",
        "stile": {"riempimento": None, "contorno": "#2e7d32"},
    },
    # Il sito puo' essere punto, linea o poligono: tre layer sulla stessa vista,
    # distinti dal filtro sul tipo di geometria (un layer QGIS ha un tipo solo).
    "vpa_sito_punti": {
        "source": "v_vpa_sito_qgis",
        "geometry_column": "geom",
        "key_column": "id",
        "display_name": "VPA - Siti (punti)",
        "wkb_type": Qgis.WkbType.MultiPoint,
        "srid": 4326,
        "filtro_sql": "geometrytype(geom) in ('POINT', 'MULTIPOINT')",
        "solo_vpa": True,
        "campi_nascosti": ("id", "cantiere_id", "id_progett", "created_at"),
        "espressione_display": "\"id_sito\" || coalesce(' - ' || \"ogn\", '')",
        "stile": {"riempimento": "#8e24aa", "contorno": "#4a148c"},
    },
    "vpa_sito_linee": {
        "source": "v_vpa_sito_qgis",
        "geometry_column": "geom",
        "key_column": "id",
        "display_name": "VPA - Siti (linee)",
        "wkb_type": Qgis.WkbType.MultiLineString,
        "srid": 4326,
        "filtro_sql": "geometrytype(geom) in ('LINESTRING', 'MULTILINESTRING')",
        "solo_vpa": True,
        "campi_nascosti": ("id", "cantiere_id", "id_progett", "created_at"),
        "espressione_display": "\"id_sito\" || coalesce(' - ' || \"ogn\", '')",
        "stile": {"riempimento": None, "contorno": "#8e24aa"},
    },
    "vpa_sito_poligoni": {
        "source": "v_vpa_sito_qgis",
        "geometry_column": "geom",
        "key_column": "id",
        "display_name": "VPA - Siti (poligoni)",
        "wkb_type": Qgis.WkbType.MultiPolygon,
        "srid": 4326,
        "filtro_sql": "geometrytype(geom) in ('POLYGON', 'MULTIPOLYGON')",
        "solo_vpa": True,
        "campi_nascosti": ("id", "cantiere_id", "id_progett", "created_at"),
        "espressione_display": "\"id_sito\" || coalesce(' - ' || \"ogn\", '')",
        "stile": {"riempimento": "#8e24aa", "contorno": "#4a148c"},
    },
    "survey_ritrovamento": {
        "source": "v_survey_ritrovamento_qgis",
        "geometry_column": "geom",
        "key_column": "id",
        "display_name": "Progettazione - Rinvenimenti",
        "wkb_type": Qgis.WkbType.Point,
        "srid": 4326,
        "campi_nascosti": ("id", "cantiere_id", "created_by", "ur_id"),
        "espressione_display": "'Rinvenimento ' || \"numero_progressivo\"",
    },
}


# Livelli del flusso "Progettazione Archeologica": nel pannello Livelli di QGIS
# stanno sotto un unico gruppo, non sparsi alla radice. Il layer "Carotaggi"
# della Lettura carotaggi ("carotaggio") resta fuori: e' un altro flusso. I
# carotaggi di progetto hanno un layer a parte ("carotaggio_progettazione").
GRUPPO_PROGETTAZIONE = "Progettazione"
# Livelli di riferimento (sola lettura) che un cantiere a valle mostra dalla
# Progettazione di origine: carotaggi per la Lettura carotaggi, trincee e saggi
# per l'Assistenza in corso d'opera (sorveglianza).
GRUPPO_RIFERIMENTO = "Da progettazione (sola lettura)"
RIFERIMENTI_PER_TIPO_INTERVENTO = {
    "carotaggi": ("carotaggio_progettazione",),
    "sorveglianza": ("trincea_progettazione", "saggio_progettazione"),
}
# Gruppo «VPA»: i layer propri della Verifica preventiva e, nei cantieri VPA, le
# ricognizioni (UR e rinvenimenti) del flusso Progettazione, riusate cosi' come sono.
GRUPPO_VPA = "VPA"
CHIAVI_GRUPPO_VPA = ("vpa_progetto", "vpa_sito_punti", "vpa_sito_linee", "vpa_sito_poligoni")
CHIAVI_RICOGNIZIONE_VPA = ("survey_ur", "survey_ritrovamento")
CHIAVI_GRUPPO_PROGETTAZIONE = (
    "progettazione_cropmark", "progettazione_vincolo", "progettazione_confine_comunale", "trincea_progettazione",
    "saggio_progettazione", "survey_ur", "survey_ritrovamento", "carotaggio_progettazione",
    "buffer_progettazione",
)


class LayerManager:
    def __init__(self, connection, session=None):
        self.connection = connection
        self.session = session
        self._layers_by_cantiere = {}
        self._nome_by_cantiere = {}
        self._riferimenti_by_cantiere = {}  # layer di sola lettura dalla Progettazione di origine
        self._vpa_cantieri = set()  # cantieri VPA con accesso VPA verificato
        self.su_layer_aggiunto = None  # callback(layer, campi_foto) dopo l'aggiunta in mappa (anteprime foto)

    def imposta_vpa(self, cantiere_id, abilitato):
        """Marca il cantiere come VPA abilitato: solo cosi' si caricano i layer `solo_vpa`."""
        if abilitato:
            self._vpa_cantieri.add(cantiere_id)
        else:
            self._vpa_cantieri.discard(cantiere_id)

    def e_vpa(self, cantiere_id):
        return cantiere_id in self._vpa_cantieri

    def chiavi_disponibili(self, cantiere_id):
        """Chiavi di LAYER_SOURCES caricabili per il cantiere (i layer VPA solo nei cantieri VPA)."""
        return [k for k, o in LAYER_SOURCES.items() if not o.get("solo_vpa") or self.e_vpa(cantiere_id)]

    def gruppo_di(self, cantiere_id, key):
        """Nome del gruppo del pannello Livelli QGIS in cui sta `key`, o None (radice)."""
        if key in CHIAVI_GRUPPO_VPA or (key in CHIAVI_RICOGNIZIONE_VPA and self.e_vpa(cantiere_id)):
            return GRUPPO_VPA
        if key in CHIAVI_GRUPPO_PROGETTAZIONE:
            return GRUPPO_PROGETTAZIONE
        return None

    def nome_cantiere(self, cantiere_id):
        """Nome del cantiere passato all'ultimo load_cantiere() per questo id, o None se non noto."""
        return self._nome_by_cantiere.get(cantiere_id)

    def _build_layer(self, cantiere_id, key):
        """Costruisce (senza aggiungere in mappa) il layer PostGIS per `key`."""
        opts = LAYER_SOURCES[key]
        uri = self.connection.postgis_uri(
            opts["source"],
            geometry_column=opts["geometry_column"],
            key_column=opts["key_column"],
            wkb_type=opts["wkb_type"],
            srid=opts["srid"],
        )
        # cantiere_id e' un uuid, non un intero: va quotato nel subset SQL.
        filtro = f"cantiere_id = '{cantiere_id}'"
        if opts.get("filtro_sql"):
            filtro += f" and {opts['filtro_sql']}"
        uri.setSql(filtro)
        # Nome cantiere nel titolo del layer (se noto) invece del suo uuid,
        # molto piu' leggibile nel pannello Livelli.
        nome_cantiere = self._nome_by_cantiere.get(cantiere_id, cantiere_id)
        layer = QgsVectorLayer(uri.uri(False), f"{opts['display_name']} - {nome_cantiere}", "postgres")
        if not layer.isValid():
            raise RuntimeError(f"Impossibile caricare il layer '{key}' da Matrice.")
        self._applica_presentazione(layer, opts)
        self._applica_default_tecnici(layer, cantiere_id)
        return layer

    def _applica_default_tecnici(self, layer, cantiere_id):
        """
        cantiere_id e created_by sono campi nascosti nel form: senza un default
        gli elementi disegnati con gli strumenti nativi QGIS (UR, buffer...)
        arrivano al DB con NULL e violano il not null su cantiere_id.
        """
        valori = {"cantiere_id": cantiere_id}
        utente_id = getattr(self.session, "user_id", None)
        if utente_id:
            valori["created_by"] = utente_id
        for nome_campo, valore in valori.items():
            indice = layer.fields().indexFromName(nome_campo)
            if indice != -1:
                layer.setDefaultValueDefinition(indice, QgsDefaultValue(f"'{valore}'", False))
        # id generato dal plugin: non dipende dal default della vista letto da
        # QGIS al caricamento (un layer aperto prima di una migrazione inviava id NULL).
        indice_id = layer.fields().indexFromName("id")
        if indice_id != -1 and layer.fields().field(indice_id).typeName() == "uuid":
            layer.setDefaultValueDefinition(indice_id, QgsDefaultValue("uuid('WithoutBraces')", False))

    def _applica_presentazione(self, layer, opts):
        """
        Nasconde le colonne uuid tecniche da tabella attributi e form di
        identificazione, e imposta l'espressione che QGIS usa come "nome"
        di ogni elemento (identifica, etichette) al posto dell'id.
        """
        campi_nascosti = opts.get("campi_nascosti", ())
        config = layer.attributeTableConfig()
        colonne = config.columns()
        for colonna in colonne:
            if colonna.name in campi_nascosti:
                colonna.hidden = True
        config.setColumns(colonne)
        layer.setAttributeTableConfig(config)

        for nome_campo in campi_nascosti:
            indice = layer.fields().indexFromName(nome_campo)
            if indice != -1:
                layer.setEditorWidgetSetup(indice, QgsEditorWidgetSetup("Hidden", {}))

        campi_sola_lettura = opts.get("campi_sola_lettura", ())
        if campi_sola_lettura:
            form_config = layer.editFormConfig()
            for nome_campo in campi_sola_lettura:
                indice = layer.fields().indexFromName(nome_campo)
                if indice != -1:
                    form_config.setReadOnly(indice, True)
            layer.setEditFormConfig(form_config)

        for nome_campo in opts.get("campi_obbligatori", ()):
            indice = layer.fields().indexFromName(nome_campo)
            if indice != -1:
                layer.setFieldConstraint(
                    indice,
                    QgsFieldConstraints.ConstraintNotNull,
                    QgsFieldConstraints.ConstraintStrengthHard,
                )
                layer.setFieldConstraint(
                    indice,
                    QgsFieldConstraints.ConstraintExpression,
                    QgsFieldConstraints.ConstraintStrengthHard,
                )
                layer.setConstraintExpression(
                    indice,
                    f'length(trim("{nome_campo}")) > 0',
                    "Il campo è obbligatorio.",
                )

        campi_foto = opts.get("campi_foto", ())
        if campi_foto:
            from .foto_cache import applica_widget_foto
            applica_widget_foto(layer, campi_foto)

        espressione_display = opts.get("espressione_display")
        if espressione_display:
            layer.setDisplayExpression(espressione_display)

        stile = opts.get("stile")
        if stile:
            self._applica_stile_fisso(layer, stile)

    @staticmethod
    def _applica_stile_fisso(layer, stile):
        """
        Colori fissi della Progettazione: trincee rosse, saggi blu, carotaggi
        arancioni, contorno UR nero. Simbolo unico, applicato a ogni apertura.
        """
        from qgis.core import QgsFillSymbol, QgsLineSymbol, QgsMarkerSymbol, QgsSingleSymbolRenderer
        from qgis.PyQt.QtGui import QColor

        contorno = QColor(stile["contorno"])
        if stile["riempimento"]:
            riempimento = QColor(stile["riempimento"])
            riempimento.setAlpha(90)
            riempimento_txt = f"{riempimento.red()},{riempimento.green()},{riempimento.blue()},{riempimento.alpha()}"
        else:
            riempimento_txt = "0,0,0,0"
        contorno_txt = f"{contorno.red()},{contorno.green()},{contorno.blue()},255"

        if layer.geometryType() == Qgis.GeometryType.Point:
            simbolo = QgsMarkerSymbol.createSimple({
                "name": "circle", "size": "3.6",
                "color": f"{QColor(stile['riempimento']).red()},{QColor(stile['riempimento']).green()},{QColor(stile['riempimento']).blue()},255",
                "outline_color": contorno_txt, "outline_width": "0.4",
            })
        elif layer.geometryType() == Qgis.GeometryType.Line:
            simbolo = QgsLineSymbol.createSimple({"line_color": contorno_txt, "line_width": "0.9"})
        else:
            simbolo = QgsFillSymbol.createSimple({
                "color": riempimento_txt, "outline_color": contorno_txt, "outline_width": "0.8",
            })
        layer.setRenderer(QgsSingleSymbolRenderer(simbolo))

    def load_cantiere(self, cantiere_id, nome_cantiere=None):
        """
        Carica (o ricarica) i layer del cantiere selezionato (sezione 4,
        passo 1). Per non affollare la mappa di livelli vuoti al primo
        utilizzo (es. un cantiere appena iniziato, dove magari e' stato
        rilevato solo un tipo di dato), vengono aggiunti subito solo i
        livelli che hanno gia' almeno una riga per questo cantiere; gli
        altri restano disponibili a richiesta (vedi show_layer), spuntabili
        dal pannello Matrice.

        Ritorna (layers, has_data): entrambi dict con le stesse chiavi di
        LAYER_SOURCES; layers contiene sempre l'oggetto QgsVectorLayer
        (anche per quelli non ancora aggiunti in mappa), has_data dice se
        all'apertura aveva gia' righe (quindi e' stato aggiunto subito).
        """
        if nome_cantiere:
            self._nome_by_cantiere[cantiere_id] = nome_cantiere

        layers = {}
        has_data = {}
        for key in self.chiavi_disponibili(cantiere_id):
            # Un layer che non si carica (es. grant mancante su una vista
            # in produzione) non deve bloccare gli altri: senza questo
            # try/except un solo RuntimeError qui impediva anche
            # populate_layer_toggles piu' sotto in matrice_plugin.py,
            # lasciando l'intero pannello "Livelli del cantiere" vuoto.
            try:
                layer = self._build_layer(cantiere_id, key)
            except RuntimeError:
                has_data[key] = False
                continue
            layers[key] = layer
            if layer.featureCount() > 0:
                self._aggiungi_in_mappa(layer, key, cantiere_id)
                has_data[key] = True
            else:
                has_data[key] = False

        self._layers_by_cantiere[cantiere_id] = layers
        return layers, has_data

    def _aggiungi_in_mappa(self, layer, key, cantiere_id=None):
        """Aggiunge `layer` al progetto, nel gruppo "Progettazione" o "VPA" se ne fa parte."""
        nome_gruppo = self.gruppo_di(cantiere_id, key)
        if nome_gruppo is None:
            QgsProject.instance().addMapLayer(layer)
            self._notifica_aggiunto(layer, key)
            return
        radice = QgsProject.instance().layerTreeRoot()
        gruppo = radice.findGroup(nome_gruppo) or radice.addGroup(nome_gruppo)
        QgsProject.instance().addMapLayer(layer, False)
        gruppo.addLayer(layer)
        self._notifica_aggiunto(layer, key)

    def _notifica_aggiunto(self, layer, key):
        campi = LAYER_SOURCES.get(key, {}).get("campi_foto")
        if self.su_layer_aggiunto is not None and campi:
            try:
                self.su_layer_aggiunto(layer, campi)
            except Exception:  # mai bloccante: le anteprime sono un di piu'
                pass

    def carica_riferimento(self, cantiere_id, origine_id, chiavi):
        """
        Mostra in mappa, SOLA LETTURA, i livelli `chiavi` della Progettazione
        `origine_id` dentro il cantiere a valle `cantiere_id` (carotaggi /
        sorveglianza): carotaggi previsti, trincee e saggi di progetto. Non si
        aggancia nessuna sincronizzazione: sono dati di riferimento, non si
        modificano da qui. Ritorna la lista dei layer aggiunti.
        """
        self.rimuovi_riferimento(cantiere_id)
        aggiunti = []
        for key in chiavi:
            layer = self._build_layer(origine_id, key)
            layer.setName(f"{LAYER_SOURCES[key]['display_name']} (da progetto)")
            layer.setReadOnly(True)
            radice = QgsProject.instance().layerTreeRoot()
            gruppo = radice.findGroup(GRUPPO_RIFERIMENTO) or radice.addGroup(GRUPPO_RIFERIMENTO)
            QgsProject.instance().addMapLayer(layer, False)
            gruppo.addLayer(layer)
            aggiunti.append(layer)
        self._riferimenti_by_cantiere[cantiere_id] = aggiunti
        return aggiunti

    def layers_riferimento(self, cantiere_id):
        return [l for l in self._riferimenti_by_cantiere.get(cantiere_id, []) if _layer_presente(l)]

    def rimuovi_riferimento(self, cantiere_id):
        for layer in self._riferimenti_by_cantiere.pop(cantiere_id, []):
            if _layer_presente(layer):
                QgsProject.instance().removeMapLayer(layer.id())

    def current_layers(self, cantiere_id):
        return self._layers_by_cantiere.get(cantiere_id, {})

    def cantiere_del_layer(self, layer):
        """
        Ritorna il cantiere_id a cui appartiene `layer` se è il rilievo
        vettoriale (rilievo_geometria) di un cantiere caricato, altrimenti None.
        Serve a "Unisci dati elenchi", che lavora solo su quel layer.
        """
        if layer is None:
            return None
        for cantiere_id, layers in self._layers_by_cantiere.items():
            rilievo = layers.get("rilievo_geometria")
            if _layer_presente(rilievo) and rilievo.id() == layer.id():
                return cantiere_id
        return None

    def is_layer_shown(self, cantiere_id, key):
        layer = self._layers_by_cantiere.get(cantiere_id, {}).get(key)
        return _layer_presente(layer)

    def show_layer(self, cantiere_id, key):
        """
        Aggiunge in mappa il livello `key` del cantiere, su richiesta
        dell'operatore (spunta nel pannello). Se era gia' stato rimosso in
        precedenza lo ricostruisce da capo (l'oggetto QGIS viene distrutto
        da removeMapLayer). Ritorna (layer, era_gia_visibile): il secondo
        valore serve a chi chiama per capire se e' la prima volta che
        questo layer compare in mappa per questo cantiere (e quindi va
        agganciato alla sincronizzazione delle modifiche).
        """
        layer = self._layers_by_cantiere.get(cantiere_id, {}).get(key)
        if _layer_presente(layer):
            return layer, True  # gia' visibile, nessuna nuova azione da fare

        layer = self._build_layer(cantiere_id, key)
        self._aggiungi_in_mappa(layer, key, cantiere_id)
        self._layers_by_cantiere.setdefault(cantiere_id, {})[key] = layer
        return layer, False

    def remove_cantiere(self, cantiere_id):
        """
        Rimuove dal progetto QGIS tutti i layer del cantiere indicato
        (passaggio a un altro cantiere, matrice_plugin._rimuovi_layer_cantiere):
        a differenza di hide_layer, qui si tolgono anche i layer senza
        spunta nel pannello, se per qualche motivo erano comunque finiti in
        mappa.
        """
        self.rimuovi_riferimento(cantiere_id)
        layers = self._layers_by_cantiere.pop(cantiere_id, {})
        for layer in layers.values():
            if _layer_presente(layer):
                QgsProject.instance().removeMapLayer(layer.id())

    def hide_layer(self, cantiere_id, key):
        """Rimuove dalla mappa il livello `key` del cantiere (spunta tolta)."""
        layer = self._layers_by_cantiere.get(cantiere_id, {}).get(key)
        if _layer_presente(layer):
            QgsProject.instance().removeMapLayer(layer.id())
        self._layers_by_cantiere.get(cantiere_id, {}).pop(key, None)

    # Le tre viste puntuali con lat/lng dirette, sincronizzate tramite
    # point_position_sync.py (distinte dalla geometria US, che usa il
    # percorso generico sync_handler.py). Il ricalcolo della quota dal DEM
    # riguarda pero' solo carotaggio e colonna_stratigrafica, non
    # sezione_prospetto -- vedi PointPositionSyncHandler.DEM_TABLES.
    POINT_SYNC_TABLES = {
        "carotaggio": "carotaggio",
        "colonna_stratigrafica": "colonna_stratigrafica",
        "sezione_prospetto": "sezione_prospetto",
    }
