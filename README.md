# JBG PDF Proofreader

En fristående FastAPI-webbtjänst för restriktiv korrekturläsning av textbaserade PDF-filer. Den är utformad efter samma övergripande arbetsflöde som JBGLangImprover, men ändrar aldrig originaldokumentet. Resultatet är en Excel-fil med ett minimalt språkförslag per rad.

## Funktioner

- accepterar endast PDF och avvisar dokument utan tillräckligt textlager
- ingen OCR och inget stöd för skannade PDF-filer
- koordinatbaserad extraktion med vänster spalt före höger spalt
- identifierar `Kapitel N` och versnummer i början av rader
- infererar vers 1 när den inte är utskriven
- använder sida/spalt/rad som reservhänvisning
- behandlar fotnoter och korshänvisningar som löpande text
- skickar texten i begränsade batcher till OpenAI Responses API
- använder strukturerad modellutdata och lokal validering
- avvisar oankrade, dubblerade och oproportionerligt stora ändringar
- skapar Excel-flikarna `Ändringsförslag`, `Extraherad text`, `Körinformation` och `Avvisade förslag`
- har offlineläge (`mock`) för installationstest utan API-nyckel

## Installation

Python 3.11 eller senare rekommenderas.

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

python -m pip install -r requirements.txt
cp .env_template .env  # valfritt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Öppna `http://127.0.0.1:8000`.

På Windows kan du i stället köra:

```powershell
.\start_local_service.ps1
```

## Test utan OpenAI

Markera **Offlinetest utan API-anrop** i webbgränssnittet. Det provar uppladdning, PDF-kontroll, extraktion, strukturering, validering och Excel-export. Mockläget gör ingen faktisk språkgranskning.

Kommandoradstest:

```bash
python -m pip install -r requirements-dev.txt
pytest -q
```

## API

`POST /review` som `multipart/form-data`:

- `file`: PDF-fil
- `api_key`: OpenAI API-nyckel; kan lämnas tom om `OPENAI_API_KEY` finns i miljön
- `model`: modellnamn
- `mock`: `true` för offlinetest

Exempel:

```bash
curl -X POST http://127.0.0.1:8000/review \
  -F "file=@dokument.pdf" \
  -F "api_key=$OPENAI_API_KEY" \
  -F "model=gpt-5-mini" \
  --output korrektur.xlsx
```

## Integritet och drift

PDF-texten skickas till vald OpenAI-modell när mockläget inte används. API-nyckeln sparas inte av applikationen. Diagnostik och resultat sparas som standard under `runs/`; sätt `KEEP_RUN_FILES=false` om du senare lägger till automatisk städning eller kör katalogen på temporär lagring.

## Begränsningar

- spaltindelningen antar i första hand två ungefär lika breda spalter
- avancerade tabeller och fler än två spalter kan kräva dokumentprofiler
- rubriker och fotnoter räknas förenklat in i textflödet enligt nuvarande krav
- modellen kan fortfarande göra misstag; Excel-filen är ett granskningsunderlag, inte en automatisk rättning
