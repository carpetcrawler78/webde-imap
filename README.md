# WEB.DE IMAP -> Gmail Forwarding Bridge

## 1. Zweck

Dieses Repository ist eine reine Transport- und Vorfilter-Bridge: Eine GitHub Action liest periodisch
ausschliesslich neue Mails aus konfigurierten WEB.DE-IMAP-Ordnern (siehe `WEBDE_IMAP_FOLDERS`, Default
nur `INBOX`), klassifiziert sie deterministisch (kein LLM, keine externe API) und leitet alle relevanten
Mails als vollstaendige, normalisierte Kopie per SMTP an ein Gmail-Konto weiter. Gmail ist der einzige
dauerhafte Speicher fuer Mailinhalte -- im Repo landet ausschliesslich technischer IMAP-Zustand.

Hintergrund fuer Multi-Ordner-Support: WEB.DE-seitige Regeln sortieren bei manchen Konten Job-/Social-
Media-Mail automatisch aus `INBOX` heraus in eigene Ordner (z.B. `Unbekannt`, `Social Media`). Nur
`INBOX` zu lesen wuerde in diesem Fall fast keine relevanten Mails erfassen -- deshalb ist die Bridge
so gebaut, dass sie eine beliebige Liste von Ordnern gleichberechtigt verarbeitet, nicht nur INBOX.

**Nicht-Ziele** (bewusst ausserhalb des Scopes dieser Bridge):

- keine LLM-/OpenAI-API-Aufrufe irgendwo im Code
- keine neuen SSOT-Tabs/-Spalten, keine Google-Sheet-Schreibvorgaenge
- kein Scoring/Ranking, keine verbindliche Deduplizierung, keine ATS-/Arbeitgeberpruefung
- keine Serverless-Dienste, keine Drittanbieter-Infrastruktur
- kein Folgen von Links, kein Abrufen von Trackingpixeln/Remote-Bildern, kein Ausfuehren von Anhaengen
- kein Mailinhalt (Betreff, Text, Adressen, URLs, Dateinamen) im Repo, in Logs oder in GitHub Outputs

Diese Aufgaben uebernehmen die bestehenden ChatGPT-Scheduler ("AI/Data Core Discovery",
"Important Email Check", "Job Evaluator") -- siehe Abschnitt 9 (Handoff).

## 2. Architektur

```
je konfiguriertem WEB.DE-Ordner (IMAP, readonly):
    -> imap_client: neue UIDs seit last_processed_uid dieses Ordners ermitteln
    -> mail_content: MIME/HTML zu Text + Linkliste + Anhangsliste
    -> classify: deterministische Kategorie (config/routing_rules.json)
    -> forward: [WEBDE][KATEGORIE]-Mail bauen und per SMTP an Gmail senden
    -> state: runtime/webde_state.json fortschreiben (nur technischer Zustand, pro Ordner)
```

Ordner werden unabhaengig voneinander verarbeitet: ein Fehler in einem Ordner stoppt nur diesen Ordner
(dessen bereits erreichter Fortschritt bleibt erhalten), nicht die anderen konfigurierten Ordner.

Code liegt als Package unter `src/webde_imap/`:

| Modul | Zweck |
|---|---|
| `config.py` | Env-Vars (inkl. Ordnerliste) + `config/routing_rules.json` laden |
| `imap_client.py` | IMAP-Verbindung, UIDVALIDITY, UID SEARCH/FETCH (Ordnernamen werden IMAP-korrekt gequotet) |
| `mail_content.py` | MIME-Walk, HTML-zu-Text + Links (nur `html.parser`, stdlib), Anhaenge |
| `classify.py` | Kategorie-Entscheidung anhand der Routing-Config |
| `event_id.py` | Stabile `WEBDE_EVENT_ID`-Ableitung |
| `forward.py` | Weiterleitungs-Mail bauen + per SMTP senden |
| `state.py` | `runtime/webde_state.json` laden/speichern, State pro Ordner, UIDVALIDITY-Handling |
| `runner.py` | Sequenzielle Pro-UID-Verarbeitung je Ordner mit Partial-Failure-Semantik |
| `cli.py` | Einstiegspunkt (`python -m webde_imap.cli`), inkl. `--dry-run` und Bootstrap-Flags |

