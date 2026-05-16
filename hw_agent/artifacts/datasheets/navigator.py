"""Smart datasheet navigator — LLM tool for finding the right pages fast.

The navigator doesn't parse specs — that's the LLM's job.
The navigator finds the RIGHT PAGES so the LLM reads 2-3 pages, not 200.

How it works:
1. scan() — reads TOC from first few pages, builds section→page map
2. find_section("electrical") — fuzzy matches, returns page numbers
3. read_page(7) — LLM reads just that page and extracts what it needs

Usage (by LLM via tool calls):
    nav = DatasheetNavigator("TPS54360.pdf")
    nav.scan()                                    # → TOC with page numbers
    pages = nav.find_section("electrical")        # → [7, 8]
    text = nav.read_page(7)                       # → LLM reads this page
    # LLM extracts specs from the text directly
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF

try:
    from rapidfuzz import fuzz, process
    HAS_RAPIDFUZZ = True
except ImportError:
    HAS_RAPIDFUZZ = False


# ─── Data Models ─────────────────────────────────────────────────────────────

@dataclass
class TOCEntry:
    """A section from the table of contents."""
    title: str
    page: int  # 0-indexed
    level: int = 0  # nesting depth
    section_num: str = ""


@dataclass
class TableRow:
    """A single row from a spec table."""
    parameter: str
    symbol: str
    conditions: str
    min: str
    typ: str
    max: str
    unit: str


@dataclass
class PageMap:
    """Map of what's on each page."""
    toc: list[TOCEntry] = field(default_factory=list)
    section_pages: dict[str, list[int]] = field(default_factory=dict)
    total_pages: int = 0
    part_number: str = ""
    manufacturer: str = ""


# ─── TOC Parser ──────────────────────────────────────────────────────────────

# Matches lines like: "6.5  Electrical Characteristics..........7"
# or: "Absolute Maximum Ratings ...................................... 5"
TOC_LINE = re.compile(
    r"^(?:(\d+(?:\.\d+)*)\s+)?"  # optional section number
    r"(.+?)"                       # title
    r"\s*[\.…·\-]+\s*"           # dots/dashes
    r"(\d+)\s*$"                   # page number
)

# Matches section headers in body text
SECTION_HEADER = re.compile(
    r"^(\d+(?:\.\d+)*)\s+(.+)$", re.MULTILINE
)


def _parse_toc(text: str, page_offset: int = 0) -> list[TOCEntry]:
    """Extract TOC entries from text that looks like a table of contents."""
    entries = []
    seen_titles = set()

    for line in text.split("\n"):
        line = line.strip()
        m = TOC_LINE.match(line)
        if m:
            section_num = m.group(1) or ""
            title = m.group(2).strip()
            page = int(m.group(3)) - 1 + page_offset  # convert to 0-indexed

            # Filter noise: skip revision history lines, changelog, etc.
            title_lower = title.lower()
            if any(skip in title_lower for skip in [
                "changed", "added", "deleted", "moved", "updated",
                "from:", "to:", "revision", "copyright",
            ]):
                continue

            # Skip if title is just a number or too short
            if len(title) < 3 or title.replace(".", "").replace("-", "").isdigit():
                continue

            # Skip duplicates
            dedup_key = (title_lower, page)
            if dedup_key in seen_titles:
                continue
            seen_titles.add(dedup_key)

            # Determine nesting level from section number
            level = len(section_num.split(".")) - 1 if section_num else 0

            entries.append(TOCEntry(
                title=title,
                page=page,
                level=level,
                section_num=section_num,
            ))
    return entries


# ─── Table Parser ────────────────────────────────────────────────────────────

# Common header patterns for spec tables
TABLE_HEADERS = [
    re.compile(r"parameter.*min.*typ.*max.*unit", re.IGNORECASE),
    re.compile(r"symbol.*parameter.*min.*typ.*max", re.IGNORECASE),
    re.compile(r"parameter.*conditions.*min.*typ.*max", re.IGNORECASE),
    re.compile(r"parameter.*test\s+condition.*min.*typ.*max", re.IGNORECASE),
    re.compile(r"symbol.*rating.*unit", re.IGNORECASE),  # abs max style
]

