"""Generate searchable API contracts and exact source from the current tree."""
from pathlib import Path
import ast
import html
import json
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
CONTEXT = {
 'main.py': 'Entrypoint. Crea QApplication e finestra, oppure esegue --self-test in una destinazione nuova.',
 'app.py': 'Controller GUI. Riceve eventi Qt, conserva lo stato del batch e coordina discovery, coda e finalizzazione. Gli handler senza return esplicito restituiscono None; gli effetti sono sui widget, sui segnali o sul filesystem.',
 'core/config.py': 'Persistenza JSON in APPDATA o nel percorso iniettato. Solo chiavi DEFAULTS, replace atomico in scrittura; valori manuali non sottoposti a validazione completa dei tipi.',
 'core/paths.py': 'Risoluzione dei percorsi source/frozen e ricerca PowerShell nel PATH. Nessuna connessione esterna.',
 'core/discovery.py': 'Processo PowerShell di sola lettura inventario. Payload preceduto da __VEEAM_INVENTORY__; segnali completed(dict) o failed(str).',
 'core/queue.py': 'Stato: pending(index, JobTask), active[index], results[index], running e cancelled. Massimo 1–4 processi; mai due job della stessa organizzazione casefold. Risultati in ordine selezione.',
 'core/runner.py': 'Wrapper QProcess. Canali stdout/stderr uniti; decoder incrementale UTF-8. Segnali output(line,level), phase_changed(label), finished(code), failed_to_start(error).',
 'core/batch_report.py': 'Aggregazione e output locale. Le funzioni build copiano i job; copy/write aggiornano LocalFile nel riepilogo ricevuto. ReportErrors è distinto da AllSuccessful.',
 'core/html_report.py': 'Rendering locale con valori HTML escaped e immagini base64 cached. Nessun accesso a file personali di sviluppo o servizi remoti.',
 'core/pdf_report.py': 'QWebEngine/Chromium: render A4, event loop locale, callback printToPdf e timeout 30 secondi. RuntimeError se caricamento/output non valido.',
 'core/reports.py': 'Modello Report e cronologia filesystem. JSON UTF-8/BOM; letture non valide escluse, scansione potata, ordine mtime decrescente.',
 'core/retention.py': 'Gestisce solo directory dirette RestoreTest_timestamp, escluse symlink/junction. Cancella le più vecchie per nome; errori OS ignorati, minimo una sessione.',
 'core/diagnostics.py': 'Autotest locale isolato. Nessuna discovery, backup o restore Veeam. Produce campioni sintetici, tre formati documento, JSON e quattro PNG.',
 'ui/widgets.py': 'Widget riutilizzabili: card workload, log e albero checkable organizzazione/job. Selezioni filtrate restano nello stato del widget.',
 'ui/dialogs.py': 'Dialoghi modali dell’app. Solo ask_confirmation restituisce bool; i wrapper informativi attendono la chiusura del dialogo.',
 'ui/theme.py': 'Palette e stylesheet Qt condiviso. Percorsi icone risolti tramite resource_path.',
}
NOTES = {
 'main': 'Instrada --self-test DEST prima della finestra normale; altrimenti imposta nome, stile Fusion, font, icona e avvia app.exec().',
 'cleanup_batch_workspace': 'Riceve workspace opzionale e directory report opzionale. Preserva il report indicato durante la pulizia; ritorna messaggio errore oppure None. Non eliminare una root evidenze arbitraria.',
 'load_reports': 'Seleziona ultimo report per organizzazione, ordina quelli che richiedono attenzione, popola tabella e card; lista vuota mostra stato idle.',
 '_select_organization': 'Legge la riga selezionata nella tabella e aggiorna dettagli usando il Report associato.',
 '_open_evidence': 'Apre con QDesktopServices la directory del report selezionato, se disponibile.',
 'load_report': 'Report opzionale → badge complessivo, card workload, dati backup, fase più lenta ed errori documento; None ripristina idle.',
 'sync_defaults': 'Ricarica root e skip-backup dalle impostazioni solo se non è in corso un batch.',
 'refresh_inventory': 'Evita discovery duplicate, verifica pwsh/script, disabilita controlli e avvia InventoryDiscovery; visualizza errore se prerequisiti assenti.',
 '_inventory_loaded': 'Payload inventario → flatten, albero e selezioni persistite, contatori, stato discovery completato.',
 '_inventory_failed': 'Messaggio errore → console/stato UI; ripristina i controlli di caricamento.',
 '_set_all_jobs': 'Applica Qt.CheckState a tutti i job, inclusi quelli nascosti dal filtro.',
 '_set_visible_jobs': 'Applica Qt.CheckState ai soli job visibili nel filtro corrente.',
 '_filter_jobs': 'Stringa query → filtro locale nell’albero e riepilogo conteggi; non ricarica Veeam.',
 '_update_selection_summary': 'Ricalcola organizzazioni/job selezionati e abilita avvio se inventario e selezione sono validi.',
 '_set_configuration_enabled': 'Booleano → abilita/disabilita i controlli operativi durante il batch.',
 '_selected_multi_org_jobs': 'Converte le coppie selezionate nell’albero in lista JobTask.',
 '_browse_restore': 'Dialogo cartella locale; aggiorna il campo solo se l’utente sceglie un percorso.',
 'RunPage.start': 'Valida selezioni, root, shell, script e collisioni nomi. Salva selezione, riserva staging univoco, avvia timer e queue con snapshot impostazioni.',
 '_queue_progress': 'completed/total/activity → progress determinato, numero worker e fasi attive.',
 '_queue_complete': 'Lista risultati e cancelled → stato finale batch e _finish_batch().',
 'RunPage.stop': 'Chiede conferma; solo accettazione invoca cancel_run().',
 'cancel_run': 'Marca stop_requested, disabilita Stop e delega a queue.stop().',
 '_finish_batch': 'Raggruppa risultati per organizzazione; scrive report/retention. Preserva staging in caso di stop o errori documento; ripristina UI e callback finale 0/2.',
 '_elapsed_seconds': 'Restituisce secondi interi monotonic del batch, minimo zero; zero se non avviato.',
 '_update_elapsed': 'Aggiorna etichetta timer attraverso format_duration(_elapsed_seconds()).',
 '_open_test_folder': 'Apre la directory del batch/report disponibile dopo il run.',
 'refresh': 'Lista Report opzionale → popola cronologia; senza lista legge restore_root. Non effettua chiamate Veeam.',
 '_select': 'Indice riga → dettagli organizzazione, job, workload, tempi e pulsanti dei documenti disponibili.',
 '_current': 'Restituisce Report della riga selezionata oppure None.',
 '_open_folder': 'Apre directory del Report corrente se presente.',
 '_open_txt': 'Apre Report_Summary.txt del Report corrente se presente.',
 '_open_html': 'Apre Report_Summary.html del Report corrente se presente.',
 '_open_pdf': 'Apre Report_Summary.pdf del Report corrente se presente.',
 '_browse_script': 'Dialogo file .ps1; aggiorna percorso custom se la selezione non è annullata.',
 '_save': 'Valida root, esistenza script custom e almeno un formato; salva default, invoca callback e mostra conferma.',
 '_is_admin': 'Interroga IsUserAnAdmin su Windows; false se la chiamata non è disponibile.',
 'show_page': 'Indice pagina 0–3 → stack corrente e pulsante navigazione; aggiorna cronologia dove previsto.',
 'refresh_all': 'Una sola list_reports per aggiornare Dashboard e Reports.',
 '_run_finished': 'Callback termine batch: ricarica Dashboard e Reports.',
 '_settings_saved': 'Aggiorna default RunPage e cronologia dopo salvataggio preferenze.',
 'closeEvent': 'Se batch attivo chiede stop e attende idle; altrimenti chiude e arresta discovery eventualmente attiva.',
 '_close_when_idle': 'Callback timer che riprova la chiusura dopo la finalizzazione del batch.',
 'load': 'Legge JSON UTF-8 e fonde sole chiavi note; FileNotFound/JSONDecode/OSError mantengono default. Ritorna copia del dizionario.',
 'save': 'Fonde valori noti, crea parent, scrive file .tmp e replace atomico; errori I/O propagano al chiamante.',
 'get': 'Restituisce valore della chiave o default esplicito.',
 'resource_path': 'Relativo → base sys._MEIPASS o repository; ritorna Path.',
 'bundled_script': 'Restituisce Path del motore incluso sotto scripts/.',
 'discovery_script': 'Restituisce Path dello script inventario incluso.',
 'resolve_script': 'Usa configured.strip non vuoto, altrimenti motore incluso; expanduser e resolve. Non verifica esistenza.',
 'powershell_path': 'shutil.which(pwsh.exe) oppure which(pwsh); stringa o None.',
 'parse_inventory_output': 'Cerca marker partendo dall’ultima linea; richiede object con Organizations list. JSONDecodeError/ValueError in caso contrario.',
 'flatten_inventory': 'Payload → mappa nome org a lista Name/Id dei job; nomi vuoti scartati.',
 'InventoryDiscovery.start': 'shell/script → QProcess con NoLogo/NoProfile/NonInteractive/ExecutionPolicy Bypass. Ignora chiamata se processo già attivo.',
 'InventoryDiscovery._finished': 'Decodifica stdout UTF-8, parse marker e segnala completed o failed. L’exit code non sostituisce la validazione payload.',
 'InventoryDiscovery._error': 'FailedToStart → failed(errorString); altri errori restano nel flusso conclusivo.',
 'is_running': 'Restituisce process.state diverso da NotRunning.',
 'JobQueue.start': 'tasks e keyword di runtime → inizializza pending/results, clamp concurrency 1–4, programma _pump. RuntimeError se batch già attivo.',
 '_pump': 'Riempie slot con organizzazioni non attive; crea root univoca per indice e connette segnali. Quando pending e active sono vuoti emette finished in ordine input.',
 '_output': 'Indice worker ancora attivo → output con prefisso org/job; ignora indici terminati.',
 '_phase': 'Aggiorna fase del worker ancora attivo e notifica progresso.',
 '_progress': 'Emette len(results), total e righe delle fasi attive.',
 '_failed': 'Logga errore avvio e conclude il worker con exit -1.',
 '_complete': 'Rimuove worker, valida report per identità/mtime, misura tempo, scollega segnali e programma prossimo pump. Doppio completamento ignorato.',
 'JobQueue.stop': 'Cancella pending con risultati espliciti, chiama stop sui worker attivi e riprogramma pump; nessun effetto se idle.',
 'PowerShellRunner.start': 'Costruisce argv separati, senza shell interpolation. Aggiunge SkipBackup e SampleCandidateLimit opzionali; reset decoder/buffer e avvia QProcess.',
 'PowerShellRunner.stop': 'terminate non bloccante seguito da kill timer 2500 ms; nessun effetto se idle.',
 '_read_output': 'Decodifica byte incrementali, emette righe complete e conserva frammento finale nel buffer.',
 '_emit_line': 'Ignora linee vuote; tag ERR/WARN/OK determinano livello, regex determinano fase informativa.',
 'PowerShellRunner._finished': 'Ferma timer, drena output residuo/decoder ed emette exit code; crash con codice zero diventa -1.',
 'PowerShellRunner._error': 'FailedToStart → failed_to_start(process.errorString()).',
 'normalize_status': 'Valore arbitrario → stringa uppercase, underscore convertiti in spazi e alias italiani/inglesi normalizzati; usa default se vuota.',
 'overall_status': 'Usa stato esplicito valido o AllSuccessful; altrimenti classifica combinazione workload/exit come SUCCESS, WARNING o FAILED.',
 'client_report_summary': 'Deepcopy senza durata aggregata/report e senza duration_seconds/PhaseTimings dei job; originale invariato.',
 'copy_workload_artifacts': 'Legge LocalFile aggregati e per-job; copia file/fratelli nelle cartelle workload, gestisce omonimi e aggiorna riferimenti. FileNotFoundError se manca evidenza.',
 'build_batch_summary': 'Organizzazione, risultati, modalità e timestamp opzionali → dizionario aggregato indipendente; successo esige tutti i job validi.',
 '_job_is_successful': 'Entry → bool: exit 0, report presente, almeno un successo e nessun fallimento, cancellazione, FatalError o CleanupErrors.',
 'build_organization_summaries': 'Raggruppa risultati in ordine di prima comparsa; somma durata per org; lista riepiloghi separati.',
 'write_batch_report': 'Root/summary/max_keep/formati → Path JSON finale. Riserva directory, copia, JSON atomico, formati, errori, timing e retention. Modifica summary con percorsi finali e ReportErrors.',
 '_get_logo_data_uri': 'Legge logo incluso una volta con cache; data URI PNG o stringa vuota.',
 '_get_footer_logo_data_uri': 'Legge footer incluso, fallback logo incluso; data URI cached o vuota.',
 '_get_workload_logo_data_uri': 'Chiave email/onedrive/sharepoint → PNG incluso base64 cached; chiave/file assente → stringa vuota.',
 '_job_successful_workloads': 'Lista nomi workload SUCCESS nel report del job; tollera report mancante.',
 '_job_has_workload_success': 'Valuta job per badge/KPI: richiede successo senza workload falliti, errori fatali/cleanup e uscita negativa.',
 'render_html_report': 'Summary → stringa documento HTML completo con CSS e immagini inline; escaping per dati utente e stato per-job.',
 'write_html_report': 'Root/summary → scrive UTF-8 Report_Summary.html e ritorna Path.',
 'write_pdf_report': 'Root/summary/html_content opzionale → PDF A4 valido; riusa HTML se fornito. Richiede Qt event loop; solleva RuntimeError su fallimento/timeout.',
 '_on_load_finished': 'Callback bool: errore termina loop; successo chiama printToPdf con callback e page_layout.',
 '_on_pdf_ready': 'Callback byte PDF → memorizza risultato e termina event loop.',
 'passed': 'Property bool derivata da AllSuccessful del JSON.',
 'timestamp': 'Property stringa DD Mon YYYY · HH:MM dal RunTimestamp; fallback mtime se formato invalido.',
 'read_report': 'Path → Report(path,data,mtime) se JSON object valido, altrimenti None; UTF-8 BOM accettato.',
 'list_reports': 'Root → lista Report per mtime decrescente; prune payload/report, no symlink walk/junction, profondità massima 3.',
 'latest_report': 'Primo elemento list_reports oppure None.',
 'human_size': 'Byte numerici → B/KB/MB/GB in base 1024; em dash per dato invalido.',
 'format_duration': 'Secondi → HH:MM:SS arrotondato e minimo zero; em dash per dato invalido.',
 '_test_dirs': 'Directory org → sottocartelle sessione ammesse, ordinate oldest-first per nome.',
 'enforce_retention': 'Cancella oldest-first finché <= max_keep (minimo 1); ritorna solo percorsi eliminati con successo; OSError ignorati.',
 'sanitize_org_name': 'Sostituisce caratteri fuori A-Za-z0-9._- con underscore, trim punti/underscore; fallback org.',
 'run_self_test': 'DEST nuovo → report e GUI sintetici, result.json ed exit 0/1. Esistenza destinazione rifiutata prima di scrivere; nessuna discovery Veeam.',
 'update_data': 'Dizionario workload opzionale → stato, colore, nomi e dimensione della card; NOT_CONFIGURED neutro.',
 'append_line': 'Testo/livello → append console con colore e scorrimento finale.',
 '_update_org_text': 'Elemento organizzazione → etichetta con selezionati/totali.',
 '_on_item_changed': 'Propaga check parent→figli o ricostruisce tri-state parent; blocca segnali ricorsivi durante aggiornamento.',
 'load_inventory': 'Mappa org→job e selezioni opzionali → albero popolato, metadata ruolo Qt e checkbox; preserva le selezioni fornite.',
 'filter_items': 'Query case-insensitive → visibilità di org/job; ritorna conteggi visibili senza alterare selezioni.',
 'selected_jobs': 'Ritorna lista (org_name, job_name) dei figli checked, compresi quelli nascosti.',
 'selected_dict': 'Raggruppa selected_jobs in mappa org→nomi job.',
 'set_all': 'Applica check state a tutti i figli, aggiorna parent ed emette selection_changed.',
 'set_visible': 'Applica check state solo ai figli visibili, aggiorna parent ed emette selection_changed.',
 'exec': 'Adatta dimensione dialogo, centra sul parent e avvia loop modale; ritorna codice Accepted/Rejected.',
 'show_information': 'Apre dialogo informativo o success; attende chiusura, ritorna None.',
 'show_warning': 'Apre dialogo warning; attende chiusura, ritorna None.',
 'show_error': 'Apre dialogo error; attende chiusura, ritorna None.',
 'ask_confirmation': 'Titolo/messaggio/etichette/destructive → dialogo; true solo se Accepted.',
 'stylesheet': 'Ritorna stylesheet Qt completo con percorso assoluto checkbox incluso.',
}

