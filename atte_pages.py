#!/usr/bin/env python3
"""
atte_pages.py — adds the three ATTE widget pages to the Grist doc.

    python3 atte_pages.py --doc <API_DOC_ID>            # dry run
    python3 atte_pages.py --doc <API_DOC_ID> --apply    # adds the pages

Same key handling as atte_build.py ($GRIST_API_KEY or ~/.atte-secrets).
Skips any page whose name already exists, so it is safe to run twice.
Each widget gets Access = Full document access.
"""
import argparse
import json
import sys

from atte_build import Grist, read_key

BASE = "https://rocksrdone.github.io/atte-widgets/"
VERSION = 10
PAGES = [  # (page name, source table, widget file)
    ("Lot Intake", "Lots", "lot-intake.html"),
    ("Item Intake", "Items", "item-intake.html"),
    ("Scanning Dashboard", "Items", "scanning-dashboard.html"),
]

def section_options(url):
    custom = {"mode": "url", "url": url, "widgetDef": None, "access": "full",
              "pluginId": "", "sectionId": "", "renderAfterReady": False,
              "widgetId": None, "widgetOptions": None, "columnsMapping": None}
    return json.dumps({"verticalGridlines": True, "horizontalGridlines": True,
                       "zebraStripes": False, "customView": json.dumps(custom)})

def records(g, table):
    return g.call("GET", f"/tables/{table}/records")["records"]

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--doc", required=True)
    ap.add_argument("--server", default="https://stash.getgrist.com")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    g = Grist(a.server, a.doc, read_key())
    table_ref = {r["fields"]["tableId"]: r["id"] for r in records(g, "_grist_Tables")}
    existing = {r["fields"]["name"] for r in records(g, "_grist_Views")}

    todo = []
    for name, table, f in PAGES:
        if table not in table_ref:
            sys.exit(f"Table {table} not found — run atte_build.py first.")
        if name in existing:
            print(f"  skip   {name} (already exists)")
        else:
            print(f"  add    {name}  ← {table}  {f}?v={VERSION}")
            todo.append((name, table, BASE + f + f"?v={VERSION}"))

    if not todo:
        print("Nothing to add."); return
    if not a.apply:
        print("\nDry run only. Re-run with --apply to add the pages."); return

    for name, table, url in todo:
        res = g.apply([["CreateViewSection", table_ref[table], 0, "custom", None, None]])
        made = res["retValues"][0]
        g.apply([
            ["UpdateRecord", "_grist_Views", made["viewRef"], {"name": name}],
            ["UpdateRecord", "_grist_Views_section", made["sectionRef"], {"options": section_options(url)}],
        ])
        print(f"  added  {name}")

    # read back and check
    secs = {r["id"]: r["fields"] for r in records(g, "_grist_Views_section")}
    views = {r["id"]: r["fields"]["name"] for r in records(g, "_grist_Views")}
    ok = 0
    for name, _, f in PAGES:
        hit = [s for s in secs.values()
               if views.get(s["parentId"]) == name and s["parentKey"] == "custom"
               and f in json.loads(json.loads(s["options"] or "{}").get("customView") or "{}").get("url", "")
               and json.loads(json.loads(s["options"]).get("customView")).get("access") == "full"]
        print(f"  {'OK     ' if hit else 'MISSING'} {name}")
        ok += bool(hit)
    print("All three pages are in place. Reload the doc in your browser." if ok == len(PAGES)
          else "Something didn't take — send me this output.")

if __name__ == "__main__":
    main()
