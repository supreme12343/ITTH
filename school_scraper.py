"""
school_scraper.py

Scraper for ISWK Oman school website worksheets.
Fetches worksheets by year -> class -> subject hierarchy.
Saves downloaded worksheets as markdown in local storage/ folder.
"""

import re
import json
import time
from pathlib import Path
from typing import Optional, List, Dict, Any
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

# MarkItDown (Microsoft) — preferred converter for rich PDF/DOCX → markdown
try:
    from markitdown import MarkItDown
    _markitdown = MarkItDown()   # single shared instance
    _MARKITDOWN_AVAILABLE = True
except ImportError:
    _MARKITDOWN_AVAILABLE = False
    _markitdown = None

# fitz (PyMuPDF) — fallback for plain text extraction from PDFs
try:
    import fitz as _fitz
    _FITZ_AVAILABLE = True
except ImportError:
    _fitz = None
    _FITZ_AVAILABLE = False


SCRIPT_DIR = Path(__file__).resolve().parent
STORAGE_DIR = SCRIPT_DIR / "storage"
INDEX_FILE = STORAGE_DIR / "worksheet_index.json"
STORAGE_DIR.mkdir(exist_ok=True)

BASE_URL = "https://www.iswkoman.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Connection": "keep-alive",
}

session = requests.Session()
session.headers.update(HEADERS)


def get_soup(url: str) -> BeautifulSoup:
    """Fetch and parse HTML page."""
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, 'html.parser')


def get_worksheet_years() -> List[Dict[str, str]]:
    """Get list of available worksheet years from main worksheet page."""
    soup = get_soup(f"{BASE_URL}/work-sheet.php")
    years = []

    for link in soup.find_all('a', href=True):
        href = link['href']
        if 'view-worksheet-year.php' in href:
            year_name = link.get_text(strip=True)
            full_url = urljoin(BASE_URL, href)
            years.append({"name": year_name, "url": full_url})

    return years


def get_classes_for_year(year_url: str) -> List[Dict[str, str]]:
    """Get list of classes for a given year."""
    soup = get_soup(year_url)
    classes = []

    for link in soup.find_all('a', href=True):
        href = link['href']
        if 'view-worksheet-subject.php' in href and 'class=' in href:
            class_name = link.get_text(strip=True)
            full_url = urljoin(BASE_URL, href)
            classes.append({"name": class_name, "url": full_url})

    return classes


def get_subjects_for_class(class_url: str) -> List[Dict[str, str]]:
    """Get list of subjects for a given class."""
    soup = get_soup(class_url)
    subjects = []

    for link in soup.find_all('a', href=True):
        href = link['href']
        if 'view-worksheet.php' in href and 'subject=' in href:
            subject_name = link.get_text(strip=True)
            full_url = urljoin(BASE_URL, href)
            subjects.append({"name": subject_name, "url": full_url})

    return subjects


def get_worksheets_for_subject(subject_url: str) -> List[Dict[str, Any]]:
    """Get list of worksheets for a subject."""
    soup = get_soup(subject_url)
    worksheets = []

    table = soup.find('table')
    if table:
        for row in table.find_all('tr')[1:]:  # skip header
            cols = row.find_all('td')
            if len(cols) >= 4:
                no = cols[0].get_text(strip=True)
                title = cols[1].get_text(strip=True)
                teacher = cols[2].get_text(strip=True)
                date = cols[3].get_text(strip=True)

                doc_link = None
                for col in cols:
                    doc_link = col.find('a', href=True)
                    if doc_link and ('uploads/work-sheet' in doc_link.get('href', '') or doc_link.get('href', '').endswith('.docx') or doc_link.get('href', '').endswith('.pdf')):
                        break

                doc_url = None
                if doc_link:
                    doc_url = urljoin(BASE_URL, doc_link['href'])

                worksheets.append({
                    "number": no,
                    "title": title,
                    "teacher": teacher,
                    "date": date,
                    "doc_url": doc_url
                })

    return worksheets


def sanitize_filename(name: str) -> str:
    """Sanitize filename for filesystem."""
    return re.sub(r'[<>:"/\\|?*]', '_', name)[:200]