# Matches a spec row: values separated by whitespace
SPEC_ROW = re.compile(
    r"^(.+?)\s{2,}"          # parameter name
    r"([\d\.\-—–]+|—)\s+"    # min (or dash)
    r"([\d\.\-—–]+|—)\s+"    # typ
    r"([\d\.\-—–]+|—)\s+"    # max
    r"([A-Za-zµΩ°/%]+)"      # unit
)


def _find_tables(text: str) -> list[dict]:
    """Find spec tables in page text by detecting header rows."""
    tables = []
    lines = text.split("\n")

    for i, line in enumerate(lines):
        # Check if this line is a table header
        for header_pat in TABLE_HEADERS:
            if header_pat.search(line):
                # Found a table header — extract rows below it
                table = {
                    "header_line": i,
                    "header": line.strip(),
                    "rows": [],
                }

                # Read rows until we hit a blank line or non-table content
                for j in range(i + 1, min(i + 50, len(lines))):
                    row_line = lines[j].strip()
                    if not row_line:
                        break
                    # Try to parse as spec row
                    cols = re.split(r"\s{2,}", row_line)
                    if len(cols) >= 3:
                        table["rows"].append(cols)

                if table["rows"]:
                    tables.append(table)
                break

    return tables


# ─── Fuzzy Section Matching ──────────────────────────────────────────────────

# Known section names in datasheets (normalized)
KNOWN_SECTIONS = {
    "absolute maximum ratings": ["absolute maximum", "max ratings", "abs max"],
    "recommended operating conditions": ["recommended operating", "operating conditions"],
    "electrical characteristics": ["electrical char", "dc characteristics", "ac characteristics"],
    "thermal information": ["thermal", "thermal resistance", "thermal data", "theta"],
    "pin configuration": ["pin config", "pinout", "pin description", "pin function"],
    "application circuit": ["typical application", "application info", "application schematic"],
    "detailed description": ["detailed desc", "functional description", "overview"],
    "device specifications": ["specifications", "device spec"],
}


def _fuzzy_match_section(query: str, toc_entries: list[TOCEntry], threshold: int = 60) -> list[TOCEntry]:
    """Find TOC entries that fuzzy-match a section name."""
    query_lower = query.lower()
    matches = []

    # Expand query to include known aliases
    search_terms = [query_lower]
    for canonical, aliases in KNOWN_SECTIONS.items():
        if query_lower in canonical or any(query_lower in a for a in aliases):
            search_terms.extend([canonical] + aliases)

    if HAS_RAPIDFUZZ:
        titles = [e.title.lower() for e in toc_entries]
        for term in search_terms:
            results = process.extract(term, titles, scorer=fuzz.partial_ratio, limit=5)
            for title, score, idx in results:
                if score >= threshold and toc_entries[idx] not in matches:
                    matches.append(toc_entries[idx])
    else:
        # Fallback: simple substring matching
        for entry in toc_entries:
            title_lower = entry.title.lower()
            for term in search_terms:
                if term in title_lower or title_lower in term:
                    if entry not in matches:
                        matches.append(entry)

    return matches


# ─── Main Navigator ──────────────────────────────────────────────────────────

