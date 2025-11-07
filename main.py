from fastapi import FastAPI, HTTPException, Response, Request
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
    image_url: Optional[str] = None  # <— ajouté

class RecipeSimple(BaseModel):
    id: str
    name: str
    glass: str
    method: str
    ingredients_text: str
    tags: str
    image_url: Optional[str] = None  # <— ajouté

# ----------------------------------------------------------
# APP
# ----------------------------------------------------------
app = FastAPI(title="Cocktail Recipes API", version="1.6.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files (images UI + drinks)
# Arborescence attendue : ./static/ui/... et ./static/drinks/...
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
                    new_url = f"https://docs.google.com/spreadsheets/d/e/{doc_id}/pub?gid={gid}&single=true&output=csv"
                    return new_url
        return url
    except Exception as e:
        print(f"Erreur conversion URL: {e}")
        return url

# ----------------------------------------------------------
# PAGES (HTML)
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
    :root{ --vert:#1f6047; --sable:#efe9dc; --encre:#222; --ligne:#ddd; }
    *{margin:0;padding:0;box-sizing:border-box}
    body{ font-family: Raleway, system-ui, -apple-system, Segoe UI, Roboto, sans-serif; background: var(--sable); color: var(--encre);}
    .wrap{ min-height:100vh; display:flex; align-items:center; justify-content:center; padding:20px;}
    .card{ width:100%; max-width:460px; border:1px solid var(--ligne); border-radius:8px; background:#fff; }
    .head{ padding:20px; border-bottom:1px solid var(--ligne); }
    .title{ font-family:"Big Shoulders Text",sans-serif; font-weight:700; font-size:22px;}
    .body{ padding:20px; }
    label{ display:block; font-size:14px; margin-bottom:6px;}
    input[type="password"], input[type="text"]{
      width:100%; border:none; border-bottom:1px solid #222; padding:10px 2px; font-size:16px; outline:none; background:transparent;
    }
    .row{ margin-top:14px;}
    button{
      all:unset; border:1px solid #222; padding:8px 14px; border-radius:4px; cursor:pointer; margin-top:14px;
    }
    .hero{background:var(--vert); color:var(--sable); text-align:center; padding:24px;}
    .hero h3{ font-family: Bayon, sans-serif; font-size:40px; letter-spacing:.06em; }
    .hero img{ width:min(60%, 520px); display:block; margin:8px auto 0; }
  </style>
</head>
<body>
  <div class="hero">
    <h3>BIENVENUE</h3>
    <img src="/static/ui/chez-vincent-titre.png" alt="Chez Vincent - Buvette Cocktail"/>
    <img src="/static/ui/chez-vincent-soustitre.png" alt="Sous-titre"/>
  </div>
  <div class="wrap">
    <form class="card" method="GET" action="/enter">
      <div class="head"><div class="title">Entrer sur l’app</div></div>
      <div class="body">
        <label for="code">Code d’accès</label>
        <input id="code" name="code" type="password" placeholder="••••••••" required />
        <div class="row">
          <button type="submit">Valider</button>
        </div>
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
  <meta name="description" content="Buvette cocktail — liste des recettes" />
  <link rel="preconnect" href="https://fonts.googleapis.com"/>
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
  <link href="https://fonts.googleapis.com/css2?family=Bayon&family=Big+Shoulders+Text:wght@400;700&family=Raleway:wght@300;400&display=swap" rel="stylesheet">
  <style>
    :root{ --vert:#1f6047; --sable:#efe9dc; --encre:#222; --gris:#b8b2a6; --ligne:#ddd; }
    *{margin:0;padding:0;box-sizing:border-box}
    body{ font-family: Raleway, system-ui, -apple-system, Segoe UI, Roboto, sans-serif; color: var(--encre); background: var(--sable); }
    /* HERO overlay */
    .hero{ position: fixed; inset:0; display:flex; flex-direction:column; align-items:center; justify-content:center; background:var(--vert); color:var(--sable); z-index:999; }
    .hero.hidden{ display:none; }
    .hero h3{ font-family: Bayon, sans-serif; letter-spacing:.08em; font-size:clamp(42px,10vw,90px); line-height:1; margin-bottom:.2em; opacity:0; transform:translateY(-40px); animation:fadeDown 1.8s ease-out forwards .05s; text-align:center; }
    .hero img{ width:min(60%,520px); display:block; margin:.35rem auto 0; opacity:0; transform:translateY(-20px); }
    .hero img.title{ animation:fadeUp 1.8s ease-out forwards .8s;}
    .hero img.sub{   animation:fadeUpS 1.8s ease-out forwards 1.55s;}
    .hero.fadeout{ animation:heroOut .6s ease-in forwards 2.4s;}
    @keyframes fadeDown{to{opacity:1; transform:translateY(0);}}
    @keyframes fadeUp{to{opacity:1; transform:translateY(-10px);}}
    @keyframes fadeUpS{to{opacity:1; transform:translateY(-5px);}}
    @keyframes heroOut{to{opacity:0; visibility:hidden;}}
    /* header */
    header{ padding:18px 16px; border-bottom:1px solid var(--ligne); background:transparent; }
    .brand{ display:flex; align-items:center; justify-content:center; }
    .brand-title{ font-family:"Big Shoulders Text",sans-serif; font-weight:700; font-size:clamp(18px,3.5vw,24px); letter-spacing:.04em; }
    .search{ padding:16px; border-bottom:1px solid var(--ligne);}
    .search input{ width:100%; font:400 16px/1.3 Raleway, sans-serif; padding:10px 2px; border:none; outline:none; background:transparent; border-bottom:1px solid var(--encre); color:var(--encre);}
    .grid{ padding:16px; display:grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap:12px;}
    .card{ background:#fff; border:1px solid var(--ligne); border-radius:6px; cursor:pointer; }
    .cover{ width:100%; aspect-ratio: 4 / 3; object-fit: cover; display:block; background:#f4f4f4; }
    .card-head{ padding:12px; border-bottom:1px solid var(--ligne); }
    .name{ font-family:"Big Shoulders Text",sans-serif; font-weight:700; font-size:20px; line-height:1.1; }
    .card-body{ padding:12px; }
    .meta{ display:flex; flex-wrap:wrap; gap:8px; margin-bottom:6px; font-size:13px; color:#444; }
    .meta .item{ border-bottom:1px solid #bbb; padding-bottom:1px;}
    .tags{ display:flex; flex-wrap:wrap; gap:6px; margin-top:8px; font-size:12px; color:#555; }
    .tag{ border:1px solid var(--ligne); border-radius:999px; padding:3px 8px; }
    .center{ text-align:center; padding:48px 16px; color:#666; }
    .modal{ position: fixed; inset:0; display:none; background: rgba(0,0,0,.06); z-index:998; padding:16px; }
    .modal.active{ display:block; }
    .panel{ background:#fff; border:1px solid var(--ligne); border-radius:8px; max-width:780px; margin:5vh auto; overflow:hidden; }
    .modal-head{ padding:16px; border-bottom:1px solid var(--ligne); background:#fff; }
    .modal-title{ font-family:"Big Shoulders Text",sans-serif; font-size:24px; font-weight:700; line-height:1.1; }
    .modal-meta{ margin-top:6px; font-size:13px; color:#444; display:flex; gap:12px; flex-wrap:wrap; }
    .modal-body{ padding:16px; }
    .section{ margin-bottom:18px; }
    .label{ font-family:Bayon,sans-serif; letter-spacing:.06em; font-size:14px; color:#333; margin-bottom:6px; }
    .ingredients{ white-space: pre-line; padding:12px; border:1px solid var(--ligne); border-radius:6px; background:#fafafa; font-size:14px; color:#222; }
    .close{ all:unset; cursor:pointer; float:right; font-size:16px; line-height:1; border-bottom:1px solid #222; padding-bottom:1px; }
    .modal-cover{ width:100%; aspect-ratio: 16 / 9; object-fit: cover; display:block; background:#f4f4f4; border-bottom:1px solid var(--ligne);}
  </style>
</head>
<body>
  <!-- HERO (anim d’entrée) -->
  <div id="hero" class="hero fadeout">
    <h3>BIENVENUE</h3>
    <img class="title" src="/static/ui/chez-vincent-titre.png" alt="Chez Vincent - Buvette Cocktail"/>
    <img class="sub"   src="/static/ui/chez-vincent-soustitre.png" alt="Sous-titre"/>
  </div>

  <header><div class="brand"><div class="brand-title">CHEZ VINCENT — Cocktails</div></div></header>
  <div class="search"><input id="search" type="text" placeholder="Rechercher un cocktail…"></div>
  <div id="app"><div class="center">Chargement des recettes…</div></div>

  <!-- Modal -->
  <div id="modal" class="modal" aria-hidden="true">
    <div class="panel" role="dialog" aria-modal="true">
      <img id="modalImg" class="modal-cover" alt="" />
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
    let cocktails = [];
    let filteredCocktails = [];

    // Masquer hero après anim (ou dès data)
    function hideHero(force=false){
      const h = document.getElementById('hero');
      if(!h) return;
      if(force){ h.classList.add('hidden'); return; }
      setTimeout(()=>{ h.classList.add('hidden'); }, 2600);
    }

    async function loadCocktails() {
      try {
        const response = await fetch(API_URL, { credentials: 'same-origin' });
        if (!response.ok) throw new Error('Erreur');
        cocktails = await response.json();
        filteredCocktails = cocktails;
        renderCocktails();
        hideHero(true);
      } catch (e) {
        document.getElementById('app').innerHTML = '<div class="center">Erreur de chargement</div>';
        hideHero();
      }
    }

    function renderCocktails() {
      const app = document.getElementById('app');
      if (!filteredCocktails.length) {
        app.innerHTML = '<div class="center">Aucun cocktail trouvé</div>';
        return;
      }
      app.innerHTML = '<div class="grid">' + filteredCocktails.map(c => `
        <div class="card" onclick="showDetails('${c.id}')">
          <img class="cover" src="${escapeHtml(c.image_url || '')}" onerror="this.onerror=null;this.src='/static/ui/placeholder.jpg';" alt="" />
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
      document.getElementById('modalImg').src = (c.image_url || '/static/ui/placeholder.jpg');
      document.getElementById('modalImg').onerror = function(){ this.onerror=null; this.src='/static/ui/placeholder.jpg'; };
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

    loadCocktails();
    hideHero();
  </script>
</body>
</html>"""

# ----------------------------------------------------------
# ROUTES + ACCESS GATE
# ----------------------------------------------------------
def has_access(request: Request) -> bool:
    return request.cookies.get("cv_access") == "1"

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def root(request: Request):
    """Page protégée par code : si pas de cookie, on affiche la page d’accès."""
    if not has_access(request):
        return HTMLResponse(LOGIN_HTML)
    return HTML_APP

@app.get("/enter", include_in_schema=False)
def enter(request: Request, code: str = ""):
    """Validation du code et pose d’un cookie simple (non HttpOnly)."""
    if code == ACCESS_CODE:
        resp = RedirectResponse(url="/", status_code=303)
        # Cookie simple (durée 12h). Pour plus de sécurité, ajoute SameSite/HttpOnly si besoin.
        resp.set_cookie("cv_access", "1", max_age=60*60*12, path="/")
        return resp
    # code invalide -> retour à la page d’accès
    return HTMLResponse(LOGIN_HTML, status_code=401)

@app.get("/api", include_in_schema=False)
def api_root():
    return {"ok": True, "endpoints": ["/api/health", "/api/recipes", "/api/recipes/simple", "/api/recipes/{slug}"]}

@app.get("/api/health")
async def health():
    try:
        rows = await load_rows()
        return {"ok": True, "csv_url_set": bool(CSV_URL), "recipes_count": len(rows), "status": "operational"}
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
    out = []
    for r in rows:
        n = normalize_row(r)
        # Image : si CSV possède une colonne "image" (URL), on la garde.
        # Sinon, fallback sur /static/drinks/<slug>.jpg
        n.image_url = n.image_url or f"/static/drinks/{n.slug}.jpg"
        out.append(n)
    return out

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

        rid = slugify(r.get("slug") or r.get("name", ""))
        image_url = (r.get("image") or "").strip() or f"/static/drinks/{rid}.jpg"

        result.append(RecipeSimple(
            id=rid,
            name=(r.get("name") or "").strip(),
            glass=(r.get("glass") or "Non spécifié").strip(),
            method=(r.get("method") or "Non spécifié").strip(),
            ingredients_text=ingredients_text,
            tags=(r.get("tags") or "").strip(),
            image_url=image_url
        ))
    return result

@app.get("/api/recipes/{slug}", response_model=Recipe)
async def get_recipe(slug: str):
    rows = await load_rows()
    wanted = slugify(slug.strip())
    for r in rows:
        current = slugify(r.get("slug") or r.get("name", ""))
        if current == wanted:
            n = normalize_row(r)
            n.image_url = n.image_url or f"/static/drinks/{n.slug}.jpg"
            return n
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

    image_url = (raw.get("image") or "").strip() or None

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
        image_url=image_url
    )

@app.exception_handler(404)
async def not_found(_: Request, __):
    return JSONResponse({"ok": False, "error": "Not Found"}, status_code=404)
