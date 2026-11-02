import os
import requests
from bs4 import BeautifulSoup
import re

WORKSHEET_DIR = "downloaded_worksheets"

def _download_file(url, folder, filename):
    os.makedirs(folder, exist_ok=True)
    file_path = os.path.join(folder, filename)
    try:
        response = requests.get(url, stream=True)
        response.raise_for_status()
        with open(file_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        return file_path
    except requests.exceptions.RequestException as e:
        print(f"Error downloading {url}: {e}")
        return None

def sync_worksheets(year: str = None, class_name: str = None, subject: str = None, max_items: int = 10):
    base_url = "https://iswkoman.com/worksheets.html"
    try:
        response = requests.get(base_url)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')

        worksheets = []
        # Assuming worksheets are listed under specific HTML structures, e.g., <div> with class 'worksheet-item'
        # This part needs to be adapted based on the actual HTML structure of iswkoman.com/worksheets.html
        # For demonstration, let's assume a simple structure where links are within <p> tags with specific text

        # This is a placeholder for actual scraping logic.
        # A more robust solution would involve inspecting the website's HTML structure
        # to correctly identify and extract worksheet links and metadata.

        # Example: searching for links that contain certain keywords within their text or href
        for link in soup.find_all('a', href=True):
            link_text = link.get_text(strip=True)
            href = link['href']

            # Simple filtering based on keywords in link text and parameters
            if "worksheet" in link_text.lower() or ".pdf" in href.lower():
                matches_year = (year is None) or (year in link_text)
                matches_class = (class_name is None) or (class_name in link_text)
                matches_subject = (subject is None) or (subject in link_text)

                if matches_year and matches_class and matches_subject:
                    full_url = requests.utils.urljoin(base_url, href)
                    # Attempt to extract filename from URL or link text
                    filename = os.path.basename(full_url)
                    if not filename or not re.search(r'\.\w+$', filename): # ensure it has an extension
                        filename = f"{re.sub(r'[^a-zA-Z0-9_.-]', '_', link_text)}.pdf" if link_text else f"worksheet_{len(worksheets)}.pdf"

                    worksheets.append({"url": full_url, "filename": filename, "text": link_text})
                    if len(worksheets) >= max_items:
                        break

        downloaded_count = 0
        for ws in worksheets:
            print(f"Downloading {ws['filename']} from {ws['url']}")
            filepath = _download_file(ws['url'], WORKSHEET_DIR, ws['filename'])
            if filepath:
                downloaded_count += 1

        return f"Successfully synced {downloaded_count} worksheets."

    except requests.exceptions.RequestException as e:
        return f"Error accessing school website: {e}"
    except Exception as e:
        return f"An unexpected error occurred during sync: {e}"

def search_local_worksheets(query: str, year: str = None, class_name: str = None, subject: str = None):
    results = []
    if not os.path.exists(WORKSHEET_DIR):
        return {"message": "No worksheets downloaded yet. Run sync_school_worksheets first."}

    for root, _, files in os.walk(WORKSHEET_DIR):
        for file in files:
            file_path = os.path.join(root, file)
            file_content = ""
            try:
                # For simplicity, we'll just check the filename for now.
                # A full text search for PDFs would require a library like pypdf2 or pdfminer.six
                # For this example, we assume content search is out of scope.
                # If it's a text file, read its content.
                if file.endswith(('.txt', '.md')):
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        file_content = f.read()

                # Basic filtering on filename and (if available) content
                if query.lower() in file.lower() or query.lower() in file_content.lower():
                    matches_year = (year is None) or (year in file)
                    matches_class = (class_name is None) or (class_name in file)
                    matches_subject = (subject is None) or (subject in file)

                    if matches_year and matches_class and matches_subject:
                        results.append({"path": file_path, "filename": file, "excerpt": file_content[:200] + "..." if file_content else "Binary content or no excerpt."})
            except Exception as e:
                print(f"Error reading file {file_path}: {e}")
                continue
    return {"results": results}

def get_worksheet_content(path: str):
    if not os.path.exists(path):
        return {"error": "File not found."}

    try:
        # Assuming worksheets are mainly PDFs.
        # For simplicity, if it's a PDF, we'll just return a message that content cannot be displayed directly.
        # A full PDF content extraction would require a library like pypdf2 or pdfminer.six
        if path.lower().endswith('.pdf'):
            return {"content": f"Content of PDF file at '{path}' cannot be displayed directly. Please open it manually."}
        else:
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            return {"content": content}
    except Exception as e:
        return {"error": f"Error reading file content: {e}"}
