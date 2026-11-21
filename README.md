# McDonald's PL – nutrition search

Search McDonald's Poland nutrition and allergen data in the browser or through a REST API.
The data is parsed from the official PDF (`nutrition_table.pdf`) when the Docker image is built.

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
python parse_pdf.py nutrition_table.pdf data.json
uvicorn server:app --reload
```

Next time, only `source .venv/bin/activate` and `uvicorn server:app --reload` are needed.
Re-run the parse step only when `nutrition_table.pdf` changes.

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

1. Replace `nutrition_table.pdf` with the new PDF.
2. Run `python parse_pdf.py` locally. It prints what changed since last time:
   ```
   Compared with products.lock: 2 new, 1 removed, 3 changed
     + McTriple® (Classic menu)
     - Snack Wrap (Limited-time offers)
     ~ Cheeseburger (Classic menu): 306 -> 315 kcal
   ```
3. Review the list, then commit `nutrition_table.pdf` and `products.lock` together and push.

Committing the lock is mandatory: it starts with the PDF's SHA-256, and the Docker
build runs `parse_pdf.py --check`, which fails if `products.lock` wasn't regenerated
for the current PDF, or if a parser change altered the output.

The parser checks its own output and stops with an error, writing nothing, when:

- a product row doesn't have exactly 26 values, or values appear without a name
- a row in the allergen table matches no product (otherwise that product would
  silently show "no allergens")
- no products or no allergen table are found (e.g. headings were reworded)
- more than 20% of the products in `products.lock` disappeared; if that's a real
  menu change, run `python parse_pdf.py --force` and commit the new `products.lock`

The same checks run during the Docker build, so a PDF the parser can't read, or a
stale lock, fails the GitHub Action and Coolify keeps serving the previous version.

New category headings show in Polish until an English name is added to
`CATEGORY_EN` in `parse_pdf.py`.