## 3. Erforderliche Secrets / Variablen

Repository Secrets (GitHub Settings -> Secrets and variables -> Actions -> Secrets):

| Name | Zweck |
|---|---|
| `WEBDE_IMAP_SERVER` | z.B. `imap.web.de` |
| `WEBDE_IMAP_USERNAME` | WEB.DE-Login |
| `WEBDE_IMAP_PASSWORD` | WEB.DE-App-Passwort |
| `WEBDE_SMTP_SERVER` | z.B. `smtp.web.de` |
| `WEBDE_SMTP_PORT` | z.B. `587` |
| `WEBDE_SMTP_USERNAME` | i.d.R. identisch zu `WEBDE_IMAP_USERNAME` |
| `WEBDE_SMTP_PASSWORD` | SMTP-Passwort/App-Passwort |
| `WEBDE_FORWARD_TO` | Ziel-Gmail-Adresse |

Optionale Repository Variables (Settings -> Secrets and variables -> Actions -> Variables):

| Name | Zweck | Default |
|---|---|---|
| `WEBDE_IMAP_FOLDERS` | Kommagetrennte Liste zu verarbeitender IMAP-Ordner, z.B. `INBOX,Unbekannt,Social Media` | `INBOX` |
| `WEBDE_MAX_ATTACHMENT_TOTAL_BYTES` | Gesamt-Anhangsgroesse pro Mail | `10485760` (10 MiB) |
| `WEBDE_MAX_BODY_CHARS` | Optionale Kuerzung sehr langer Mailtexte | unbegrenzt |

`WEBDE_IMAP_FOLDERS` wird im Workflow sowohl aus Secrets als auch aus Variables gelesen
(`secrets.WEBDE_IMAP_FOLDERS || vars.WEBDE_IMAP_FOLDERS`) -- es ist also egal, in welchem der beiden
Tabs du es anlegst.

Lokal: dieselben Variablen in `.env` (bereits `.gitignore`-geschuetzt).

## 4. Routing-Logik

Sechs Kategorien, geprueft in fester Reihenfolge (short-circuit) in `classify.classify_message()`:

1. **JOB** -- Jobplattformen (LinkedIn/XING Jobs, StepStone, Indeed, jobvector, Get in IT) inkl. Job-Alerts/-Digests
2. **MESSAGE** -- LinkedIn-/XING-Direktnachrichten, persoenliche Recruiter-/Networking-Nachrichten
3. **APPLICATION** -- Bewerbungsstatus (Eingang, Absage, Zusage, Einladung, Vertrag, Fristen)
4. **IMPORTANT** -- Sicherheit, Konto, Vertrag, Behoerde, Versicherung, Rechnung, Fristen allgemein
5. **IGNORE** -- *nur* wenn `List-Unsubscribe`-Header vorhanden **und** ein Marketing-Keyword matcht
6. **REVIEW** -- alles nicht sicher Einzuordnende (Default)

Nur `IGNORE` wird verworfen, alle anderen fuenf Kategorien werden weitergeleitet. Die Job-/Message-
Plattform-Allowlist wird **vor** dem `IGNORE`-Check geprueft, damit Job-Alerts mit Newsletter-Headern
trotzdem als `JOB` erkannt werden.

Regeln liegen in `config/routing_rules.json` (Sender-Domains, Sender-Local-Parts, `List-Id`-Muster,
Subject-/Body-Keywords) und lassen sich dort ohne Codeaenderung erweitern. Die mitgelieferten Listen
sind ein v1-Startpunkt -- bitte nach dem Go-Live durchsehen und ergaenzen, insbesondere bei
Jobplattformen und Marketing-Keywords, da Under-Matching dort die einzige Richtung mit echtem Risiko ist
(ein verpasstes `JOB` landet sicher in `REVIEW`, ein verpasstes `IGNORE` faellt ebenfalls in `REVIEW`,
nicht ins Nichts).

## 5. Inhalt der weitergeleiteten Mail

