# Offensive Pressure HT — CLOUD (solo cellulare)

Questa versione elimina completamente l'uso del PC dopo il deploy.

## Architettura
Cellulare -> pagina web -> server cloud -> Flashscore -> filtro -> risultati

## Deploy consigliato su Render
Il progetto è già predisposto con:
- `Dockerfile`
- `render.yaml`
- endpoint `/health`
- Gunicorn
- Chromium/Playwright installato nel container
- scansione giornaliera in background con avanzamento

### Da cellulare
1. Crea un repository GitHub vuoto.
2. Carica TUTTI i file di questo ZIP nel repository.
3. Su Render crea un nuovo **Web Service** dal repository GitHub.
4. Render rileverà il Dockerfile.
5. Dopo il deploy apri l'URL pubblico generato da Render.
6. Salvalo nella schermata Home del telefono.

Non serve più che il PC sia acceso.

## Uso
- `Scansiona partite di oggi`: esegue il palinsesto lato server.
- Incolla URL Flashscore: analizza una singola partita.
- La UI mostra soltanto STANDARD/STRONG nella scansione giornaliera.

## Nota importante sul modello
Le soglie numeriche Top 30% / Top 20% in `config.json` sono ancora da calibrare
sul dataset storico originale. Il filtro e la struttura sono implementati, ma finché
non sostituiamo quei placeholder il badge STANDARD/STRONG non è ancora la replica
matematica definitiva del backtest.

## Limite tecnico
Flashscore non espone un'API pubblica documentata per questo flusso. Il collector
usa Playwright sul server. Se Flashscore modifica il layout o applica blocchi anti-bot,
il parser può richiedere aggiornamenti.