class DatasheetNavigator:
    """Navigate large datasheets efficiently.

    Reads TOC first, then jumps to specific pages.
    Never reads the whole document.
    """

    def __init__(self, pdf_path: str | Path):
        self.pdf_path = Path(pdf_path)
        if not self.pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {self.pdf_path}")
        self.map = PageMap()
        self._doc: fitz.Document | None = None
        self._page_cache: dict[int, str] = {}
        self._scanned = False

    def scan(self) -> PageMap:
        """Quick scan: read first few pages to build TOC and page map.

        This is fast — only reads 3-5 pages, not the whole PDF.
        """
        doc = self._open()
        self.map.total_pages = len(doc)

        # Read first 5 pages to find TOC
        toc_text = ""
        for i in range(min(5, len(doc))):
            text = self._get_page_text(i)
            toc_text += text + "\n"

            # Try to extract part number from first page
            if i == 0:
                pn_match = re.search(r"\b([A-Z]{2,}[0-9]{2,}[A-Z0-9\-]*)\b", text)
                if pn_match:
                    self.map.part_number = pn_match.group(1)

        # Parse TOC
        self.map.toc = _parse_toc(toc_text)

        # Build section → page mapping
        for entry in self.map.toc:
            key = entry.title.lower().strip()
            self.map.section_pages.setdefault(key, [])
            self.map.section_pages[key].append(entry.page)

        # If no TOC found, scan section headers from body text
        if not self.map.toc:
            for i in range(min(10, len(doc))):
                text = self._get_page_text(i)
                for m in SECTION_HEADER.finditer(text):
                    title = m.group(2).strip()
                    if len(title) > 5 and len(title) < 80:
                        self.map.toc.append(TOCEntry(
                            title=title, page=i,
                            section_num=m.group(1),
                        ))

        self._scanned = True
        return self.map

    def find_section(self, query: str) -> list[int]:
        """Find pages for a section by fuzzy matching against TOC.

        Args:
            query: Section name like "electrical characteristics" or "thermal"

        Returns:
            List of 0-indexed page numbers, most relevant first.
        """
        if not self._scanned:
            self.scan()

        matches = _fuzzy_match_section(query, self.map.toc)
        return [m.page for m in matches]

    def read_page(self, page_num: int) -> str:
        """Read text from a specific page."""
        return self._get_page_text(page_num)

    def extract_tables(self, page_num: int) -> list[dict]:
        """Find and parse spec tables on a specific page."""
        text = self._get_page_text(page_num)
        return _find_tables(text)

    def find_spec(self, spec_name: str, pages: list[int] | None = None) -> list[dict]:
        """Search for a specific spec across relevant pages.

        Args:
            spec_name: What to look for (e.g., "dropout voltage", "quiescent current")
            pages: Pages to search. If None, searches all pages with tables.

        Returns:
            List of matches with page, line, and context.
        """
        if not self._scanned:
            self.scan()

        if pages is None:
            # Search pages that likely have specs
            pages = set()
            for query in ["electrical", "specifications", "characteristics",
                          "maximum", "thermal", "operating"]:
                pages.update(self.find_section(query))
            if not pages:
                pages = set(range(min(15, self.map.total_pages)))
            pages = sorted(pages)

        results = []
        for page_num in pages:
            text = self._get_page_text(page_num)

            if HAS_RAPIDFUZZ:
                # Fuzzy match against each line
                lines = text.split("\n")
                for i, line in enumerate(lines):
                    score = fuzz.partial_ratio(spec_name.lower(), line.lower())
                    if score >= 70:
                        ctx_start = max(0, i - 2)
                        ctx_end = min(len(lines), i + 3)
                        results.append({
                            "page": page_num,
                            "score": score,
                            "line": line.strip(),
                            "context": "\n".join(lines[ctx_start:ctx_end]),
                        })
            else:
                # Simple substring
                for line in text.split("\n"):
                    if spec_name.lower() in line.lower():
                        results.append({
                            "page": page_num,
                            "score": 100,
                            "line": line.strip(),
                        })

        # Sort by match quality
        results.sort(key=lambda r: r["score"], reverse=True)
        return results

    def summary(self) -> str:
        """Quick overview of what's in this datasheet."""
        if not self._scanned:
            self.scan()

        lines = [
            f"Datasheet: {self.pdf_path.name}",
            f"Part: {self.map.part_number}",
            f"Pages: {self.map.total_pages}",
            f"TOC entries: {len(self.map.toc)}",
            "",
            "Sections found:",
        ]
        for entry in self.map.toc:
            indent = "  " * (entry.level + 1)
            lines.append(f"{indent}{entry.section_num} {entry.title} → page {entry.page + 1}")

        return "\n".join(lines)

    def _open(self) -> fitz.Document:
        if self._doc is None:
            self._doc = fitz.open(str(self.pdf_path))
        return self._doc

    def _get_page_text(self, page_num: int) -> str:
        if page_num in self._page_cache:
            return self._page_cache[page_num]
        doc = self._open()
        if page_num >= len(doc):
            return ""
        text = doc[page_num].get_text()
        self._page_cache[page_num] = text
        return text

    def close(self):
        if self._doc:
            self._doc.close()
            self._doc = None
