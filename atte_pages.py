#!/usr/bin/env python3
"""
atte_pages.py — adds the ATTE widget pages to the Grist doc.

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
PAGES = [  # (page name, source table, widget file, version, linked grid beside it?)
    ("Lot Intake", "Lots", "lot-intake.html", 11, False),
    ("Item Intake", "Items", "item-intake.html", 11, False),
    ("Scanning Dashboard", "Items", "scanning-dashboard.html", 10, False),
    # Research works on the row selected in an Items grid on the same page.
    ("Research", "Items", "research-console.html", 1, True),
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
    for name, table, f, ver, linked in PAGES:
        if table not in table_ref:
            sys.exit(f"Table {table} not found — run atte_build.py first.")
        if name in existing:
            print(f"  skip   {name} (already exists)")
        else:
            print(f"  add    {name}  ← {table}  {f}?v={ver}" + ("  (beside an Items grid)" if linked else ""))
            todo.append((name, table, BASE + f + f"?v={ver}", linked))

    if not todo:
        print("Nothing to add."); return
    if not a.apply:
        print("\nDry run only. Re-run with --apply to add the pages."); return

    for name, table, url, linked in todo:
        if linked:
            grid = g.apply([["CreateViewSection", table_ref[table], 0, "record", None, None]])["retValues"][0]
            view = grid["viewRef"]
            made = g.apply([["CreateViewSection", table_ref[table], view, "custom", None, None]])["retValues"][0]
            layout = {"children": [{"children": [{"leaf": grid["sectionRef"]}, {"leaf": made["sectionRef"]}]}]}
            g.apply([
                ["UpdateRecord", "_grist_Views", view, {"name": name, "layoutSpec": json.dumps(layout)}],
                ["UpdateRecord", "_grist_Views_section", made["sectionRef"], {
                    "options": section_options(url), "title": name,
                    "linkSrcSectionRef": grid["sectionRef"]}],
            ])
        else:
            made = g.apply([["CreateViewSection", table_ref[table], 0, "custom", None, None]])["retValues"][0]
            g.apply([
                ["UpdateRecord", "_grist_Views", made["viewRef"], {"name": name}],
                ["UpdateRecord", "_grist_Views_section", made["sectionRef"], {"options": section_options(url)}],
            ])
        print(f"  added  {name}")

    # read back and check
    secs = {r["id"]: r["fields"] for r in records(g, "_grist_Views_section")}
    views = {r["id"]: r["fields"]["name"] for r in records(g, "_grist_Views")}
    ok = 0
    for name, _, f, _, linked in PAGES:
        hit = [s for s in secs.values()
               if views.get(s["parentId"]) == name and s["parentKey"] == "custom"
               and f in json.loads(json.loads(s["options"] or "{}").get("customView") or "{}").get("url", "")
               and json.loads(json.loads(s["options"]).get("customView")).get("access") == "full"
               and (not linked or s.get("linkSrcSectionRef"))]
        print(f"  {'OK     ' if hit else 'MISSING'} {name}")
        ok += bool(hit)
    print("All pages are in place. Reload the doc in your browser." if ok == len(PAGES)
          else "Something didn't take — send me this output.")

if __name__ == "__main__":
    main()
