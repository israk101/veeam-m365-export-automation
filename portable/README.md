# Portable 1.3.1

Scarica [VeeamM365RestoreTester-1.3.1-portable.zip](https://media.githubusercontent.com/media/israk101/veeam-m365-export-automation/main/portable/VeeamM365RestoreTester-1.3.1-portable.zip), verifica [SHA256SUMS.txt](SHA256SUMS.txt) ed estrai l'intera cartella. Avvia l'EXE lasciando `_internal` accanto.

Questa è l'unica distribuzione dell'app. Il file ZIP è in Git LFS: dopo il clone eseguire `git lfs pull`. La build locale ricrea anche la cartella estratta, esclusa da Git.

Il pacchetto non contiene documentazione, test, schermate o backup; conserva gli avvisi di licenza necessari. [Prerequisiti e istruzioni](../README.md) · [Dettagli tecnici](../docs/index.html).

```powershell
Get-FileHash .\VeeamM365RestoreTester-1.3.1-portable.zip -Algorithm SHA256
Expand-Archive .\VeeamM365RestoreTester-1.3.1-portable.zip -DestinationPath C:\Tools\VeeamRestoreTester
```

`release.json` registra versione, dimensioni, numero di file e SHA-256 del pacchetto.
