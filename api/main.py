"""
ATTE Scanning Pipeline API
FastAPI backend for the At the Tail End scanning workflow.
Deploy via Coolify on Hetzner.
"""
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
import subprocess
import shutil
import os
import tempfile
import httpx
import asyncio
from pathlib import Path

app = FastAPI(title="ATTE Scanning API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict to your GitHub Pages URL in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── CONFIG ────────────────────────────────────────────────────────────────────
import os

NEXTCLOUD_URL = "https://nx99546.your-storageshare.de"
NEXTCLOUD_USER = "info@squirrelystash.com"
NEXTCLOUD_PASS = os.environ.get("NEXTCLOUD_PASS", "")
NEXTCLOUD_BASE = f"{NEXTCLOUD_URL}/remote.php/dav/files/{NEXTCLOUD_USER}/At the Tail End"

GRIST_API_KEY = os.environ.get("GRIST_API_KEY", "")
GRIST_DOC_ID = "sU9aJthLkBs7FmMhcH6LAd"
GRIST_API = f"https://docs.getgrist.com/api/docs/{GRIST_DOC_ID}"

WORK_DIR = Path("/tmp/atte-pipeline")
WORK_DIR.mkdir(exist_ok=True)

# ── HELPERS ───────────────────────────────────────────────────────────────────
def nextcloud_path(subfolder: str, filename: str) -> str:
    return f"{NEXTCLOUD_BASE}/{subfolder}/{filename}"

async def nc_download(remote_path: str, local_path: Path) -> bool:
    """Download a file from Nextcloud via WebDAV."""
    url = f"{NEXTCLOUD_URL}/remote.php/dav/files/{NEXTCLOUD_USER}/At the Tail End/{remote_path}"
    auth = (NEXTCLOUD_USER, NEXTCLOUD_PASS)
    async with httpx.AsyncClient() as client:
        r = await client.get(url, auth=auth, timeout=60)
        if r.status_code == 200:
            local_path.write_bytes(r.content)
            return True
        return False

async def nc_upload(local_path: Path, remote_path: str) -> bool:
    """Upload a file to Nextcloud via WebDAV."""
    url = f"{NEXTCLOUD_URL}/remote.php/dav/files/{NEXTCLOUD_USER}/At the Tail End/{remote_path}"
    auth = (NEXTCLOUD_USER, NEXTCLOUD_PASS)
    async with httpx.AsyncClient() as client:
        r = await client.put(url, auth=auth, content=local_path.read_bytes(), timeout=120)
        return r.status_code in (200, 201, 204)

async def nc_exists(remote_path: str) -> bool:
    """Check if a file exists on Nextcloud."""
    url = f"{NEXTCLOUD_URL}/remote.php/dav/files/{NEXTCLOUD_USER}/At the Tail End/{remote_path}"
    auth = (NEXTCLOUD_USER, NEXTCLOUD_PASS)
    async with httpx.AsyncClient() as client:
        r = await client.head(url, auth=auth, timeout=10)
        return r.status_code == 200

async def nc_list(remote_folder: str) -> List[str]:
    """List files in a Nextcloud folder via WebDAV PROPFIND."""
    url = f"{NEXTCLOUD_URL}/remote.php/dav/files/{NEXTCLOUD_USER}/At the Tail End/{remote_folder}"
    auth = (NEXTCLOUD_USER, NEXTCLOUD_PASS)
    headers = {"Depth": "1", "Content-Type": "application/xml"}
    body = '<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:displayname/></d:prop></d:propfind>'
    async with httpx.AsyncClient() as client:
        r = await client.request("PROPFIND", url, auth=auth, headers=headers, content=body, timeout=15)
        if r.status_code == 207:
            import re
            names = re.findall(r'<d:displayname>([^<]+)</d:displayname>', r.text)
            return [n for n in names if n and '.' in n]
        return []

# ── FOLDER MAP ────────────────────────────────────────────────────────────────
FOLDER_MAP = {
    'ephemera': 'masters/ephemera',
    'gaming': 'masters/gaming',
    'music': 'masters/music',
    'media': 'masters/media',
    'toys': 'masters/toys',
    'smallgoods': 'masters/smallgoods',
}

# ── ENDPOINTS ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "ATTE Scanning API"}


@app.get("/scan-status/{item_id}")
async def scan_status(item_id: str):
    """
    Check what files exist in Nextcloud for a given item.
    Returns lists of found masters, JPGs, PNGs, and PDF status.
    """
    # Determine folders to check based on item prefix
    prefix = item_id.split('-')[0].lower()
    folder_map = {
        'eph': 'ephemera', 'game': 'gaming', 'music': 'music',
        'media': 'media', 'toy': 'toys', 'smgds': 'smallgoods',
    }
    subfolder = folder_map.get(prefix, 'ephemera')

    masters = await nc_list(f"masters/{subfolder}")
    jpgs = await nc_list(f"derivatives/jpg/{subfolder}")
    pngs = await nc_list(f"derivatives/png_transparent/{subfolder}")

    item_masters = [f for f in masters if f.startswith(item_id)]
    item_jpgs = [f for f in jpgs if f.startswith(item_id)]
    item_pngs = [f for f in pngs if f.startswith(item_id)]

    czur_pdf = f"research_pdfs/{item_id}_czur.pdf"
    final_pdf = f"research_pdfs/{item_id}_complete.pdf"

    return {
        "item_id": item_id,
        "subfolder": subfolder,
        "masters_found": item_masters,
        "jpgs_found": item_jpgs,
        "pngs_found": item_pngs,
        "research_pdf_exists": await nc_exists(final_pdf),
        "czur_pdf_exists": await nc_exists(czur_pdf),
    }


@app.post("/upload-czur-pdf")
async def upload_czur_pdf(
    file: UploadFile = File(...),
    item_id: str = Form(...),
    folder: str = Form("ephemera"),
):
    """Upload a CZUR-scanned PDF to Nextcloud research_pdfs folder."""
    if not file.filename.endswith('.pdf'):
        raise HTTPException(400, "File must be a PDF")

    local = WORK_DIR / f"{item_id}_czur.pdf"
    with open(local, 'wb') as f:
        f.write(await file.read())

    remote = f"research_pdfs/{item_id}_czur.pdf"
    ok = await nc_upload(local, remote)
    local.unlink(missing_ok=True)

    if not ok:
        raise HTTPException(500, "Failed to upload to Nextcloud")

    return {"status": "ok", "path": remote}


class ScanRef(BaseModel):
    scan_id: str
    master_filename: str
    folder: str

class ReplacePagesRequest(BaseModel):
    item_id: str
    scans: List[ScanRef]

@app.post("/replace-pages")
async def replace_pages(req: ReplacePagesRequest):
    """
    Download CZUR PDF and flatbed TIFF masters from Nextcloud,
    replace the corresponding pages in the PDF, re-upload.
    """
    item_id = req.item_id
    work = WORK_DIR / item_id
    work.mkdir(exist_ok=True)

    # Download CZUR PDF
    czur_local = work / f"{item_id}_czur.pdf"
    ok = await nc_download(f"research_pdfs/{item_id}_czur.pdf", czur_local)
    if not ok:
        raise HTTPException(404, f"CZUR PDF not found for {item_id}. Upload it first.")

    # Download each TIFF master
    tiff_locals = []
    for scan in req.scans:
        tiff_local = work / scan.master_filename
        ok = await nc_download(f"masters/{scan.folder}/{scan.master_filename}", tiff_local)
        if ok:
            tiff_locals.append((scan, tiff_local))

    if not tiff_locals:
        raise HTTPException(404, "No TIFF masters found in Nextcloud for this item")

    # Convert each TIFF to a single-page PDF
    replacement_pdfs = []
    for scan, tiff_path in tiff_locals:
        page_pdf = work / f"{scan.scan_id}_page.pdf"
        result = subprocess.run(
            ['img2pdf', str(tiff_path), '-o', str(page_pdf)],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            replacement_pdfs.append((scan, page_pdf))

    # Get page count of CZUR PDF
    result = subprocess.run(
        ['pdftk', str(czur_local), 'dump_data'],
        capture_output=True, text=True
    )
    page_count = 0
    for line in result.stdout.split('\n'):
        if 'NumberOfPages' in line:
            page_count = int(line.split(':')[1].strip())
            break

    # Build replacement — for now replace pages in order of scan registration
    # A future enhancement: map scan page_side to actual PDF page numbers
    output_pdf = work / f"{item_id}_complete.pdf"

    if replacement_pdfs:
        # Simple approach: use pdftk to burst the CZUR PDF and replace pages
        burst_dir = work / "burst"
        burst_dir.mkdir(exist_ok=True)
        subprocess.run(['pdftk', str(czur_local), 'burst', 'output', str(burst_dir / 'pg_%04d.pdf')],
                      capture_output=True)

        # Replace pages — maps first replacement to page 1, etc.
        # For precise page mapping, use the page_mapping.json approach below
        pages_replaced = 0
        for i, (scan, rep_pdf) in enumerate(replacement_pdfs):
            page_num = i + 1
            target = burst_dir / f'pg_{page_num:04d}.pdf'
            if target.exists():
                shutil.copy(rep_pdf, target)
                pages_replaced += 1

        # Reassemble
        page_files = sorted(burst_dir.glob('pg_*.pdf'))
        if page_files:
            subprocess.run(
                ['pdftk'] + [str(p) for p in page_files] + ['cat', 'output', str(output_pdf)],
                capture_output=True
            )
    else:
        shutil.copy(czur_local, output_pdf)

    # Upload result
    remote_out = f"research_pdfs/{item_id}_complete_pre_ocr.pdf"
    await nc_upload(output_pdf, remote_out)

    # Cleanup
    shutil.rmtree(work, ignore_errors=True)

    return {
        "status": "ok",
        "pages_replaced": len(replacement_pdfs),
        "output_path": remote_out,
    }


class OcrRequest(BaseModel):
    item_id: str

@app.post("/run-ocr")
async def run_ocr(req: OcrRequest):
    """Run ocrmypdf on the assembled PDF."""
    item_id = req.item_id
    work = WORK_DIR / f"{item_id}_ocr"
    work.mkdir(exist_ok=True)

    input_pdf = work / "input.pdf"
    output_pdf = work / "output.pdf"

    # Download pre-OCR PDF
    ok = await nc_download(f"research_pdfs/{item_id}_complete_pre_ocr.pdf", input_pdf)
    if not ok:
        # Fall back to CZUR PDF if no replacement was done
        ok = await nc_download(f"research_pdfs/{item_id}_czur.pdf", input_pdf)
        if not ok:
            raise HTTPException(404, "No PDF found to OCR")

    result = subprocess.run(
        ['ocrmypdf', '--skip-text', '--optimize', '1', str(input_pdf), str(output_pdf)],
        capture_output=True, text=True, timeout=300
    )

    if result.returncode not in (0, 6):  # 6 = already has text, still ok
        raise HTTPException(500, f"OCR failed: {result.stderr[:200]}")

    remote_out = f"research_pdfs/{item_id}_complete.pdf"
    await nc_upload(output_pdf, remote_out)
    shutil.rmtree(work, ignore_errors=True)

    return {"status": "ok", "output_path": remote_out}


class DerivativeRequest(BaseModel):
    item_id: str
    scans: List[ScanRef]

@app.post("/generate-derivatives")
async def generate_derivatives(req: DerivativeRequest):
    """
    For each TIFF master in the scan list:
    - Generate a 300 DPI web JPG
    - Generate a transparent PNG
    Upload both to Nextcloud derivatives folders.
    """
    work = WORK_DIR / f"{req.item_id}_deriv"
    work.mkdir(exist_ok=True)

    jpgs_created = 0
    pngs_created = 0

    for scan in req.scans:
        tiff_local = work / scan.master_filename
        ok = await nc_download(f"masters/{scan.folder}/{scan.master_filename}", tiff_local)
        if not ok:
            continue

        # Generate JPG at 300 DPI
        jpg_name = scan.master_filename.replace('_master.tif', '_web.jpg')
        jpg_local = work / jpg_name
        result = subprocess.run([
            'convert', '-density', '300', '-quality', '85',
            str(tiff_local), str(jpg_local)
        ], capture_output=True)
        if result.returncode == 0 and jpg_local.exists():
            await nc_upload(jpg_local, f"derivatives/jpg/{scan.folder}/{jpg_name}")
            jpgs_created += 1

        # Generate transparent PNG
        # Use ImageMagick to remove white background
        png_name = scan.master_filename.replace('_master.tif', '_transparent.png')
        png_local = work / png_name
        result = subprocess.run([
            'convert', str(tiff_local),
            '-alpha', 'set',
            '-fuzz', '10%',
            '-fill', 'none',
            '-draw', 'color 0,0 floodfill',
            str(png_local)
        ], capture_output=True)
        if result.returncode == 0 and png_local.exists():
            await nc_upload(png_local, f"derivatives/png_transparent/{scan.folder}/{png_name}")
            pngs_created += 1

        tiff_local.unlink(missing_ok=True)

    shutil.rmtree(work, ignore_errors=True)

    return {
        "status": "ok",
        "jpgs_created": jpgs_created,
        "pngs_created": pngs_created,
    }
