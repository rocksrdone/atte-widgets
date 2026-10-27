#!/usr/bin/env python3
"""
atte_build.py — builds the At the Tail End schema into an EMPTY Grist doc.

    python3 atte_build.py --doc <API_DOC_ID>            # dry run: shows what it will do
    python3 atte_build.py --doc <API_DOC_ID> --apply    # builds it

Server defaults to https://stash.getgrist.com (the Stash team site).
API key is read from $GRIST_API_KEY, else from ~/.atte-secrets (a line
GRIST_API_KEY=...). The key is never printed.

Safety: refuses to touch a doc that already has any table other than the
blank "Table1" Grist creates with every new doc. Run it once, on a new doc.
No third-party packages needed — standard library only.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

# ─────────────────────────────────────────────────────────────────────────────
# Choice lists (carried over from the test build so Item Intake keeps working)
# ─────────────────────────────────────────────────────────────────────────────
PREFIXES = ["EPH", "GAME", "MUSIC", "MEDIA", "TOY", "SMGDS"]
CATEGORIES = ["Ephemera - Paper", "Ephemera - Postcard", "Ephemera - Photo",
              "Ephemera - Book", "Ephemera - Cookbook", "Ephemera - Ad/Menu/Receipt",
              "Retro Gaming", "Music - Physical", "DVD / VHS", "Vintage Toy",
              "Office/Small Goods", "Other"]
ERAS = ["Pre-1900", "1900-1909", "1910-1919", "1920-1929", "1930-1939", "1940-1949",
        "1950-1959", "1960-1969", "1970-1979", "1980-1989", "1990-1999", "2000-2009",
        "Victorian (1837-1901)", "Edwardian (1901-1910)", "WWI era (1914-1918)",
        "Prohibition era (1920-1933)", "Great Depression (1929-1939)",
        "WWII era (1939-1945)", "Post-war (1945-1955)", "Atomic age (1945-1965)",
        "Space age (1957-1969)", "Psychedelic era (1965-1975)", "Disco era (1975-1980)",
        "Early computing (1975-1990)", "Reagan era (1980-1988)", "Grunge era (1990-1996)",
        "Y2K era (1995-2005)", "Unknown", "Mixed / Various"]
CONDITIONS = ["Excellent", "Very Good", "Good", "Fair", "Poor"]
ITEM_STATUS = ["In stock", "Listed", "Reserved", "Sold", "Pulled",
               "Parts only - digital", "Void"]
YES_NO = ["Yes", "No"]
CHANNELS = ["eBay", "Etsy", "Shopify"]
SALE_CHANNELS = ["eBay", "Etsy", "Shopify", "Show / in person", "Other"]
LISTING_TYPES = ["Physical", "Digital Single", "Digital Pack"]

# ─────────────────────────────────────────────────────────────────────────────
# Formula helpers
# ─────────────────────────────────────────────────────────────────────────────
def seq_id(table, col, prefix, width=4):
    """'Highest number so far, plus one' — tolerates gaps, survives mid-table deletes."""
    return (
        "nums = []\n"
        f"for r in {table}.all:\n"
        "  if r.id >= $id:  # earlier rows only (no circular refs on bulk adds)\n"
        "    continue\n"
        f"  tail = str(r.{col} or '').rsplit('-', 1)[-1]\n"
        "  if tail.isdigit():\n"
        "    nums.append(int(tail))\n"
        f"'{prefix}-' + str((max(nums) if nums else 0) + 1).zfill({width})"
    )

ITEM_ID = (
    "prefix = $prefix or 'EPH'\n"
    "nums = []\n"
    "for r in Items.lookupRecords(prefix=prefix):\n"
    "  if r.id >= $id:  # earlier rows only (no circular refs on bulk adds)\n"
    "    continue\n"
    "  tail = str(r.item_id or '').rsplit('-', 1)[-1]\n"
    "  if tail.isdigit():\n"
    "    nums.append(int(tail))\n"
    "prefix + '-' + str((max(nums) if nums else 0) + 1).zfill(4)"
)

SCAN_ID = (
    "item = $source_item_id.item_id\n"
    "if not item:\n"
    "  return ''\n"
    "nums = []\n"
    "for r in Scans.lookupRecords(source_item_id=$source_item_id):\n"
    "  if r.id >= $id:  # earlier rows only (no circular refs on bulk adds)\n"
    "    continue\n"
    "  tail = str(r.scan_id or '').rsplit('-S', 1)[-1]\n"
    "  if tail.isdigit():\n"
    "    nums.append(int(tail))\n"
    "item + '-S' + str((max(nums) if nums else 0) + 1).zfill(2)"
)

def latest(table, **kw):
    """Formula returning the most recent matching record (or empty)."""
    args = ", ".join(f"{k}={v}" for k, v in kw.items())
    return (f"rows = {table}.lookupRecords({args}, sort_by='-{_DATE[table]}')\n"
            "rows[0] if rows else None")

_DATE = {"Sales": "sale_date", "PD_Research": "verified_date"}

SOURCE_ROLLUP = (  # this source plus its booths (one level)
    "ids = [$id] + [b.id for b in Sources.lookupRecords(parent_source=$id)]\n"
    "items = [i for s in ids for i in Items.lookupRecords(acquired_source=s)]\n"
)

PD_RULE = (
    "# Suggests a basis; the verdict stays yours. US rules, recomputed each year.\n"
    "y = TODAY().year\n"
    "pub_cut = y - 96      # published in or before this year = PD\n"
    "unpub_cut = y - 121   # unpublished, author unknown, created in or before = PD\n"
    "death_cut = y - 71    # unpublished, author died in or before = PD\n"
    "p = $publication_year\n"
    "if $published == 'Yes':\n"
    "  if not p:\n"
    "    return 'Need publication year.'\n"
    "  if p <= pub_cut:\n"
    "    return 'PD: published %d (in or before %d).' % (p, pub_cut)\n"
    "  if p <= 1963:\n"
    "    return 'Published %d: PD only if not renewed. Search the CCE / Stanford renewals.' % p\n"
    "  if p <= 1977:\n"
    "    return 'Published %d: PD only if published without a copyright notice.' % p\n"
    "  if p <= 1989:\n"
    "    return 'Published %d: no notice AND no registration within 5 years needed. Assume protected.' % p\n"
    "  return 'Published %d: assume protected.' % p\n"
    "if $published == 'No':\n"
    "  d = $author_death_year\n"
    "  c = $creation_year\n"
    "  if d and d <= death_cut:\n"
    "    return 'PD: unpublished, author died %d (in or before %d).' % (d, death_cut)\n"
    "  if not $author and c and c <= unpub_cut:\n"
    "    return 'PD: unpublished, author unknown, created %d (in or before %d).' % (c, unpub_cut)\n"
    "  if d or c:\n"
    "    return 'Unpublished: likely still protected.'\n"
    "  return 'Unpublished: need author death year or creation year.'\n"
    "return 'First decide: was it published? (sold or distributed to the public)'"
)

# ─────────────────────────────────────────────────────────────────────────────
# Column builders
# ─────────────────────────────────────────────────────────────────────────────
MONEY = {"numMode": "currency", "decimals": 2}
DATE = {"dateFormat": "YYYY-MM-DD", "isCustomDateFormat": False}

def data(cid, typ, **opts):        return dict(id=cid, type=typ, kind="data", **opts)
def text(cid):                     return data(cid, "Text")
def money(cid):                    return data(cid, "Numeric", wo=MONEY)
def date(cid):                     return data(cid, "Date", wo=DATE)
def choice(cid, choices):          return data(cid, "Choice", wo={"choices": choices})
def ref(cid, table, show):         return data(cid, "Ref:" + table, visible=show)
def formula(cid, typ, f, **opts):  return dict(id=cid, type=typ, kind="formula", formula=f, **opts)
def trigger(cid, typ, f, deps=None):
    return dict(id=cid, type=typ, kind="trigger", formula=f, deps=deps or [])

# ─────────────────────────────────────────────────────────────────────────────
# THE SCHEMA  (table order = creation order; refs may point at any table)
# ─────────────────────────────────────────────────────────────────────────────
SPEC = [
  ("Sources", [
    text("name"),
    text("source_type"),                       # Text on purpose: Lot Intake supplies the picker
    ref("parent_source", "Sources", "name"),   # booth -> its mall
    text("company"), text("contact_name"),
    text("street_address"), text("city"), text("state"), text("zip"),
    text("website_url"),
    choice("quality_rating", ["Excellent", "Good", "Fair", "Poor"]),
    choice("visit_frequency", ["Daily", "Weekly", "Bi-weekly", "Monthly", "Seasonal",
                               "As listed", "As found", "One-time"]),
    choice("active", YES_NO),
    text("best_for"), text("notes"),
    formula("booth_count", "Int", "len(Sources.lookupRecords(parent_source=$id))"),
    formula("item_count", "Int", SOURCE_ROLLUP + "len(items)"),
    formula("cost_total_usd", "Numeric", SOURCE_ROLLUP + "round(sum(i.cost_basis_usd or 0 for i in items), 2)", wo=MONEY),
    formula("avg_cost_basis", "Numeric", "round($cost_total_usd / $item_count, 2) if $item_count else 0", wo=MONEY),
    formula("profit_total_usd", "Numeric", SOURCE_ROLLUP + "round(sum(i.net_profit_usd or 0 for i in items), 2)", wo=MONEY),
  ]),
  ("Categories", [
    # One row per Items.category. field_guide is an Obsidian note TITLE (not a link),
    # per the vault's Grist–Obsidian Boundary note, so it survives vault reorganisation.
    text("category"),
    choice("prefix", PREFIXES),
    text("field_guide"),
    text("notes"),
    formula("item_count", "Int", "len(Items.lookupRecords(category=$category))"),
  ]),
  ("Lots", [
    trigger("lot_id", "Text", seq_id("Lots", "lot_id", "LOT")),
    text("lot_name"),
    date("lot_date"),
    ref("source", "Sources", "name"),
    money("total_cost_usd"),
    text("storage_location"),
    data("est_items", "Int"),
    text("receipt_file"),
    text("notes"),
    formula("items_pulled", "Int", "len(Items.lookupRecords(lot_id=$id))"),
    formula("cost_per_item", "Numeric", "round(($total_cost_usd or 0) / $items_pulled, 2) if $items_pulled else 0", wo=MONEY),
    formula("allocated_usd", "Numeric", "round(sum(i.cost_basis_usd or 0 for i in Items.lookupRecords(lot_id=$id)), 2)", wo=MONEY),
    formula("unallocated_usd", "Numeric", "round(($total_cost_usd or 0) - $allocated_usd, 2)", wo=MONEY),
    formula("profit_usd", "Numeric", "round(sum(i.net_profit_usd or 0 for i in Items.lookupRecords(lot_id=$id)), 2)", wo=MONEY),
  ]),
  ("Items", [
    trigger("item_id", "Text", ITEM_ID),
    choice("prefix", PREFIXES),
    text("title"),
    choice("category", CATEGORIES),
    choice("era", ERAS),
    choice("condition_grade", CONDITIONS),
    text("condition_notes"),
    date("acquired_date"),
    ref("acquired_source", "Sources", "name"),
    money("cost_basis_usd"),
    ref("lot_id", "Lots", "lot_name"),
    text("location_notes"),
    choice("status", ITEM_STATUS),
    choice("will_be_scanned", YES_NO),
    text("obsidian_note"),                     # rare long-form note title (grist_ref in vault)
    text("notes"),
    formula("field_guide", "Text",
            "c = Categories.lookupOne(category=$category)\n"
            "c.field_guide or 'Field Guide Index'"),
    # ── derived: sale side (Sales is the only place a sale is written) ──
    formula("sale", "Ref:Sales", latest("Sales", item_id="$id"), visible="sale_id"),
    formula("sold_date", "Date", "$sale.sale_date if $sale else None", wo=DATE),
    formula("sold_price_usd", "Numeric", "$sale.sale_price_usd if $sale else None", wo=MONEY),
    formula("sold_channel", "Text", "$sale.channel if $sale else ''"),
    formula("net_profit_usd", "Numeric", "$sale.net_profit_usd if $sale else None", wo=MONEY),
    formula("net_days_in_stock", "Int",
            "if not $acquired_date:\n  return None\n(($sold_date or TODAY()) - $acquired_date).days"),
    # ── derived: research, listings, comps ──
    formula("pd_research", "Ref:PD_Research", latest("PD_Research", item_id="$id"), visible="research_id"),
    formula("pd_verdict", "Text", "$pd_research.verdict if $pd_research else ''"),
    formula("active_listings", "Int", "len(Listings.lookupRecords(item_id=$id, status='Active'))"),
    formula("comp_count", "Int", "len(Comps.lookupRecords(item_id=$id))"),
    formula("comp_median_usd", "Numeric",
            "v = sorted(c.total_usd for c in Comps.lookupRecords(item_id=$id) if c.total_usd)\n"
            "if not v:\n  return None\n"
            "n = len(v)\n"
            "round(v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2.0, 2)", wo=MONEY),
  ]),
  ("Scans", [
    trigger("scan_id", "Text", SCAN_ID, deps=["source_item_id"]),
    ref("source_item_id", "Items", "item_id"),
    choice("page_side", ["Front", "Back", "Full item", "Page 2", "Page 3", "Page 4",
                         "Page 5", "Page 6", "Cover", "Inside cover", "Detail",
                         "Full spread", "Other"]),
    date("scan_date"),
    data("resolution_dpi", "Int"),
    choice("trademark_flag", YES_NO),
    choice("listed_individually", YES_NO),
    choice("status", ["Draft", "Listed", "Retired"]),
    text("notes"),
    formula("title", "Text", "($source_item_id.item_id + ' — ' + $page_side) if $source_item_id and $page_side else ''"),
    formula("category", "Text", "$source_item_id.category"),
    formula("folder", "Text",
            "{'EPH': 'ephemera', 'GAME': 'gaming', 'MUSIC': 'music', 'MEDIA': 'media',\n"
            " 'TOY': 'toys', 'SMGDS': 'smallgoods'}.get($source_item_id.prefix, 'smallgoods')"),
    formula("master_filename", "Text", "f'{$scan_id}_master.tif' if $scan_id else ''"),
    formula("master_location", "Text",
            "'nextcloud://At the Tail End/masters/' + $folder + '/' + $master_filename if $scan_id else ''"),
    formula("jpg_filename", "Text", "f'{$scan_id}_web.jpg' if $scan_id else ''"),
    formula("png_filename", "Text", "f'{$scan_id}_transparent.png' if $scan_id else ''"),
    formula("pd_research", "Ref:PD_Research",
            "own = PD_Research.lookupRecords(scan_id=$id, sort_by='-verified_date')\n"
            "if own:\n  return own[0]\n"
            "$source_item_id.pd_research if $source_item_id else None", visible="research_id"),
    formula("pd_status", "Text",
            "v = $pd_research.verdict if $pd_research else ''\n"
            "{'Verified PD': 'Verified PD', 'Not PD': 'Not PD - do not list',\n"
            " 'Uncertain': 'Uncertain - do not list'}.get(v, 'Needs research')"),
  ]),
  ("PD_Research", [
    trigger("research_id", "Text", seq_id("PD_Research", "research_id", "PD")),
    ref("item_id", "Items", "item_id"),
    ref("scan_id", "Scans", "scan_id"),        # blank = applies to the whole item
    text("title"),
    choice("published", ["Yes", "No", "Unknown"]),
    data("publication_year", "Int"),
    data("creation_year", "Int"),
    text("author"),
    data("author_death_year", "Int"),
    text("publisher"),
    choice("copyright_notice", ["Yes", "No", "Not checked"]),
    text("notice_text"),
    choice("renewal_searched", YES_NO),
    text("renewal_result"),
    text("sources_checked"),
    formula("rule_suggestion", "Text", PD_RULE),
    text("pd_basis"),
    text("named_subjects"),
    text("trademark_concern"),
    choice("verdict", ["Verified PD", "Not PD", "Uncertain"]),
    choice("confidence", ["High", "Medium", "Low"]),
    date("verified_date"),
    text("approved_listing_language"),
    text("notes"),
  ]),
  ("Packs", [
    trigger("pack_id", "Text", seq_id("Packs", "pack_id", "PACK")),
    text("title"), text("theme"),
    money("price_usd"),
    choice("status", ["Draft", "Active", "Retired"]),
    text("zip_filename"), text("zip_location"),
    date("created_date"),
    text("notes"),
    formula("scan_count", "Int", "len(Pack_Contents.lookupRecords(pack_id=$id))"),
    formula("pack_ready", "Bool",
            "rows = Pack_Contents.lookupRecords(pack_id=$id)\n"
            "bool(rows) and all(r.scan_id.pd_status == 'Verified PD' for r in rows)"),
  ]),
  ("Pack_Contents", [
    ref("pack_id", "Packs", "pack_id"),
    ref("scan_id", "Scans", "scan_id"),
    formula("scan_title", "Text", "$scan_id.title"),
    formula("pd_status", "Text", "$scan_id.pd_status"),
  ]),
  ("Listings", [
    trigger("listing_id", "Text", seq_id("Listings", "listing_id", "L")),
    choice("channel", CHANNELS),
    choice("listing_type", LISTING_TYPES),
    ref("item_id", "Items", "item_id"),
    ref("scan_id", "Scans", "scan_id"),
    ref("pack_id", "Packs", "pack_id"),
    text("channel_listing_id"),
    text("title"),
    money("price_usd"),
    choice("status", ["Draft", "Active", "Sold", "Ended"]),
    date("listed_date"),
    date("ended_date"),
    data("views", "Int"),
    text("notes"),
    formula("url", "Text",
            "lid = $channel_listing_id\n"
            "if not lid:\n  return ''\n"
            "if $channel == 'eBay':\n  return 'https://www.ebay.com/itm/' + lid\n"
            "if $channel == 'Etsy':\n  return 'https://www.etsy.com/listing/' + lid\n"
            "''"),
  ]),
  ("Sales", [
    trigger("sale_id", "Text", seq_id("Sales", "sale_id", "SALE")),
    choice("channel", SALE_CHANNELS),
    text("channel_order_id"),
    text("channel_line_id"),                   # eBay line item: the sync's de-dupe key
    ref("listing_id", "Listings", "listing_id"),
    ref("item_id", "Items", "item_id"),
    ref("scan_id", "Scans", "scan_id"),
    ref("pack_id", "Packs", "pack_id"),
    date("sale_date"),
    choice("item_type", LISTING_TYPES),
    money("sale_price_usd"),
    money("channel_fee_usd"),
    money("shipping_charged"),
    money("shipping_cost"),
    text("buyer_country"),
    choice("master_requested", YES_NO),
    choice("master_sent", YES_NO),
    text("notes"),
    formula("cost_basis_usd", "Numeric", "$item_id.cost_basis_usd if $item_id else 0", wo=MONEY),
    formula("net_profit_usd", "Numeric",
            "round(($sale_price_usd or 0) + ($shipping_charged or 0) - ($channel_fee_usd or 0)"
            " - ($shipping_cost or 0) - ($cost_basis_usd or 0), 2)", wo=MONEY),
  ]),
  ("Expenses", [
    trigger("expense_id", "Text", seq_id("Expenses", "expense_id", "EXP")),
    date("expense_date"),
    choice("category", ["Inventory - Lot purchase", "Inventory - Single item",
                        "Supplies - Shipping", "Supplies - Scanning", "Supplies - Storage",
                        "Equipment", "Fees - Marketplace", "Fees - Payment processing",
                        "Hosting & Software", "Shipping - Outbound", "Marketing", "Other"]),
    text("description"),
    money("amount_usd"),
    ref("item_id", "Items", "item_id"),
    ref("lot_id", "Lots", "lot_name"),
    text("payment_method"),
    text("receipt_file"),
    text("notes"),
  ]),
  ("Comps", [
    ref("item_id", "Items", "item_id"),
    choice("comp_source", ["eBay sold", "eBay active", "Etsy", "WorthPoint",
                           "Auction", "Show / dealer", "Other"]),
    text("title"),
    text("url"),
    date("sold_date"),
    money("price_usd"),
    money("shipping_usd"),
    text("condition"),
    text("notes"),
    formula("total_usd", "Numeric", "round(($price_usd or 0) + ($shipping_usd or 0), 2)", wo=MONEY),
  ]),
]

# Starting rows for Categories (edit freely in Grist afterwards)
GUIDE_FOR = {
    "Ephemera - Postcard": "Postcards - Field ID",
    "Ephemera - Photo": "Photographs - Field ID",
    "Ephemera - Book": "Books - Field ID",
    "Ephemera - Cookbook": "Books - Field ID",
    "Ephemera - Ad/Menu/Receipt": "Trade Cards and Victorian Scrap - Field ID",
}
PREFIX_FOR = {
    "Retro Gaming": "GAME", "Music - Physical": "MUSIC", "DVD / VHS": "MEDIA",
    "Vintage Toy": "TOY", "Office/Small Goods": "SMGDS", "Other": "SMGDS",
}

def seed_actions():
    return [["BulkAddRecord", "Categories", [None] * len(CATEGORIES), {
        "category": CATEGORIES,
        "prefix": [PREFIX_FOR.get(c, "EPH") for c in CATEGORIES],
        "field_guide": [GUIDE_FOR.get(c, "Field Guide Index") for c in CATEGORIES],
    }]]

# ─────────────────────────────────────────────────────────────────────────────
# Action builders (shared with the offline test)
# ─────────────────────────────────────────────────────────────────────────────
def phase1_actions():
    """Create every table empty, then add columns. Refs to later tables are fine
    because all tables exist before the first AddColumn runs."""
    acts = [["AddTable", t, []] for t, _ in SPEC]
    for t, cols in SPEC:
        for c in cols:
            info = {"type": c["type"], "label": c["id"]}
            if c["kind"] == "data":
                info["isFormula"] = False
            elif c["kind"] == "formula":
                info["isFormula"] = True
                info["formula"] = c["formula"]
            else:  # trigger: data column + formula; recalcWhen 0 = new records (+ deps)
                info["isFormula"] = False
                info["formula"] = c["formula"]
                info["recalcWhen"] = 0
            if c.get("wo"):
                info["widgetOptions"] = json.dumps(c["wo"])
            acts.append(["AddColumn", t, c["id"], info])
    return acts

def phase2_actions(colref):
    """colref(table, col) -> numeric column row id. Sets Ref display columns and
    trigger dependencies, which need the ids of columns created in phase 1."""
    acts = []
    for t, cols in SPEC:
        for c in cols:
            if c.get("visible"):
                target = c["type"].split(":", 1)[1]
                acts.append(["ModifyColumn", t, c["id"], {"visibleCol": colref(target, c["visible"])}])
                acts.append(["SetDisplayFormula", t, None, colref(t, c["id"]), f"${c['id']}.{c['visible']}"])
            if c["kind"] == "trigger" and c["deps"]:
                acts.append(["UpdateRecord", "_grist_Tables_column", colref(t, c["id"]),
                             {"recalcDeps": ["L"] + [colref(t, d) for d in c["deps"]]}])
    return acts

# ─────────────────────────────────────────────────────────────────────────────
# Talking to Grist
# ─────────────────────────────────────────────────────────────────────────────
def read_key():
    k = os.environ.get("GRIST_API_KEY")
    if k:
        return k.strip()
    path = os.path.expanduser("~/.atte-secrets")
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:]
                if line.startswith("GRIST_API_KEY"):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    sys.exit("No API key: set GRIST_API_KEY or add a GRIST_API_KEY=... line to ~/.atte-secrets")

class Grist:
    def __init__(self, server, doc, key):
        self.base = f"{server.rstrip('/')}/api/docs/{doc}"
        self.key = key

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={
            "Authorization": "Bearer " + self.key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                txt = r.read().decode()
                return json.loads(txt) if txt else None
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:800]
            if e.code == 404:
                sys.exit("404 from Grist. Use the doc ID from Settings → API (the long one), "
                         "not the one in the address bar.\n" + detail)
            if e.code in (401, 403):
                sys.exit(f"{e.code}: Grist refused the key. Check it is the current key for "
                         "an account that owns this doc.\n" + detail)
            sys.exit(f"HTTP {e.code} on {method} {path}\n{detail}")

    def apply(self, actions):
        return self.call("POST", "/apply", actions)

    def tables(self):
        return [t["id"] for t in self.call("GET", "/tables")["tables"]]

    def columns(self, table):
        return self.call("GET", f"/tables/{table}/columns?hidden=true")["columns"]

def verify(g):
    """Read the schema back and compare it with SPEC."""
    problems = 0
    for t, cols in SPEC:
        live = {c["id"]: c["fields"] for c in g.columns(t)}
        for c in cols:
            f = live.get(c["id"])
            if f is None:
                print(f"  MISSING  {t}.{c['id']}"); problems += 1; continue
            want_formula = c["kind"] == "formula"
            if f["type"] != c["type"] or bool(f["isFormula"]) != want_formula:
                print(f"  WRONG    {t}.{c['id']}: {f['type']} formula={f['isFormula']}, "
                      f"want {c['type']} formula={want_formula}"); problems += 1
            if c["kind"] != "data" and (f.get("formula") or "") != c["formula"]:
                print(f"  FORMULA  {t}.{c['id']} differs"); problems += 1
            if c.get("visible") and not f.get("visibleCol"):
                print(f"  DISPLAY  {t}.{c['id']} has no display column"); problems += 1
    return problems

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--doc", required=True, help="API doc ID (Settings → API)")
    ap.add_argument("--server", default="https://stash.getgrist.com")
    ap.add_argument("--apply", action="store_true", help="actually build (default is a dry run)")
    a = ap.parse_args()

    p1 = phase1_actions()
    ncols = sum(len(c) for _, c in SPEC)
    print(f"Plan: {len(SPEC)} tables, {ncols} columns -> {a.server} doc {a.doc}")
    for t, cols in SPEC:
        print(f"  {t:14} {len(cols)} columns")

    g = Grist(a.server, a.doc, read_key())
    existing = g.tables()
    print("Doc currently has:", ", ".join(existing) or "(nothing)")
    extra = [t for t in existing if t != "Table1"]
    if extra:
        sys.exit("Refusing: this doc already has tables (" + ", ".join(extra) +
                 "). Run this only on a brand-new, empty doc.")
    drop_table1 = False
    if "Table1" in existing:
        rows = g.call("GET", "/tables/Table1/records?limit=1")["records"]
        if rows:
            sys.exit("Refusing: Table1 has data in it. Use a brand-new doc.")
        drop_table1 = True

    if not a.apply:
        print("\nDry run only. Connection and key work. Re-run with --apply to build.")
        return

    print("Building tables and columns…")
    g.apply(p1)
    refs = {}
    for t, _ in SPEC:
        for c in g.columns(t):
            refs[(t, c["id"])] = c["fields"]["colRef"]
    print("Setting reference display columns and trigger dependencies…")
    g.apply(phase2_actions(lambda t, c: refs[(t, c)]))
    g.apply(seed_actions())
    print(f"Seeded Categories ({len(CATEGORIES)} rows).")
    if drop_table1:
        g.apply([["RemoveTable", "Table1"]])
        print("Removed the blank Table1.")

    print("Verifying…")
    n = verify(g)
    print("All columns match the spec." if n == 0 else f"{n} problem(s) above — send me this output.")

if __name__ == "__main__":
    main()
