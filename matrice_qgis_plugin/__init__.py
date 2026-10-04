"""
Entry point richiesto da QGIS.

QGIS cerca in questo file una funzione `classFactory(iface)` che restituisce
l'istanza del plugin. Non aggiungere altro codice qui: la logica vive in
matrice_plugin.py e nei moduli di core/.
"""


def classFactory(iface):
    from .matrice_plugin import MatricePlugin
    return MatricePlugin(iface)
