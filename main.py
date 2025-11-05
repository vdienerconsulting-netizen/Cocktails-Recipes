from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
import csv, io, time
import httpx
from typing import List, Optional
import unicodedata, re


CSV_URL = "PASTE_PUBLISHED_CSV_URL"
CACHE_TTL = 60
_cache = {"at": 0, "rows": []}


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


app = FastAPI(title="Cocktail Recipes API", version="1.0.0")


@app.get("/recipes", response_model=List[Recipe])
async def list_recipes(q: Optional[str] = None, tag: Optional[str] = None):
rows = await load_rows()
out = [normalize_row(r) for r in rows]
if q:
ql = q.lower()
out = [r for r in out if ql in (r.name or '').lower() or ql in (r.spec_ml or '').lower() or ql in str(r.ingredients).lower()]
if tag:
tl = tag.lower()
out = [r for r in out if any((t or '').lower() == tl for t in r.tags)]
return out


@app.get("/recipes/{slug}", response_model=Recipe)
async def get_recipe(slug: str):
rows = await load_rows()
for r in rows:
s = r.get('slug') or slugify(r.get('name',''))
if s == slug:
return normalize_row(r)
raise HTTPException(404, detail="Not found")


async def load_rows():
now = time.time()
if now - _cache["at"] < CACHE_TTL and _cache["rows"]:
return _cache["rows"]
async with httpx.AsyncClient(timeout=10) as client:
resp = await client.get(CSV_URL)
resp.raise_for_status()
text = resp.text
buf = io.StringIO(text)
reader = csv.DictReader(buf)
rows = [r for r in reader if (r.get('name') or '').strip()]
_cache["rows"] = rows
_cache["at"] = now
return rows


def normalize_row(raw: dict) -> Recipe:
slug = raw.get('slug') or slugify(raw.get('name',''))
# parse ingredients JSON if present
ings = None
return s
