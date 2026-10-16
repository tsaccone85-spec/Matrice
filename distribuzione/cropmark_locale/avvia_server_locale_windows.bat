@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
title Matrice - Server locale rilevazione cropmark

echo ================================================================
echo   Matrice - Server locale per la rilevazione cropmark
echo ================================================================
echo.
echo Questo programma avvia il server locale sulla porta 5057.
echo Lascialo aperto: torna alla pagina Matrice nel browser e clicca
echo "Avvia analisi", lo strumento rileva da solo il server.
echo.
echo Per chiuderlo: premi un tasto qualsiasi in questa finestra, o
echo chiudila direttamente.
echo.
pause

rem ----------------------------------------------------------------
rem Credenziali Copernicus Data Space (OAuth 2.0)
rem ----------------------------------------------------------------
rem Le credenziali vengono lette SOLO dalle variabili d'ambiente
rem dell'account Windows (HKCU\Environment), impostate una sola volta
rem con setx. Non vengono mai hardcodate nel .bat: chiunque apra questo
rem file non vede segreti, e ogni utente usa la propria quota Copernicus.
rem
rem Se le variabili non sono presenti (primo avvio), guidiamo l'utente
rem a ottenerle e le salviamo con setx per tutte le sessioni future.
rem ----------------------------------------------------------------

rem Rileggi le variabili utente dal registro, nel caso siano state
rem appena impostate con setx in una sessione precedente di questo
rem stesso .bat (setx scrive nel registro ma non aggiorna l'ambiente
rem della sessione cmd gia' aperta).
for /f "tokens=2,*" %%A in ('reg query "HKCU\Environment" /v COPERNICUS_CLIENT_ID 2^>nul') do set "COPERNICUS_CLIENT_ID=%%B"
for /f "tokens=2,*" %%A in ('reg query "HKCU\Environment" /v COPERNICUS_CLIENT_SECRET 2^>nul') do set "COPERNICUS_CLIENT_SECRET=%%B"

if not defined COPERNICUS_CLIENT_ID (
  echo.
  echo ================================================================
  echo   Credenziali Copernicus Data Space ^(una tantum^)
  echo ================================================================
  echo.
  echo Per usare lo strumento serve un account gratuito su Copernicus.
  echo Segui questi passi ^(o guarda il tutorial video qui sotto^):
  echo   https://youtu.be/XMgJmdNuyiI
  echo.
  echo  1. REGISTRAZIONE
  echo     Vai su https://dataspace.copernicus.eu, clicca Register,
  echo     compila il modulo e conferma l'account via e-mail.
  echo.
  echo  2. GENERAZIONE DEI CODICI
  echo     Fai login, vai sulla tua Dashboard:
  echo       https://shapps.dataspace.copernicus.eu/dashboard/
  echo     Clicca sul tuo indirizzo e-mail in alto a destra ^> Settings
  echo     ^(icona ingranaggio^). Nella pagina trova il pannello
  echo     "OAuth clients" e clicca + Create.
  echo.
  echo  3. IMPOSTAZIONI DEL CLIENT
  echo     Assegna un nome ^(es. "matrice-pc"^) e spunta "Never expire"
  echo     per garantire la validita' permanente. Dopo la conferma il
  echo     sistema mostra Client ID e Client Secret: SALVALI SUBITO,
  echo     non saranno piu' visibili dopo aver chiuso quella finestra.
  echo.
  echo  4. Incolla qui sotto i due codici quando richiesto.
  echo     Attenzione: i nomi delle variabili devono essere scritti
  echo     ESATTAMENTE come indicati ^(maiuscole e trattini bassi^),
  echo     altrimenti il software non si autentica.
  echo.
  set /p "COPERNICUS_CLIENT_ID=Client ID: "
  set /p "COPERNICUS_CLIENT_SECRET=Client Secret: "
  echo.
  if "!COPERNICUS_CLIENT_ID!"=="" (
    echo ERRORE: Client ID non inserito. Riavvia e riprova.
    pause
    exit /b 1
  )
  if "!COPERNICUS_CLIENT_SECRET!"=="" (
    echo ERRORE: Client Secret non inserito. Riavvia e riprova.
    pause
    exit /b 1
  )
  rem Salva nelle variabili dell'account utente Windows (HKCU\Environment).
  rem setx scrive in modo persistente: saranno disponibili a ogni sessione
  rem futura senza doverle reinserire, e sono visibili solo a questo utente.
  setx COPERNICUS_CLIENT_ID "!COPERNICUS_CLIENT_ID!" >nul
  setx COPERNICUS_CLIENT_SECRET "!COPERNICUS_CLIENT_SECRET!" >nul
  echo Credenziali salvate nelle variabili del tuo account Windows.
  echo.
)

rem Verifica finale: se dopo il salvataggio le variabili sono ancora vuote
rem (non dovrebbe accadere, ma meglio un messaggio chiaro che un errore
rem criptico piu' avanti).
if "!COPERNICUS_CLIENT_ID!"=="" (
  echo.
  echo ERRORE: COPERNICUS_CLIENT_ID non disponibile. Controlla le variabili
  echo d'ambiente del tuo account Windows ^(Pannello di controllo ^>
  echo Sistema ^> Variabili d'ambiente^) e riavvia.
  pause
  exit /b 1
)

echo.
echo Avvio il server locale su http://localhost:5057 ...
echo.
cd /d "%~dp0"

rem Tutto (standard + alta qualita') e' compilato in questo unico
rem eseguibile: nessun Python/libreria richiesto sul PC dell'utente, e
rem nessun file .py in chiaro distribuito accanto all'exe.
server_locale.exe

echo.
echo Il server si e' fermato.
pause
