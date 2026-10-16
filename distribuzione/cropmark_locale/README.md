# Cropmark locale — sorgenti del pacchetto distribuito via GitHub Release

Sorgenti del programma scaricabile dalla release GitHub `Cropmark-v1.0`.
Tenuti qui in versione controllata (git) — **non vanno distribuiti come
file `.py` in chiaro insieme all'exe**: tutto è pensato per essere
compilato in un unico eseguibile con PyInstaller.

## File

- `server_locale.py` — server HTTP locale (porta 5057), punto di ingresso
- `rileva_segni.py` — pipeline di rilevamento (NDVI + microrilievo DTM)
- `fonte_satellitare.py` — download Sentinel-2 / Copernicus DEM
- `fonte_geoportale_er.py` — download Geoportale Emilia-Romagna (alta risoluzione)
- `report_pdf.py` — generazione del report PDF
- `super_res.py` — super-risoluzione opzionale (SEN2SRLite, modalità "alta qualità")
- `avvia_server_locale_windows.bat` — launcher per l'utente finale (nessuna
  dipendenza da Python/pip: lancia solo `server_locale.exe`)
- `build_exe_windows.bat` — script di build (da lanciare su Windows, non qui)

## Architettura: tutto dentro un unico exe

`server_locale.py` **non lancia più `rileva_segni.py` come processo
esterno** (subprocess + interprete Python di sistema): lo importa e lo
chiama direttamente (`rileva_segni.esegui_analisi_automatica(...)`, in
`rileva_segni.py`). Questo era il motivo per cui, nella release precedente,
andava comunque distribuito un file `.py` in chiaro anche quando si voleva
usare solo l'exe: il sorgente serviva a un python esterno invocato in
sottoprocesso.

Con l'import diretto, PyInstaller compila *tutto* (compresa la modalità
"alta qualità" con torch/sen2sr/mlstac) in un solo `server_locale.exe`
autosufficiente: l'utente finale non installa nulla, non vede nessun `.py`,
e il `.bat` si riduce alla sola configurazione delle credenziali Copernicus
+ avvio dell'exe.

## Come ricompilare la release

Questa build va fatta su una macchina **Windows** (PyInstaller non fa
cross-compiling verso `.exe` da Linux/Mac):

1. Copia questa cartella su un PC Windows con Python 3.11+.
2. Lancia `build_exe_windows.bat` (installa le dipendenze — incluse
   `torch`, `sen2sr`, `mlstac` — e compila con PyInstaller).
3. L'eseguibile finito è in `dist\server_locale.exe`. Copialo insieme ad
   `avvia_server_locale_windows.bat` in una cartella vuota, zippala: questi
   2 file sono tutto ciò che va nella release GitHub `Cropmark-v1.0`
   (nessun `.py` nello zip finale).

Nota sul peso: con torch/sen2sr/mlstac sempre incluse, l'exe cresce
sensibilmente rispetto alla versione precedente (solo modalità standard).
È una scelta deliberata per non distribuire mai il codice in chiaro, a
costo di un download più pesante anche per chi userà solo la modalità
standard.

## Verificato (2026-10-16)

Con le dipendenze Python installate in un ambiente di test (non PyInstaller,
solo per validare la logica), il server si avvia e risponde correttamente
su `GET /ping`, e `rileva_segni.esegui_analisi_automatica` è importabile e
richiamabile direttamente da `server_locale.py` senza subprocess.
