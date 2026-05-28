# Test fixtures

Real SEC documents (truncated where noted) for the `test_stock.py` SEC filing tests.

## File guide

| File | Provenance | Used by |
|---|---|---|
| `ktos_8k_2026-03-02_index.json` | Kratos Defense (CIK 1069258), slim `recent` filings JSON. Contains the 2026-03-02 8-K. | `TestEdgarListFilings` (filter / item-filter / case-insensitivity tests) |
| `sym_10q_2026-q2.htm` | Symbotic 10-Q (filed 2026-05-06, `sym-20260328.htm`), truncated 80K head + 40K tail | `TestFilingTextRegression` (10-Q MD&A extraction) |
| `ktos_s3asr_2026-02.htm` | Kratos S-3ASR 2026-02-26, truncated | `TestExtractSectionProspectus` (S-3ASR section regex) |
| `sym_8k_ex991_2026-05-06.htm` | Symbotic 2026-05-06 8-K EX-99.1 press release, truncated | `TestFilingText8K` (exhibit HTML fetch) |
| `sym_8k_ex992_2026-05-06.pdf` | **Substitution:** American Coastal Insurance (ACIC, CIK 1401521) Q1 2026 earnings deck, kept full (886 KB, 14 pages). SYM doesn't publish PDF investor decks (uses HTML with embedded JPGs), so a different real SEC investor deck PDF was used. Filename keeps `sym_` prefix to match test fixture path. Tests only verify generic PDF properties (page count, table extraction, text length), not company-specific content. | `TestPdfToText`, `TestFilingText8K` (PDF exhibit fetch) |
| `ambq_s1_2024.htm` | **Substitution:** Ambiq Micro (CIK 1500412) S-1 filed 2025-07-03 (Ambiq has no 2024 S-1; first S-1 is 2025), truncated. Filename keeps `2024` suffix; tests only verify prospectus section structure, not filing date. | `TestExtractSectionProspectus` (S-1 sections) |
| `def14a_sample.htm` | NVDA DEF 14A 2026-05-12, truncated. Chose NVDA for stability. | `TestExtractSectionDef14a` (governance sections) |
| `corrupt.pdf` | First 1 KB of `sym_8k_ex992_2026-05-06.pdf` — deliberately truncated to trigger pdfplumber/pdfminer error | `TestPdfToText.test_corrupt_pdf_raises` |
| `scanned.pdf` | Image-only PDF (no text layer) generated via reportlab + Pillow | `TestPdfToText.test_scanned_pdf_returns_empty_text` |

## Truncation

HTML fixtures > 120 KB are truncated to first 80 KB + last 40 KB with a `<!-- TRUNCATED FOR TEST FIXTURE -->` marker in between. This preserves both header anchors (TOC, Item 1 / RISK FACTORS) and tail anchors (signatures, late items), which is what the section-extraction regexes need.

PDF fixtures are kept full because pdfplumber's table extraction needs complete page structure.
