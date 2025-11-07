from fastapi import FastAPI, HTTPException, Response, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
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

class RecipeSimple(BaseModel):
    id: str
    name: str
    glass: str
    method: str
    ingredients_text: str
    tags: str

# ----------------------------------------------------------
# APP
# ----------------------------------------------------------
app = FastAPI(title="Cocktail Recipes API", version="1.5.0")

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
    try:
        u = urlparse(url)
        if "docs.google.com" in u.netloc and "/spreadsheets/" in u.path:
            if "/d/e/" in u.path:
                parts = u.path.split("/")
                if len(parts) >= 5:
                    doc_id = parts[4]
                    q = parse_qs(u.query, keep_blank_values=True)
                    gid = q.get("gid", ["0"])[0]
                    new_url = f"https://docs.google.com/spreadsheets/d/e/{doc_id}/pub?gid={gid}&single=true&output=csv"
                    return new_url
        return url
    except Exception as e:
        print(f"Erreur conversion URL: {e}")
        return url

# ----------------------------------------------------------
# HTML FRONTEND
# ----------------------------------------------------------
HTML_APP = """<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="theme-color" content="#1a1a2e">
    <title>🍸 Cocktails Bar</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        :root {
            --primary: #e94560;
            --bg-dark: #0f0f23;
            --bg-card: #1a1a2e;
            --text: #eaeaea;
            --text-dim: #a8a8b3;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--bg-dark);
            color: var(--text);
            padding-bottom: 20px;
        }
        header {
            background: linear-gradient(135deg, var(--primary) 0%, #c72a4d 100%);
            padding: 20px;
            text-align: center;
            box-shadow: 0 4px 20px rgba(233, 69, 96, 0.3);
            position: sticky;
            top: 0;
            z-index: 100;
        }
        h1 { font-size: 1.8rem; font-weight: 700; }
        .search-bar {
            padding: 15px 20px;
            background: var(--bg-card);
        }
        #search {
            width: 100%;
            padding: 12px 20px;
            border: 2px solid #2d2d44;
            border-radius: 25px;
            background: var(--bg-dark);
            color: var(--text);
            font-size: 1rem;
            outline: none;
        }
        #search:focus { border-color: var(--primary); }
        .loading {
            text-align: center;
            padding: 40px 20px;
            color: var(--text-dim);
        }
        .spinner {
            border: 3px solid #2d2d44;
            border-top: 3px solid var(--primary);
            border-radius: 50%;
            width: 40px;
            height: 40px;
            animation: spin 1s linear infinite;
            margin: 0 auto 15px;
        }
        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
        .error {
            background: #ff4444;
            color: white;
            padding: 15px 20px;
            margin: 15px 20px;
            border-radius: 10px;
        }
        .cocktails-grid {
            padding: 15px;
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
            gap: 15px;
        }
        .cocktail-card {
            background: var(--bg-card);
            border-radius: 15px;
            cursor: pointer;
            transition: transform 0.2s;
            border: 1px solid #2d2d44;
        }
        .cocktail-card:hover {
            transform: translateY(-4px);
            box-shadow: 0 8px 25px rgba(233, 69, 96, 0.2);
        }
        .card-header {
            background: linear-gradient(135deg, #2d2d44 0%, #3a3a52 100%);
            padding: 15px;
            border-bottom: 2px solid var(--primary);
        }
        .card-name {
            font-size: 1.3rem;
            font-weight: 600;
            text-transform: capitalize;
        }
        .card-body { padding: 15px; }
        .card-info {
            display: flex;
            gap: 10px;
            margin-bottom: 10px;
            flex-wrap: wrap;
        }
        .badge {
            background: #2d2d44;
            padding: 5px 12px;
            border-radius: 15px;
            font-size: 0.85rem;
            color: var(--text-dim);
        }
        .badge.glass {
            background: rgba(233, 69, 96, 0.2);
            color: var(--primary);
        }
        .tags {
            margin-top: 10px;
            display: flex;
            gap: 6px;
            flex-wrap: wrap;
        }
        .tag {
            background: #0f0f23;
            color: var(--text-dim);
            padding: 4px 10px;
            border-radius: 12px;
            font-size: 0.75rem;
        }
        .modal {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0, 0, 0, 0.9);
            z-index: 1000;
            overflow-y: auto;
        }
        .modal.active {
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        .modal-content {
            background: var(--bg-card);
            border-radius: 20px;
            max-width: 600px;
            width: 100%;
            max-height: 90vh;
            overflow-y: auto;
        }
        .modal-header {
            background: linear-gradient(135deg, var(--primary) 0%, #c72a4d 100%);
            padding: 25px;
            position: relative;
        }
        .close-btn {
            position: absolute;
            top: 15px;
            right: 15px;
            background: rgba(255, 255, 255, 0.2);
            border: none;
            color: white;
            width: 35px;
            height: 35px;
            border-radius: 50%;
            font-size: 1.5rem;
            cursor: pointer;
        }
        .modal-title {
            font-size: 1.8rem;
            font-weight: 700;
            text-transform: capitalize;
        }
        .modal-body { padding: 25px; }
        .detail-section { margin-bottom: 25px; }
        .detail-label {
            color: var(--primary);
            font-weight: 600;
            font-size: 0.9rem;
            text-transform: uppercase;
            margin-bottom: 8px;
        }
        .ingredients-list {
            background: var(--bg-dark);
            padding: 15px;
            border-radius: 10px;
            border-left: 3px solid var(--primary);
            white-space: pre-line;
        }
        .empty-state {
            text-align: center;
            padding: 60px 20px;
            color: var(--text-dim);
        }
    </style>
</head>
<body>
    <header><h1>🍸 Cocktails Bar</h1></header>
    <div class="search-bar">
        <input type="text" id="search" placeholder="Rechercher un cocktail...">
    </div>
    <div id="app">
        <div class="loading">
            <div class="spinner"></div>
            <p>Chargement des recettes...</p>
        </div>
    </div>
    <div id="modal" class="modal">
        <div class="modal-content">
            <div class="modal-header">
                <button class="close-btn" onclick="closeModal()">×</button>
                <h2 class="modal-title" id="modalTitle"></h2>
                <div class="card-info" id="modalQuickInfo"></div>
            </div>
            <div class="modal-body" id="modalBody"></div>
        </div>
    </div>
    <script>
        const API_URL = '/api/recipes/simple';
        let cocktails = [];
        let filteredCocktails = [];

        async function loadCocktails() {
            try {
                const response = await fetch(API_URL);
                if (!response.ok) throw new Error('Erreur');
                cocktails = await response.json();
                filteredCocktails = cocktails;
                renderCocktails();
            } catch (error) {
                document.getElementById('app').innerHTML = '<div class="error">⚠️ Erreur de chargement</div>';
            }
        }

        function renderCocktails() {
            const app = document.getElementById('app');
            if (filteredCocktails.length === 0) {
                app.innerHTML = '<div class="empty-state"><p>Aucun cocktail trouvé</p></div>';
                return;
            }
            app.innerHTML = '<div class="cocktails-grid">' + filteredCocktails.map(c => `
                <div class="cocktail-card" onclick="showDetails('${c.id}')">
                    <div class="card-header">
                        <div class="card-name">${c.name}</div>
                    </div>
                    <div class="card-body">
                        <div class="card-info">
                            <span class="badge glass">🥃 ${c.glass}</span>
                            <span class="badge">⚡ ${c.method}</span>
                        </div>
                        ${c.tags ? '<div class="tags">' + c.tags.split(',').map(t => 
                            '<span class="tag">#' + t.trim() + '</span>'
                        ).join('') + '</div>' : ''}
                    </div>
                </div>
            `).join('') + '</div>';
        }

        document.getElementById('search').addEventListener('input', (e) => {
            const q = e.target.value.toLowerCase();
            filteredCocktails = cocktails.filter(c => 
                c.name.toLowerCase().includes(q) || (c.tags && c.tags.toLowerCase().includes(q))
            );
            renderCocktails();
        });

        function showDetails(id) {
            const c = cocktails.find(x => x.id === id);
            if (!c) return;
            document.getElementById('modalTitle').textContent = c.name;
            document.getElementById('modalQuickInfo').innerHTML = 
                '<span class="badge glass">🥃 ' + c.glass + '</span>' +
                '<span class="badge">⚡ ' + c.method + '</span>';
            document.getElementById('modalBody').innerHTML = 
                (c.ingredients_text ? 
                    '<div class="detail-section"><div class="detail-label">🍹 Ingrédients</div>' +
                    '<div class="ingredients-list">' + c.ingredients_text + '</div></div>' : '') +
                (c.tags ? 
                    '<div class="detail-section"><div class="detail-label">🏷️ Tags</div><div class="tags">' +
                    c.tags.split(',').map(t => '<span class="tag">#' + t.trim() + '</span>').join('') +
                    '</div></div>' : '');
            document.getElementById('modal').classList.add('active');
        }

        function closeModal() {
            document.getElementById('modal').classList.remove('active');
        }

        document.getElementById('modal').addEventListener('click', (e) => {
            if (e.target.id === 'modal') closeModal();
        });

        loadCocktails();
    </script>
</body>
</html>"""

