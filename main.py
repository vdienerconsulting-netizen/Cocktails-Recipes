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

# Nouveau modèle simplifié pour FlutterFlow
class RecipeSimple(BaseModel):
    id: str  # slug utilisé comme ID
    name: str
    glass: str
    method: str
    ingredients_text: str  # Version texte des ingrédients
    tags: str  # Tags en format texte séparé par virgules

# ----------------------------------------------------------
# APP
# ----------------------------------------------------------
app = FastAPI(title="Cocktail Recipes API", version="1.4.0")

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
        if "docs.google.com" in u.netloc and "/spreadsheets/" in u.path:
            # Extraire l'ID du spreadsheet
            if "/d/e/" in u.path:
                # Format: /spreadsheets/d/e/DOCUMENT_ID/...
                parts = u.path.split("/")
                if len(parts) >= 5:
                    doc_id = parts[4]
                    q = parse_qs(u.query, keep_blank_values=True)
                    gid = q.get("gid", ["0"])[0]
                    # Construire l'URL CSV correcte
                    new_url = f"https://docs.google.com/spreadsheets/d/e/{doc_id}/pub?gid={gid}&single=true&output=csv"
                    return new_url
        return url
    except Exception as e:
        print(f"Erreur conversion URL: {e}")
        return url

# ----------------------------------------------------------
# ROUTES
# ----------------------------------------------------------
@app.get("/", include_in_schema=False)
def root():
    return {
        "ok": True,
        "endpoints": [
            "/health", 
            "/recipes", 
            "/recipes/simple",  # NOUVEAU
            "/recipes/names", 
            "/recipes/{slug}", 
            "/debug/source",
            "/debug/test-csv"  # NOUVEAU
        ]
    }

@app.get("/health")
async def health():
    try:
        # Test si on peut charger les données
        rows = await load_rows()
        return {
            "ok": True, 
            "csv_url_set": bool(CSV_URL),
            "recipes_count": len(rows),
            "status": "operational"
        }
    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "csv_url_set": bool(CSV_URL)
        }

@app.get("/debug/test-csv", include_in_schema=False)
async def debug_test_csv():
    """Teste la connexion au CSV et affiche les données brutes"""
    if not CSV_URL:
        return {"error": "CSV_URL non définie"}
    
    effective_url = google_pubhtml_to_csv(CSV_URL)
    
    try:
        async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
            resp = await client.get(effective_url)
            resp.raise_for_status()
            text = resp.text[:2000]  # Premiers 2000 caractères
            
            return {
                "original_url": CSV_URL,
                "effective_url": effective_url,
                "status_code": resp.status_code,
                "content_preview": text,
                "is_html": "<html" in text.lower(),
                "content_type": resp.headers.get("content-type")
            }
    except Exception as e:
        return {
            "error": str(e),
            "original_url": CSV_URL,
            "effective_url": effective_url
        }

@app.get("/debug/source", include_in_schema=False)
async def debug_source():
    await load_rows(force=True)
    meta = _cache.get("meta", {})
    return {
        "csv_url_original": CSV_URL,
        "csv_url_effective": meta.get("effective_url"),
        "rows_count": len(_cache.get("rows") or []),
        "fieldnames_original": meta.get("fieldnames_original"),
        "first_row_sample": _cache.get("rows")[0] if _cache.get("rows") else None,
        "note": "Si rows_count = 0, vérifie le lien Google Sheet et le partage public."
    }

@app.get("/recipes", response_model=List[Recipe])
async def list_recipes():
    rows = await load_rows()
    return [normalize_row(r) for r in rows]

# NOUVEAU ENDPOINT SIMPLIFIÉ POUR FLUTTERFLOW
@app.get("/recipes/simple", response_model=List[RecipeSimple])
async def list_recipes_simple():
    """Version simplifiée pour FlutterFlow - plus facile à afficher"""
    rows = await load_rows()
    result = []
    
    for r in rows:
        # Convertir les ingrédients en texte simple
        ingredients_text = ""
        ings_val = (r.get("ingredients") or "").strip()
        if ings_val.startswith("["):
            try:
                data = json.loads(ings_val)
                ingredients_text = "\n".join([
                    f"{ing.get('item', '')} - {ing.get('ml', '')}ml" 
                    for ing in data if ing.get('item')
                ])
            except:
                ingredients_text = r.get("spec_ml") or r.get("spec_oz") or ""
        else:
            ingredients_text = r.get("spec_ml") or r.get("spec_oz") or ""
        
        result.append(RecipeSimple(
            id=slugify(r.get("slug") or r.get("name", "")),
            name=(r.get("name") or "").strip(),
            glass=(r.get("glass") or "Non spécifié").strip(),
            method=(r.get("method") or "Non spécifié").strip(),
            ingredients_text=ingredients_text,
            tags=(r.get("tags") or "").strip()
        ))
    
    return result

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
    print(f"Loading CSV from: {effective_url}")  # Log pour debug

    async with httpx.AsyncClient(
        timeout=25,
        follow_redirects=True,
        headers={"User-Agent": "cocktail-recipes-api/1.4", "Accept": "text/csv,*/*"},
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
    
    print(f"Loaded {len(rows)} recipes")  # Log pour debug
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
