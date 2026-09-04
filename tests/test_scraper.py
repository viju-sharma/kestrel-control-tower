from bs4 import BeautifulSoup

from kestrel.scraper import parse_card, parse_price, page_count
from kestrel.matching import clean_title, parse_pack, match_listing

MUMBAI = '''<div class="card product-item" data-listing-id="1">
<a href="/product/1.html"><strong>Combo AMRITVALLEY RICE 400ML (New)</strong></a>
<div class="muted">DailyKart &middot; 400 ml &middot; Staples</div>
<span class="price">&#8377;267.74</span>
<div class="muted">MRP &#8377;288 &middot; Currently unavailable &middot; rated 3.9 (1993)</div>
<div class="muted">Last seen: 2026-06-14</div></div>'''
BLR = '''<div class="card product-item" data-listing-id="297">
<a href="/product/297.html"><strong>Kestrel Sel. Rusk 400G</strong></a>
<div class="muted">FreshCart &middot; 400 g &middot; Bakery</div>
<span class="pricing-block" data-price-paise="19731" data-currency="INR">Price on card</span>
<div class="muted">MRP &#8377;245 &middot; In stock &middot; rated 4.0 (901)</div>
<div class="muted">Last seen: 2026-06-13</div></div>'''
DELHI = '''<div class="card product-item" data-listing-id="589">
<a href="/product/589.html"><strong>Hillfare Butter 750g</strong></a>
<div class="muted">MetroBazaar &middot; 750 g &middot; Dairy</div>
<div class="amt"><em>Rs.</em> 88.68 <small>incl. taxes</small></div>
<div class="muted">MRP &#8377;96 &middot; In stock &middot; rated 3.5 (2694)</div>
<div class="muted">Last seen: 2026-06-25</div></div>'''
CHENNAI = '''<div class="card product-item" data-listing-id="845">
<a href="/product/845.html"><strong>Pack of 1 Marwar Chips 150G</strong></a>
<div class="muted">FreshCart &middot; 150 g &middot; Snacks</div>
<b class="sellingPrice">INR 229.86</b>
<div class="muted">MRP &#8377;226 &middot; In stock &middot; rated 3.3 (3330)</div>
<div class="muted">Last seen: 2026-06-11</div></div>'''


def card(html):
    return BeautifulSoup(html, "html.parser").select_one(".product-item")


def test_price_markup_per_city():
    assert parse_price(card(MUMBAI)) == 267.74
    assert parse_price(card(BLR)) == 197.31
    assert parse_price(card(DELHI)) == 88.68
    assert parse_price(card(CHENNAI)) == 229.86


def test_card_fields():
    r = parse_card(card(MUMBAI), "Mumbai")
    assert r["listing_id"] == 1 and r["retailer"] == "DailyKart" and r["category"] == "Staples"
    assert r["mrp_inr"] == 288 and r["in_stock"] == 0 and r["last_seen"] == "2026-06-14"
    assert (r["pack_value"], r["pack_uom"]) == (400.0, "ML")
    assert parse_card(card(BLR), "Bengaluru")["in_stock"] == 1


def test_page_count_from_breadcrumb():
    assert page_count('<p class="muted">Home / Mumbai / page 1 of 17</p>') == 17
    assert page_count("no breadcrumb") == 1


def test_title_cleaning_and_pack():
    assert clean_title("Combo Kestrel Sel. Ketchup 100g | Best Before 6M") == "kestrel select ketchup 100g"
    assert parse_pack("Pack of 1 Bluepeak Frozen Peas 150kg") == (150.0, "KG")
    assert parse_pack("no size here") == (None, None)
    assert clean_title("Kestrel Frzn Paratha 150ml") == "kestrel frozen paratha 150ml"
    assert clean_title("Bluepeak Inst. Noodles 1000ml") == "bluepeak instant noodles 1000ml"


PRODUCTS = [
    {"product_id": 1, "sku_code": "S1", "name": "Kestrel Rusk 400g", "brand": "Kestrel",
     "type_words": {"rusk"}, "select": False, "pack_value": 400.0, "pack_uom": "G", "mrp": 245},
    {"product_id": 2, "sku_code": "S2", "name": "Kestrel Select Rusk 400g", "brand": "Kestrel",
     "type_words": {"rusk"}, "select": True, "pack_value": 400.0, "pack_uom": "G", "mrp": 260},
    {"product_id": 3, "sku_code": "S3", "name": "Amrit Valley Iced Tea 150ml", "brand": "Amrit",
     "type_words": {"iced", "tea"}, "select": False, "pack_value": 150.0, "pack_uom": "ML", "mrp": 60},
]


def test_matching_distinguishes_select_subbrand():
    pid, conf, _ = match_listing({"title": "Kestrel Sel. Rusk 400G", "pack_value": 400.0, "pack_uom": "G"}, PRODUCTS)
    assert (pid, conf) == (2, 1.0)
    pid, conf, _ = match_listing({"title": "Pack of 1 Kestrel Rusk 400g", "pack_value": 400.0, "pack_uom": "G"}, PRODUCTS)
    assert (pid, conf) == (1, 1.0)


def test_matching_tolerates_unit_confusion_with_lower_confidence():
    pid, conf, note = match_listing({"title": "Kestrel Rusk 400kg", "pack_value": 400.0, "pack_uom": "KG"}, PRODUCTS)
    assert pid == 1 and conf == 0.7 and "unit differs" in note


def test_matching_brand_alias_and_multiword_type():
    pid, conf, _ = match_listing({"title": "AmritValley Iced Tea 150ml (New)", "pack_value": 150.0, "pack_uom": "ML"}, PRODUCTS)
    assert (pid, conf) == (3, 1.0)
    assert match_listing({"title": "Unknown Brand Tea 150ml", "pack_value": 150.0, "pack_uom": "ML"}, PRODUCTS)[0] is None
