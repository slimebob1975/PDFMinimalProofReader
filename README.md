
# PDF Minimal ProofReader

En fristående FastAPI-webbtjänst för restriktiv korrekturläsning av textbaserade PDF-filer. Tjänsten är byggd efter samma övergripande arbetsflöde som JBGLangImprover, men ändrar aldrig originaldokumentet. Resultatet är en Excel-fil med ett minimalt och nödvändigt språkförslag per rad.

## Funktioner

- accepterar PDF-filer med textlager
- ingen OCR och inget stöd för skannade PDF-filer
- koordinatbaserad extraktion med stöd för tvåspaltslayout
- identifierar `Kapitel N` och versnummer i början av textenheter
- infererar vers 1 när den inte är utskriven
- använder sida och intern textposition som reservhänvisning när kapitel/vers saknas
- behandlar fotnoter och korshänvisningar som en del av textflödet
- AI-policyn är restriktiv och ska endast föreslå nödvändiga rättelser, t.ex. stavfel, grammatikfel, syftningsfel och interpunktionsfel
- AI-policyn ska ignorera inskjutna bibelreferenser/korshänvisningar och layoutrelaterade mellanslagsproblem mellan fotnotsmarkörer och fotnotstext
- vald GPT-modell väljs i webbgränssnittet från en dropdown-lista
- skickar texten i begränsade batcher till OpenAI Responses API
- använder strukturerad modellutdata och lokal validering
- avvisar oankrade, dubblerade och oproportionerligt stora ändringar
- visar löpande körningsinformation i terminalen, inklusive uppskattat totalt antal GPT-anrop och aktuell anropsräknare
- låser webbformuläret under körning och visar en spinner tills Excel-filen har genererats och nedladdningen startats
- skapar Excel-flikarna `Ändringsförslag`, `Extraherad text`, `Körinformation` och `Avvisade förslag`
- har ett offlineläge (`mock`) för installationstest utan API-anrop

## Rekommenderad installation och start på Windows

Den rekommenderade arbetsgången är:

1. Klona repot.
2. Anpassa den lokala konfigurationen högst upp i `start_local_service.ps1`.
3. Kör startskriptet från PowerShell.
4. Öppna tjänsten i webbläsaren.

Exempel:

```powershell
git clone <REPO-URL>
cd PDFMinimalProofReader
.\start_local_service.ps1
```

Om PowerShell blockerar lokala skript kan du för den aktuella PowerShell-processen tillåta körning med:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

Kör därefter:

```powershell
.\start_local_service.ps1
```

Startskriptet gör automatiskt följande:

- skapar en virtuell Python-miljö under `C:\temp` om den inte redan finns
- installerar/uppgraderar `pip`
- kör `git pull` i projektkatalogen
- installerar paketen i `requirements.txt`
- installerar/uppgraderar `uvicorn[standard]`
- verifierar att FastAPI-applikationen kan importeras
- startar tjänsten med Uvicorn

Som standard öppnas tjänsten på:

```text
http://127.0.0.1:8060
```

### Rader i `start_local_service.ps1` som kan behöva anpassas lokalt

Konfigurationen ligger högst upp i skriptet. Framför allt bör du kontrollera dessa rader:

```powershell
$TempDir = 'C:\temp'
$DevRoot = $PSScriptRoot
$BasePython = 'C:\Users\Grro\AppData\Local\Programs\Python\Python313\python.exe'

$VenvName = 'pdfminimalproofreader\_venv'

$App  = 'app.main:app'
$Port = 8060
```

Normalt behöver endast följande ändras:

- **`$BasePython`** – måste peka på den Python-installation som finns på den lokala datorn. Kontrollera vid behov med:

  ```powershell
  where.exe python
  py -0p
  ```
- **`$TempDir`** – ändra endast om du inte vill eller kan använda `C:\temp`.
- **`$VenvName`** – kan ändras om du vill ge den virtuella miljön ett annat namn. Den ligger under `$TempDir` och inte inne i Git-repot.
- **`$Port`** – ändra om port `8060` redan används eller om du vill köra tjänsten på en annan port.

Följande bör normalt **inte** behöva ändras:

- **`$DevRoot = $PSScriptRoot`** – gör att skriptet automatiskt använder katalogen där `start_local_service.ps1` ligger. Därför kan repot klonas eller flyttas utan att sökvägen hårdkodas.
- **`$App = 'app.main:app'`** – FastAPI-applikationens importväg.

