@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
title Matrice - Installazione dipendenza per QGIS

echo ================================================================
echo   Matrice - Installazione dipendenza per il plugin QGIS
echo ================================================================
echo.
echo Questo programma cerca QGIS sul tuo computer e installa al suo
echo interno la libreria "psycopg2", necessaria al plugin Matrice per
echo collegarsi al database. Va eseguito UNA SOLA VOLTA, PRIMA di
echo attivare il plugin in QGIS (Estensioni - Gestione estensioni).
echo.
echo Non serve essere amministratore. Se qualcosa non funziona, in
echo fondo trovi il metodo alternativo (manuale).
echo.
pause

set "TROVATO="

REM --- 1) Installazione standalone di QGIS (il programma scaricato da
REM     qgis.org): contiene python-qgis-ltr.bat o python-qgis.bat dentro
REM     la cartella "bin" - e' lo script giusto perche' imposta da solo
REM     tutte le variabili di ambiente di cui QGIS ha bisogno prima di
REM     lanciare Python, cosa che chiamare python.exe direttamente non fa.
for %%R in ("%ProgramFiles%" "%ProgramFiles(x86)%" "%LocalAppData%\Programs") do (
  if exist "%%~R" (
    for /d %%D in ("%%~R\QGIS*") do (
      if exist "%%~D\bin\python-qgis-ltr.bat" (
        set "TROVATO=%%~D\bin\python-qgis-ltr.bat"
        goto :trovato
      )
      if exist "%%~D\bin\python-qgis.bat" (
        set "TROVATO=%%~D\bin\python-qgis.bat"
        goto :trovato
      )
    )
  )
)

REM --- 2) Installazione OSGeo4W (un altro modo comune di installare QGIS
REM     su Windows, spesso usato da chi lavora gia' con altri strumenti
REM     GIS): qui lo script giusto si chiama semplicemente diverso.
for %%R in ("C:\OSGeo4W64" "C:\OSGeo4W") do (
  if exist "%%~R\bin\python-qgis-ltr.bat" (
    set "TROVATO=%%~R\bin\python-qgis-ltr.bat"
    goto :trovato
  )
  if exist "%%~R\bin\python-qgis.bat" (
    set "TROVATO=%%~R\bin\python-qgis.bat"
    goto :trovato
  )
)

:non_trovato
echo.
echo Non sono riuscito a trovare QGIS automaticamente in Programmi.
echo Questo puo' succedere se QGIS e' installato in una cartella non
echo standard, o se e' installato in un altro modo.
echo.
echo METODO ALTERNATIVO - 2 minuti:
echo   1. Apri QGIS.
echo   2. Menu in alto: Plugin - Console Python.
echo   3. Incolla questa riga e premi Invio (sostituisci PERCORSO con la
echo      cartella dove hai estratto/copiato il plugin):
echo.
echo      exec(open(r"PERCORSO\matrice_qgis_plugin\scripts\install_dependencies.py").read())
echo.
echo   4. Attendi il messaggio di conferma, poi riavvia QGIS.
echo.
pause
exit /b 1

:trovato
echo.
echo Trovato QGIS: !TROVATO!
echo.
echo Installazione in corso, attendere (puo' richiedere un minuto)...
echo.
call "!TROVATO!" -m pip install --user psycopg2-binary
set "ESITO=%ERRORLEVEL%"
echo.
if "%ESITO%"=="0" (
  echo ================================================================
  echo  FATTO! La dipendenza e' installata.
  echo ================================================================
  echo.
  echo Ora puoi:
  echo   1. Chiudere questa finestra.
  echo   2. Riavviare QGIS, se era aperto.
  echo   3. Attivare il plugin: Estensioni - Gestione estensioni -
  echo      Installate - "Matrice - Rilievo di Scavo".
) else (
  echo ================================================================
  echo  INSTALLAZIONE NON RIUSCITA (codice %ESITO%)
  echo ================================================================
  echo.
  echo Prova il metodo alternativo:
  echo   1. Cerca "OSGeo4W Shell" nel menu Start di Windows e aprilo.
  echo   2. Scrivi questa riga e premi Invio:
  echo         python -m pip install psycopg2-binary
  echo   3. Riavvia QGIS.
  echo.
  echo Se il problema persiste, segnala l'errore qui sopra.
)
echo.
pause
