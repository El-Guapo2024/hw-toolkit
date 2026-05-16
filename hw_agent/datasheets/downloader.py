"""Datasheet downloader — grabs full PDFs from multiple sources.

Tries in order:
1. LCSC (via jlc_get_part datasheet URL)
2. Manufacturer direct URL patterns (TI, ST, Diodes Inc, etc.)
3. Alldatasheet.com
4. Google search fallback

Usage:
    from hw_agent.datasheets.downloader import download_datasheet

    path = download_datasheet("STM32H723VGT6", output_dir="data/datasheets")
    path = download_datasheet("AP2112K", output_dir="data/datasheets")
    path = download_datasheet("TPS54360", output_dir="data/datasheets")
"""

import re
import subprocess
from pathlib import Path

import httpx


# Known manufacturer datasheet URL patterns
MANUFACTURER_URLS = {
    "TI": [
        "https://www.ti.com/lit/ds/symlink/{part_lower}.pdf",
    ],
    "STMicroelectronics": [
        "https://www.st.com/resource/en/datasheet/{part_lower}.pdf",
    ],
    "Diodes Incorporated": [
        "https://www.diodes.com/assets/Datasheets/{part_base}.pdf",
    ],
    "Microchip": [
        "https://ww1.microchip.com/downloads/en/DeviceDoc/{part}.pdf",
    ],
    "Nordic Semiconductor": [
        "https://infocenter.nordicsemi.com/pdf/{part}_PS_v1.0.pdf",
    ],
    "u-blox": [
        "https://content.u-blox.com/sites/default/files/{part}_DataSheet.pdf",
    ],
    "SIMCom": [
        "https://simcom.ee/documents/{part}/{part}_datasheet.pdf",
    ],
}

# Minimum valid PDF size (skip redirect pages, error pages)
MIN_PDF_SIZE = 100_000  # 100KB — real datasheets are at least this


def download_datasheet(
    part_number: str,
    output_dir: str | Path = "data/datasheets",
    manufacturer: str | None = None,
    lcsc_url: str | None = None,
) -> Path | None:
    """Download a component datasheet PDF.

    Args:
        part_number: MPN like "STM32H723VGT6" or "AP2112K"
        output_dir: Where to save the PDF
        manufacturer: If known, try manufacturer URLs first
        lcsc_url: Direct LCSC datasheet URL if known

    Returns:
        Path to downloaded PDF, or None if all sources failed.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{part_number}.pdf"

    # Already downloaded?
    if output_path.exists() and output_path.stat().st_size > MIN_PDF_SIZE:
        return output_path

    urls_to_try = []

    # 1. LCSC URL if provided
    if lcsc_url:
        urls_to_try.append(("LCSC", lcsc_url))

    # 2. Manufacturer direct URLs
    part_lower = part_number.lower()
    part_base = re.sub(r"[-_]\d+.*$", "", part_number)  # AP2112K-3.3 → AP2112K → AP2112

    if manufacturer and manufacturer in MANUFACTURER_URLS:
        for pattern in MANUFACTURER_URLS[manufacturer]:
            url = pattern.format(
                part=part_number,
                part_lower=part_lower,
                part_base=part_base,
            )
            urls_to_try.append((manufacturer, url))
    else:
        # Try all manufacturers
        for mfr, patterns in MANUFACTURER_URLS.items():
            for pattern in patterns:
                url = pattern.format(
                    part=part_number,
                    part_lower=part_lower,
                    part_base=part_base,
                )
                urls_to_try.append((mfr, url))

    # 3. Try each URL
    client = httpx.Client(
        follow_redirects=True,
        timeout=30.0,
        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
    )

    for source, url in urls_to_try:
        try:
            resp = client.get(url)
            if resp.status_code == 200 and len(resp.content) > MIN_PDF_SIZE:
                # Verify it's actually a PDF
                if resp.content[:4] == b"%PDF":
                    output_path.write_bytes(resp.content)
                    client.close()
                    return output_path
        except Exception:
            continue

    client.close()

    # 4. Try wget as fallback (handles more redirects/cookies)
    for source, url in urls_to_try[:3]:  # only first few
        try:
            result = subprocess.run(
                ["curl", "-fsSL", "-o", str(output_path),
                 "-H", "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                 url],
                capture_output=True, timeout=30,
            )
            if output_path.exists() and output_path.stat().st_size > MIN_PDF_SIZE:
                return output_path
        except Exception:
            continue

    # Clean up failed downloads
    if output_path.exists() and output_path.stat().st_size < MIN_PDF_SIZE:
        output_path.unlink()

    return None


def download_from_lcsc(lcsc_code: str, output_dir: str | Path = "data/datasheets") -> Path | None:
    """Download datasheet using LCSC code. Requires pcbparts MCP for URL lookup.

    This is meant to be called by the LLM which has MCP access:
    1. LLM calls jlc_get_part(lcsc=code) → gets datasheet URL
    2. LLM calls this function with the URL
    """
    # This is a helper — the LLM provides the URL after calling MCP
    # Can't call MCP from here (we're in Python, not in LLM context)
    return None