### Första körningen

Vid första körningen skapas den virtuella miljön. På den aktuella Windows/Python 3.13-konfigurationen skapas den med `--without-pip`, varefter `ensurepip` körs separat. Detta finns i startskriptet för att undvika att `python -m venv` fastnar under den automatiska pip-installationen.

Om en tidigare misslyckad virtuell miljö behöver byggas om kan den tas bort manuellt:

```powershell
Remove-Item -Recurse -Force 'C:\temp\pdfminimalproofreader\_venv' -ErrorAction SilentlyContinue
```

Kör sedan startskriptet igen.

## Manuell start

Startskriptet är den rekommenderade metoden på Windows, men tjänsten kan även startas manuellt.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8060 --reload
```

På macOS/Linux kan motsvarande göras med:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8060 --reload
```

## Användning

1. Öppna `http://127.0.0.1:8060`.
2. Välj PDF-fil.
3. Ange OpenAI API-nyckel, om den inte redan finns i miljön.
4. Välj GPT-modell i dropdown-listan.
5. Välj vid behov **Offlinetest utan API-anrop**.
6. Klicka på knappen för att starta granskningen.

Under körningen låses formuläret och en spinner visas tills analysen är klar. Terminalen visar samtidigt extraktionsstatus och GPT-anropens framdrift.

När körningen är klar startas nedladdningen av Excel-filen automatiskt och formuläret återställs.

## Test utan OpenAI

Markera **Offlinetest utan API-anrop** i webbgränssnittet. Det provar uppladdning, PDF-kontroll, extraktion, strukturering, validering och Excel-export. Mockläget gör ingen faktisk språkgranskning.

För att köra testsviten:

```powershell
python -m pip install -r requirements-dev.txt
pytest -q
```

## API

`POST /review` använder `multipart/form-data` med bland annat:

- `file`: PDF-fil
- `api_key`: OpenAI API-nyckel; kan lämnas tom om `OPENAI_API_KEY` finns i miljön
- `model`: vald modell
- `mock`: `true` för offlinetest

Exempel:

```bash
curl -X POST http://127.0.0.1:8060/review \
  -F "file=@dokument.pdf" \
  -F "api_key=$OPENAI_API_KEY" \
  -F "model=gpt-5-mini" \
  --output korrektur.xlsx
```

## Filer, JSON och körningsdata

Vid start skapas katalogen `uploads` i projektroten om den inte redan finns.

Varje körning får en egen undermapp, exempelvis:

```text
uploads/
  20260810T065012Z_a1b2c3d4/
    dokument.pdf
    extraction.json
    suggestions_raw.json
    suggestions_rejected.json
    dokument_korrektur.xlsx
```

Filerna används enligt följande:

- `dokument.pdf` – kopia av den uppladdade PDF-filen
- `extraction.json` – extraherad och strukturerad text samt diagnostisk information
- `suggestions_raw.json` – den samlade strukturerade modellutdatan efter parsning
- `suggestions_rejected.json` – förslag som den lokala valideringen avvisat
- `<filnamn>_korrektur.xlsx` – genererad Excel-fil

API-nyckeln sparas inte i dessa filer.

Katalogen `uploads/` bör normalt ligga i `.gitignore` så att uppladdade dokument, körningsdata och resultat inte råkar versionshanteras.

## Excel-resultat

Fliken **Ändringsförslag** är avsiktligt förenklad. Den innehåller bland annat:

- Förslags-ID
- Dokument
- Kapitel
- Vers
- Hänvisning
- PDF-sida
- Ursprunglig text
- Föreslagen text
- Feltyp
- Motivering
- Kontext
- Säkerhet
- Status

Interna layoutfält som spalt och radintervall visas inte i denna flik.

## Integritet

När offlineläget inte används skickas den extraherade PDF-texten till den OpenAI-modell som valts i webbgränssnittet. Original-PDF-filen ändras aldrig.

API-nyckeln används endast för API-anropet och sparas inte av applikationen.

## Begränsningar

- skannade PDF-filer stöds inte
- layoutanalysen är främst anpassad för dokument med en eller två spalter
- avancerade tabeller, ovanlig typografi eller komplexa sidlayouter kan kräva ytterligare dokumentprofiler
- fotnoter och korshänvisningar behandlas förenklat som del av textflödet
- AI-modellen kan fortfarande göra misstag; Excel-filen är ett granskningsunderlag och ingen automatisk korrigering av originalet
