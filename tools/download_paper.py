"""Download arxiv PDF and convert to markdown.

Usage: py tools/download_paper.py <arxiv_id> [paper_slug]

Examples:
    py tools/download_paper.py 2606.16274
    py tools/download_paper.py 2606.16274 graphworld
"""
import fitz
import urllib.request
import os, sys

if len(sys.argv) < 2:
    print("Usage: py tools/download_paper.py <arxiv_id> [paper_slug]")
    sys.exit(1)

arxiv_id = sys.argv[1]
slug = sys.argv[2] if len(sys.argv) > 2 else arxiv_id.replace("/", "-")
out_dir = f"knowledge/paper/{slug}"
os.makedirs(out_dir, exist_ok=True)
pdf_path = os.path.join(out_dir, "paper.pdf")
md_path = os.path.join(out_dir, "paper.md")

# Download
pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
print(f"[1/3] Downloading {pdf_url}...")
req = urllib.request.Request(pdf_url, headers={"User-Agent": "Mozilla/5.0"})
with urllib.request.urlopen(req, timeout=90) as resp:
    with open(pdf_path, "wb") as f:
        f.write(resp.read())
size_mb = os.path.getsize(pdf_path) / 1024 / 1024
print(f"      Done: {size_mb:.1f} MB -> {pdf_path}")

# Convert
print("[2/3] Converting PDF to Markdown...")
doc = fitz.open(pdf_path)
text_parts = []
for i, page in enumerate(doc):
    text = page.get_text()
    text_parts.append(f"\n## Page {i+1}\n\n{text}")
full_md = "\n".join(text_parts)
with open(md_path, "w", encoding="utf-8") as f:
    f.write(full_md)
doc.close()
print(f"      Done: {len(full_md):,} chars, {len(text_parts)} pages -> {md_path}")

# Init walkthrough
walkthrough_dir = os.path.join(out_dir, "walkthrough")
os.makedirs(walkthrough_dir, exist_ok=True)
print(f"[3/3] Initialized walkthrough dir: {walkthrough_dir}")
print(f"\n✓ Paper ready: knowledge/paper/{slug}/")
