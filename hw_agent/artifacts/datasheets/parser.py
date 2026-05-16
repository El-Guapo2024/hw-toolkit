"""Datasheet PDF parser — regex-first, OCR fallback.

Strategy (in order of preference):
1. PyMuPDF text extraction (fast, works on most modern datasheets)
2. Regex pattern matching to find specs in structured text
3. Tesseract OCR (via PyMuPDF) for scanned pages
4. Surya OCR for complex layouts if Tesseract fails

Usage:
    from hw_agent.artifacts.datasheets.parser import DatasheetParser

    ds = DatasheetParser("path/to/AP2112.pdf")
    ds.parse()

    print(ds.summary())
    specs = ds.extract_specs()       # regex-based spec extraction
    tables = ds.extract_tables()     # structured table extraction
    results = ds.search("dropout")   # full-text search
    ds.to_json("ap2112_specs.json")  # export everything
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF


# ─── Data Models ─────────────────────────────────────────────────────────────

@dataclass
class Spec:
    """A single extracted specification."""
    name: str
    min: str
    typ: str
    max: str
    unit: str
    conditions: str
    page: int
    section: str

    def __str__(self):
        vals = []
        if self.min: vals.append(f"min={self.min}")
        if self.typ: vals.append(f"typ={self.typ}")
        if self.max: vals.append(f"max={self.max}")
        return f"{self.name}: {', '.join(vals)} {self.unit} ({self.conditions})"


@dataclass
class SpecTable:
    """A table extracted from the datasheet."""
    title: str
    headers: list[str]
    rows: list[dict[str, str]]
    page: int
    section: str = ""

    def to_markdown(self) -> str:
        if not self.headers or not self.rows:
            return f"### {self.title}\n(empty table)"
        lines = [f"### {self.title}"]
        lines.append("| " + " | ".join(self.headers) + " |")
        lines.append("| " + " | ".join(["---"] * len(self.headers)) + " |")
        for row in self.rows:
            cells = [row.get(h, "") for h in self.headers]
            lines.append("| " + " | ".join(cells) + " |")
        return "\n".join(lines)


@dataclass
class DatasheetInfo:
    """Parsed datasheet contents."""
    filename: str
    part_number: str = ""
    manufacturer: str = ""
    description: str = ""
    total_pages: int = 0
    sections: dict[str, list[int]] = field(default_factory=dict)
    tables: list[SpecTable] = field(default_factory=list)
    specs: list[Spec] = field(default_factory=list)
    text_by_page: dict[int, str] = field(default_factory=dict)
    ocr_pages: list[int] = field(default_factory=list)

    def to_json(self, path: str | None = None) -> str:
        data = {
            "part_number": self.part_number,
            "manufacturer": self.manufacturer,
            "description": self.description,
            "total_pages": self.total_pages,
            "sections": self.sections,
            "specs": [
                {"name": s.name, "min": s.min, "typ": s.typ, "max": s.max,
                 "unit": s.unit, "conditions": s.conditions, "page": s.page,
                 "section": s.section}
                for s in self.specs
            ],
            "tables": [
                {"title": t.title, "headers": t.headers, "rows": t.rows,
                 "page": t.page, "section": t.section}
                for t in self.tables
            ],
        }
        json_str = json.dumps(data, indent=2)
        if path:
            Path(path).write_text(json_str)
        return json_str


# ─── Regex Patterns for Spec Extraction ──────────────────────────────────────

# Matches lines like: "Input Voltage Range     2.5    —    6.0    V"
# or:                  "VIN  Input Voltage  —  250  350  mV"
SPEC_LINE_PATTERN = re.compile(
    r"^(?P<name>[A-Za-z][A-Za-z0-9\s/\(\)_\-\.]+?)"  # Parameter name
    r"\s{2,}"                                           # Separator (2+ spaces)
    r"(?P<values>[\d\.\-—–±]+(?:\s+[\d\.\-—–±]+)*)"   # Values (min/typ/max)
    r"\s+"
    r"(?P<unit>[A-Za-zµΩ°/%]+(?:/[A-Za-zµΩ°/%]+)?)"   # Unit
    r"\s*$",
    re.MULTILINE,
)

# More relaxed: catches "Parameter  Min  Typ  Max  Unit" style rows
SPEC_ROW_PATTERN = re.compile(
    r"^(?P<name>.+?)"
    r"\s{2,}"
    r"(?P<v1>[\d\.\-—–]+|—)"
    r"\s+"
    r"(?P<v2>[\d\.\-—–]+|—)"
    r"\s+"
    r"(?P<v3>[\d\.\-—–]+|—)"
    r"\s+"
    r"(?P<unit>[A-Za-zµΩ°/%]+)",
    re.MULTILINE,
)

# Common units in datasheets
UNITS = {
    "V", "mV", "µV", "kV",
    "A", "mA", "µA", "nA",
    "W", "mW", "µW",
    "Ω", "kΩ", "MΩ", "mΩ",
    "F", "µF", "nF", "pF",
    "H", "µH", "nH", "mH",
    "Hz", "kHz", "MHz", "GHz",
    "°C", "°C/W",
    "dB", "dBm",
    "%", "ppm",
    "ns", "µs", "ms", "s",
}


# ─── Section Detection ───────────────────────────────────────────────────────

SECTION_PATTERNS = {
    "absolute_maximum_ratings": [
        r"absolute\s+maximum\s+ratings?",
        r"abs\.?\s*max",
    ],
    "recommended_operating": [
        r"recommended\s+operating\s+conditions?",
    ],
    "electrical_characteristics": [
        r"electrical\s+characteristics?",
        r"dc\s+characteristics?",
        r"ac\s+characteristics?",
    ],
    "thermal_data": [
        r"thermal\s+(data|characteristics?|information|resistance)",
        r"theta.?ja", r"θja",
    ],
    "pinout": [
        r"pin\s*(out|configuration|description|assignment|function)",
    ],
    "package_info": [
        r"package\s+(information|dimensions?|outline)",
        r"ordering\s+information",
    ],
    "application_circuit": [
        r"typical\s+application",
        r"application\s+(circuit|schematic|diagram)",
    ],
    "power_supply": [
        r"power\s+supply", r"supply\s+voltage", r"power\s+consumption",
    ],
}


def _classify_sections(text: str) -> list[str]:
    text_lower = text.lower()
    found = []
    for name, patterns in SECTION_PATTERNS.items():
        for pat in patterns:
            if re.search(pat, text_lower):
                found.append(name)
                break
    return found


# ─── Spec Extraction (regex-based) ───────────────────────────────────────────

def _extract_specs_from_text(text: str, page_num: int) -> list[Spec]:
    """Extract specs using regex pattern matching on structured text.

    This is the primary extraction method — no AI needed for well-formatted
    datasheet tables. Works on the majority of component datasheets.
    """
    specs = []
    sections = _classify_sections(text)
    section = sections[0] if sections else ""

    # Try the structured row pattern first (Min/Typ/Max columns)
    for m in SPEC_ROW_PATTERN.finditer(text):
        name = m.group("name").strip()
        v1 = m.group("v1").replace("—", "").replace("–", "").strip()
        v2 = m.group("v2").replace("—", "").replace("–", "").strip()
        v3 = m.group("v3").replace("—", "").replace("–", "").strip()
        unit = m.group("unit").strip()

        # Skip header rows
        if name.lower() in ("parameter", "symbol", "description", "test"):
            continue

        specs.append(Spec(
            name=name,
            min=v1, typ=v2, max=v3,
            unit=unit,
            conditions="",
            page=page_num,
            section=section,
        ))

    return specs


# ─── Table Extraction (whitespace-aligned) ───────────────────────────────────

def _extract_tables_from_text(text: str, page_num: int) -> list[SpecTable]:
    """Extract tables by detecting whitespace-aligned columns."""
    tables = []
    lines = text.split("\n")
    sections = _classify_sections(text)
    section = sections[0] if sections else ""

    current_table: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if len(current_table) >= 3:
                tables.append(_lines_to_table(current_table, page_num, section))
            current_table = []
            continue

        cols = re.split(r"\s{2,}", stripped)
        if len(cols) >= 3:
            current_table.append(stripped)
        else:
            if len(current_table) >= 3:
                tables.append(_lines_to_table(current_table, page_num, section))
            current_table = []

    if len(current_table) >= 3:
        tables.append(_lines_to_table(current_table, page_num, section))

    return tables


def _lines_to_table(lines: list[str], page_num: int, section: str) -> SpecTable:
    cols_per_line = [re.split(r"\s{2,}", line.strip()) for line in lines]
    headers = cols_per_line[0]
    rows = []
    for cols in cols_per_line[1:]:
        row = {}
        for i, h in enumerate(headers):
            row[h] = cols[i] if i < len(cols) else ""
        rows.append(row)
    return SpecTable(
        title=headers[0] if headers else "Table",
        headers=headers, rows=rows,
        page=page_num, section=section,
    )


# ─── OCR Backends ────────────────────────────────────────────────────────────

def _ocr_tesseract(page: fitz.Page) -> str:
    """OCR using Tesseract via PyMuPDF's built-in support."""
    # PyMuPDF 1.19+ has get_text("text", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    # For OCR: page.get_textpage_ocr() requires Tesseract installed
    try:
        tp = page.get_textpage_ocr(flags=0, language="eng", dpi=300)
        return page.get_text(textpage=tp)
    except Exception:
        return ""


