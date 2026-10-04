r"""
Installa le dipendenze Python necessarie al plugin Matrice PRIMA di
attivarlo (sezione "Installazione per test" del README). Va eseguito UNA
SOLA VOLTA dalla Console Python di QGIS -- non con il Python di sistema,
ne' con uno script .bat/.sh esterno: su Windows in particolare ci sono
spesso piu' installazioni di Python sulla stessa macchina, e la
dipendenza deve finire in quella usata DA QGIS, non in una qualunque.
Eseguendo questo script da dentro QGIS (sys.executable) si e' sempre
sicuri che sia quella giusta, senza dover indovinare percorsi diversi per
ogni sistema operativo.

Come usarlo
-----------
1. In QGIS: Plugin -> Console Python (o l'icona dedicata nella barra).
2. Incollare ed eseguire:

       exec(open(r"PERCORSO_COMPLETO/matrice_qgis_plugin/scripts/install_dependencies.py").read())

   (su Windows il percorso avra' le barre rovesciate, es.
   r"C:\Users\nome\...\matrice_qgis_plugin\scripts\install_dependencies.py")

3. Attendere il messaggio di conferma, poi riavviare QGIS.
4. Solo a questo punto attivare il plugin da Estensioni -> Gestione estensioni.

In alternativa, se psycopg2 risultasse gia' installato (alcune
distribuzioni QGIS lo includono di default), lo script lo segnala e non
fa nulla.
"""

import subprocess
import sys

PACCHETTI_RICHIESTI = ["psycopg2-binary"]


def dipendenze_gia_presenti():
    try:
        import psycopg2  # noqa: F401
    except ImportError:
        return False
    return True


def installa_dipendenze():
    if dipendenze_gia_presenti():
        print("psycopg2 e' gia' installato: nessuna azione necessaria.")
        return

    print(f"Installazione di {', '.join(PACCHETTI_RICHIESTI)} nell'interprete Python di QGIS...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--user", *PACCHETTI_RICHIESTI])
    except subprocess.CalledProcessError as exc:
        print(
            "Installazione non riuscita. Su Windows, se il problema persiste, provare "
            "da 'OSGeo4W Shell' (menu Start, installato insieme a QGIS) con:\n"
            f"    python -m pip install {' '.join(PACCHETTI_RICHIESTI)}"
        )
        raise RuntimeError("Installazione dipendenze fallita, vedi messaggio sopra.") from exc

    print("Installazione completata. Riavviare QGIS prima di attivare il plugin Matrice.")


if __name__ == "__main__":
    installa_dipendenze()
