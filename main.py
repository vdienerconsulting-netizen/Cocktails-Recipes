from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional, Dict
import csv, io, time, os, json, unicodedata, re
import httpx
from urllib.parse import urlparse, parse_qs

# ----------------------------------------------------------
# CONFIG
# ----------------------------------------------------------
CSV_URL = os.environ.get("CSV_URL", "").strip()
ACCESS_CODE = os.environ.get("ACCESS_CODE", "orgeatsalécestmeilleur")
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
app = FastAPI(title="Cocktail Recipes API", version="1.9.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Fichiers statiques (visuels)
app.mount("/static", StaticFiles(directory="static"), name="static")

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
                    return f"https://docs.google.com/spreadsheets/d/e/{doc_id}/pub?gid={gid}&single=true&output=csv"
        return url
    except Exception:
        return url

# ----------------------------------------------------------
# ACCES (gate)
# ----------------------------------------------------------
def has_access(request: Request) -> bool:
    return request.cookies.get("cv_access") == "1"

def require_access(request: Request):
    if not has_access(request):
        # On renvoie 401 côté API, et la page login côté /
        raise HTTPException(401, detail="Unauthorized")

# ----------------------------------------------------------
# PAGES HTML — Dark + hero qui devient header
# ----------------------------------------------------------
LOGIN_HTML = """<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Accès — Chez Vincent</title>
  <link rel="preconnect" href="https://fonts.googleapis.com"/>
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
  <link href="https://fonts.googleapis.com/css2?family=Bayon&family=Big+Shoulders+Text:wght@400;700&family=Raleway:wght@300;400&display=swap" rel="stylesheet">
  <style>
    :root{ --bg:#0f0f14; --panel:#17181f; --line:#2a2b31; --text:#e5e7eb; --muted:#9aa0a6; }
    *{margin:0;padding:0;box-sizing:border-box}
    body{ background:var(--bg); color:var(--text); font-family:Raleway, system-ui, -apple-system, Segoe UI, Roboto, sans-serif; }
    .wrap{ min-height:100vh; display:flex; align-items:center; justify-content:center; padding:24px; }
    .card{ width:100%; max-width:460px; border:1px solid var(--line); border-radius:8px; background:var(--panel); }
    .head{ padding:18px; border-bottom:1px solid var(--line); text-align:center; }
    .title{ font-family:Bayon,sans-serif; letter-spacing:.06em; font-size:30px; }
    .body{ padding:18px; }
    label{ display:block; font-size:14px; color:var(--muted); margin-bottom:6px; }
    input[type="password"]{
      width:100%; border:none; border-bottom:1px solid var(--text);
      background:transparent; color:var(--text); padding:10px 2px; font-size:16px; outline:none;
    }
    .row{ margin-top:14px; display:flex; justify-content:center; }
    button{ all:unset; border:1px solid var(--text); color:var(--text); padding:8px 14px; border-radius:4px; cursor:pointer; }
    .logos{ text-align:center; padding:16px 0 6px; border-bottom:1px solid var(--line); background:transparent; }
    .logos img{ display:block; margin:0 auto 10px; height:auto; }
    .logos .title{ width:min(70%,640px); }
    .logos .subtitle{ width:min(60%,520px); opacity:.9; }
  </style>
</head>
<body>
  <div class="logos">
    <img class="title" src="/static/ui/chez-vincent-titre.png" alt="Chez Vincent"/>
    <img class="subtitle" src="/static/ui/chez-vincent-soustitre.png" alt="Sous-titre"/>
  </div>
  <div class="wrap">
    <form class="card" method="GET" action="/enter">
      <div class="head"><div class="title">BIENVENUE</div></div>
      <div class="body">
        <label for="code">Code d’accès</label>
        <input id="code" name="code" type="password" placeholder="••••••••" required />
        <div class="row"><button type="submit">Valider</button></div>
      </div>
    </form>
  </div>
</body>
</html>"""

HTML_APP = """<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Chez Vincent's Recipes</title>
  <meta name="description" content="Buvette cocktail — recettes" />
  <link rel="preconnect" href="https://fonts.googleapis.com"/>
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
  <link href="https://fonts.googleapis.com/css2?family=Bayon&family=Big+Shoulders+Text:wght@400;700&family=Raleway:wght@300;400&display=swap" rel="stylesheet">
  <style>
    :root{ --bg:#0f0f14; --panel:#17181f; --line:#2a2b31; --text:#e5e7eb; --muted:#9aa0a6; --headerH:96px; }
    *{margin:0;padding:0;box-sizing:border-box}
    body{ background:var(--bg); color:var(--text); font-family:Raleway, system-ui, -apple-system, Segoe UI, Roboto, sans-serif; }

    /* -------- HERO qui devient HEADER -------- */
    .heroHeader{
      position: fixed; inset:0; z-index:999;
      display:flex; flex-direction:column; align-items:center; justify-content:center;
      background:var(--bg); border-bottom:1px solid transparent;
      transition: height .7s ease, padding .7s ease, transform .7s ease, border-color .7s ease, background .7s ease;
      height: 100vh; padding: 24px 16px;
    }
    .heroHeader .logoWrap{
      display:flex; flex-direction:column; align-items:center; gap:8px;
      transform: translateY(0); transition: transform .7s ease, scale .7s ease, opacity .7s ease;
    }
    .heroHeader img{ display:block; height:auto; }
    .heroHeader .title{ width:min(70%, 640px); }
    .heroHeader .subtitle{ width:min(60%, 520px); opacity:.9; }

    /* ÉTAT "réduit" = header sticky */
    .heroHeader.shrink{
      height: var(--headerH);
      padding: 8px 12px;
      align-items:center; justify-content:center;
      border-bottom-color: var(--line);
      background: rgba(15,15,20,0.92);
    }
    .heroHeader.shrink .logoWrap{
      transform: translateY(0);
    }
    .heroHeader.shrink .title{ width: 260px; }
    .heroHeader.shrink .subtitle{ width: 220px; opacity:.85; }

    /* Contenu page (fade-in) */
    .page{ opacity:0; transform: translateY(8px); transition: opacity .45s ease .15s, transform .45s ease .15s; }
    .page.show{ opacity:1; transform: translateY(0); }

    /* Espace en haut pour ne pas passer sous le header sticky */
    main{ padding-top: calc(var(--headerH) + 8px); }

    /* Search */
    .search{ padding:16px; border-bottom:1px solid var(--line); }
    .search input{
      width:100%; font:400 16px/1.3 Raleway, sans-serif; padding:10px 2px;
      border:none; outline:none; background:transparent; border-bottom:1px solid var(--text); color:var(--text);
    }
    .search input::placeholder{ color:var(--muted); }

    /* Grid */
    .grid{ padding:16px; display:grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap:12px; }
    .card{ background:var(--panel); border:1px solid var(--line); border-radius:6px; cursor:pointer; }
    .card-head{ padding:12px; border-bottom:1px solid var(--line); }
    .name{ font-family:"Big Shoulders Text",sans-serif; font-weight:700; font-size:20px; line-height:1.1; color:var(--text); }
    .card-body{ padding:12px; }
    .meta{ display:flex; flex-wrap:wrap; gap:8px; margin-bottom:6px; font-size:13px; color:var(--muted); }
    .meta .item{ border-bottom:1px solid var(--line); padding-bottom:1px; }
    .tags{ display:flex; flex-wrap:wrap; gap:6px; margin-top:8px; font-size:12px; color:var(--muted); }
    .tag{ border:1px solid var(--line); border-radius:999px; padding:3px 8px; }
    .center{ text-align:center; padding:48px 16px; color:var(--muted); }

    /* Modal */
    .modal{ position: fixed; inset:0; display:none; background: rgba(0,0,0,.4); z-index:998; padding:16px; }
    .modal.active{ display:block; }
    .panel{ background:var(--panel); border:1px solid var(--line); border-radius:8px; max-width:780px; margin:5vh auto; overflow:hidden; }
    .modal-head{ padding:16px; border-bottom:1px solid var(--line); }
    .modal-title{ font-family:"Big Shoulders Text",sans-serif; font-size:24px; font-weight:700; line-height:1.1; color:var(--text); }
    .modal-meta{ margin-top:6px; font-size:13px; color:var(--muted); display:flex; gap:12px; flex-wrap:wrap; }
    .modal-body{ padding:16px; color:var(--text); }
    .section{ margin-bottom:18px; }
    .label{ font-family:Bayon,sans-serif; letter-spacing:.06em; font-size:14px; color:var(--muted); margin-bottom:6px; }
    .ingredients{ white-space: pre-line; padding:12px; border:1px solid var(--line); border-radius:6px; background:#111218; font-size:14px; color:var(--text); }
    .close{ all:unset; cursor:pointer; float:right; font-size:16px; line-height:1; border-bottom:1px solid var(--text); padding-bottom:1px; color:var(--text); }
  </style>
</head>
<body>
  <!-- HERO qui devient HEADER -->
  <header id="heroHeader" class="heroHeader" role="banner">
    <div class="logoWrap">
      <img class="title" src="/static/ui/chez-vincent-titre.png" alt="Chez Vincent"/>
      <img class="subtitle" src="/static/ui/chez-vincent-soustitre.png" alt="Sous-titre"/>
    </div>
  </header>

  <!-- PAGE -->
  <div id="page" class="page">
    <main>
      <div class="search"><input id="search" type="text" placeholder="Rechercher un cocktail…"></div>
      <div id="app"><div class="center">Chargement des recettes…</div></div>
    </main>
  </div>

  <!-- Modal -->
  <div id="modal" class="modal" aria-hidden="true">
    <div class="panel" role="dialog" aria-modal="true">
      <div class="modal-head">
        <button class="close" onclick="closeModal()">fermer</button>
        <div class="modal-title" id="modalTitle"></div>
        <div class="modal-meta" id="modalQuickInfo"></div>
      </div>
      <div class="modal-body" id="modalBody"></div>
    </div>
  </div>

  <script>
    const API_URL = '/api/recipes/simple';
    let cocktails = []; let filteredCocktails = [];

    // Lance la réduction du hero en header sticky + fade-in du contenu
    function startTransition(immediate=false){
      const header = document.getElementById('heroHeader');
      const page = document.getElementById('page');
      if(immediate){
        header.classList.add('shrink');
        page.classList.add('show');
        return;
      }
      // Démarre la réduction
      header.classList.add('shrink');
      // On déclenche l'apparition de la page légèrement après
      setTimeout(()=> page.classList.add('show'), 180);
    }

    async function loadCocktails() {
      try {
        const res = await fetch(API_URL, { credentials: 'same-origin' });
        if (!res.ok) throw new Error('Erreur');
        cocktails = await res.json();
        filteredCocktails = cocktails;
        renderCocktails();
        // Data prêtes -> transition immédiate
        startTransition(true);
      } catch (e) {
        document.getElementById('app').innerHTML = '<div class="center">Erreur de chargement</div>';
        // Fallback: on déclenche quand même la transition après un court délai
        setTimeout(()=>startTransition(false), 700);
      }
    }

    function renderCocktails() {
      const app = document.getElementById('app');
      if (!filteredCocktails.length) { app.innerHTML = '<div class="center">Aucun cocktail trouvé</div>'; return; }
      app.innerHTML = '<div class="grid">' + filteredCocktails.map(c => `
        <div class="card" onclick="showDetails('${c.id}')">
          <div class="card-head"><div class="name">${escapeHtml(c.name)}</div></div>
          <div class="card-body">
            <div class="meta">
              <div class="item">${escapeHtml(c.glass || '')}</div>
              <div class="item">${escapeHtml(c.method || '')}</div>
            </div>
            ${
              c.tags
              ? '<div class="tags">' + c.tags.split(',').map(t => (
                  '<span class="tag">' + escapeHtml(t.trim()) + '</span>'
                )).join('') + '</div>'
              : ''
            }
          </div>
        </div>
      `).join('') + '</div>';
    }

    document.getElementById('search').addEventListener('input', (e) => {
      const q = e.target.value.toLowerCase();
      filteredCocktails = cocktails.filter(c =>
        (c.name || '').toLowerCase().includes(q) ||
        (c.tags || '').toLowerCase().includes(q)
      );
      renderCocktails();
    });

    function showDetails(id) {
      const c = cocktails.find(x => x.id === id);
      if (!c) return;
      document.getElementById('modalTitle').textContent = c.name || '';
      document.getElementById('modalQuickInfo').innerHTML =
        `<div>${escapeHtml(c.glass || '')}</div>` +
        `<div>${escapeHtml(c.method || '')}</div>`;
      document.getElementById('modalBody').innerHTML =
        (c.ingredients_text
          ? `<div class="section">
               <div class="label">INGRÉDIENTS</div>
               <div class="ingredients">${escapeHtml(c.ingredients_text)}</div>
             </div>` : ''
        ) +
        (c.tags
          ? `<div class="section">
               <div class="label">TAGS</div>
               <div class="tags">${
                 c.tags.split(',').map(t => `<span class="tag">${escapeHtml(t.trim())}</span>`).join('')
               }</div>
             </div>` : ''
        );
      const m = document.getElementById('modal');
      m.classList.add('active');
      m.setAttribute('aria-hidden','false');
    }

    function closeModal(){
      const m = document.getElementById('modal');
      m.classList.remove('active');
      m.setAttribute('aria-hidden','true');
    }
    document.getElementById('modal').addEventListener('click', (e)=>{
      if(e.target.id === 'modal') closeModal();
    });

    function escapeHtml(s){
      return (s||'').replace(/[&<>"']/g, m => ({
        '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
      }[m]));
    }

    // Go
    loadCocktails();
  </script>
</body>
</html>"""

# ----------------------------------------------------------
# ROUTES (gate appliqué à la page ET à l'API)
# ----------------------------------------------------------
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def root(request: Request):
    if not has_access(request):
        return HTMLResponse(LOGIN_HTML)
    return HTML_APP

@app.get("/enter", include_in_schema=False)
def enter(request: Request, code: str = ""):
    if code == ACCESS_CODE:
        resp = RedirectResponse(url="/", status_code=303)
        resp.set_cookie("cv_access", "1", max_age=60*60*12, path="/")
        return resp
    return HTMLResponse(LOGIN_HTML, status_code=401)

@app.get("/api", include_in_schema=False)
def api_root(request: Request):
    require_access(request)
    return {"ok": True, "endpoints": ["/api/health", "/api/recipes", "/api/recipes/simple", "/api/recipes/{slug}"]}

@app.get("/api/health")
async def health(request: Request):
    require_access(request)
    try:
        rows = await load_rows()
        return {"ok": True, "csv_url_set": bool(CSV_URL), "recipes_count": len(rows), "status": "operational"}
    except Exception as e:
        return {"ok": False, "error": str(e), "csv_url_set": bool(CSV_URL)}

@app.get("/api/debug/test-csv", include_in_schema=False)
async def debug_test_csv(request: Request):
    require_access(request)
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
async def list_recipes(request: Request):
    require_access(request)
    rows = await load_rows()
    return [normalize_row(r) for r in rows]

@app.get("/api/recipes/simple", response_model=List[RecipeSimple])
async def list_recipes_simple(request: Request):
    require_access(request)
    rows = await load_rows()
    result = []
    for r in rows:
        ings_text = ""
        ings_val = (r.get("ingredients") or "").strip()
        if ings_val.startswith("["):
            try:
                data = json.loads(ings_val)
                ings_text = "\n".join([f"{ing.get('item','')} - {ing.get('ml','')}ml"
                                       for ing in data if ing.get('item')])
            except:
                ings_text = r.get("spec_ml") or r.get("spec_oz") or ""
        else:
            ings_text = r.get("spec_ml") or r.get("spec_oz") or ""
        result.append(RecipeSimple(
            id=slugify(r.get("slug") or r.get("name","")),
            name=(r.get("name") or "").strip(),
            glass=(r.get("glass") or "Non spécifié").strip(),
            method=(r.get("method") or "Non spécifié").strip(),
            ingredients_text=ings_text,
            tags=(r.get("tags") or "").strip()
        ))
    return result

@app.get("/api/recipes/{slug}", response_model=Recipe)
async def get_recipe(slug: str, request: Request):
    require_access(request)
    rows = await load_rows()
    wanted = slugify(slug.strip())
    for r in rows:
        current = slugify(r.get("slug") or r.get("name",""))
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
    slug = slugify(raw.get("slug") or raw.get("name",""))
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
