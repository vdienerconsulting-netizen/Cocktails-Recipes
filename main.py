# main.py
from fastapi import FastAPI, HTTPException, Response, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Optional, Dict
import csv, io, time, os, json, unicodedata, re
import httpx

# ----- Config -----
CSV_URL = os.environ.get("CSV_URL", "").strip()
CACHE_TTL = 60  # secondes
_cache = {"at": 0.0, "rows": [], "meta": {}}

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

# CORS permissif pour démarrer
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Log des routes au démarrage
@app.on_event("startup")
async def _log_routes():
    try:
        print("ROUTES:", [r.path for r in app.router.routes])
    except Exception:
        pass

# ----- Utils entêtes -----
CANONICAL = [
    "name","slug","glass","method","ice","garnish",
    "ingredients","spec_ml","spec_oz","history","tags",
    "abv_est","notes","source","last_update"
]
CANON_SET = set(CANONICAL)

def norm_header(h: str) -> str:
    # Nettoie: trim, lowercase, enlève accents, remplace non-alnum par _
    h = (h or "").strip().lower()
    h = unicodedata.normalize("NFD", h)
    h = "".join(c for c in h if unicodedata.category(c) != "Mn")
    h = re.sub(r"[^a-z0-9]+", "_", h).strip("_")
    # Remap légers
    remap = {
        "specml": "spec_ml",
        "specoz": "spec_oz",
        "lastupdate": "last_update",
    }
    return remap.get(h, h)

def build_header_map(fieldnames: List[str]) -> Dict[str, str]:
    # Mappe entêtes d'origine -> entêtes canoniques si proches
    mapping: Dict[str, str] = {}
    used = set()
    for orig in fieldnames or []:
        n = norm_header(orig)
        if n in CANON_SET and n not in used:
            mapping[orig] = n
            used.add(n)
        else:
            # Si ça ne colle pas, garde l'original (on ne jette pas l'info)
            mapping[orig] = orig
    return mapping

def remap_row(row: dict, hmap: Dict[str, str]) -> dict:
    out = {}
    for k, v in row.items():
        out[hmap.get(k, k)] = v
    return out

# ----- Routes utilitaires -----
@app.get("/", include_in_schema=False)
def root():
    return JSONResponse({
        "ok": True,
        "endpoints": ["/health", "/recipes", "/recipes/{slug}", "/docs", "/debug/source"]
    })

@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)

@app.get("/health")
async def health():
    return {"ok": True, "csv_url_set": bool(CSV_URL)}

@app.get("/debug/source", include_in_schema=False)
async def debug_source():
    # Montre ce que l'API a détecté (délimiteur, entêtes après normalisation, nb lignes)
    await load_rows(force=True)  # refresh meta
    meta = _cache.get("meta", {})
    return {
        "csv_url_set": bool(CSV_URL),
        "detected_delimiter": meta.get("delimiter"),
        "fieldnames_original": meta.get("fieldnames_original"),
        "header_map": meta.get("header_map"),
        "rows_count": len(_cache.get("rows") or []),
        "note": "Assure-toi d'utiliser Fichier > Publier sur le web (format CSV) sur l'onglet contenant les recettes."
    }

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
    wanted = slugify(slug.strip())
    for r in rows:
        raw_slug = (r.get("slug") or "").strip()
        current = slugify(raw_slug) if raw_slug else slugify(r.get("name", ""))
        if current == wanted:
            return normalize_row(r)
    raise HTTPException(404, detail="Not found")

# ----- Chargement CSV -----
async def load_rows(force: bool = False):
    if not CSV_URL:
        raise HTTPException(500, detail="CSV_URL environment variable not set")

    now = time.time()
    if not force and _cache["rows"] and (now - _cache["at"] < CACHE_TTL):
        return _cache["rows"]

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(CSV_URL)
        resp.raise_for_status()
        text = resp.text

    # Nettoyage BOM + trim
    text = text.lstrip("\ufeff")

    # Détecte le délimiteur
    try:
        sample = text[:2048]
        sniffer = csv.Sniffer()
        dialect = sniffer.sniff(sample, delimiters=[",", ";", "\t"])
        delimiter = dialect.delimiter
    except Exception:
        # Fallback: virgule, puis ; si la virgule ne marche pas
        delimiter = ","

    # Lecture DictReader
    buf = io.StringIO(text)
    reader = csv.DictReader(buf, delimiter=delimiter)
    fieldnames = reader.fieldnames or []

    # Construis le header map
    hmap = build_header_map(fieldnames)

    # Remap + filtre lignes vides (name obligatoire, en tolérant les variantes d'entête)
    rows_raw = [remap_row(r, hmap) for r in reader]
    def get_name(d):
        # récupère 'name' même si l'entête originale était un peu différente
        return (d.get("name") or d.get("Name") or d.get("NAME") or "").strip()

    rows = [r for r in rows_raw if get_name(r)]

    _cache["rows"] = rows
    _cache["at"] = now
    _cache["meta"] = {
        "delimiter": delimiter,
        "fieldnames_original": fieldnames,
        "header_map": hmap
    }
    return rows

# ----- Normalisation recette -----
def normalize_row(raw: dict) -> Recipe:
    raw_slug = (raw.get("slug") or "").strip()
    slug = slugify(raw_slug) if raw_slug else slugify(raw.get("name", ""))
    # ingredients en JSON (si présent)
    ingredients: Optional[List[Ingredient]] = None
    ings_val = (raw.get("ingredients") or "").strip()
    if ings_val.startswith("["):
        try:
            tmp = json.loads(ings_val)
            ingredients = [Ingredient(**x) for x in tmp]
        except Exception:
            ingredients = None

    # tags CSV -> liste
    tags = [t.strip() for t in (raw.get("tags") or "").split(",") if t.strip()]
    # abv
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

# ----- 404 propre -----
@app.exception_handler(404)
async def not_found(_: Request, __):
    return JSONResponse(
        {"ok": False, "error": "Not Found", "hint": "Try /docs or /recipes"},
        status_code=404
    )
