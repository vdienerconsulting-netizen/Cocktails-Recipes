# main.py
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from typing import List, Optional
import csv, io, time, os, json, unicodedata, re
import httpx

# ----- Config -----
CSV_URL = os.environ.get("CSV_URL", "").strip()
CACHE_TTL = 60  # secondes
_cache = {"at": 0.0, "rows": []}

# ----- Schémas -----
class Ingredient(BaseModel):
    item: str
    ml: Optional[float] = None
    oz: Optional[float] = None

class Recipe(BaseModel):
    name: str
    slug: str
    glass: Optional[str] = None
    method: Optional[str] = None
    ice: Optional[str] = None
    garnish: Optional[str] = None
    ingredients: Optional[List[Ingredient]] = None
    spec_ml: Optional[str] = None
    spec_oz: Optional[str] = None
    history: Optional[str] = None
    tags: List[str] = []
    abv_est: Optional[float] = None
    notes: Optional[str] = None
    source: Optional[str] = None
    last_update: Optional[str] = None

# ----- App -----
app = FastAPI(title="Cocktail Recipes API", version="1.0.0")

# CORS ouvert pour démarrer (resserre plus tard si besoin)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----- Routes utilitaires -----
@app.get("/", include_in_schema=False)
def index():
    # Redirige la racine vers la doc interactive
    return RedirectResponse(url="/docs")

@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    # Évite les 404 pour le favicon
    return Response(status_code=204)

@app.get("/health")
async def health():
    return {"ok": True, "csv_url_set": bool(CSV_URL)}

# ----- Endpoints métier -----
@app.get("/recipes", response_model=List[Recipe])
async def list_recipes(q: Optional[str] = None, tag: Optional[str] = None):
    rows = await load_rows()
    data = [normalize_row(r) for r in rows]
    if q:
        ql = q.lower()
        data = [
            r for r in data
            if ql in (r.name or "").lower()
            or ql in (r.spec_ml or "").lower()
            or ql in json.dumps([ing.dict() for ing in (r.ingredients or [])]).lower()
        ]
    if tag:
        tl = tag.lower()
        data = [r for r in data if any((t or "").lower() == tl for t in r.tags)]
    return data

@app.get("/recipes/{slug}", response_model=Recipe)
async def get_recipe(slug: str):
    rows = await load_rows()
    for r in rows:
        s = r.get("slug") or slugify(r.get("name", ""))
        if s == slug:
            return normalize_row(r)
    raise HTTPException(404, detail="Not found")

# ----- Helpers -----
async def load_rows():
    if not CSV_URL:
        raise HTTPException(500, detail="CSV_URL environment variable not set")

    now = time.time()
    if _cache["rows"] and (now - _cache["at"] < CACHE_TTL):
        return _cache["rows"]

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(CSV_URL)
        resp.raise_for_status()
        text = resp.text

    buf = io.StringIO(text)
    reader = csv.DictReader(buf)
    rows = [r for r in reader if (r.get("name") or "").strip()]

    _cache["rows"] = rows
    _cache["at"] = now
    return rows

def normalize_row(raw: dict) -> Recipe:
    slug = raw.get("slug") or slugify(raw.get("name", ""))
    # ingredients en JSON (si présent)
    ingredients: Optional[List[Ingredient]] = None
    ings_val = (raw.get("ingredients") or "").strip()
    if ings_val.startswith("["):
        try:
            tmp = json.loads(ings_val)
            ingredients = [Ingredient(**x) for x in tmp]
        except Exception:
            ingredients = None

    tags = [t.strip() for t in (raw.get("tags") or "").split(",") if t.strip()]
    try:
        abv = float(raw.get("abv_est")) if raw.get("abv_est") else None
    except Exception:
        abv = None

    return Recipe(
        name=(raw.get("name") or "").strip(),
        slug=slug,
        glass=raw.get("glass") or None,
        method=raw.get("method") or None,
        ice=raw.get("ice") or None,
        garnish=raw.get("garnish") or None,
        ingredients=ingredients,
        spec_ml=raw.get("spec_ml") or None,
        spec_oz=raw.get("spec_oz") or None,
        history=raw.get("history") or None,
        tags=tags,
        abv_est=abv,
        notes=raw.get("notes") or None,
        source=raw.get("source") or None,
        last_update=raw.get("last_update") or None,
    )

def slugify(s: str) -> str:
    s = s.lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s
