# Test fixtures

Real SEC documents (full, untruncated) for the `test_stock.py` SEC filing tests.

## File guide

| File | Provenance | Used by |
|---|---|---|
| `ktos_8k_2026-03-02_index.json` | Kratos Defense (CIK 1069258), slim `recent` filings JSON. Contains the 2026-03-02 8-K. | `TestEdgarListFilings` (filter / item-filter / case-insensitivity tests) |
| `sym_10q_2026-q2.htm` | Symbotic 10-Q (CIK 1837240, acc 0001837240-26-000024, filed 2026-05-06, `sym-20260328.htm`), full document (~2 MB) | `TestFilingTextRegression` (10-Q MD&A extraction) |
| `ktos_s3asr_2026-02.htm` | Kratos S-3ASR (CIK 1069258, acc 0001628280-26-012259, filed 2026-02-26, `kratos-sx3asr.htm`), full document (~336 KB) | `TestExtractSectionProspectus` (S-3ASR section regex) |
| `sym_8k_ex991_2026-05-06.htm` | Symbotic 2026-05-06 8-K EX-99.1 press release (CIK 1837240, acc 0001837240-26-000023, `q2268-k_ex991.htm`), full document (~375 KB) | `TestFilingText8K` (exhibit HTML fetch) |
| `sym_8k_ex992_2026-05-06.pdf` | **Substitution:** American Coastal Insurance (ACIC, CIK 1401521) Q1 2026 earnings deck, kept full (886 KB, 14 pages). SYM doesn't publish PDF investor decks (uses HTML with embedded JPGs), so a different real SEC investor deck PDF was used. Filename keeps `sym_` prefix to match test fixture path. Tests only verify generic PDF properties (page count, table extraction, text length), not company-specific content. | `TestPdfToText`, `TestFilingText8K` (PDF exhibit fetch) |
| `ambq_s1_2024.htm` | **Substitution:** Ambiq Micro (CIK 1500412, acc 0001193125-25-155270, filed 2025-07-03, `d377490ds1.htm`) S-1. Filename keeps `2024` suffix; tests only verify prospectus section structure, not filing date. Full document (~2 MB). | `TestExtractSectionProspectus` (S-1 sections) |
| `def14a_sample.htm` | NVDA DEF 14A (CIK 1045810, acc 0001045810-26-000036, filed 2026-05-12, `nvda-20260512.htm`), full document (~1.4 MB). | `TestExtractSectionDef14a` (governance sections) |
| `corrupt.pdf` | First 1 KB of `sym_8k_ex992_2026-05-06.pdf` — deliberately truncated to trigger pdfplumber/pdfminer error | `TestPdfToText.test_corrupt_pdf_raises` |
| `scanned.pdf` | Image-only PDF (no text layer) generated via reportlab + Pillow | `TestPdfToText.test_scanned_pdf_returns_empty_text` |

## Why fixtures are kept full (not truncated)

An earlier version of this directory truncated HTML fixtures to 80 KB head + 40 KB tail.
That cut MD&A, risk-factor, and dilution sections — which live in the middle of long documents —
out of the HTML, causing `_extract_section_10k` to return `None` for those sections and
breaking all section-extraction tests. All HTML fixtures are now stored in full. The total
fixtures directory size is ~8 MB, well within repository limits.

PDF fixtures are kept full because pdfplumber's table extraction needs complete page structure.

## Re-fetching

If a fixture becomes stale, re-fetch it with:

```bash
UA="stock-prices-tests as80639as@gmail.com"
curl -sS -H "User-Agent: $UA" <url> -o scripts/test_fixtures/<filename>
```

URLs are recorded in the Provenance column above. To look up a filing by accession number:
`https://www.sec.gov/Archives/edgar/data/<CIK>/<acc-no-dashes>/<primary-document>`