def get_storage_path(year: str, class_name: str, subject: str, title: str, ext: str = ".md") -> Path:
    """Get the local storage path for a worksheet."""
    year_clean = sanitize_filename(year)
    class_clean = sanitize_filename(class_name)
    subject_clean = sanitize_filename(subject)
    title_clean = sanitize_filename(title)

    path = STORAGE_DIR / year_clean / class_clean / subject_clean / f"{title_clean}{ext}"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def download_file(doc_url: str, save_path: Path) -> bool:
    """Download a file from URL to local path."""
    try:
        resp = session.get(doc_url, timeout=30)
        resp.raise_for_status()
        save_path.write_bytes(resp.content)
        return True
    except Exception as e:
        print(f"Error downloading {doc_url}: {e}")
        return False


def convert_to_markdown(doc_path: Path) -> str:
    """
    Convert a downloaded document to clean markdown text.

    Strategy (in priority order):
      1. MarkItDown (Microsoft) — best quality. Handles PDF, DOCX, PPTX,
         XLSX, HTML, images embedded in documents, and preserves tables as
         proper markdown tables. For images inside PDFs it notes them as
         [image] rather than losing the context entirely.
      2. fitz plain text — fallback for PDFs when MarkItDown is not installed.
      3. Raw UTF-8 read — last resort for plain-text file types.
    """
    if _MARKITDOWN_AVAILABLE:
        try:
            result = _markitdown.convert(str(doc_path))
            md = result.text_content or ""
            if md.strip():
                return md
            # Empty result — fall through to fitz
        except Exception as e:
            print(f"[MarkItDown] Warning: {e} — falling back to fitz")

    ext = doc_path.suffix.lower()
    if ext == ".pdf" and _FITZ_AVAILABLE:
        try:
            doc = _fitz.open(str(doc_path))
            parts = []
            for page in doc:
                text = page.get_text()
                if text.strip():
                    parts.append(text.strip())
            doc.close()
            return "\n\n".join(parts) if parts else "[No text content found in PDF]"
        except Exception as e:
            return f"[Error extracting PDF: {e}]"

    try:
        return doc_path.read_text(encoding="utf-8")
    except Exception:
        return f"[Cannot read file type: {ext}]"

def load_index() -> Dict[str, Any]:
    """Load the worksheet index."""
    if INDEX_FILE.exists():
        try:
            return json.loads(INDEX_FILE.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {"worksheets": []}


def save_index(index: Dict[str, Any]):
    """Save the worksheet index."""
    INDEX_FILE.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding='utf-8')