# ----------------------------------------------------------
# ROUTES
# ----------------------------------------------------------
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def root():
    """Page d'accueil de l'app"""
    return HTML_APP

@app.get("/api", include_in_schema=False)
def api_root():
    return {
        "ok": True,
        "endpoints": ["/api/health", "/api/recipes", "/api/recipes/simple", "/api/recipes/{slug}"]
    }

@app.get("/api/health")
async def health():
    try:
        rows = await load_rows()
        return {
            "ok": True, 
            "csv_url_set": bool(CSV_URL),
            "recipes_count": len(rows),
            "status": "operational"
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "csv_url_set": bool(CSV_URL)}

@app.get("/api/debug/test-csv", include_in_schema=False)
async def debug_test_csv():
    if not CSV_URL:
        return {"error": "CSV_URL non définie"}
    effective_url = google_pubhtml_to_csv(CSV_URL)
    try:
        async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
            resp = await client.get(effective_url)
            resp.raise_for_status()
            text = resp.text[:2000]
            return {
                "original_url": CSV_URL,
                "effective_url": effective_url,
                "status_code": resp.status_code,
                "content_preview": text,
                "is_html": "<html" in text.lower()
            }
    except Exception as e:
        return {"error": str(e), "original_url": CSV_URL, "effective_url": effective_url}

