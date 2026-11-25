# Page setup and journal presets

*Figures › Page setup…* sets the paper of the current figure: a journal
preset, the page size, orientation, number of pages, background, grid and
snap. Applying it is one logged change to the figure.

## Choosing a preset

Pick the **publisher**, then the **size**, for example *Nature Portfolio ›
single column — 89 mm*. The publisher list's tooltip names the journals each
publisher's presets apply to. **Custom** holds your own presets (made in
*Preferences*) and *Custom page (free size)*: its width and maximum height
are whatever page size you type.

Below the choice, the dialog shows what the preset says:

- width × maximum height, minimum dpi for photos and micrographs, default
  font and size (with the journal's range when it gives one), thinnest line;
- the journals it applies to, a note, and a link to the source;
- a status badge: **Official** (read on the publisher's own page or PDF),
  **Secondary — verify** (only found in consistent third-party summaries) or
  **Mixed** (some values official, some not).

Values the journal does not state are greyed and marked *(default, not stated
by the journal)*: Pycrograph filled them in with a conservative value. The
status badge only covers the values the journal states.

### Slides and orientation

*Presentation slides* gives PowerPoint and Google Slides page sizes (16:9,
4:3, 16:10). A slide opens as a wide page, in **landscape**; switching to
portrait turns the same paper. Picking a size always sets the orientation
that lays it out at its own width × height: landscape for any page at least
1.25 times wider than tall (every slide), portrait otherwise. Journal
presets open in portrait, even the ones slightly wider than tall (*Nature
double column*, 183 × 170 mm). For slides, dpi, font size and line width
are screen defaults, and there is no font range.

```{warning}
Journal rules change, and many journals within one publisher have their own.
The presets are a starting point: **verify with the journal's current author
guidelines** before submitting.
```

A figure keeps its own copy of the preset it was set up with. Updating the
catalog (a new Pycrograph version) or your presets never changes an existing
figure; the dialog says when the figure's copy differs from the catalog, and
picking the size again applies the current values.

## What the consistency check uses

*Check consistency* (and the check before *Export…*) compares the figure
with its preset:

- **Page size**: the page is wider than the preset width or taller than its
  maximum height (the height usually leaves room for the legend).
- **Font size**: a label or scale-bar text is smaller than the journal's
  minimum or larger than its maximum, when the journal gives them. A
  one-letter label (a panel letter such as *a* or *B'*) is checked only
  against the minimum, since journals usually size panel letters
  separately (Nature: 8 pt bold).
- **Resolution** and **line width**: below the preset dpi and minimum line.

When a limit is a default rather than a journal rule, the warning says so.
Presets you made yourself are checked against their own sizes only.

## Built-in presets

The built-in presets come from a catalog shipped with Pycrograph
(`pycrograph/resources/journal_presets.json`). Each preset name links to its
source.

<!-- BEGIN journal presets table (scripts/make_journal_presets_table.py) -->

Catalog checked 2026-11-25. *Italics*: a default, not stated by the journal.

| Publisher | Preset | Width (mm) | Max height (mm) | dpi | Font (pt) | Min line (pt) | Status |
|---|---|---|---|---|---|---|---|
| Nature Portfolio | [Nature single column](https://research-figure-guide.nature.com/figures/building-and-exporting-figure-panels/) | 89 | 170 | 300 | 7 (5–7) | 0.25 | Official |
| Nature Portfolio | [Nature 1.5 column (120 mm)](https://www.nature.com/nature/for-authors/final-submission) | 120 | 170 | 300 | 7 (5–7) | 0.25 | Official |
| Nature Portfolio | [Nature 1.5 column (136 mm)](https://www.nature.com/nature/for-authors/final-submission) | 136 | 170 | 300 | 7 (5–7) | 0.25 | Official |
| Nature Portfolio | [Nature double column](https://research-figure-guide.nature.com/figures/building-and-exporting-figure-panels/) | 183 | 170 | 300 | 7 (5–7) | 0.25 | Official |
| Nature Portfolio | [Nature Reviews (max)](https://www.nature.com/documents/natrev-artworkguide.pdf) | 180 | 215 | 300 | 8 | *0.5* | Official |
| Cell Press | [Cell Press 2-column journals: 1 column (85 mm)](https://www.cell.com/information-for-authors/figure-guidelines) | 85 | *225* | 300 | 7 (6–…) | *0.5* | Secondary — verify |
| Cell Press | [Cell Press 2-column journals: 1.5 column (114 mm)](https://www.cell.com/information-for-authors/figure-guidelines) | 114 | *225* | 300 | 7 (6–…) | *0.5* | Secondary — verify |
| Cell Press | [Cell Press 2-column journals: full width (174 mm)](https://www.cell.com/information-for-authors/figure-guidelines) | 174 | *225* | 300 | 7 (6–…) | *0.5* | Secondary — verify |
| Cell Press | [Cell Press 3-column journals: 1 column (55 mm)](https://www.cell.com/information-for-authors/figure-guidelines) | 55 | *225* | 300 | 7 (6–…) | *0.5* | Secondary — verify |
| Cell Press | [Cell Press 3-column journals: 2 columns (114 mm)](https://www.cell.com/information-for-authors/figure-guidelines) | 114 | *225* | 300 | 7 (6–…) | *0.5* | Secondary — verify |
| Cell Press | [Cell Press 3-column journals: full width (174 mm)](https://www.cell.com/information-for-authors/figure-guidelines) | 174 | *225* | 300 | 7 (6–…) | *0.5* | Secondary — verify |
| AAAS | [Science: 1 column (57 mm)](https://www.science.org/content/page/instructions-preparing-initial-manuscript) | 57 | *199* | 300 | 7 (5–…) | *0.5* | Secondary — verify |
| AAAS | [Science: 2 columns (121 mm)](https://www.science.org/content/page/instructions-preparing-initial-manuscript) | 121 | *199* | 300 | 7 (5–…) | *0.5* | Secondary — verify |
| AAAS | [Science: 3 columns (184 mm)](https://www.science.org/content/page/instructions-preparing-initial-manuscript) | 184 | *199* | 300 | 7 (5–…) | *0.5* | Secondary — verify |
| AAAS | [Science Advances: 1 column (90 mm)](https://www.science.org/cms/asset/a720088d-4243-48fb-92d8-14a24f180a0f/sciadv_guide_to_preparing_figures_2026.pdf) | 90 | 199 | *300* | *7* | *0.5* | Mixed |
| AAAS | [Science Advances: intermediate (max 144 mm)](https://www.science.org/cms/asset/a720088d-4243-48fb-92d8-14a24f180a0f/sciadv_guide_to_preparing_figures_2026.pdf) | 144 | 199 | *300* | *7* | *0.5* | Mixed |
| AAAS | [Science Advances: 2 columns (184 mm)](https://www.science.org/cms/asset/a720088d-4243-48fb-92d8-14a24f180a0f/sciadv_guide_to_preparing_figures_2026.pdf) | 184 | 199 | *300* | *7* | *0.5* | Mixed |
| PNAS | PNAS: 1 column (87 mm) | 87 | 225 | 300 | 8 (6–12) | *0.5* | Secondary — verify |
| PNAS | PNAS: 1.5 column (114 mm) | 114 | 225 | 300 | 8 (6–12) | *0.5* | Secondary — verify |
| PNAS | PNAS: 2 columns (178 mm) | 178 | 225 | 300 | 8 (6–12) | *0.5* | Secondary — verify |
| PLOS | [PLOS: text column (132 mm)](https://journals.plos.org/plosone/s/figures) | 132 | 222.3 | 300 | 8 (8–12) | *0.5* | Official |
| PLOS | [PLOS: full page (190.5 mm)](https://journals.plos.org/plosone/s/figures) | 190.5 | 222.3 | 300 | 8 (8–12) | *0.5* | Official |
| Rockefeller University Press | [Rockefeller UP: full width (175 mm)](https://rupress.org/jcb/pages/fig-vid-guidelines) | 175 | 228 | 300 | *8* | *0.5* | Official |
| The Company of Biologists | [Company of Biologists: max (180 mm)](https://journals.biologists.com/jcs/pages/manuscript-prep) | 180 | 210 | 300 | 8 | *0.5* | Official |
| Elsevier | [Elsevier: single column (90 mm)](https://www.elsevier.com/about/policies-and-standards/author/artwork-and-media-instructions/artwork-sizing) | 90 | *240* | 300 | 7 (6–…) | *0.5* | Official |
| Elsevier | [Elsevier: 1.5 column (140 mm)](https://www.elsevier.com/about/policies-and-standards/author/artwork-and-media-instructions/artwork-sizing) | 140 | *240* | 300 | 7 (6–…) | *0.5* | Official |
| Elsevier | [Elsevier: double column (190 mm)](https://www.elsevier.com/about/policies-and-standards/author/artwork-and-media-instructions/artwork-sizing) | 190 | *240* | 300 | 7 (6–…) | *0.5* | Official |
| ASBMB | [JBC: 1 column (85 mm)](https://legacyfileshare.elsevier.com/promis_misc/JBCDigitalArtGuidelines.pdf) | 85 | 248 | *300* | *8* | *0.5* | Official |
| ASBMB | [JBC: 1.5 column (127 mm)](https://legacyfileshare.elsevier.com/promis_misc/JBCDigitalArtGuidelines.pdf) | 127 | 248 | *300* | *8* | *0.5* | Official |
| ASBMB | [JBC: 2 columns (175 mm)](https://legacyfileshare.elsevier.com/promis_misc/JBCDigitalArtGuidelines.pdf) | 175 | 248 | *300* | *8* | *0.5* | Official |
| Springer | [Springer (large format): 1 column (84 mm)](https://rd.springer.com/journal/11789/submission-guidelines) | 84 | 234 | *300* | 8 (8–12) | 0.3 | Official |
| Springer | [Springer (large format): 1.5 column (129 mm)](https://rd.springer.com/journal/11789/submission-guidelines) | 129 | 234 | *300* | 8 (8–12) | 0.3 | Official |
| Springer | [Springer (large format): full width (174 mm)](https://rd.springer.com/journal/11789/submission-guidelines) | 174 | 234 | *300* | 8 (8–12) | 0.3 | Official |
| Springer | [Springer (small format): 80 mm](https://rd.springer.com/journal/11789/submission-guidelines) | 80 | 198 | *300* | 8 (8–12) | 0.3 | Official |
| Springer | [Springer (small format): 122 mm](https://rd.springer.com/journal/11789/submission-guidelines) | 122 | 198 | *300* | 8 (8–12) | 0.3 | Official |
| BMC (Springer Nature) | [BMC: half page (85 mm)](https://www.biomedcentral.com/getpublished/writing-resources/data-presentation) | 85 | 225 | 300 | 8 (8–…) | 0.5 | Official |
| BMC (Springer Nature) | [BMC: full page (170 mm)](https://www.biomedcentral.com/getpublished/writing-resources/data-presentation) | 170 | 225 | 300 | 8 (8–…) | 0.5 | Official |
| Wiley | [Wiley: small (80 mm)](https://authors.wiley.com/author-resources/Journal-Authors/Prepare/manuscript-preparation-guidelines.html/figure-preparation.html) | 80 | *240* | 300 | *8* | *0.5* | Official |
| Wiley | [Wiley: large (180 mm)](https://authors.wiley.com/author-resources/Journal-Authors/Prepare/manuscript-preparation-guidelines.html/figure-preparation.html) | 180 | *240* | 300 | *8* | *0.5* | Official |
| Frontiers | [Frontiers: 1 column (85 mm)](https://www.frontiersin.org/journals/cell-and-developmental-biology/for-authors/author-guidelines) | 85 | *240* | 300 | 8 (8–…) | *0.5* | Mixed |
| Frontiers | [Frontiers: 2 columns (180 mm)](https://www.frontiersin.org/journals/cell-and-developmental-biology/for-authors/author-guidelines) | 180 | *240* | 300 | 8 (8–…) | *0.5* | Mixed |
| ASCI | [JCI / JCI Insight: 90 mm](https://www.jci.org/kiosk/publish/figures) | 90 | 172.5 | 600 | *8* | *0.5* | Official |
| ASCI | [JCI / JCI Insight: 180 mm](https://www.jci.org/kiosk/publish/figures) | 180 | 172.5 | 600 | *8* | *0.5* | Official |
| American Physical Society | [Physical Review (APS): 1 column (86 mm)](https://journals.aps.org/info/infoE.html) | 86 | *240* | *300* | *8* | *0.5* | Official |
| American Physical Society | [Physical Review (APS): 2 columns (172 mm)](https://res.cloudinary.com/apsphysics/raw/upload/v1722521536/PhysRev-template_zysvy0.docx) | 172 | *240* | *300* | *8* | *0.5* | Official |
| American Chemical Society | [ACS: 1 column (84.7 mm)](https://researcher-resources.acs.org/publish/author_guidelines?coden=jacs) | 84.7 | *240* | 300 | *8* | *0.5* | Official |
| American Chemical Society | [ACS: 2 columns (177.8 mm)](https://researcher-resources.acs.org/publish/author_guidelines?coden=jacs) | 177.8 | *240* | 300 | *8* | *0.5* | Official |
| Royal Society of Chemistry | [RSC: 1 column (83 mm)](https://www.rsc.org/journals-books-databases/author-and-reviewer-hub/authors-information/prepare-and-format/figures-graphics-images) | 83 | 233 | 600 | 7 | *0.5* | Official |
| Royal Society of Chemistry | [RSC: 2 columns (171 mm)](https://www.rsc.org/journals-books-databases/author-and-reviewer-hub/authors-information/prepare-and-format/figures-graphics-images) | 171 | 233 | 600 | 7 | *0.5* | Official |
| IOP Publishing | [IOP Publishing: small (85 mm)](https://publishingsupport.iopscience.iop.org/questions/figures-journal-articles/) | 85 | *240* | *300* | 8 (8–12) | *0.5* | Official |
| IOP Publishing | [IOP Publishing: large (150 mm)](https://publishingsupport.iopscience.iop.org/questions/figures-journal-articles/) | 150 | *240* | *300* | 8 (8–12) | *0.5* | Official |
| IUCr | [IUCr: 1 column (88.5 mm)](https://journals.iucr.org/services/help/artwork/guide.html) | 88.5 | *240* | 400 | *8* | 0.35 | Official |
| IUCr | [IUCr: full page (180 mm)](https://journals.iucr.org/services/help/artwork/guide.html) | 180 | *240* | 400 | *8* | 0.35 | Official |
| Presentation slides | [PowerPoint widescreen 16:9 (13.333 x 7.5 in)](https://support.microsoft.com/en-us/powerpoint/change-the-size-of-your-powerpoint-slides) | 338.67 | 190.5 | *150* | *14* | *0.5* | Official |
| Presentation slides | [PowerPoint standard 4:3 (10 x 7.5 in)](https://support.microsoft.com/en-us/powerpoint/change-the-size-of-your-powerpoint-slides) | 254 | 190.5 | *150* | *14* | *0.5* | Official |
| Presentation slides | [Google Slides 16:9 (10 x 5.625 in)](https://support.microsoft.com/en-us/powerpoint/change-the-size-of-your-powerpoint-slides) | 254 | 142.875 | *150* | *14* | *0.5* | Mixed |
| Presentation slides | [16:10 slide (10 x 6.25 in)](https://support.microsoft.com/en-us/powerpoint/change-the-size-of-your-powerpoint-slides) | 254 | 158.75 | *150* | *14* | *0.5* | Mixed |

Not included yet:

- **EMBO Press (EMBO J, EMBO Reports, Mol Syst Biol, EMBO Mol Med)**: Official guide gives a 230 mm max height including legend, but the column widths could not be read from the PDF (only third-party values, 86-87 / 180 mm). Add once checked in EMBOPress_Figure_Guidelines.pdf.
- **eLife**: No fixed figure widths in the author guide (online-first). Any preset works; 300 dpi minimum.
- **MBoC (ASCB)**: Current figure specs not found.
- **Cancer Research (AACR), Oncogene**: Not researched yet.

<!-- END journal presets table -->
