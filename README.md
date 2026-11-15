# McDonald's PL – nutrition search

Search McDonald's Poland nutrition and allergen data in the browser or through a REST API.
The data is parsed from the official PDF (`nutrient_table.pdf`) when the Docker image is built.

The PDF is in Polish, so product names stay in Polish. Everything else (UI, API,
categories, allergen names) is in English; each item also keeps its original Polish
category in `category_pl`. Search ignores Polish accents, so `wiesmac` finds `WieśMac®`.

## Run locally

With Docker:

```bash
docker build -t mcn .
docker run -p 8000:8000 mcn
```

Without Docker (needs only Python 3.15):

```bash
python3.15 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python parse_pdf.py nutrient_table.pdf data.json
uvicorn server:app --reload
```

Next time, only `source .venv/bin/activate` and `uvicorn server:app --reload` are needed.
Re-run the parse step only when `nutrient_table.pdf` changes.

Open http://localhost:8000. API docs are at http://localhost:8000/docs.

## API

| Endpoint | Description |
|---|---|
| `GET /api/items` | Search and filter (`q`, `category`, `max_kcal`, `exclude_allergens`, `sort`, …) |
| `GET /api/items/{id}` | One product |
| `GET /api/categories` | Categories |
| `GET /api/allergens` | Allergen codes |
| `GET /api/meal?ids=3,50,130` | Nutrition totals for several products |

## Deploy

Every push to `main` builds the image, pushes `ghcr.io/<owner>/<repo>:latest`
and restarts the Coolify service so it pulls the new image.

Set these in GitHub → Settings → Secrets and variables → Actions:

- `COOLIFY_URL` (secret): e.g. `https://coolify.example.com`
- `COOLIFY_TOKEN` (secret): Coolify API token with deploy permission
- `COOLIFY_SERVICE_UUID` (secret): the service's UUID

The GHCR image is private by default. Make it public, or log the Coolify server
in to `ghcr.io` with a token that has `read:packages`.

## Updating the data

Replace `nutrient_table.pdf` with the new PDF and push. The image build parses it again.