@app.get("/api/recipes", response_model=List[Recipe])
async def list_recipes():
    rows = await load_rows()
    return [normalize_row(r) for r in rows]

@app.get("/api/recipes/simple", response_model=List[RecipeSimple])
async def list_recipes_simple():
    rows = await load_rows()
    result = []
    for r in rows:
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

@app.get("/api/recipes/{slug}", response_model=Recipe)
async def get_recipe(slug: str):
    rows = await load_rows()
    wanted = slugify(slug.strip())
    for r in rows:
        current = slugify(r.get("slug") or r.get("name", ""))
        if current == wanted:
            return normalize_row(r)
    raise HTTPException(404, detail="Not found")

# ----------------------------------------------------------
# CHARGEMENT CSV
# ----------------------------------------------------------
async def load_rows(force: bool = False):
    if not CSV_URL:
        raise HTTPException(500, detail="CSV_URL not set")
    now = time.time()
    if not force and _cache["rows"] and (now - _cache["at"] < CACHE_TTL):
        return _cache["rows"]

    effective_url = google_pubhtml_to_csv(CSV_URL)
    async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
        resp = await client.get(effective_url)
        resp.raise_for_status()
        text = resp.text

    if "<html" in text.lower():
        raise HTTPException(500, detail="CSV_URL ne renvoie pas un CSV brut")

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

    _cache.update({"rows": rows, "at": now, "meta": {"effective_url": effective_url}})
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

@app.exception_handler(404)
async def not_found(_: Request, __):
    return JSONResponse({"ok": False, "error": "Not Found"}, status_code=404)
