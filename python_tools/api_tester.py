"""Send a REST request and inspect the response without installing dependencies."""
import argparse
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("method", choices=["GET", "POST", "PUT", "PATCH", "DELETE"])
    parser.add_argument("url")
    parser.add_argument("--headers", default="", help="Headers, one Name: value per line")
    parser.add_argument("--body", default="", help="Optional request body")
    parser.add_argument("--timeout", type=int, choices=[5, 10, 15, 30], default=15)
    args = parser.parse_args()
    parts = urlsplit(args.url)
    if parts.scheme not in {"http", "https"} or not parts.netloc: parser.error("enter a complete http:// or https:// URL")
    headers = {}
    for line in args.headers.splitlines():
        if not line.strip(): continue
        if ":" not in line: parser.error(f"invalid header line: {line}")
        name, value = line.split(":", 1)
        if not name.strip(): parser.error("header names cannot be empty")
        headers[name.strip()] = value.strip()
    data = args.body.encode("utf-8") if args.body else None
    if data and not any(key.lower() == "content-type" for key in headers):
        headers["Content-Type"] = "application/json"
    request = Request(args.url, data=data, headers=headers, method=args.method)
    start = time.perf_counter()
    try:
        response = urlopen(request, timeout=args.timeout)
        status, response_headers, raw = response.status, response.headers, response.read(1_000_001)
    except HTTPError as exc:
        status, response_headers, raw = exc.code, exc.headers, exc.read(1_000_001)
    except (URLError, TimeoutError, OSError) as exc:
        parser.error(f"request failed: {exc}")
    elapsed = (time.perf_counter() - start) * 1000
    truncated = len(raw) > 1_000_000
    text = raw[:1_000_000].decode("utf-8", errors="replace")
    try: text = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
    except (json.JSONDecodeError, TypeError): pass
    print(f"{args.method} {args.url}\nStatus: {status}    Time: {elapsed:.0f} ms")
    print("\nResponse headers:")
    for name, value in response_headers.items(): print(f"{name}: {value}")
    print("\nResponse body:")
    print(text + ("\n… response truncated at 1 MB" if truncated else ""))

if __name__ == "__main__":
    from ctk_tools import run_tool
    run_tool("api_tester", main)