def functions(node, prefix=''):
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield prefix + child.name, child
            yield from functions(child, prefix + child.name + '.')
        elif isinstance(child, ast.ClassDef):
            yield from functions(child, prefix + child.name + '.')
        else:
            yield from functions(child, prefix)


def main() -> None:
    escape = html.escape
    entries = []
    missing = []
    counts = {}
    for name, context in CONTEXT.items():
        path = ROOT / name
        source = path.read_text(encoding='utf-8-sig')
        tree = ast.parse(source)
        cards = []
        for qualified, node in functions(tree):
            note = NOTES.get(qualified, NOTES.get(node.name, ast.get_docstring(node)))
            if node.name == '__init__':
                note = f'Costruisce {qualified.rsplit(".", 1)[0]} con i parametri riportati; inizializza stato, dipendenze e callback indicati nell’implementazione. {context}'
            if not note:
                missing.append(name + ':' + qualified)
                note = 'Implementazione riportata integralmente sotto.'
            signature = f'{qualified}({ast.unparse(node.args)})'
            annotation = ast.unparse(node.returns) if node.returns else 'Non annotato: vedere return ed effetti descritti.'
            returns = [ast.unparse(n.value) if n.value else 'None' for n in ast.walk(node) if isinstance(n, ast.Return)]
            return_text = '; '.join(dict.fromkeys(returns)) if returns else 'None implicito; risultato tramite stato/segnali/file descritti.'
            code = ast.get_source_segment(source, node)
            identifier = re.sub(r'[^a-zA-Z0-9_-]', '-', name + '-' + qualified)
            cards.append(f'<article class="entry" id="{identifier}"><h3>{escape(qualified)}</h3><span class="badge">{escape(name)}:{node.lineno}</span><pre>{escape(signature)}</pre><p>{escape(note)}</p><p><b>Ingresso:</b> parametri e default nella firma; self è l’istanza corrente, cls la classe. <b>Uscita dichiarata:</b> <code>{escape(annotation)}</code>.</p><details><summary>Rami return (incluse callback annidate)</summary><pre>{escape(return_text)}</pre></details><details><summary>Implementazione esatta e gestione errori</summary><pre><code>{escape(code)}</code></pre></details></article>')
        counts[name] = len(cards)
        entries.append(f'<section><h2>{escape(name)}</h2><p>{escape(context)}</p>{"".join(cards)}</section>')
    if missing:
        raise ValueError('Missing function contracts: ' + ', '.join(missing))
    # PowerShell's own parser provides exact function extents and parameter blocks.
    ps_command = "$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); $rows=@(); foreach($f in Get-ChildItem -LiteralPath 'scripts' -Filter '*.ps1'){ $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile($f.FullName,[ref]$tokens,[ref]$errors); if($errors.Count){throw $errors}; foreach($fn in $ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst]},$true)){ $rows += @{File=$f.Name;Name=$fn.Name;Line=$fn.Extent.StartLineNumber;Params=[string]$fn.Body.ParamBlock;Source=$fn.Extent.Text} } }; ConvertTo-Json -InputObject $rows -Depth 4"
    result = subprocess.run(['pwsh', '-NoProfile', '-Command', ps_command], cwd=ROOT, check=True, capture_output=True, encoding='utf-8')
    ps_notes = {
        'Write-StepHeader': 'Message string → intestazione console colorata; nessun oggetto di pipeline.',
        'Write-InfoLog': 'Message string → Write-Host con prefisso [INFO].',
        'Write-SuccessLog': 'Message string → Write-Host con prefisso [OK].',
        'Write-WarnLog': 'Message string → Write-Host con prefisso [WARN].',
        'Write-ErrorLog': 'Message string → Write-Host con prefisso [ERR]; il log da solo non termina il processo.',
        'Write-SampleExportWarning': 'Message string → Write-WarnLog se presente, altrimenti Write-Warning.',
        'Test-RestoreDocumentCandidate': 'Item anche null, RequireExtension opzionale → bool. Rifiuta null, nome vuoto, IsContainer/IsFolder e, se richiesto, nome senza estensione.',
        'Invoke-VerifiedSampleExport': 'Candidates object[], DestinationRoot, ExportAction(candidate,attemptDirectory), Workload, MaxAttempts 1–1000 default 25, DisableRandomization opzionale → oggetto Success/Candidate/File/SHA256/Attempts/LastError. Ripulisce tentativi in finally, salta export vuoti/errori, limita move alla root, verifica file finale e hash. ExportAction scrive sul filesystem e non deve emettere candidati in pipeline.',
    }
    ps_cards = []
    for fn in json.loads(result.stdout):
        note = ps_notes[fn['Name']]
        ps_cards.append(f'<article class="entry"><h3>{escape(fn["Name"])}</h3><span class="badge">scripts/{escape(fn["File"])}:{fn["Line"]}</span><p>{escape(note)}</p><pre>{escape(fn["Params"])}</pre><details><summary>Implementazione esatta</summary><pre>{escape(fn["Source"])}</pre></details></article>')
    for script in sorted((ROOT / 'scripts').glob('*.ps1')):
        ps_cards.append(f'<article class="entry"><h3>{escape(script.name)} · programma completo</h3><p>Parametri, sequenza top-level, filtri, catch/finally e codici d’uscita. Le callback ExportAction ricevono candidato e cartella tentativo; salvano tramite il cmdlet del workload.</p><details><summary>Script completo</summary><pre>{escape(script.read_text(encoding="utf-8-sig"))}</pre></details></article>')
    count = sum(counts.values())
    header = f'<!doctype html><html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Riferimento funzioni · Restore Tester 1.3.1</title><link rel="stylesheet" href="manual.css"></head><body><aside><b>API · 1.3.1</b><p><a href="index.html">← Manuale tecnico</a></p><label>Cerca nome, parametro o effetto<input class="search" id="search" type="search"></label><output id="count" aria-live="polite"></output><p>{count} funzioni Python; {len(ps_notes)} funzioni PowerShell.</p><a href="#powershell">PowerShell →</a></aside><main><h1>Contratti delle funzioni</h1><p>Generato da <code>tools/build_reference.py</code>. Ogni firma proviene dall’AST del codice corrente. Le descrizioni chiariscono ingressi, uscite ed effetti; l’implementazione espandibile conserva tutti i rami e i dettagli degli errori. I parametri non annotati mantengono il comportamento dinamico Python; non sono convertiti in uno schema più restrittivo inventato.</p>'
    footer = '<script>const cards=[...document.querySelectorAll(".entry")];function filter(){const query=document.getElementById("search").value.toLowerCase();let n=0;for(const c of cards){c.hidden=!c.textContent.toLowerCase().includes(query);if(!c.hidden)n++}for(const section of document.querySelectorAll("main section")){section.hidden=![...section.querySelectorAll(".entry")].some(c=>!c.hidden)}document.getElementById("count").textContent=n+" schede visibili"}document.getElementById("search").addEventListener("input",filter);filter();</script></main></body></html>'
    document = header + ''.join(entries) + '<section id="powershell"><h2>PowerShell</h2>' + ''.join(ps_cards) + '</section>' + footer
    document = '\n'.join(line.rstrip() for line in document.splitlines()) + '\n'
    (ROOT / 'docs/reference.html').write_text(document, encoding='utf-8', newline='\n')
    print(json.dumps(dict(python_functions=count, powershell_functions=len(ps_notes), modules=counts)), flush=True)


if __name__ == '__main__':
    main()
