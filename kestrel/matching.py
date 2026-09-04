"""Match a BazaarPulse listing title to a Kestrel SKU.

Titles carry no key. They do carry brand, product type and pack size wrapped
in retailer noise ("Combo", "Pack of 1", "(New)", "| Best Before 6M"). We
strip the noise, normalise the brand, then require brand + product words +
pack size to agree with the product master. Unit disagreements (400kg vs
400g) are tolerated at lower confidence because the master itself has them."""
import re

NOISE = [
    r"\bcombo\b", r"\bpack of \d+\b", r"\(new\)", r"\|\s*best before \d+\s*m", r"-\s*family pack",
]
BRANDS = {
    "amritvalley": "Amrit", "amrit valley": "Amrit", "amrit": "Amrit",
    "bluepeak": "Bluepeak", "coastline": "Coastline", "hillfare": "Hillfare",
    "kestrel": "Kestrel", "marwar": "Marwar",
}
UOM = {"g": "G", "gm": "G", "gms": "G", "kg": "KG", "ml": "ML", "l": "L", "ltr": "L"}

_pack = re.compile(r"(\d+(?:\.\d+)?)\s*(kg|gms|gm|g|ml|ltr|l)\b", re.I)


def parse_pack(text):
    if not text:
        return None, None
    m = _pack.search(text)
    if not m:
        return None, None
    return float(m.group(1)), UOM[m.group(2).lower()]


def clean_title(title):
    t = title.lower()
    for pat in NOISE:
        t = re.sub(pat, " ", t)
    t = t.replace("sel.", "select")
    return re.sub(r"\s+", " ", t).strip()


def tokens(text):
    return set(re.findall(r"[a-z]+", text.lower()))


def brand_of(text):
    t = text.lower()
    for k in sorted(BRANDS, key=len, reverse=True):
        if k in t:
            return BRANDS[k]
    return None


def load_products(conn):
    """Product master rows in the shape the matcher wants."""
    rows = conn.execute("""SELECT product_id, sku_code, product_name, brand, subcategory,
                                  pack_size_value, pack_size_uom, mrp_inr FROM products""").fetchall()
    out = []
    for r in rows:
        pid, sku, name, brand, sub, pv, pu, mrp = r
        out.append({
            "product_id": pid, "sku_code": sku, "name": name, "brand": brand,
            "type_words": tokens(sub), "select": "select" in name.lower(),
            "pack_value": float(pv) if pv is not None else None, "pack_uom": pu, "mrp": mrp,
        })
    return out


def match_listing(listing, products):
    """Return (product_id, confidence, note). confidence 0..1, None if no match."""
    title = clean_title(listing["title"])
    brand = brand_of(title)
    if not brand:
        return None, None, "no brand"
    words = tokens(title)
    pv, pu = listing.get("pack_value"), listing.get("pack_uom")
    if pv is None:
        pv, pu = parse_pack(title)
    is_select = "select" in words

    best, best_conf, note = None, 0.0, "no candidate"
    for p in products:
        if p["brand"] != brand or not p["type_words"] <= words or p["select"] != is_select:
            continue
        if pv is None or p["pack_value"] is None:
            conf, why = 0.5, "no pack size"
        elif abs(p["pack_value"] - pv) < 1e-6 and p["pack_uom"] == pu:
            conf, why = 1.0, "brand+type+pack"
        elif abs(p["pack_value"] - pv) < 1e-6:
            conf, why = 0.7, f"unit differs ({pu} vs {p['pack_uom']})"
        else:
            continue
        if conf > best_conf:
            best, best_conf, note = p, conf, why
    if best is None:
        return None, None, note
    return best["product_id"], best_conf, note