def sync_worksheets(year: str = None, class_name: str = None, subject: str = None, max_items: int = None) -> Dict[str, Any]:
    """
    Sync worksheets from school website to local storage.

    Args:
        year: Filter by year (e.g., "2026-2027"), None = all years
        class_name: Filter by class (e.g., "XI"), None = all classes
        subject: Filter by subject (e.g., "Computer Science"), None = all subjects
        max_items: Maximum worksheets to download per subject

    Returns:
        Dict with sync results
    """
    results = {
        "synced": 0,
        "skipped": 0,
        "errors": 0,
        "details": []
    }

    index = load_index()
    existing_paths = {ws["path"] for ws in index["worksheets"]}

    years = get_worksheet_years()
    if year:
        years = [y for y in years if year in y["name"]]

    for y in years:
        print(f"Processing year: {y['name']}")
        classes = get_classes_for_year(y["url"])
        if class_name:
            classes = [c for c in classes if class_name.upper() in c["name"].upper()]

        for cls in classes:
            print(f"  Class: {cls['name']}")
            subjects = get_subjects_for_class(cls["url"])
            if subject:
                subjects = [s for s in subjects if subject.lower() in s["name"].lower()]

            for subj in subjects:
                print(f"    Subject: {subj['name']}")
                worksheets = get_worksheets_for_subject(subj["url"])

                if max_items:
                    worksheets = worksheets[:max_items]

                for ws in worksheets:
                    if not ws.get("doc_url"):
                        results["skipped"] += 1
                        results["details"].append({
                            "status": "skipped",
                            "reason": "no_document_url",
                            "worksheet": ws["title"]
                        })
                        continue

                    doc_url = ws["doc_url"]
                    ext = Path(urlparse(doc_url).path).suffix
                    if not ext:
                        ext = ".docx"

                    save_path = get_storage_path(y["name"], cls["name"], subj["name"], ws["title"], ext)
                    rel_path = str(save_path.relative_to(STORAGE_DIR))

                    if rel_path in existing_paths:
                        results["skipped"] += 1
                        results["details"].append({
                            "status": "skipped",
                            "reason": "already_exists",
                            "path": rel_path
                        })
                        continue

                    print(f"      Downloading: {ws['title']}")
                    if download_file(doc_url, save_path):
                        markdown_content = convert_to_markdown(save_path)
                        md_path = save_path.with_suffix('.md')
                        md_path.write_text(markdown_content, encoding='utf-8')
                        md_rel_path = str(md_path.relative_to(STORAGE_DIR))

                        index["worksheets"].append({
                            "path": md_rel_path,
                            "year": y["name"],
                            "class": cls["name"],
                            "subject": subj["name"],
                            "title": ws["title"],
                            "teacher": ws["teacher"],
                            "date": ws["date"],
                            "text_preview": markdown_content[:500]
                        })
                        existing_paths.add(md_rel_path)

                        save_path.unlink(missing_ok=True)

                        results["synced"] += 1
                        results["details"].append({
                            "status": "synced",
                            "path": md_rel_path,
                            "title": ws["title"],
                            "year": y["name"],
                            "class": cls["name"],
                            "subject": subj["name"]
                        })
                    else:
                        results["errors"] += 1
                        results["details"].append({
                            "status": "error",
                            "worksheet": ws["title"],
                            "url": doc_url
                        })

                    time.sleep(0.5)

    save_index(index)
    return results


def list_local_worksheets() -> List[Dict[str, Any]]:
    """List all locally stored worksheets."""
    index = load_index()
    worksheets = []
    for ws in index["worksheets"]:
        full_path = STORAGE_DIR / ws["path"]
        content = ""
        if full_path.exists():
            content = full_path.read_text(encoding='utf-8')
        worksheets.append({
            "year": ws["year"],
            "class": ws["class"],
            "subject": ws["subject"],
            "title": ws["title"],
            "path": ws["path"],
            "content": content
        })
    return worksheets


def search_local_worksheets(query: str, year: str = None, class_name: str = None, subject: str = None) -> List[Dict[str, Any]]:
    """Search local worksheets by keyword and filters."""
    all_ws = list_local_worksheets()
    results = []

    query_lower = query.lower()
    for ws in all_ws:
        if year and ws["year"] != year:
            continue
        if class_name and class_name.upper() not in ws["class"].upper():
            continue
        if subject and subject.lower() not in ws["subject"].lower():
            continue

        if (query_lower in ws["title"].lower() or
            query_lower in ws["content"].lower() or
            query_lower in ws["subject"].lower()):
            results.append(ws)

    return results


def get_worksheet_content(path: str) -> Optional[str]:
    """Get the markdown content of a worksheet."""
    try:
        root = STORAGE_DIR.resolve()
        p = (root / str(path)).resolve()
        p.relative_to(root)
    except (OSError, ValueError):
        return None
    if p.is_file():
        try:
            return p.read_text(encoding='utf-8')
        except OSError:
            return None
    return None


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "sync":
        year = sys.argv[2] if len(sys.argv) > 2 else None
        class_name = sys.argv[3] if len(sys.argv) > 3 else None
        subject = sys.argv[4] if len(sys.argv) > 4 else None
        result = sync_worksheets(year=year, class_name=class_name, subject=subject)
        print(json.dumps(result, indent=2))
    elif len(sys.argv) > 1 and sys.argv[1] == "search":
        query = sys.argv[2] if len(sys.argv) > 2 else ""
        results = search_local_worksheets(query)
        print(json.dumps(results, indent=2))
    else:
        print("Usage:")
        print("  python school_scraper.py sync [year] [class] [subject]")
        print("  python school_scraper.py search <query>")
