# Cropmark locale — pacchetto distribuito via GitHub Release

Sorgenti del programma scaricabile dalla release GitHub `Cropmark-v1.0`
(`matrice_cropmark_locale.zip`). Tenute qui in versione controllata perché
lo zip pubblicato il 2026-10-15 conteneva solo `avvia_server_locale_windows.bat`
e `server_locale.exe`, senza questi `.py`: con la modalità "alta qualità"
(torch installato), il `.bat` tenta `python server_locale.py` e fallisce con
`No such file or directory`, perché il file non era mai stato incluso nello
zip.

File di questa cartella, da inserire nello zip della release insieme a
`avvia_server_locale_windows.bat` e `server_locale.exe` (quest'ultimo resta
compilato a parte, non è generato da questi sorgenti):

- `server_locale.py` — server HTTP locale (porta 5057), punto di ingresso
- `rileva_segni.py` — pipeline di rilevamento (NDVI + microrilievo DTM)
- `fonte_satellitare.py` — download Sentinel-2 / Copernicus DEM
- `fonte_geoportale_er.py` — download Geoportale Emilia-Romagna (alta risoluzione)
- `report_pdf.py` — generazione del report PDF
- `super_res.py` — super-risoluzione opzionale (SEN2SRLite, modalità "alta qualità")

Verificato (2026-10-16): con queste dipendenze installate (`numpy rasterio
geopandas scikit-image scipy shapely matplotlib requests Pillow`), il server
si avvia e risponde correttamente su `GET /ping`.
