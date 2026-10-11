# AI and parametric sewing-pattern generation as a front end to Marvelous Designer

Research date: 2026-10-11. Web research only; nothing was downloaded, installed or run, and no
Marvelous Designer (MD) tool was used. "Authors claim" marks a result reported by the people who
built the system; nothing below was independently reproduced unless it says so. Repo-local
measurements are cited to `docs/md-conventions.md` (measured on MD 2026.0.315 by this project).

## 1. Parametric pattern programs (GarmentCode/pygarment, Korosteleva 2021 generator, FreeSewing, Seamly2D/Valentina)

### Takeaway
GarmentCode (pygarment) is the only parametric system whose output already *is* what md-mcp needs:
flat panels as vertex/edge lists **plus an explicit stitch list** of (panel, edge) pairs, in an
MIT-licensed, pip-installable Python library. It has hoods, collars, lapels and circle panels but
**no cape, cloak or lining**. FreeSewing and Seamly2D draw good flat pieces (FreeSewing even has a
Roman cloak) but carry **no machine-readable seam pairing**, so they would need seams supplied
separately.

### Cited Findings

**GarmentCode / pygarment (Korosteleva and Sorkine-Hornung)**
- Paper: "GarmentCode: Programming Parametric Sewing Patterns", SIGGRAPH Asia 2023, ACM TOG 42(6) article 197 (Dec 2023); arXiv 2306.03642 submitted 6 June 2023. Authors describe it as the first domain-specific language for garment modelling, object-oriented, building patterns component by component with reusable parameterised parts and automated low-level tasks such as dart placement — [arXiv](https://arxiv.org/abs/2306.03642)
- Repository is MIT-licensed; it is the official implementation of both GarmentCode and GarmentCodeData. Timeline: first release 18 Oct 2023; PyGarment v2.0.0 with new GUI 30 Aug 2024; online configurator launched 20 Nov 2024, offline April 2025, back 3 June 2025 — [GitHub](https://github.com/maria-korosteleva/GarmentCode)
- Base types are Edge, Panel, Component and Interface; basic JSON representation classes became part of PyGarment in v2.0.0 — [GitHub](https://github.com/maria-korosteleva/GarmentCode)
- Install: `pip install pygarment` for the core library, or an editable install; documented with Python 3.9 in conda. On Windows the cairoSVG dependency needs bundled DLLs added to PATH. The draping/data-generation pipeline needs a **custom fork of NVIDIA Warp built manually**; GPU/CUDA requirements are not stated — [Installation.md](https://github.com/maria-korosteleva/GarmentCode/blob/main/docs/Installation.md)
- The repo also keeps an "old Maya+Qualoth tools" route and a Warp-based "data generation" route — [GitHub](https://github.com/maria-korosteleva/GarmentCode)
- Serialised output: `<name>_specification.json` (the pattern), plus `_pattern.svg` and `_pattern.png` always, `_3d_pattern.png` by default, and printable `_print_pattern.svg/.pdf` on request. **No DXF or any other CAD export exists in the code.** Visualisation assumes centimetres (3 px = 1 cm) — [wrappers.py](https://raw.githubusercontent.com/maria-korosteleva/GarmentCode/main/pygarment/pattern/wrappers.py); [core.py](https://raw.githubusercontent.com/maria-korosteleva/GarmentCode/main/pygarment/pattern/core.py)
- Spec JSON: `pattern.panels.<name>` holds `vertices` [[x,y]…], `edges` [{`endpoints`:[i,j], optional `curvature`}], and per-panel 3D placement `translation` [x,y,z] and `rotation` [rx,ry,rz]; `pattern.stitches` is a list of two-sided entries, each side `{"panel": <name>, "edge": <edge index>}` — [core.py](https://raw.githubusercontent.com/maria-korosteleva/GarmentCode/main/pygarment/pattern/core.py); [template spec](https://raw.githubusercontent.com/maria-korosteleva/Garment-Pattern-Generator/master/docs/template_spec_with_comments.json)
- Curvature: a bare `[x,y]` is a relative quadratic Bézier control point (in the edge's local frame, edge = vector (1,0)); a dict form supports `quadratic`, `cubic` (list of control points) and `circle` (`radius`, `large_arc`, `right`). `units_in_meter` defaults to 100 (cm); other values are rescaled to cm. No `right_wrong` flag is serialised in the stitch entries of `core.py` — [core.py](https://raw.githubusercontent.com/maria-korosteleva/GarmentCode/main/pygarment/pattern/core.py)
- The older spec states it supports only "1-to-1 stitches" (one edge to one edge) — [template spec](https://raw.githubusercontent.com/maria-korosteleva/Garment-Pattern-Generator/master/docs/template_spec_with_comments.json)
- Garment components shipped in `assets/garment_programs/`: bands, bodice, circle_skirt, collars, godet, meta_garment, pants, shapes, skirt_levels, skirt_paneled, sleeves, tee — [GitHub directory](https://github.com/maria-korosteleva/GarmentCode/tree/main/assets/garment_programs)
- `collars.py` defines `NoPanelsCollar`, `Turtle` (two-band collar), `SimpleLapelPanel`/`SimpleLapel` (lapel collar), `HoodPanel` and `Hood2Panels` (a two-panel hood). Stitching is declared by `pyg.Interface` objects and `stitching_rules` tuples, e.g. `(self.front.interfaces['right'], self.back.interfaces['right'])`; interfaces can be merged, reversed and flagged `set_right_wrong(True)` — [collars.py](https://raw.githubusercontent.com/maria-korosteleva/GarmentCode/main/assets/garment_programs/collars.py)
- `meta_garment.py` assembles an upper garment, a lower garment (`SkirtCircle`, `AsymmSkirtCircle`, `SkirtManyPanels`, pants, godet, paneled and tiered skirts) and a waistband/belt. **No cape, cloak, open-front over-garment or lining appears** — [meta_garment.py](https://raw.githubusercontent.com/maria-korosteleva/GarmentCode/main/assets/garment_programs/meta_garment.py)
- A fork, GarmentCodeRC (MIT), adds "support for openfront garments & tighter pants & high-waist garments"; it is the generator behind ChatGarment — [GarmentCodeRC](https://github.com/biansy000/GarmentCodeRC); [ChatGarment](https://github.com/biansy000/ChatGarment)
- No GarmentCode exporter to MD, CLO or DXF was found in the repo or in an extended web search; a GitHub "market scan" issue lists GarmentCode as a research tool only — [search-surfaced issue](https://github.com/GustavoTrielli/smart-modeling/issues/2); [GitHub](https://github.com/maria-korosteleva/GarmentCode)

**Korosteleva and Lee 2021 generator and NeuralTailor**
- "Generating Datasets of 3D Garments with Sewing Patterns", NeurIPS 2021 Datasets and Benchmarks; MIT licence; 19 garment types and over 20,000 samples on Zenodo; simulation in Maya 2022+ (closed simulator; open-source replacement listed as a to-do) — [Garment-Pattern-Generator](https://github.com/maria-korosteleva/Garment-Pattern-Generator)
- Templates are the same spec JSON as above plus `parameters` (value, range, type `length`/`additive_length`/`curve`, influence on edges) and `constraints` (`length_equality`) — [template spec](https://raw.githubusercontent.com/maria-korosteleva/Garment-Pattern-Generator/master/docs/template_spec_with_comments.json)
- NeuralTailor (ACM TOG 41(4), July 2022) reconstructs sewing-pattern structure from 3D point clouds; the official repo ships pre-trained models and training/evaluation scripts — [Garment-Pattern-Estimation](https://github.com/maria-korosteleva/Garment-Pattern-Estimation); [arXiv 2201.13063](https://arxiv.org/abs/2201.13063v2)

**FreeSewing**
- JavaScript/Node.js monorepo (core library, designs, plugins), MIT licence; the GitHub repo was **archived on 2 April 2025** and the project moved to Codeberg — [GitHub](https://github.com/freesewing/freesewing)
- Patterns render to SVG (e.g. from Node.js); tiled PDF comes from a separate `svg2pdf` backend — [pattern-via-io](https://github.com/freesewing/pattern-via-io); [svg2pdf](https://npmjs.com/package/@freesewing/svg2pdf)
- No FreeSewing DXF exporter was found — [search result summary; FreeSewing forum](https://forum.freesewing.eu/t/issues-exporting-and-printing-custom-patterns/128)
- Markings are SVG "snippets" (notch, bnotch, button, buttonhole, eyelet, snaps…) implemented as SVG `defs`; the reference documents **no concept of seams or stitch pairing between parts** — [freesewing.dev snippets](https://freesewing.dev/reference/snippets)
- Designs include **Lunetius lacerna, "a historical Roman cloak"** (by Rika Tamaike, measurements include waist-to-knee/floor, HPS-to-waist, neck circumference, shoulder-to-shoulder), Walburga wappenrock (tabard), Tiberius tunica, three hoodies with hoods (Hannah, Huey, Hugo), Carlton/Carlita coats, Jaeger jacket, Gozer ghost costume; no cape or poncho design and no standalone hood — [FreeSewing designs](https://freesewing.eu/designs/); [Lunetius](https://freesewing.eu/designs/lunetius/)

**Seamly2D / Valentina**
- GPLv3+ — [LinuxLinks](https://linuxlinks.com/seamly2d-pattern-making)
- `.sm2d` is schema-validated XML with no official specification; a moderator says "the schemas can and do change at any time" — [Seamly forum](https://forum.seamly.io/t/looking-for-official-documentation-or-xml-schema-for-sm2d-files/15403)
- Command-line export formats include SVG, PDF, PNG, Wavefront OBJ, PS, EPS and several AutoCAD DXF versions — [Debian man page](https://dyn.manpages.debian.org/experimental/seamly2d/seamly2d.1.en.html); [Seamly wiki](https://wiki.seamly.io/wiki/Command_Line)
- Users report its DXF going into other CAD with losses: "all I get are the grainlines" in Lectra; notches and grainlines not imported in VStitcher; a developer is said to be working on a CLO3D-compatible DXF-ASTM layout export (status unconfirmed) — [Seamly forum](https://forum.seamly.io/t/exporting-dxf-pattern-to-be-imported-into-lectra/17010)
- Its "Union" tool joins two pieces into one; no seam-pairing record between pieces was found — [Seamly forum search summary](https://forum.seamly.io/t/looking-for-official-documentation-or-xml-schema-for-sm2d-files/15403) (third-party overview, unverified)

### Inferences
- **GarmentCode's spec maps almost one-to-one onto MD's pattern JSON.** Panel → `PatternList[]` entry; GarmentCode edge → one MD line (sample the Bézier/arc into interior `Polyline` points so the curve stays one line, as MD absorbs curve points between straight points); `stitches` (panel, edge) pairs → `SeamLinePairGroupList[].PairList{First, Second}` with `LengthParam{fStart, fEnd}` = that edge's span along the outline; cm → mm (×10). The edge-index failure class md-mcp suffers from disappears because seams come from the program, not from a model counting lines.
- Because the pattern half of pygarment is pure Python (svgwrite/cairoSVG/numpy), **md-mcp could use GarmentCode without its Warp simulator at all**: MD is the simulator. That sidesteps the hardest Windows dependency (the manually built Warp fork).
- A cape is cheap to add to GarmentCode: a circle/half-circle cape is geometrically a `circle_skirt`-style `CircleArcPanel` hung from the neck circumference instead of the waist, and `Hood2Panels` and `Turtle`/`SimpleLapel` collars already exist with interfaces to attach. A lining would be a second copy of the panels on a separate MD layer, which no surveyed parametric system models.
- FreeSewing's Lunetius is the only ready-made cloak found, but reaching MD would mean parsing SVG paths and hand-authoring the seam list, i.e. the same seam problem md-mcp already has.
- Seamly2D/Valentina are useful for a human pattern maker, not as a programmatic front end: unstable XML schema, GPL, no seam pairing.
- Stitch direction is the subtle part of any converter: GarmentCode tracks orientation through `Interface.reverse()`/`set_right_wrong()` at build time, but `core.py` serialises only (panel, edge) pairs, so a converter must decide MD's `Direction` from edge orientation and panel placement and verify it in MD.

### Gaps
- Whether GarmentCode serialises interface-level many-to-many stitching as subdivided 1-to-1 edge pairs (it appears so, but the assembly code was not read).
- Whether the online configurator offers a JSON download (not checked).
- Whether `pip install pygarment` works on Windows/Python 3.14 without the Warp fork (documented only for Python 3.9; not tested, per constraints).
- I found no "Tau" or other sewing-pattern DSL beyond GarmentCode and NGL (section 2); this may be a gap in search coverage rather than absence.
- AAMA/ASTM handling inside FreeSewing was not confirmed either way beyond "no exporter found".

## 2. Learned generation (image/text to pattern): inputs, outputs, availability, licence, compute, coverage

### Takeaway
Nearly every learned system emits Korosteleva-style spec JSON (2021 format or GarmentCode), so one
GarmentCode→MD converter would make all of them usable. Of those with **released code and weights**,
AIpparel (MIT, text/image → pattern, CVPR 2025) is the strongest fit; ChatGarment (Apache-2.0,
outputs GarmentCode parameters, needs GPT-4o) is the most structurally robust but its weights were
not found linked; DressCode/SewingGPT and Sewformer have weights but older, narrower datasets. None
covers capes, and the 2026 crop (NGL, SewFusion, GarmentWeaver, seam-correspondence) has no code yet.

### Cited Findings

**AIpparel (Nakayama, …, Korosteleva, Sorkine-Hornung, Guibas, Yang, Wetzstein)**
- arXiv 2412.03937, v1 5 Dec 2024, v5 5 Apr 2025; repo states CVPR 2025 Highlight — [arXiv](https://arxiv.org/abs/2412.03937); [GitHub](https://github.com/georgeNakayama/AIpparel-Code)
- Authors claim: a dataset of over 120,000 garments with text, image and pattern annotations; fine-tuned large multimodal model; a new sewing-pattern tokenisation; state of the art on text-to-garment and image-to-garment; interactive editing — [arXiv](https://arxiv.org/abs/2412.03937)
- Code MIT; weights `aipparel_pretrained.pth` on Hugging Face; inputs image and/or text via `inference_example.json`; tested with torch 2.3.1, CUDA 12.1, Python 3.10 (conda); GPU requirement not stated; data = GarmentCodeData plus GarmentCodeData-Multimodal (captions and editing instructions) — [GitHub](https://github.com/georgeNakayama/AIpparel-Code); [Hugging Face](https://huggingface.co/georgeNakayama/AIpparel)

**ChatGarment (Bian, Xu, Xiu, Grigorev, Liu, Lu, Black, Feng)**
- arXiv 2412.17811, v1 23 Dec 2024, v3 3 Apr 2025; CVPR 2025 — [arXiv](https://arxiv.org/abs/2412.17811)
- A fine-tuned VLM outputs a JSON of garment type/style text plus continuous numeric attributes; a refined GarmentCode program turns that into sewing patterns; authors say they expanded garment types and simplified GarmentCode for VLM fine-tuning; claims in-the-wild images and sketches work — [arXiv](https://arxiv.org/abs/2412.17811)
- Code Apache-2.0, built on LLaVA (v1.5 paths) and LISA with LoRA; **GPT-4o rewrites prompts** for generation/editing; README links a training dataset on Hugging Face (`sy000/ChatGarmentDataset`) but **no model-weights link**; simulation via forks GarmentCodeRC and ContourCraft-CG; ships a `cog.yaml` — [GitHub](https://github.com/biansy000/ChatGarment)

**Design2GarmentCode (Zhou et al., Style3D Research)**
- arXiv 2412.08603; CVPR 2025. A pre-trained LMM interprets the design; a fine-tuned LLM writes **GarmentCode programs** rather than vector patterns — [arXiv](https://arxiv.org/abs/2412.08603); [CVPR](https://cvpr.thecvf.com/virtual/2025/poster/33416)
- Simulation success rates of 100% (text) and 94% (image) are reported by a secondary summary, not checked against the paper — [search summary citing themoonlight.io](https://themoonlight.io/review/design2garmentcode-turning-design-concepts-to-tangible-garments-through-program-synthesis)
- The GitHub repo contains **only a project page** (index.html, static/), no code or weights; the page content is CC BY-SA 4.0 — [GitHub](https://github.com/Style3D/design2garmentcode)

**DressCode / SewingGPT (He, Yao, Zhang, Yu, Liu, Xu)**
- SIGGRAPH 2024 (ACM TOG). Text prompts such as `dress, sleeveless, midi length/green velvet`; SewingGPT is a GPT-style model with text cross-attention, trained on the Korosteleva and Lee 2021 dataset with GPT-4V captions; also generates PBR textures — [GitHub](https://github.com/IHe-KaiI/DressCode)
- Pretrained SewingGPT weights on Hugging Face; needs Stable Diffusion 2.1 base for CLIP embeddings; licence not stated on the README; optional simulation is **Windows-only and needs Maya (`mayapy.exe`) and Blender** — [GitHub](https://github.com/IHe-KaiI/DressCode)

**Sewformer (Liu, Xu, Lin, Liang, Yan)**
- "Towards Garment Sewing Pattern Reconstruction from a Single Image", ACM TOG / SIGGRAPH Asia 2023; single image in; pre-trained checkpoint (`liulj/sewformer`) and the SewFactory dataset on Hugging Face; SewFactory's simulation runs in Maya's mayapy on Windows; licence not stated — [GitHub](https://github.com/sail-sg/sewformer)

**GarmentDiffusion (Li, Yao, Wang)**
- IJCAI 2025; arXiv 2504.21476. Diffusion transformer over edge tokens from text, images or partial patterns; authors claim sequences ~10× shorter and generation ~100× faster than SewingGPT, and state of the art on DressCodeData and GarmentCodeData. No code repository found — [IJCAI](https://www.ijcai.org/proceedings/2025/163); [arXiv](https://arxiv.org/abs/2504.21476v4)

**SewingLDM (Liu et al.)**
- arXiv 2412.14453 (Dec 2024); ICCV 2025 pp. 17640–17650. Conditions on text, body shape and sketch; extended pattern vector compressed to a latent; authors claim centimetre-level precision and better complex-garment control. Code availability not verified — [arXiv](https://arxiv.org/abs/2412.14453); [ICCV](https://openaccess.thecvf.com/content/ICCV2025/html/Liu_Multimodal_Latent_Diffusion_Model_for_Complex_Sewing_Pattern_Generation_ICCV_2025_paper.html)

**GarmageNet (Style3D Research)**
- arXiv 2504.01483; SIGGRAPH Asia 2025 (ACM TOG). "Garmage" stores each panel of a **multi-layer** garment as a geometry image; a latent diffusion transformer generates panels and GarmageJigsaw predicts point-to-point sewing; inputs text, sketch, image, pattern or point cloud. Authors claim 99.16% precision / 97.13% recall for sewing-point identification and 91.41% simulation-initialisation success on 150 complex patterns — [arXiv](https://arxiv.org/html/2504.01483v1)
- GarmageSet: "over 10,000" garments in arXiv v1; 14,801 professionally created garments in Style3D press (31 Aug 2026) — [arXiv](https://arxiv.org/html/2504.01483v1); [press release](https://www.malaymail.com/news/money/mediaoutreach/2026/08/31/style3d-advances-fashion-ai-from-content-generation-to-connected-product-workflows/483870)

**DiffAvatar (Li et al.)**
- CVPR 2024; optimises a clothing template in 2D pattern space with differentiable simulation to match captured 3D clothing. No code repository found — [CVF](https://openaccess.thecvf.com/content/CVPR2024/html/Li_DiffAvatar_Simulation-Ready_Garment_Optimization_with_Differentiable_Simulation_CVPR_2024_paper.html); [project page](https://people.csail.mit.edu/liyifei/publication/diffavatar)

**2026 successors (all arXiv preprints; none links code)**
- NGL: Natural Garment Language (Badalyan, …, Michael Black), 24 Feb 2026, rev. 19 May 2026. Training-free: large VLMs write a garment DSL that is deterministically parsed into valid sewing patterns; authors claim multi-layer outfits including occluded parts, SOTA geometry metrics and human/GPT preference; code "will be released for research use" — [arXiv 2602.20700](https://arxiv.org/abs/2602.20700)
- SewFusion, 20 Sep 2026: autoregressive topology plus flow-matched panel geometry; authors claim +6.36% panel accuracy and +11.30% stitch accuracy over prior SOTA on SewFactory and GCD-MM — [arXiv 2609.23548](https://arxiv.org/abs/2609.23548)
- GarmentWeaver, 31 Aug 2026: schema-aware hierarchical targets on a pretrained VLM with feasibility regularisation; claims more executable, simulation-compatible patterns; no metrics in abstract — [arXiv 2608.30550](https://arxiv.org/abs/2608.30550)
- Learning-based Seam Correspondence Reconstruction (Wang, …, Huamin Wang), 23 Jul 2026: infers panel connectivity and fine seam correspondences (many-to-one, intra-panel, curved) **from 2D panels alone**; paper text is CC BY-NC-ND 4.0 — [arXiv 2607.21213](https://arxiv.org/abs/2607.21213)
- DressWild (Feb 2026, pattern parameters from one in-the-wild photo) and Latent Flow Matching of Implicit Fields (Jan 2026, separate stitching module) were seen only in search snippets — [arXiv 2602.16502](https://arxiv.org/abs/2602.16502); [arXiv 2601.17740](https://arxiv.org/abs/2601.17740)
- AutoSew (WACV 2026) builds on GarmentCode data and handles straight, circular-arc, quadratic and cubic edges; only a search snippet of its supplement was seen — [WACV supplement](https://openaccess.thecvf.com/content/WACV2026/supplemental/Rios-Navarro_AutoSew_A_Geometric_WACV_2026_supplemental.pdf)

### Inferences
- **Output-format convergence is the key finding.** AIpparel (GarmentCodeData), ChatGarment (GarmentCode program), Design2GarmentCode (GarmentCode program) and GarmentDiffusion (GarmentCodeData/DressCodeData) all land in GarmentCode's spec; DressCode and Sewformer use the Korosteleva 2021 format, which shares the panels/edges/curvature/stitches core. One converter covers all of them.
- **ChatGarment's design is the most attractive for md-mcp even without its weights**: its model emits a garment type plus numeric parameters and a program draws the pattern, so seams are always valid by construction. md-mcp's own model (Claude) could play the VLM's role directly — choose a GarmentCode/GarmentCodeRC design and parameters — with no fine-tuned model at all. NGL (training-free VLM → DSL → parser) is published evidence for that approach, though its code is unreleased.
- Raw sequence generators (AIpparel, SewingGPT, Sewformer, GarmentDiffusion) can emit invalid or mismatched stitches; the authors' own focus on "simulation success rate" implies failures occur, so md-mcp's seam-ledger checks would still be needed.
- Compute (inference only; not tested): a 7B-class LLaVA-based model in fp16 fits 24 GB, so AIpparel/ChatGarment inference is plausible on an RTX 3090 Ti, but every repo targets Linux/conda with CUDA 12 and some (ChatGarment's `cog.yaml`) assume Docker, which is not installed; Windows-native setup is unproven. DressCode/Sewformer simulation needs Maya, but simulation is unnecessary when MD drapes.
- **Coverage**: all training sets are everyday garments (tops, shirts, dresses, jumpsuits, skirts, pants). A cape, hood-plus-cape or lined cloak is out of distribution for every learned model found; only GarmageNet and NGL claim multi-layer garments.
- The seam-correspondence work (2607.21213) and GarmageJigsaw address exactly the "panels without a seam list" problem (FreeSewing SVG, DXF), but with no code they are not usable today.

### Gaps
- Exact AIpparel output file format (assumed GarmentCode spec JSON from its dataset; README does not say), base-model size and VRAM.
- Whether ChatGarment weights are published elsewhere (README links only the dataset).
- Code/weights status for SewingLDM, GarmageNet and the 2026 preprints beyond their arXiv pages.
- Any independent benchmark of these systems on real designer garments; all metrics found are the authors' own.

## 3. Datasets: coverage, formats, capes

### Takeaway
GarmentCodeData (115k, CC BY 4.0) is the reference dataset and the format most later work trains on;
none of the datasets examined lists capes or cloaks, and only GarmageSet claims professional
multi-layer garments.

### Cited Findings
- GarmentCodeData: 115,000 garments; tops, shirts, dresses, jumpsuits, skirts, pants "among others"; bodies from a CAESAR-based statistical model plus a reference body; three fabrics; open XPBD draping pipeline; ECCV 2024; v2 4 Sept 2024 at doi 10.3929/ethz-b-000690432 — [project page](https://igl.ethz.ch/projects/GarmentCodeData/); [GitHub](https://github.com/maria-korosteleva/GarmentCode)
- GarmentCodeData licence **CC BY 4.0**, published 29 May 2024 — [ETH Research Collection](https://www.research-collection.ethz.ch/entities/researchdata/d9df8fe3-2add-49a2-bb58-21750ca7970c)
- Korosteleva and Lee 2021: 19 garment types, 20,000+ samples, Zenodo doi 10.5281/zenodo.5267549; body is SMPL female average (CC BY 4.0) — [Garment-Pattern-Generator](https://github.com/maria-korosteleva/Garment-Pattern-Generator)
- GarmentCodeData-Multimodal (captions and editing instructions) ships with AIpparel; AIpparel's paper cites over 120,000 garments — [AIpparel GitHub](https://github.com/georgeNakayama/AIpparel-Code); [arXiv](https://arxiv.org/abs/2412.03937)
- SewFactory (Sewformer) on Hugging Face — [Sewformer GitHub](https://github.com/sail-sg/sewformer)
- ChatGarmentDataset on Hugging Face — [ChatGarment GitHub](https://github.com/biansy000/ChatGarment)
- GarmageSet: 10,000+ (arXiv v1) or 14,801 (press) professionally created multi-layer garments with vertex-wise correspondences — [arXiv](https://arxiv.org/html/2504.01483v1); [press](https://www.malaymail.com/news/money/mediaoutreach/2026/08/31/style3d-advances-fashion-ai-from-content-generation-to-connected-product-workflows/483870)
- No source examined mentions capes, cloaks or linings in any of these datasets — [GarmentCodeData](https://igl.ethz.ch/projects/GarmentCodeData/); [meta_garment.py](https://raw.githubusercontent.com/maria-korosteleva/GarmentCode/main/assets/garment_programs/meta_garment.py)

### Inferences
- Since GarmentCode ships `Hood2Panels`, some GarmentCodeData samples may be hooded, but the category list does not say so.
- For md-mcp the datasets matter less as training data than as **a library of known-good, seam-complete patterns**: any sample's `specification.json` could be converted and built in MD as a regression test for the converter (CC BY 4.0 permits that with attribution).

### Gaps
- Per-sample file listing and download size of GarmentCodeData (the dataset documentation page 404'd; the ETH file list hit rate limits).
- Licence of the Korosteleva 2021 Zenodo dataset, SewFactory and GarmageSet (not confirmed).

## 4. Commercial and consumer image-to-pattern tools

### Takeaway
Commercial "photo to pattern" claims come mainly from vendors' own marketing (Style3D) with no
independent accuracy benchmark; MD's own drafting aid (Pattern Drafter, 2025.1 beta) covers T-shirts
only, and nothing found exports a seam list MD can consume.

### Cited Findings
- MD 2025.1 (21 Aug 2025) added **Pattern Drafter (beta)**, a wizard that drafts from real-world measurements and "currently only works for T-shirts"; also an online AI Pose Generator (beta) — [CG Channel](https://www.cgchannel.com/2025/08/clo-virtual-fashion-releases-marvelous-designer-2025-1/)
- A search summary of MD's support page said Pattern Drafter can also work from keywords or an uploaded image; CG Channel mentions only measurements. Unresolved conflict — [CG Channel](https://www.cgchannel.com/2025/08/clo-virtual-fashion-releases-marvelous-designer-2025-1/); [MD support (search snippet only)](https://support.marvelousdesigner.com/hc/en-us/articles/49694260744089)
- MD 2026.0 (April 2026) headline features are a 3D Pencil and lacing tool, toon shader and glTF; no AI pattern generation was reported — [Digital Production](https://digitalproduction.com/2026/04/15/marvelous-designer-2026-0-adds-3d-pencil-and-lacing/)
- Style3D AI claims pattern generation from images or text, drape simulation and sewing-relationship analysis (vendor blog) — [Style3D blog](https://www.style3d.ai/blog/best-ai-tool-for-sewing-patterns/)
- An independent-looking review says CLO's AI lives "around the pattern work rather than inside it" (textures, graphics, rendering, posing), with drafting still manual; it lists fashionINSTA as exporting DXF for CLO, Gerber, Lectra and Browzwear — [Wearview](https://www.wearview.co/blog/best-ai-pattern-making-software); [fashionINSTA (own blog)](https://fashioninsta.ai/blog/best-ai-pattern-making-tool-2026-fashioninsta-leads)
- Optitex and Gerber AccuMark AI are listed as AI pattern tools in a Style3D post (vendor source) — [search summary of Style3D blog](https://www.style3d.ai/blog/what-are-the-top-ai-pattern-making-tools-for-designers-in-2025/)
- A community CLO MCP server drives CLO's Python API (pattern editing, sewing, simulation); experimental and unofficial — [Glama listing](https://glama.ai/mcp/servers/ayyu628vdt)

### Inferences
- Commercial tools are closed and mostly output DXF or their own formats (Style3D `.sproj`, CLO `.zprj`); DXF loses seam pairings (section 5), so none is a better MD front end than an open GarmentCode route.

### Gaps
- No independent accuracy comparison of photo-to-pattern products was found.
- Whether MD's Pattern Drafter is exposed through MD's Python API (not checked; MD tools were out of scope).

## 5. Format bridge: DXF-AAMA/ASTM, what MD imports, and what a converter must emit

### Takeaway
**DXF is a dead end for md-mcp twice over**: MD does not import DXF (vendor staff say so, and this
repo measured no DXF call in MD 2026.0.315's API), and AAMA/ASTM DXF carries per-piece sew lines,
notches and grainlines but not which edge sews to which. The workable bridge is to emit MD's own
pattern JSON (`ImportPatternJSON`) or the equivalent `CreatePatternWithPoints` +
`AddSeamlinePairGroup` calls, from a GarmentCode-style spec.

### Cited Findings
- MD staff in MD's support community: DXF (and PDF) pattern files cannot be imported into MD; they advise CLO for that — [MD community reply](https://support.marvelousdesigner.com/hc/ja/community/posts/48267712674585/comments/48267712824089) (page 403 to direct fetch; content from search summary)
- MD support: MD's pipeline is for 3D asset creation, not physical production; pattern export to AI/PDF is CLO-exclusive — [MD community reply](https://support.marvelousdesigner.com/hc/ja/community/posts/48268925915673/comments/48268941020441) (search summary)
- CLO's own comparison: CLO can import and export DXF-AAMA/ASTM, MD is aimed at CG/animation/games — [CLO support](https://support.clo3d.com/hc/en-us/articles/115012666547)
- Repo measurement (MD 2026.0.315): `ApiTypes.ImportDxfOption`/`ExportDxfOption` exist but **no function takes them**; `.zprj`, `.zpac` and pattern JSON are the formats that move patterns — `docs/md-conventions.md`, section "Adding a sewing pattern"
- Repo measurement: `ImportPatternJSON` round-trips pieces, names, seam groups and exact lengths; it reads documents authored from scratch (`PatternList` alone suffices); seams are `SeamLinePairGroupList[].PairList{First, Second}` with `ShapeID`, `LengthParam{fStart, fEnd}` (fractions along the outline) and `Direction`; MD validates nothing (omitted fields read uninitialised memory); per-piece arrangement offsets in the JSON are **discarded**; import replaces the scene but keeps the avatar and duplicates the fabric list — `docs/md-conventions.md`, "As a document"
- CLO's importer: File > Import > DXF (AAMA/ASTM), works with YUKA, Gerber, Lectra and Optitex files, with scale/auto-scale, rotation and a "Swap Cutting Line and Sewing Line" option — [CLO Import DXF](https://support.clo3d.com/hc/en-us/articles/115012380628-Import-DXF); [CLO 2D Pattern DXF](https://support.clo3d.com/hc/en-us/articles/115000493067-2D-Pattern-DXF-Import-Export) (both via search summaries; direct fetch 403)
- DXF layer convention (Lectra example in Browzwear help; table partly garbled): 1 contour/cut line, 2 end (turn) points, 3 points on curves, 4 notches, 5 grading, 6 mirror axis, 7 material axis, 8/9/11 internal lines, 10 grain line, 13 drill holes, **14 seam (sew) lines**, 19 text — [Browzwear help](https://help.browzwear.com/hc/en-us/articles/4921417374617)
- ASTM D6673 defines sew lines as internal lines "that indicate where stitching of pattern pieces is to be done" (per-piece), i.e. a stitching line within one piece, not a pairing between pieces — [ASTM D6673-04 PDF (search snippet; host later unreachable)](https://softbank.iust.ac.ir/Railway/Codes/ASTM-standard/ASTM_2004_Standards/ASTM%202004%20PART%20IIA/ASTM%202004%20Volume%2007.02%20Textiles%20(II)%20D%204393%20-%20latest/D%206673%20-%2004%20%20_RDY2NZM_.pdf)
- CLO users report DXF export "will not save the sewn lines" and that sewing-edge data is generally not part of DXF-AAMA; a CLO Python-API thread says DXF export carries "the 2D pattern without the stitching information" and that there was no API to create sewing lines (thread date unknown) — [CLO forum](https://support.clo3d.com/hc/es/community/posts/900003347303-How-to-export-a-DXF-AAMA-file-with-sewn-lines-information-included); [CLO forum](https://support.clo3d.com/hc/en-us/community/posts/23007244355865-PatternSewing-using-python-API) (search summaries)
- Vendors interpret AAMA differently, so DXF exchange "doesn't always work properly" — [search summary of Valentina issue 391](https://bitbucket.org/dismine/valentina/issues/391/new-feature-export-pattern-to-dxf-aama); Optitex advises switching cut line to sew line after import — [Optitex](https://help.optitex.com/1382687/Content/Optitex_2D/Import_best_practices_for_standard_formats.htm)
- No existing GarmentCode→MD, GarmentCode→CLO or FreeSewing→MD/CLO converter was found — [extended search; GarmentCode repo](https://github.com/maria-korosteleva/GarmentCode)

### Inferences
- **What a GarmentCode→MD converter must emit** (for `ImportPatternJSON`, or the same data as API calls):
  1. Per panel: closed outline in **mm** (GarmentCode cm × 10), one MD line per GarmentCode edge, with curved edges sampled into interior points of type `Polyline` so each edge stays one line; a name; fabric UUID; grainline angle; every field written explicitly (MD reads garbage for omitted fields — start from a document MD exported).
  2. Per stitch: one `SeamLinePairGroup` with `PairList{First, Second}`, each side `ShapeID` = panel ID and `LengthParam{fStart, fEnd}` = the edge's start/end as fractions of the outline perimeter, plus `Direction` chosen so the two edges run the same way in 3D. This format cannot express the line-index mistakes that sank the cape session.
  3. Optional extras unreachable by API calls but present in JSON: notches (`NotchList`), seam allowances, internal lines.
  4. **3D placement is the open problem**: GarmentCode carries per-panel `translation`/`rotation` around the body, but MD discards JSON arrangement offsets and `SetArrangementPosition` did nothing in this repo's measurements, so the converter would need another placement route (arrangement points, or moving pieces after import) before simulation.
- Indirect routes exist but add a licence and a manual step: DXF → CLO (which imports AAMA/ASTM) → `.zprj`/`.zpac` → MD would still lose seams at the DXF step. OBJ import gives a mesh, not flat pieces with seams.
- The converter is small and generic: a pure-Python function from spec JSON to MD JSON, testable without MD against GarmentCodeData samples, which fits the repo rule that `md_mcp` never imports MD host modules.

### Gaps
- Whether MD can open a CLO `.zprj`/`.zpac` that was built from DXF (plausible as sibling products, not verified).
- The exact meaning and sign convention of MD's `Direction` field in `SeamLinePairGroupList` (needs a live measurement).
- A working route to set a piece's 3D position in MD 2026.0.315 for an imported pattern (repo measured two routes that do nothing).
- The authoritative AAMA/ASTM layer table (ASTM D6673 text not retrievable; the Browzwear table is garbled and Lectra-specific).
