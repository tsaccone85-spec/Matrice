"""
Classi Python corrispondenti alle entita' del modello dati REALE di Matrice
(sezione 3 della specifica, verificato su Supabase). Usate come oggetti di
trasferimento tra i moduli core/ e l'interfaccia utente -- non un ORM
completo in questa versione MVP.

NOTA (principio "elenco" iniziale, vedi nota utente in sezione 3/4): le
classi US/Sepoltura/Reperto qui sotto restano l'elenco leggero usato per
l'associazione (id + pochi campi identificativi), non l'intera anagrafica
di Matrice -- quella e' stata aggiunta in seguito in ui/us_detail_dialog.py
(scheda US completa su doppio click), che pero' lavora su dict grezzi da
PostgREST, non su queste dataclass.

Valori ammessi per `definizione` (equivalente del vecchio "tipo_us" mai
esistito in produzione): allineati alla terminologia di cantiere. Elenco
indicativo, non vincolante lato codice (il campo reale e' testo libero).
"""

from dataclasses import dataclass, field
from typing import Optional

DEFINIZIONE_US_TIPICHE = ("strato", "USM", "taglio", "riempimento", "negativo")


@dataclass
class Fase:
    """
    `fase` non e' una tabella a se' in Matrice: e' un campo testo libero
    sulla scheda US (sezione 3). Questa classe esiste solo per uso lato
    QGIS (etichetta + eventuale colore assegnato localmente per lo stile),
    non ha una controparte 1:1 in una tabella di produzione.
    """
    nome: str
    colore_hex: Optional[str] = None  # assegnato lato plugin, non salvato su Matrice


@dataclass
class Sepoltura:
    """Raggruppamento funerario -- tabella reale `sepoltura` (sezione 3)."""
    id: str  # uuid
    numero_tomba: Optional[int] = None
    cantiere_id: str = None


@dataclass
class US:
    """
    Elenco leggero della US per l'associazione in QGIS (principio "elenco
    US", non la scheda US completa con i suoi ~46 campi). Tabella reale:
    `us`; il legame con `sepoltura` passa dalla tabella ponte
    `sepoltura_us`, non da una FK diretta su `us`.
    """
    id: str  # uuid
    numero_us: int
    cantiere_id: str
    definizione: Optional[str] = None  # es. strato, USM, taglio... (testo libero)
    fase: Optional[str] = None
    attiva: bool = True
    sepoltura_id: Optional[str] = None  # da sepoltura_us, non da us direttamente


@dataclass
class RilievoGeometria:
    """Tabella reale `rilievo_geometria` (nuova, creata per questo plugin)."""
    id_geom: str  # uuid
    cantiere_id: str
    us_id: str
    tipo_geometria: str  # punto / linea / poligono / ... (CHECK coerente col GeometryType di geom)
    wkt: str
    layer: Optional[str] = None  # caratterizzazione/raggruppamento grafico aggiuntivo
    sistema_riferimento_origine: Optional[str] = None
    fonte_rilievo: str = "manuale"  # gps_differenziale / stazione_totale / manuale (CHECK)
    data_rilievo: Optional[str] = None
    operatore_id: Optional[str] = None


@dataclass
class RilievoQuota:
    """Tabella reale `rilievo_quota` (nuova, creata per questo plugin)."""
    id: str  # uuid
    cantiere_id: str
    us_id: str
    valore_quota: float
    tipo_quota: str  # max / min / piano_di_posa (CHECK)
    wkt_punto: Optional[str] = None
    operatore_id: Optional[str] = None


@dataclass
class Reperto:
    """Elenco leggero reperti per associazione (tabella reale `reperto`)."""
    id: str  # uuid
    us_id: str
    categoria_materiale: Optional[str] = None    # FK vocabolario ICCD (chiuso)
    definizione_tipologia: Optional[str] = None  # FK vocabolario ICCD (aperto, 2 livelli)


@dataclass
class Carotaggio:
    """Tabella reale `carotaggio`, estesa con lat/lng/quota_slm (sezione 5)."""
    id: str  # uuid
    cantiere_id: str
    lat: Optional[float] = None
    lng: Optional[float] = None
    quota_slm: Optional[float] = None


@dataclass
class ColonnaStratigrafica:
    """Tabella reale `colonna_stratigrafica`, estesa con lat/lng/quota_slm."""
    id: str  # uuid
    cantiere_id: str
    lat: Optional[float] = None
    lng: Optional[float] = None
    quota_slm: Optional[float] = None


@dataclass
class SezioneProspetto:
    """Tabella reale `sezione_prospetto`, estesa con lat/lng/quota_slm."""
    id: str  # uuid
    cantiere_id: str
    lat: Optional[float] = None
    lng: Optional[float] = None
    quota_slm: Optional[float] = None


@dataclass
class UsStoricoModifica:
    """Tabella reale `us_storico_modifiche` (nuova, log immutabile di modifiche)."""
    id: str  # uuid
    cantiere_id: str
    tabella: str
    record_id: str
    utente_id: str
    campo: str
    valore_precedente: Optional[str] = None
    valore_nuovo: Optional[str] = None
    tipo_operazione: str = "aggiornamento"  # creazione / aggiornamento / eliminazione (CHECK)
