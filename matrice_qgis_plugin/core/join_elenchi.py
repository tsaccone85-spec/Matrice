"""
Join dei dati degli elenchi (schede US) sul layer del rilievo, per numero US.

Il join è un QgsVectorLayerJoinInfo di QGIS: i dati restano letti da Matrice
(list_us, sola lettura) e nessuna scrittura torna al database. Le colonne
arrivano con il prefisso `us_` (es. `us_definizione`), così non collidono con
i campi del rilievo.
"""

from qgis.core import QgsFeature, QgsField, QgsProject, QgsVectorLayer, QgsVectorLayerJoinInfo
from qgis.PyQt.QtCore import QVariant

PREFISSO = "us_"
CAMPO_CHIAVE = "numero_us"
# Proprietà del layer che ricorda il layer di comodo creato per il join,
# così un nuovo join lo sostituisce invece di accumularne.
PROPRIETA_ELENCO = "matrice/join_elenco_id"


def _rimuovi_join_precedente(layer):
    """Toglie il join creato da un'esecuzione precedente, se c'è."""
    for join in layer.vectorJoins():
        if join.prefix() == PREFISSO:
            layer.removeJoin(join.joinLayerId())
    vecchio_id = layer.customProperty(PROPRIETA_ELENCO)
    if vecchio_id:
        QgsProject.instance().removeMapLayer(vecchio_id)
        layer.removeCustomProperty(PROPRIETA_ELENCO)


def _layer_elenco(righe_us):
    """Layer di memoria con una riga per US (campi scelti dal database Matrice)."""
    elenco = QgsVectorLayer("None", "elenco_us_matrice", "memory")
    prov = elenco.dataProvider()
    prov.addAttributes([
        QgsField(CAMPO_CHIAVE, QVariant.Int),
        QgsField("definizione", QVariant.String),
        QgsField("fase", QVariant.String),
        QgsField("attiva", QVariant.Bool),
    ])
    elenco.updateFields()
    funzioni = elenco.fields()
    feature = []
    for r in righe_us:
        f = QgsFeature(funzioni)
        f.setAttributes([r["numero_us"], r.get("definizione"), r.get("fase"), r.get("attiva")])
        feature.append(f)
    prov.addFeatures(feature)
    return elenco


def aggiungi_join_elenchi(layer, righe_us):
    """
    Aggancia le schede US al layer del rilievo sul numero US.

    Ritorna un dizionario con:
      - agganciati: poligoni che hanno una scheda;
      - senza_scheda: numeri US presenti nel rilievo senza scheda in Matrice;
      - senza_poligono: schede in Matrice senza poligono nel rilievo.
    """
    _rimuovi_join_precedente(layer)
    elenco = _layer_elenco(righe_us)
    QgsProject.instance().addMapLayer(elenco, False)  # False: non nella legenda
    layer.setCustomProperty(PROPRIETA_ELENCO, elenco.id())

    join = QgsVectorLayerJoinInfo()
    join.setJoinLayerId(elenco.id())
    join.setJoinFieldName(CAMPO_CHIAVE)
    join.setTargetFieldName(CAMPO_CHIAVE)
    join.setPrefix(PREFISSO)
    join.setUsingMemoryCache(True)
    join.setEditable(False)
    layer.addJoin(join)

    # Confronto come testo: il campo numero US può essere intero o testo nel layer.
    numeri_schede = {str(r["numero_us"]) for r in righe_us}
    numeri_rilievo = set()
    agganciati = 0
    for f in layer.getFeatures():
        valore = f[CAMPO_CHIAVE]
        if valore is None:
            continue
        numeri_rilievo.add(str(valore))
        if str(valore) in numeri_schede:
            agganciati += 1

    return {
        "agganciati": agganciati,
        "senza_scheda": sorted(numeri_rilievo - numeri_schede),
        "senza_poligono": sorted(numeri_schede - numeri_rilievo),
    }
