# main.py
from fastapi import FastAPI, HTTPException, Response, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Optional, Dict
import csv, io, time, os, json, unicodedata, re
import httpx
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

# ----------------------------------------------------------
# CONFIG
# ----------------------------------------------------------
CSV_URL = os.environ.get("CSV_URL", "").strip()
CACHE_TTL = 60  # secondes
_cache = {"at": 0.0, "rows": [], "meta": {}}

# ----------------------------------------------------------
# MODELES
# ----------------------------------------------------------
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

# ----------------------------------------------------------
# APP
# ----------------------------------------------------------
app = FastAPI(title="Cocktail Recipes API", version="1.3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------------------------------------------
# UTILS
# ----------------------------------------------------------
CANONICAL = [
    "name","slug","glass","method","ice","garnish",
    "ingredients","spec_ml","spec_oz","history","tags",
    "abv_est","notes","source","last_update"
]
CANON_SET = set(CANONICAL)

def norm_header(h: str) -> str:
    h = (h or "").strip().lower()
    h = unicodedata.normalize("NFD", h)
    h = "".join(c for c in h if unicodedata.category(c) != "Mn")
    h = re.sub(r"[^a-z0-9]+", "_", h).strip("_")
    remap = {"specml": "spec_ml", "specoz": "spec_oz", "lastupdate": "last_update"}
    return remap.get(h, h)

def build_header_map(fieldnames: List[str]) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    used = set()
    for orig in fieldnames or []:
        n = norm_header(orig)
        if n in CANON_SET and n not in used:
            mapping[orig] = n
            used.add(n)
        else:
            mapping[orig] = orig
    return mapping

def slugify(s: str) -> str:
    s = s.lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s

def google_pubhtml_to_csv(url: str) -> str:
    """Convertit automatiquement un lien pubhtml en lien CSV"""
    try:
        u = urlparse(url)
        if "docs.google.com" in u.netloc and "/spreadsheets/" in u.path and u.path.endswith("/pubhtml"):
            new_path = u.path[:-7]  # retire 'pubhtml'
            if not new_path.endswith("/"):
                new_path += "/"
            new_path += "pub"
            q = parse_qs(u.query, keep_blank_values=True)
            q["output"] = ["csv"]
            new_query = urlencode({k: v[0] if isinstance(v, list) else v for k, v in q.items()})
            fixed = urlunparse((u.scheme, u.netloc, new_path, "", new_query, ""))
            return fixed
        return url
    except Exception:
        return url

# ----------------------------------------------------------
# ROUTES
# ----------------------------------------------------------
@app.get("/", include_in_schema=False)
def root():
    return {
        "ok": True,
        "endpoints": ["/health", "/recipes", "/recipes/names", "/recipes/{slug}", "/debug/source"]
    }

@app.get("/health")
async def health():
    return {"ok": True, "csv_url_set": bool(CSV_URL)}

@app.get("/debug/source", include_in_schema=False)
async def debug_source():
    await load_rows(force=True)
    meta = _cache.get("meta", {})
    return {
        "csv_url_effective": meta.get("effective_url"),
        "rows_count": len(_cache.get("rows") or []),
        "fieldnames_original": meta.get("fieldnames_original"),
        "note": "Si rows_count = 0, vérifie le lien Google Sheet et le partage public."
    }

@app.get("/recipes", response_model=List[Recipe])
async def list_recipes():
    rows = await load_rows()
    return [normalize_row(r) for r in rows]

@app.get("/recipes/{slug}", response_model=Recipe)
async def get_recipe(slug: str):
    rows = await load_rows()
    wanted = slugify(slug.strip())
    for r in rows:
        current = slugify(r.get("slug") or r.get("name", ""))
        if current == wanted:
            return normalize_row(r)
    raise HTTPException(404, detail="Not found")

@app.get("/recipes/names", response_model=List[str])
async def recipe_names():
    rows = await load_rows()
    return [(r.get("name") or "").strip() for r in rows if (r.get("name") or "").strip()]

# ----------------------------------------------------------
# CHARGEMENT CSV
# ----------------------------------------------------------
async def load_rows(force: bool = False):
    if not CSV_URL:
        raise HTTPException(500, detail="CSV_URL environment variable not set")

    now = time.time()
    if not force and _cache["rows"] and (now - _cache["at"] < CACHE_TTL):
        return _cache["rows"]

    effective_url = google_pubhtml_to_csv(CSV_URL)

    async with httpx.AsyncClient(
        timeout=25,
        follow_redirects=True,
        headers={"User-Agent": "cocktail-recipes-api/1.3", "Accept": "text/csv,*/*"},
    ) as client:
        resp = await client.get(effective_url)
        resp.raise_for_status()
        text = resp.text

    if "<html" in text.lower():
        raise HTTPException(
            500,
            detail="CSV_URL ne renvoie pas un CSV brut. Vérifie le lien 'output=csv' et les droits de partage."
        )

    text = text.lstrip("\ufeff")

    delimiter = ","
    try:
        sniffer = csv.Sniffer()
        dialect = sniffer.sniff(text[:1024], delimiters=[",", ";", "\t"])
        delimiter = dialect.delimiter
    except Exception:
        pass

    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    hmap = build_header_map(reader.fieldnames or [])
    rows = [{hmap.get(k, k): v for k, v in row.items()} for row in reader]
    rows = [r for r in rows if (r.get("name") or "").strip()]

    _cache.update({
        "rows": rows,
        "at": now,
        "meta": {"effective_url": effective_url, "fieldnames_original": reader.fieldnames},
    })
    return rows

# ----------------------------------------------------------
# NORMALISATION
# ----------------------------------------------------------
def normalize_row(raw: dict) -> Recipe:
    slug = slugify(raw.get("slug") or raw.get("name", ""))
    tags = [t.strip() for t in (raw.get("tags") or "").split(",") if t.strip()]
    ingredients = None
    ings_val = (raw.get("ingredients") or "").strip()
    if ings_val.startswith("["):
        try:
            data = json.loads(ings_val)
            ingredients = [Ingredient(**x) for x in data]
        except Exception:
            pass
    return Recipe(
        name=(raw.get("name") or "").strip(),
        slug=slug,
        glass=raw.get("glass"),
        method=raw.get("method"),
        ice=raw.get("ice"),
        garnish=raw.get("garnish"),
        ingredients=ingredients,
        spec_ml=raw.get("spec_ml"),
        spec_oz=raw.get("spec_oz"),
        history=raw.get("history"),
        tags=tags,
        abv_est=float(raw.get("abv_est")) if (raw.get("abv_est") or "").replace(".", "").isdigit() else None,
        notes=raw.get("notes"),
        source=raw.get("source"),
        last_update=raw.get("last_update"),
    )

# ----------------------------------------------------------
# ERREUR 404
# ----------------------------------------------------------
@app.exception_handler(404)
async def not_found(_: Request, __):
    return JSONResponse({"ok": False, "error": "Not Found", "hint": "Try /docs or /recipes"}, status_code=404)