def _ocr_surya(page: fitz.Page) -> str:
    """OCR using Surya (deep learning, complex layouts)."""
    try:
        from surya.ocr import run_ocr
        from surya.model.detection.model import load_model as load_det_model
        from surya.model.recognition.model import load_model as load_rec_model
        from PIL import Image
        import io

        mat = fitz.Matrix(300 / 72, 300 / 72)
        pix = page.get_pixmap(matrix=mat)
        img = Image.open(io.BytesIO(pix.tobytes("png")))

        det_model = load_det_model()
        rec_model = load_rec_model()
        results = run_ocr([img], [["en"]], det_model, rec_model)

        if results and results[0].text_lines:
            return "\n".join(line.text for line in results[0].text_lines)
        return ""
    except ImportError:
        return ""
    except Exception:
        return ""


# ─── Main Parser ─────────────────────────────────────────────────────────────

class DatasheetParser:
    """Parse component datasheets from PDF.

    Priority: text extraction → regex specs → Tesseract OCR → Surya OCR.
    """

    def __init__(self, pdf_path: str | Path):
        self.pdf_path = Path(pdf_path)
        if not self.pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {self.pdf_path}")
        self.info = DatasheetInfo(filename=self.pdf_path.name)
        self._parsed = False

    def parse(
        self,
        pages: list[int] | None = None,
        max_pages: int = 50,
        ocr: bool = True,
    ) -> DatasheetInfo:
        """Parse the datasheet.

        Args:
            pages: Specific 0-indexed pages. None = all (up to max_pages).
            max_pages: Limit pages to process.
            ocr: Enable OCR for scanned pages.
        """
        doc = fitz.open(str(self.pdf_path))
        self.info.total_pages = len(doc)

        if pages is None:
            pages = list(range(min(len(doc), max_pages)))

        for page_num in pages:
            if page_num >= len(doc):
                continue
            page = doc[page_num]

            # Step 1: Try direct text extraction
            text = page.get_text()

            # Step 2: If text is sparse, try OCR
            if len(text.strip()) < 50 and ocr:
                text = _ocr_tesseract(page)
                if len(text.strip()) < 50:
                    text = _ocr_surya(page)
                if text.strip():
                    self.info.ocr_pages.append(page_num)

            self.info.text_by_page[page_num] = text

            # Step 3: Classify sections
            for section in _classify_sections(text):
                self.info.sections.setdefault(section, [])
                if page_num not in self.info.sections[section]:
                    self.info.sections[section].append(page_num)

            # Step 4: Extract specs via regex
            self.info.specs.extend(_extract_specs_from_text(text, page_num))

            # Step 5: Extract tables via whitespace alignment
            self.info.tables.extend(_extract_tables_from_text(text, page_num))

        # Extract header info from page 1
        if 0 in self.info.text_by_page:
            self._extract_header_info(self.info.text_by_page[0])

        doc.close()
        self._parsed = True
        return self.info

    def _extract_header_info(self, text: str):
        lines = text.strip().split("\n")

        manufacturers = [
            "Texas Instruments", "STMicroelectronics", "Analog Devices",
            "Microchip", "NXP", "Infineon", "Nordic Semiconductor",
            "u-blox", "SIMCom", "Quectel", "Diodes Incorporated",
            "ISSI", "Maxim", "ON Semiconductor", "Vishay", "Murata",
            "TDK", "Würth", "Broadcom", "Renesas", "Silicon Labs",
            "Espressif", "Bosch", "FTDI", "Torex", "Rohm",
        ]

        for line in lines[:20]:
            for mfr in manufacturers:
                if mfr.lower() in line.lower():
                    self.info.manufacturer = mfr
                    break

        pn_pattern = r"\b([A-Z]{2,}[0-9]{2,}[A-Z0-9\-]*)\b"
        for line in lines[:10]:
            match = re.search(pn_pattern, line)
            if match:
                self.info.part_number = match.group(1)
                break

        for line in lines[:5]:
            if len(line.strip()) > 20:
                self.info.description = line.strip()
                break

    # ─── Query Methods ───────────────────────────────────────────────────

    def search(self, query: str) -> list[dict]:
        """Full-text search across all parsed pages."""
        self._ensure_parsed()
        results = []
        for page_num, text in self.info.text_by_page.items():
            lines = text.split("\n")
            for i, line in enumerate(lines):
                if re.search(re.escape(query), line, re.IGNORECASE):
                    ctx_start = max(0, i - 2)
                    ctx_end = min(len(lines), i + 3)
                    results.append({
                        "page": page_num,
                        "line": line.strip(),
                        "context": "\n".join(lines[ctx_start:ctx_end]),
                    })
        return results

    def search_pattern(self, pattern: str) -> list[dict]:
        """Regex search across all parsed pages."""
        self._ensure_parsed()
        results = []
        for page_num, text in self.info.text_by_page.items():
            for m in re.finditer(pattern, text, re.IGNORECASE | re.MULTILINE):
                results.append({
                    "page": page_num,
                    "match": m.group(0),
                    "groups": m.groups(),
                    "span": m.span(),
                })
        return results

    def get_specs_by_section(self, section: str) -> list[Spec]:
        self._ensure_parsed()
        return [s for s in self.info.specs if s.section == section]

    def get_tables_by_section(self, section: str) -> list[SpecTable]:
        self._ensure_parsed()
        return [t for t in self.info.tables if t.section == section]

    def get_page_text(self, page: int) -> str:
        self._ensure_parsed()
        return self.info.text_by_page.get(page, "")

    def summary(self) -> str:
        self._ensure_parsed()
        lines = [
            f"Datasheet: {self.info.filename}",
            f"Part: {self.info.part_number or '(unknown)'}",
            f"Manufacturer: {self.info.manufacturer or '(unknown)'}",
            f"Pages: {self.info.total_pages} total, {len(self.info.text_by_page)} parsed",
            f"OCR pages: {len(self.info.ocr_pages)}",
            f"Specs extracted: {len(self.info.specs)}",
            f"Tables found: {len(self.info.tables)}",
            f"Sections: {', '.join(self.info.sections.keys()) or '(none)'}",
        ]
        return "\n".join(lines)

    def _ensure_parsed(self):
        if not self._parsed:
            self.parse()