- Betreff: `[WEBDE][KATEGORIE] <Originalbetreff>`
- Maschinenlesbarer Header-Block (`WEBDE_EVENT_ID`, `WEBDE_CATEGORY`, `ORIGINAL_FOLDER`, `ORIGINAL_UID`,
  `ORIGINAL_MESSAGE_ID`, `ORIGINAL_FROM`, `ORIGINAL_TO`, `ORIGINAL_REPLY_TO`, `ORIGINAL_DATE`,
  `FETCHED_AT_UTC`)
- Vollstaendiger bereinigter Mailtext (bevorzugt `text/plain`; bei reinem HTML wird daraus lesbarer Text
  extrahiert -- keine Trackingpixel, keine Remote-Bilder, kein aktiver HTML-Inhalt)
- Linkliste (Ankertext + Original-URL), ohne dass Links jemals aufgerufen werden
- Anhangsliste (Dateiname, MIME-Typ, Groesse); Anhaenge werden bis zur konfigurierten Gesamtgroesse
  (Default 10 MiB) tatsaechlich mitgeschickt, darueber nur als Metadaten mit Hinweis
  `nicht weitergeleitet` gefuehrt. Anhaenge werden nie ausgefuehrt.

`WEBDE_EVENT_ID` wird deterministisch aus Mailbox-Scope, Folder, UIDVALIDITY, UID und (falls vorhanden)
Message-ID abgeleitet (`event_id.py`) -- damit koennen nachgelagerte Systeme trotz fehlender
Exactly-once-Garantie von SMTP zuverlaessig deduplizieren.

## 6. Datenschutz- und Datengrenzen

- Gmail ist der einzige dauerhafte Speicher fuer Mailinhalte.
- `runtime/webde_state.json` enthaelt ausschliesslich technischen Zustand (UIDVALIDITY, letzte
  verarbeitete UID, Zeitstempel, Laufstatus) -- niemals Absender, Betreff, Text, URLs oder Dateinamen.
- Logs und GitHub-Action-Outputs enthalten nur UID/Kategorie/Event-ID, keine Mailinhalte.
- Keine Zugangsdaten werden geloggt.

## 7. Bootstrap-Verhalten

Beim ersten produktiven Lauf eines Ordners (kein vorhandener State fuer diesen Ordner) wird
`last_processed_uid` auf die aktuell hoechste UID dieses Ordners gesetzt -- es wird **nichts** aus der
bestehenden Mailbox rueckwirkend weitergeleitet. Das gilt pro Ordner unabhaengig: wird `WEBDE_IMAP_FOLDERS`
spaeter um einen weiteren Ordner erweitert, bootstrapped nur der neue Ordner, bestehende Ordner-States
bleiben unangetastet.

Ein kontrollierter manueller Import ist ausschliesslich ueber `workflow_dispatch` moeglich und gilt fuer
**alle** konfigurierten Ordner gleichzeitig:

- `bootstrap_mode: last_n` + `bootstrap_last_n: <N>` -- importiert je Ordner die letzten N Mails
- `bootstrap_mode: time_window` + `bootstrap_window_hours: <Stunden>` -- importiert je Ordner Mails der letzten N Stunden

Beide Modi sind standardmaessig deaktiviert (`bootstrap_mode` ist leer) und laufen ueber dieselbe
Pro-UID-Verarbeitungslogik wie der Normalbetrieb -- anders als der automatische Bootstrap wird dabei
tatsaechlich weitergeleitet, nicht nur der State-Zeiger vorgesetzt.

**Migration von v1-State**: Ein vorhandener `runtime/webde_state.json` im alten Ein-Ordner-Format
(`{"folder": "INBOX", ...}`) wird beim ersten Lauf automatisch verlustfrei in das neue Pro-Ordner-Schema
(`{"folders": {"INBOX": {...}}}`) migriert -- der bisherige Fortschritt fuer diesen Ordner bleibt erhalten,
neu hinzugefuegte Ordner starten bei sich selbst mit einem sauberen Bootstrap.

## 8. Lokale Nutzung, Tests, Dry-Run

