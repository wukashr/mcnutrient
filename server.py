"""McDonald's nutrition search — REST API + browser GUI.

Run:   uvicorn server:app --reload        (then open http://localhost:8000)
Docs:  http://localhost:8000/docs         (auto-generated OpenAPI / Swagger UI)
"""
import json
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse

from textnorm import norm

HERE = Path(__file__).parent
DATA = json.loads((HERE / "data.json").read_text(encoding="utf-8"))
ITEMS = DATA["items"]
BY_ID = {it["id"]: it for it in ITEMS}
for it in ITEMS:  # precomputed search key: diacritics-insensitive ("wiesmac" finds "WieśMac®")
    it["_key"] = norm(f"{it['name']} {it['category']} {it['category_pl']}")

NUTRIENT = Literal["energy_kj", "energy_kcal", "fat_g", "saturated_fat_g", "carbs_g",
                   "sugars_g", "fiber_g", "protein_g", "salt_g"]

app = FastAPI(title="McDonald's PL nutrition API", version="1.0")


def public(it):
    return {k: v for k, v in it.items() if not k.startswith("_")}


def codes(it, field):
    return {a["code"] for a in it["allergens"][field]}


@app.get("/api/items")
def search_items(
    q: Optional[str] = Query(None, description="Text search over product names (Polish) and "
                                               "categories (English or Polish); all words must "
                                               "match, accents ignored"),
    category: Optional[str] = None,
    min_kcal: Optional[float] = Query(None, description="Per portion"),
    max_kcal: Optional[float] = Query(None, description="Per portion"),
    min_protein: Optional[float] = Query(None, description="Grams per portion"),
    exclude_allergens: Optional[str] = Query(
        None, description="Comma-separated codes, e.g. '7,3' or '1' (all gluten). "
                          "Products that contain them are dropped."),
    strict: bool = Query(False, description="Also drop products that *may* contain them"),
    sort: NUTRIENT = "energy_kcal",
    basis: Literal["per_portion", "per_100g"] = "per_portion",
    order: Literal["asc", "desc"] = "asc",
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    words = norm(q).split() if q else []
    excl = {c.strip() for c in exclude_allergens.split(",")} if exclude_allergens else set()

    def blocked(it):
        found = codes(it, "contains") | (codes(it, "may_contain") if strict else set())
        # "1" excludes 1a..1f, "8" excludes 8a..8i
        return any(c in excl or c.rstrip("abcdefghi") in excl for c in found)

    res = [
        it for it in ITEMS
        if all(w in it["_key"] for w in words)
        and (not category or norm(category) in (norm(it["category"]), norm(it["category_pl"])))
        and (min_kcal is None or it["nutrition"]["energy_kcal"]["per_portion"] >= min_kcal)
        and (max_kcal is None or it["nutrition"]["energy_kcal"]["per_portion"] <= max_kcal)
        and (min_protein is None or it["nutrition"]["protein_g"]["per_portion"] >= min_protein)
        and not (excl and blocked(it))
    ]
    res.sort(key=lambda it: it["nutrition"][sort][basis], reverse=order == "desc")
    return {"total": len(res), "offset": offset, "limit": limit,
            "items": [public(it) for it in res[offset:offset + limit]]}


@app.get("/api/items/{item_id}")
def get_item(item_id: int):
    if item_id not in BY_ID:
        raise HTTPException(404, "Item not found")
    return public(BY_ID[item_id])


@app.get("/api/categories")
def categories():
    out = {}
    for it in ITEMS:
        out[it["category"]] = out.get(it["category"], 0) + 1
    return [{"name": k, "count": v} for k, v in out.items()]


@app.get("/api/allergens")
def allergens():
    return [{"code": k, "name": v} for k, v in DATA["legend"].items()]


@app.get("/api/meal")
def meal(ids: str = Query(..., description="Comma-separated item ids, e.g. 3,49,130")):
    """Sum nutrition for a combo (e.g. Big Mac + medium fries + Coke)."""
    try:
        picked = [BY_ID[int(i)] for i in ids.split(",") if i.strip()]
    except (KeyError, ValueError):
        raise HTTPException(400, "Unknown or invalid id")
    totals = {k: round(sum(it["nutrition"][k]["per_portion"] for it in picked), 2)
              for k in picked[0]["nutrition"]} if picked else {}
    return {"items": [it["name"] for it in picked], "totals_per_portion": totals}


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(HERE / "index.html")