```bash
# Tests (stdlib unittest, keine echte IMAP-/SMTP-Verbindung, keine Netzwerkabhaengigkeit)
PYTHONPATH=src python -m unittest discover -s tests -v

# Lokaler Dry-Run gegen das echte WEB.DE-Postfach (.env erforderlich)
# Klassifiziert und zaehlt nur -- sendet nichts, speichert keinen State.
PYTHONPATH=src python -m webde_imap.cli --dry-run
```

Der Dry-Run gibt ausschliesslich `processed=... forwarded=... ignored=... had_failure=...` sowie pro
Mail `uid=... category=... event_id=...` aus -- keine personenbezogenen Mailinhalte.

## 9. GitHub-Workflow-Verhalten

`.github/workflows/webde-imap.yml`:

- Trigger: 15-Minuten-Cron (`7,22,37,52 * * * *`) + `workflow_dispatch` (mit Dry-Run- und
  Bootstrap-Inputs)
- `concurrency`-Gruppe stellt sicher, dass immer nur ein WEB.DE-Lauf aktiv ist
- Ablauf: Checkout -> Python-Setup -> Dependencies -> Unit-Tests (bricht bei rotem Test ab) -> Worker-Lauf
  -> bedingter State-Commit
- `permissions: contents: write` ist ausschliesslich fuer den State-Commit noetig
- Der State (`runtime/webde_state.json`) wird nur committet, wenn er sich tatsaechlich geaendert hat
  (kein Leer-Commit), Commit-Autor ist `github-actions[bot]`
- Mailinhalte erscheinen nie in Logs, Outputs oder Artifacts

## 10. Fehler- und Retry-Verhalten

- Verarbeitung ist strikt sequenziell nach UID, je Ordner unabhaengig; der State wird nach **jeder** UID
  sofort persistiert.
- Schlaegt der SMTP-Versand fehl, wird die betroffene UID nicht als verarbeitet markiert und dieser
  Ordner bricht ab (`break`, kein Ueberspringen) -- bereits erfolgreich verarbeitete UIDs bleiben
  erhalten. Andere konfigurierte Ordner werden davon nicht blockiert und trotzdem weiterverarbeitet.
- Der Workflow-Lauf endet als fehlgeschlagen, sobald irgendein Ordner einen Fehler hatte; der
  State-Commit-Schritt laeuft trotzdem (`if: always()`), damit kein Teilerfolg verloren geht.
- Eine Wiederholung mit demselben State sendet keine bereits erfolgreich verarbeitete Mail erneut.
- **UIDVALIDITY-Wechsel** (seltenes Ereignis bei Server-seitigem Reindex): wird erkannt und loest einen
  stillen Re-Bootstrap aus (State auf die neue UIDVALIDITY + aktuell hoechste UID gesetzt, Warnung ohne
  Mailinhalt geloggt). Es wird bewusst **kein** Massen-Resend ausgeloest -- die Kehrseite ist ein
  theoretisch kleines Luecken-Fenster genau am Wechselzeitpunkt.

## 11. Handoff an die bestehenden ChatGPT-Scheduler

**Nur Dokumentation -- dieses Repository aendert weder Google Sheet noch ChatGPT-Automation.**
Folgende Anpassungen sind spaeter manuell in den bestehenden Scheduler-Aufgaben vorzunehmen:

### AI/Data Core Discovery

Zusaetzlich Gmail-Mails mit Praefix `[WEBDE][JOB]` als automatisierte Jobquelle beruecksichtigen. Die
Mail vollstaendig auswerten, moegliche Sammelmails in einzelne Stellen zerlegen und plausible Kandidaten
nach dem bestehenden Discovery-Vertrag nach `RAW_DISCOVERY` schreiben. Verbindliche Deduplizierung und
offizielle Pruefung bleiben beim Job Evaluator.

### Important Email Check

Gmail-Mails mit Praefixen `[WEBDE][APPLICATION]`, `[WEBDE][MESSAGE]`, `[WEBDE][IMPORTANT]` und
`[WEBDE][REVIEW]` beruecksichtigen. Den bestehenden Scope zusaetzlich auf allgemeine wichtige
Nachrichten mit realem Handlungsbedarf erweitern. Keine Rohmails in der SSOT speichern. Nur kompakte
relevante Meldungen nach `NTFY_OUTBOX!A:F`; G:K bleiben ausschliesslich beim externen Worker.
