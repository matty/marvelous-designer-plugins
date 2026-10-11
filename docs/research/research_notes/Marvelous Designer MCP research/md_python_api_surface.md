# Marvelous Designer's programmatic surface (Python API, install, live build) for md-mcp

Target: MD 2026.0.315 Enterprise Network Offline, `C:\Program Files\Marvelous Designer Enterprise Network Offline`. Research date 2026-10-11.

**Method note, read first.** MD was **not running** during this research: `Get-Process` found no MD process and `md_status` answered "No Marvelous Designer listener on 127.0.0.1:5121". So the live `md_api` index could not be queried. In its place, the installed binary was read **read-only**: the printable strings of `MarvelousDesigner_Enterprise_Offline.exe` (194 MB) were extracted to the scratchpad. Three kinds of string matter:

- **`__FUNCSIG__` strings** give exact C++ signatures for 739 members of seven API classes: `ExportAPI` 86, `FabricAPI` 134, `ImportAPI` 38, `PatternAPI` 153, `RestAPI` 8, `SampleAPI` 5, `UtilityAPI` 315.
- **`"[Python] <Name> "` wrapper strings** mark the functions that have a Python binding: Export 35, Fabric 114, Import 21, Pattern 133, Rest 3, Sample 5, Utility 259.
- **Bare name strings** in the pybind11 binding block, file offsets about 121.89M–122.19M.

A C++ member with neither a wrapper string nor a bare name in that block is treated as **compiled in but not exposed to Python**. One check supports this heuristic: `ImportDXF`/`ExportDXF` exist as C++ members, have no Python wrapper, and are confirmed absent from the live index by `docs/md-conventions.md`. It is still an inference from the binary, not a live `md_api` reading. Everything marked "binding present" below should be confirmed with `md_api` once MD is open.

## Q1. Official documentation: where it lives, which modules, what changed between releases, and official samples

### Takeaway
MD's Python API is documented at developer.marvelousdesigner.com ("Marvelous Designer API 0.1"). That site covers MD 2024.2.189 to 2025.1.201 only: there are no 2026 entries, nothing is listed as removed, and much of what 2026.0.315 exposes is missing from it. CLO's developer.clo3d.com documents the same lineage and is far more current (V11.0.0, August 2026), with a strict superset of MD's documented names. Its newest additions are not in MD 2026.0.315: a seam-removing merge, geometry getters and physical material setters. There is no official sample repository; the only official samples are four short "API Scenario" snippets.

### Cited Findings
- **Site structure.** The MD API docs site has these sections: Changelog, API List (`EXPORT_API`, `IMPORT_API`, `PATTERN_API`, `FABRIC_API`, `UTILITY_API`, `REST_API`), ApiTypes, Python API, Plug-in Management and API Scenario. The header reads "Marvelous Designer API 0.1 documentation", © 2009–2025. — [MD API docs](https://developer.marvelousdesigner.com/)
- **Changelog coverage.** The changelog lists only MD 2024.2.189, 2025.0.157, 2025.0.243, 2025.0.281, 2025.0.285, 2025.1.107, 2025.1.157 and 2025.1.201. No 2026 release appears, and nothing is listed as removed or deprecated. — [MD changelog source](https://developer.marvelousdesigner.com/_sources/changelog.rst.txt)
- **Changelog items worth knowing:**
  - 2025.1.157 fixed `PatternAPI::MovePatternPoint` "where moving a single indexed point also moved other selected points".
  - 2025.1.107 added `.py` plugins in the Plug-in Manager, built-in pip on Windows, `AlignAvatarsAndGarmentToCenter`, `AddGraphicToPattern`, `Get/Set AvatarTextureMap`, Show/Hide options, `SetViewControlsDefaults`, `SetFormat3DBackground` and `SetTrimDisplaySettings`.
  - 2024.2.189 added `ImportAlembic`, `Get/Set map` and `GetFabricIndexForPattern`.
  - 2025.0.157 added `RunAnimationRecording(startFrame, endFrame)` and `MoveAnimationFrame(frame)`.
  - — [MD changelog](https://developer.marvelousdesigner.com/changelog.html)
- **Stale menu path.** The docs say the Python Editor is at "Main Menu → Script → Python". This build has no Script menu: the editor is at Main Menu → Plugins → Python Editor. — [MD python.rst](https://developer.marvelousdesigner.com/_sources/python.rst.txt); contradicted by repo measurement in `docs/md-conventions.md` (Execution environment)
- **Plug-in registration in the docs.** Plug-ins are `.py` files on Windows and macOS, added via Plugin tab → Plug-in Manager → + ADD. — [MD register.rst](https://developer.marvelousdesigner.com/_sources/register.rst.txt)
- **The only official samples** are four "API Scenario" snippets:
  - Animation: `import_api.ImportFile(...zprj)`, `SetStartAnimationFrame`, `SetEndAnimationFrame` and `SetAnimationRecording`.
  - Simulation: `ImportAvatar(avt, ApiTypes.ImportExportOption())`, `ImportZpac(zpac, op)` and `Simulate(100)`.
  - Pattern: three `CreatePatternWithPoints` shapes.
  - Substance: `ImportSubstanceFileAsFaceType(idx, sbsar, "Front")`, with faces "Front, Back, Side".
  - — [MD scenario.rst](https://developer.marvelousdesigner.com/_sources/scenario.rst.txt)
- **Modules in the docs.** The six documented module headings are `EXPORT_API`, `IMPORT_API`, `PATTERN_API`, `FABRIC_API`, `UTILITY_API` and `REST_API`. The importable names are lower-case (`import pattern_api`, `utility_api`, `export_api`, `import_api`, `fabric_api`), plus `ApiTypes`. — [MD scenario.rst](https://developer.marvelousdesigner.com/_sources/scenario.rst.txt); [MD API List](https://developer.marvelousdesigner.com/list.html)
- **`REST_API` is an outbound HTTP client, not a server.** Its only documented calls are:
  - `CallbackRestRequest(restRequest: CloApiRestRequest, callbackFunc)`
  - `CallbackRestMultiRequest(restRequests, callbackFunc, callbackFinished)`
  - `CallRESTPut(restReq, progressBarText) -> str`
  - ApiTypes carries `ApiRestRequest{header, method, payload, url, attachements, formData}` and `ApiRestResponse{header, buffer, responseCode}`.
  - — [MD API List](https://developer.marvelousdesigner.com/list.html); [MD ApiTypes](https://developer.marvelousdesigner.com/optiontype.html)
- **Availability.** The Python API arrived with the Linux edition, announced 1 October 2025. It is offered only on a new Enterprise-tier plan with floating licences (US$2,300/year), not as part of existing subscriptions. — [CG Channel, 2025-10-01](https://www.cgchannel.com/2025/10/marvelous-designer-is-now-available-for-linux/)
- **2026 coverage of the API.** CG Channel's coverage of 2026.0/2026.1 (3D Pencil, lacing, glTF, camera import/export, preserving original UVs on OBJ-as-garment import) says nothing about API changes. — [CG Channel 2026.1](https://www.cgchannel.com/?p=178281); [Digital Production 2026.0](https://digitalproduction.com/2026/04/15/marvelous-designer-2026-0-adds-3d-pencil-and-lacing/)
- **CLO's docs as a lineage reference.** CLO's API docs ("CLO API 0.1") carry the same `@ingroup EXPORT_API`/`PATTERN_API`/… docstrings and show Python and C++ side by side. Of the documented names, 822 are CLO's and 509 are MD's; every name in MD's docs also appears in CLO's. — [CLO API List](https://developer.clo3d.com/list.html) (counted from `_sources/list.rst.txt`)
- **CLO V10.0.3 (July 2026)** removed `ReDrape3DArrangement` ("which redraped the 3D arrangement of all patterns") and the pattern CPX APIs. — [CLO changelog](https://developer.clo3d.com/changelog.html)
- **CLO V11.0.0 (August 2026)** re-introduced `ReDrape3DArrangement` and the pattern CPX APIs, and added:
  - `MergeSewnPatterns`, plus `GetUnusedSinglePatterns`, `ActivateAllPatterns` and `GetPatternOnBodyDetails`;
  - geometry getters `GetPatternOutlinePoints`, `GetPatternLineInfo`, `GetIntersectionPoints` and others;
  - `AttachPatternToAvatarMeasure`/`RemovePatternFromAvatarMeasure`/`GetAvatarMeasurements`;
  - `GetMaterialProperties`/`GetMaterialPropertiesAsJson`/`SetMaterialProperties`.
  - — [CLO changelog](https://developer.clo3d.com/changelog.html)
- **None of the V11 additions is in MD 2026.0.315.** None of `MergeSewnPatterns`, `SetMaterialProperties`, `GetMaterialPropertiesAsJson` or `GetAvatarMeasurements` occurs anywhere in the MD binary's strings. — [MD exe strings](<C:/Program Files/Marvelous Designer Enterprise Network Offline/MarvelousDesigner_Enterprise_Offline.exe>)
- **Official GitHub samples.** Searches found no official MarvelousDesigner or CLO GitHub organisation with Python API samples. — web searches "MarvelousDesigner GitHub API sample plugin python" and "import pattern_api marvelous designer" (no repository results)

### Inferences
- MD 2026.0.315's binding set sits **between** the MD docs (2025.1) and CLO V11. It has calls the MD docs never mention (`ReDrape3DArrangement`, `GetPatternArchiveState`, `GetLinkedPatternLists`), and none of CLO V11's. CLO's docs are therefore the better reference for meaning, but every CLO-only name must be checked against `md_api` before being relied on.
- The "no `REST_API`" finding in `md-conventions.md` matters less than it looks: even where it exists, `REST_API` is a client for MD to call out, so it never offered a way to dial into MD.

### Gaps
- MD 2026.0/2026.1 release notes. `ChangeLog_Marvelousdesigner.html` in the install is a meta-refresh to `https://www.marvelousdesigner.com/move/5300`, which returned only a title. The MD support article on Python Script returned HTTP 403. No 2026 API changelog was found.
- Whether MD shipped any official example script. One community post reportedly mentions `C:\Program Files\Marvelous Designer Personal\example.py` (search-result summary only, page not fetched). No `example.py` exists in this Enterprise Offline install root.

## Q2. Local install and user folders: shipped docs, stubs, samples, logs and schemas

### Takeaway
The install ships **no Python docs, no `.pyi` stubs, no sample scripts and no help files**. The API is compiled into the exe and Python's stdlib comes from `python311.zip`. There is **no error log for UI messages**: the "pattern boundary is intersected" text is a UI string in the exe. The only log files MD writes are `api_plugin_log.txt` (plugin registration) and `authlog.txt` (licensing). Useful local material: arrangement-point and bounding-volume preset files (`.arr`/`.pan`), modular block arrangement JSON, MD's per-session temp/autosave folder, and headless-mode command-line strings that mention a log file.

### Cited Findings
- **The install root** holds the exe, Qt 6 and third-party DLLs, `python311.dll`, `python311.zip`, licence texts, `ChangeLog_Marvelousdesigner.html` (a 144-byte redirect to `marvelousdesigner.com/move/5300`) and `ErrorLogURL.txt`. That last file contains only "Marvelous Designer / https://api.clo3d.com/errorlog" and the same for CLO3D, i.e. a crash-report upload endpoint. There is no `.py`, `.pyi`, `.chm` or `.pdf` API documentation anywhere under the install. — [install root](<C:/Program Files/Marvelous Designer Enterprise Network Offline>) (recursive listing, 2026-10-11)
- **MD's Python environment.** MD's Python configuration lives at `C:/Users/Public/Documents/MarvelousDesigner/Configuration/python311` (Lib, DLLs, site-packages). MD's `sys.path` includes the user-writable `site-packages`. — `docs/md-conventions.md` (MD's own UI, from Python); folder listing 2026-10-11
- **`api_plugin_log.txt` is a plugin registration log.** `C:\Users\Public\Documents\MarvelousDesigner\New Assets\api_plugin_log.txt` contains, verbatim, with a literal unformatted date prefix:
  - "dd.MM.yyyy, 03:48:12 Skipped Python folder registration because no .py files were found: C:/Users/Public/Documents/MarvelousDesigner/External/ShimaSeiki"
  - "dd.MM.yyyy, 03:49:10 Failed to register Python script because the script path is invalid: C:/Program Files/Marvelous Designer Enterprise Network Offline/md-mcp"
  - It records registration only, not API errors.
  - — [api_plugin_log.txt](<C:/Users/Public/Documents/MarvelousDesigner/New Assets/api_plugin_log.txt>)
- **`authlog.txt`** in the same folder holds only `api.clo3d.com : HTTP 401` lines (offline licence). **`pluginSettings.json`** is `{"plugins": [], "version": 2}`. — [authlog.txt](<C:/Users/Public/Documents/MarvelousDesigner/New Assets/authlog.txt>); [pluginSettings.json](<C:/Users/Public/Documents/MarvelousDesigner/Plugins/pluginSettings.json>)
- **The meshing error is UI text, not a log line.** The exe stores "Pattern Boundary is intersected while meshing. Please decrease the particle distance.\nPattern Name:" in a UI message table beside:
  - "Overlapping Sewing Line"
  - "* SeamLines are not symmetric. Please check the length."
  - "Due to mesh issues, some pattern's mesh structure has been changed from rectangular mesh to triangular mesh."
  - "The following patterns can not be remeshed due to complex shapes"
  - "There are some abnormal seamlines in the file (marked as red)"
  - The same source string is translated in `Language/MD_zh.qm`.
  - — [MD exe strings](<C:/Program Files/Marvelous Designer Enterprise Network Offline/MarvelousDesigner_Enterprise_Offline.exe>) (offset ~121,689,636); [MD_zh.qm](<C:/Program Files/Marvelous Designer Enterprise Network Offline/Language/MD_zh.qm>)
- **Log-file names in the exe.** The exe names these files: `temp_log.txt`, `Headless/headless_log.txt`, `headless_log.txt`, `mvfile.log`, `authentication.log`, `authlog.txt`, `api_plugin_log.txt` and `GoZ_InstallLog.txt`. It also carries Breakpad crash strings ("MD-CRASH", "BreakpadQt: writeMinidump() successed.", "CrashDialogApp.exe"). Recursive searches of `%APPDATA%`, `%LOCALAPPDATA%`, `C:\Users\Public\Documents\MarvelousDesigner` and `%TEMP%` found no `.log` or `.dmp` from MD other than the two `.txt` files above. — [MD exe strings](<C:/Program Files/Marvelous Designer Enterprise Network Offline/MarvelousDesigner_Enterprise_Offline.exe>); filesystem search 2026-10-11
- **Headless-mode strings exist in this MD binary.**
  - Status strings: "[Python] Initialize Headless Mode ... ", "[Python] Running on Headless Mode", "[Python] Exit Headless mode."
  - Command-line option names with help texts: `headlessconfig`, `remote` ("Executed in a remote environment."), `headlesslog` ("Writing a log file."), "Headless API AccessKey.", `secretkey` ("Headless API SecretKey."), `deleteKey` ("Delete headless API key") and `performlog` ("Perform log analysis").
  - — [MD exe strings](<C:/Program Files/Marvelous Designer Enterprise Network Offline/MarvelousDesigner_Enterprise_Offline.exe>) (offsets ~122,305,880 and ~122,674,024)
- **MD support on headless.** According to a search-result summary of an MD support thread, support staff said "there is no headless Marvelous Designer session at the moment" (page not fetched directly). — [MD support community (search result)](https://support.marvelousdesigner.com/hc/ko/community/posts/48273620823705/comments/48273605186969)
- **MD's per-session temp/autosave folder.** MD writes scene parts to `%LOCALAPPDATA%\CLO Virtual Fashion\Marvelous Designer Enterprise Offline Annual\<pid>\<n>\`. On 2026-10-11 this held `autosave.zpac`, `autosave.xml` (an `<Environment Version="6.0">` XML with wind, lights and matrices), `autosave.smp`, `autosave_0.avt`, `.cpt`, `.cmp` and `.cmt`, a `clofiles.json` index of every unpacked file, and the same set for `VampireCloak.*` from the cape session. — [MD temp folder](<C:/Users/matty/AppData/Local/CLO Virtual Fashion/Marvelous Designer Enterprise Offline Annual/12184>)
- **Arrangement data ships as binary CLO-format presets** in `Preset/Avatar/Arrangement/`:
  - `Adult_V1/V2_Arrangement_Point.arr`, `Adult_V1/V2_Arrangement_Bounding_Volume.pan`, `Kid_*`, `Adult_Auto_Arrangement_Point.arr`, plus ML models `Auto_Arrangement_Model_2_1_0.eom` (139 MB) and `Raglan_all_512.eom`.
  - `.arr` files begin `ARR … CLO` and list each point with its parent volume, e.g. `Arm_Back_1_L → Arm_L`.
  - `.pan` files begin `PAN … CLO` and bind volumes to skeleton joints, e.g. `Arm_L → Left_Hand/Left_Arm`, `Body_L → Spine/Spine3`, `Shoulder_L → Left_Arm/Left_Shoulder`.
  - The exe has GUI strings "Load Arrangement Point", "Load Arrangement Bounding Volume", "Load Arrangement" and file filters `*.arr`/`*.pan`.
  - — [Arrangement presets](<C:/Program Files/Marvelous Designer Enterprise Network Offline/Preset/Avatar/Arrangement>); [MD exe strings](<C:/Program Files/Marvelous Designer Enterprise Network Offline/MarvelousDesigner_Enterprise_Offline.exe>)
- **Modular block placement is plain JSON.** `Preset/Modular/PatternerBlockArrangement.json` maps blocks to points, e.g. "Body Front" → `Body_Front_Center_3`, "Sleeve Left" → `Arm_Outside_2_L`, "Collar" → `Neck_R`, "Front Leg" → `Leg_Skirt_Front` with `PositionX: 0, PositionY: 50`. — [PatternerBlockArrangement.json](<C:/Program Files/Marvelous Designer Enterprise Network Offline/Preset/Modular/PatternerBlockArrangement.json>)
- **Other shipped JSON, none of it a pattern-export schema:**
  - `Preset/Parametric_Pattern/parametric_pattern_info_*.json` (Basic, Raglan, Set-In and others) and `landmarks_*_arr.eom`
  - `Preset/Project/MD/Default_MD.zprj` and `Default_MD_DisplaySettings.json`
  - `Preset/Simulation_Property/{Normal,Nonlinear,Complete}.smp`
  - `Preset/Fold3D/folding fabric.zfab`
  - `Style/Tooltip_Info.xml`
  - — [Preset folder](<C:/Program Files/Marvelous Designer Enterprise Network Offline/Preset>)

### Inferences
- **There is no file to tail for MD's error messages.** UI messages such as the meshing warning are shown in the GUI and are not persisted anywhere this research found. P0.2 ("surface MD's own errors") cannot be met by reading a log in normal GUI mode.
- `--headlesslog` may write a log in headless mode, but MD support says headless is not offered for MD, and nothing about it was tested.
- **The `.arr`/`.pan` formats are a lead for P3** (an avatar whose volumes ignore the tail): the bounding volumes are bone-bound shapes. But the API offers no loader for them (see Q4), only the GUI "Load Arrangement Bounding Volume" dialog.
- The `<pid>` temp folder with `autosave.zpac` is a passive, MD-maintained recovery source. A checkpoint tool could fall back on it, but it is not a substitute for an explicit `ExportZPrj`.

### Gaps
- Whether MD writes anything to a log on a meshing failure when started with any command-line flag. Not tested: launching MD was out of scope.
- The `.arr`/`.pan` binary layouts beyond the readable names. The volume dimensions and offsets appear as floats after each name, but were not decoded.

## Q3. What the build exposes (live `md_api` unavailable; binary used instead)

### Takeaway
The live index could not be read because MD was closed. The exe's binding strings agree with the repo's six-module picture and add three things. First, two further pybind11 module names, `rest_api` and `sample_api`. Second, a sizeable set of CLO API members that are compiled into MD but **not bound** to Python. Third, several bound calls that `notes.py`/`md-conventions.md` never cover: `ReDrape3DArrangement`, `GetBoundingBoxOfPattern`, `GetAllStitchProperty`, `GetLinkedPatternLists`, `GetGraphicStylePosition`, `CombineZfab`, `ExportFabric`/`.jfab`, `GetCustomViewInformation` and `SetPatternArchiveState`.

### Cited Findings
- **Module-name strings.** The binding block carries the strings `ApiTypes`, `export_api`, `fabric_api`, `import_api`, `pattern_api`, **`rest_api`**, **`sample_api`** and `utility_api`. `sample_api` has an authorisation gate: "Unauthorized: Sample API access is restricted.", "[SampleAPI] Authorization failed: Empty headquarters ID". Its bound functions are `TestExportSampleAPI`, `TestImportSampleAPI`, `TestFabricSampleAPI`, `TestUtilitySampleAPI` and `TestRestSampleAPI`. — [MD exe strings](<C:/Program Files/Marvelous Designer Enterprise Network Offline/MarvelousDesigner_Enterprise_Offline.exe>) (offsets 121,892,256–122,134,263)
- **The repo's measured module set.** The repo measured six modules: `pattern_api` 147, `utility_api` 278, `export_api` 37, `import_api` 22, `fabric_api` 112 and `ApiTypes` 92, making 688. It also measured that "importing `REST_API` raises ModuleNotFoundError". The listener's `SCRIPT_MODULES` and bridge `MD_MODULES` enumerate only those six. — `docs/md-conventions.md` (end); `plugin/md_mcp_listener.py:232`; `src/md_mcp/bridge.py:251`
- **`rest_api` binding.** It binds `CallbackRestRequest`, `CallbackRestMultiRequest` and `CallRESTPut`. The C++ class also has `CallRESTGet`, `CallRESTPost`, `CallRESTPost2`, `CallRESTPostWithMultipartFormData` and `GetByteArrayFromStream`, none of them bound. — [MD exe strings](<C:/Program Files/Marvelous Designer Enterprise Network Offline/MarvelousDesigner_Enterprise_Offline.exe>)
- **C++ members compiled in but with no Python binding.** These have neither a `[Python]` wrapper nor a bare name in the binding block:
  - `FabricAPI::ChangeFabricWithJson(uint, string&)` and `(uint, uint, string&)`, plus `W` twins; `GetFabricWidth`/`SetFabricWidth`; `ApplyXtexFile`; `ImportGLTFAsFabric`
  - `PatternAPI::GetNestingFixedPatternPiecePos(int) -> pair<int,int>`, `SetNestingFixedPatternPiecePos(int,int,int)`, `AddPatternAnnotation`/`EditPatternAnnotation`/`GetPatternAnnotation`, and the grading-size getters
  - `UtilityAPI::GetStrainMapStatus`/`SetStrainMapStatus`, `GetStressMapStatus`/`SetStressMapStatus`, `StartNesting`/`StopNesting`, `RemovePlugInFromList`, `RefreshPlugIns`, `UsePlugInFromList`, `TerminatePlugInFromList`, `AddPlugInFromFile`, and the colourway family (`GetColorwayCount`, `SetCurrentColorwayIndex`, …)
  - `ExportAPI::ExportStdViewImage(int, string&, int, int, int)`, `ExportMultiViewImages`, `ExportMaskSnapshot3D`, `ExportRenderingImage`, `ExportGLTF`/`ExportGLB`, `ExportDXF`, `ExportTechPack`, `ExportGarmentInformation`
  - `ImportAPI::ImportDXF`, `ImportGLTF`, `ImportCPT`, `ImportVLP`/`VMP`/`VRP`, `ImportGarmentInformation`
  - — [MD exe strings](<C:/Program Files/Marvelous Designer Enterprise Network Offline/MarvelousDesigner_Enterprise_Offline.exe>)
- **`ChangeFabricWithJson` in CLO's docs.** CLO documents it as "Overwrite all the properties of a fabric with json file(.jfab)". A CLO forum developer used ExportFabric → edit `.jfab` → `ChangeFabricWithJson` to change a fabric's colour from the C++ SDK. — [CLO API List](https://developer.clo3d.com/list.html); [CLO forum post](https://support.clo3d.com/hc/zh-cn/community/posts/6876906333977-Clo3D-automation-using-API)
- **Bound in this build but absent from MD's published docs:**
  - `ReDrape3DArrangement`, with C++ signature `void UtilityAPI::ReDrape3DArrangement(const ImportExportOption&)`
  - `GetPatternArchiveState(int) -> bool` / `SetPatternArchiveState(int, bool)`
  - `GetLinkedPatternLists() -> string`
  - `GetPatternsAttachedToAvatarMeasures`
  - `FitPatternUVToUDIM`, `GetGradingSizeQuantityMix` and others
  - Documented in CLO's docs: `ReDrape3DArrangement(_options: ImportExportOption) -> None`, "Redrape all patterns 3D Arrangement"; `GetLinkedPatternLists`, "Get all linked pattern group lists (e.g., Layer Clone)" returning JSON; `GetPatternArchiveState`, "true if the pattern is archived".
  - — [MD exe strings](<C:/Program Files/Marvelous Designer Enterprise Network Offline/MarvelousDesigner_Enterprise_Offline.exe>); [CLO API List](https://developer.clo3d.com/list.html)
- **No error, log or undo calls.** No bound identifier contains Error/Log/Message/Warning/Undo/Redo/Last/History, other than `DisplayMessageBox`/`DisplayMessageBoxW` (output only) and struct names such as `StatusData`. — [MD exe strings](<C:/Program Files/Marvelous Designer Enterprise Network Offline/MarvelousDesigner_Enterprise_Offline.exe>)

### Inferences
- `import rest_api` (lower case) was apparently never tried. The repo tried `REST_API`. Whether `rest_api` and `sample_api` import inside MD is a five-second `md_api`/`run_md_python` check. It matters only for completeness of the index, since neither offers a way into MD.
- `ChangeFabricWithJson` would have been the fabric-physics setter, but it is not bound in 2026.0.315. The bound JSON fabric route is `ExportFabric` → `AddFabric(.jfab)` (Q4, materials).

### Gaps
- Exact Python argument names and default values for the calls above. The FUNCSIG strings give C++ types only; pybind11 argument names did not appear as recoverable strings. `md_api` against a running MD is needed.
- A check that the "bound" heuristic holds for every name. It was validated on DXF only.

## Q4. For each roadmap need: is there any route in MD's programmatic surface? (calls, signatures, modules)

### Takeaway
- **3D placement:** there is still **no per-piece 3D transform call**. The only new routes are scene-level:
  - `ReDrape3DArrangement(ImportExportOption)`;
  - translation fields on `ImportZPRJOption`/`ImportExportOption` (garment-wide offsets at import);
  - an arity-correct re-test of `SetArrangementPosition(i, posX, posY, offset)`, which is four ints.
- **2D board:** fully addressable with `GetPatternPiecePos`, `SetPatternPiecePos`, `SetPatternPieceMove` and `GetBoundingBoxOfPattern`.
- **Seams:** readable; **removal has no call**. CLO's `MergeSewnPatterns` is absent; JSON round-trip and `GetAllStitchProperty` are the leads.
- **Geometry:** `GetGraphicStylePosition(g, False)` is an unmeasured 3D probe besides OBJ export.
- **Pins:** count and remove only. **Tack:** none. **Freeze:** write-only.
- **Errors:** no route.
- **Materials:** `.jfab` via `ExportFabric`/`AddFabric` and `CombineZfab` are the physics routes; two-sided colour has explicit face-index calls (`SetUseSameColorAsFront`).
- **Camera:** the docs give the full `SetCamViewPoint` 0–9 table, and camera matrices are readable.

### Cited Findings

#### 3D placement and transform [Arrangement, Position, Transform, Translate, Rotate, Move, 3D, Gizmo, Fold, Reset, Superimpose]
- **The arrangement calls (`pattern_api`):**
  - `SetArrangement(_patternIndex: int, _arrangementIndex: int) -> None`
  - `SetArrangementOrientation(_patternIndex: int, _orientation: int) -> None`
  - `SetArrangementShapeStyle(_patternIndex: int, shapeStyle: str)`, "Flat" or "Curved", plus `W`
  - `SetArrangementPosition(_patternIndex: int, _positionX: int, _positionY: int, _offset: int) -> None`, documented as "Set Arrangement Position using positionX, positionY, offset". The C++ signature is `void PatternAPI::SetArrangementPosition(int,int,int,int)`.
  - — [MD API List source](https://developer.marvelousdesigner.com/_sources/list.rst.txt); [MD exe strings](<C:/Program Files/Marvelous Designer Enterprise Network Offline/MarvelousDesigner_Enterprise_Offline.exe>)
- **Repo measurements on these calls.** `SetArrangementPosition` "does nothing (swept over five values … moved 7.7mm against an expected 377)". `SetArrangementOrientation` moves pieces, in degrees, default 180. Pattern JSON `ArrangementPointDataMap` offsets are discarded on import. — `src/md_mcp/notes.py`; `docs/md-conventions.md` (Arrangement)
- **`ReDrape3DArrangement(_options: ImportExportOption) -> None`** (`utility_api`, bound): "Redrape all patterns 3D Arrangement". CLO removed it in V10.0.3 and restored it in V11.0.0. CLO's GUI "Re-drape 3D Arrangement" re-drapes to the avatar's size and "is supported only with CLO avatars as well as custom avatars that have a Fitting Suit added". — [CLO API List](https://developer.clo3d.com/list.html); [CLO changelog](https://developer.clo3d.com/changelog.html); [CLO support: Re-drape 3D Arrangement](https://support.clo3d.com/hc/en-us/articles/360034333873-Re-drape-3D-Arrangement-ver-5-1); [MD exe strings](<C:/Program Files/Marvelous Designer Enterprise Network Offline/MarvelousDesigner_Enterprise_Offline.exe>)
- **Scene-level reset and alignment (`utility_api`):**
  - `ResetClothArrangement() -> None`, "Restore the shape of cloth to when the cloth was loaded"
  - `CopyFromFirstClothCache() -> None`, "Copy the first cloth cache to current frame"
  - `AlignAvatarsAndGarmentToCenter() -> None`
  - `ResetUVTo2DArrangement() -> None`, which acts on UVs, not placement
  - — [MD API List source](https://developer.marvelousdesigner.com/_sources/list.rst.txt)
- **Import-time translation.** These option structs carry translation fields:
  - `ImportZPRJOption`: `bAppend`, `bLoadGarment`, `bLoadAvatar`, `translationValueX/Y/Z`, `bLoadSceneAndProps`, `bLoadRenderProperties`, `bLoadCustomView`
  - `ImportExportOption` (used by `ImportZpac`, `ImportOBJ`, `ImportFile`): `bMoveGarment`, `bAutoTranslate`, `bAdd`, `translationValueX/Y/Z`, `bAddArrangementPoints`, `bAutoCreateFittingSuit`, `ImportObjectType`, `bTrace2DPatternsUVMap`
  - `ImportAlembicOption`: `boolMoveGarmentToStartPosition`, `boolAddArrangementPoints`, `translationValueX/Y/Z`
  - — [MD ApiTypes](https://developer.marvelousdesigner.com/optiontype.html)
- **`ImportTrim(_filePath: str, _bTranslateToOrigin: bool) -> None`** — "true: move trim to the pos(0,0) false: move trim to original pos". — [MD API List source](https://developer.marvelousdesigner.com/_sources/list.rst.txt)
- **`UnfoldPatternPiece(_patternIndex, _lineIndex, _bHalfSymmetry) -> bool`** is a 2D unfold about a line, not "Fold Arrangement". **`FlipPatternPiece(int, bool bHorizontally, bool bEach)`** is 2D only. — [MD API List source](https://developer.marvelousdesigner.com/_sources/list.rst.txt); [MD exe strings](<C:/Program Files/Marvelous Designer Enterprise Network Offline/MarvelousDesigner_Enterprise_Offline.exe>)
- **No call named for 3D transforms.** No bound function name contains Transform (apart from the texture-only `TransformAOPOnFabric`), Translate, Rotate, Gizmo, Superimpose, Tack or "Fold" (apart from `UnfoldPatternPiece`). The strings matching "Gizmo", "Tack" and "Fold" are UI colour keys (`TackLineColor`, `Ghost_Fold_Color`, …). — [MD exe strings](<C:/Program Files/Marvelous Designer Enterprise Network Offline/MarvelousDesigner_Enterprise_Offline.exe>)
- **CLO V11 placement readback, absent from MD.** CLO V11 adds `GetPatternOnBodyDetails() -> str`, which returns each pattern's `arrangementPosition`, `semanticLabel` and `bodyRegion`. It documents the point naming convention as `{Region}_{Facing}_{Zone}_{Side}`, with Zone 1 closest to the torso, 2 mid and 3 distal. This call is not in the MD binary. — [CLO API List](https://developer.clo3d.com/list.html)

#### Arrangement points and avatar arrangement data [Arrangement, Avatar, Point]
- **Arrangement readback (`pattern_api`):**
  - `GetArrangementList() -> list[map[str,str]]`, giving "Arrangement Name, Type, Offset x,y,z, Orientation"
  - `GetArrangementOfPattern()`, `(int)` and `(str)`
  - Bound keys include `ArrangementIndex`, `ArrangementName`, `ArrangementOffsetX/Y/Z`, `ArrangementOrientation` and `ArrangementType`.
  - — [MD API List source](https://developer.marvelousdesigner.com/_sources/list.rst.txt); [MD exe strings](<C:/Program Files/Marvelous Designer Enterprise Network Offline/MarvelousDesigner_Enterprise_Offline.exe>)
- **No call loads, edits, adds or removes arrangement points or bounding volumes.** The GUI-only strings "Load Arrangement Point" and "Load Arrangement Bounding Volume" are present, with no bound counterpart. Indirect routes are `ImportExportOption.bAddArrangementPoints`, `bAutoCreateFittingSuit` and `ImportAlembicOption.boolAddArrangementPoints`. — [MD exe strings](<C:/Program Files/Marvelous Designer Enterprise Network Offline/MarvelousDesigner_Enterprise_Offline.exe>); [MD ApiTypes](https://developer.marvelousdesigner.com/optiontype.html)
- **The repo's measurement of `bAddArrangementPoints`.** It "does not add any" for the shipped `Genesis8_Female.avt`, while the `Nardo` avatar imported from `.obj` had 109 points. — `docs/md-conventions.md` (Arrangement)
- **Avatar property calls (`utility_api`):**
  - `SetAvatarProperties(_avatarIndex: int, avatarPropertyMap: map[str,str])` and `GetAvatarProperties(_avatarIndex) -> map[str,str]`. Documented keys are `KineticFriction`, `SkinOffsetMM`, `StaticFriction`, `DivideMesh` and `SoftBodySimulation`.
  - `SetAvatarSoftBodyStiffness(i, 5000–10000; 0 disables)`
  - `SetShowHideAvatar(bool[, i])`
  - `SetAvatarActivation(name, bool) -> bool`
  - `DeleteAvatar(list[int]) -> bool`
  - — [MD API List source](https://developer.marvelousdesigner.com/_sources/list.rst.txt)

#### Seam readback and seam removal [Seam, Sewing, Delete, Remove]
- **Sewing calls (`pattern_api`):**
  - `AddSeamlinePairGroup` has three overloads: (A, lineA, B, lineB, dirA, dirB); (A, lineA, B, childB, lineB, dirA, dirB); and (A, childA, lineA, B, childB, lineB, dirA, dirB). All are `-> bool`. The docstring "@return name of sewing group" is wrong per the repo.
  - `GetSeamlinePairGroupCount() -> int`
  - `GetSeamlinePairGroupListInPattern(_patternIndex) -> list[int]`
  - `GetSeamlinePairGroupName(i) -> str` (+W)
  - `GetSeamlinePairGroupIndexFromName(name) -> int` (+W)
  - `AddSeamlineTopstitch(_seamlinePairGroupIndex, _startRatio, _endRatio, _topStichStyleIndex) -> bool`
  - `SymmetryPatternPiece(_patternIndex, _bWithSewing) -> None`
  - — [MD API List source](https://developer.marvelousdesigner.com/_sources/list.rst.txt)
- **`GetAllStitchProperty() -> str`** (`pattern_api`): "Gets property information for all the stitches that exist in the scene. It will be returned as a JSON string." It is bound, with no note in `notes.py`, so its content is unmeasured. — [MD API List source](https://developer.marvelousdesigner.com/_sources/list.rst.txt)
- **Linked patterns:**
  - `GetLinkedPatternIndex(patternIndex) -> list[int]`, "the pattern index information connected to the pattern"
  - `GetLinkedPatternLists() -> str`, JSON of linked groups "(e.g., Layer Clone)", bound in MD and documented by CLO
  - — [MD API List source](https://developer.marvelousdesigner.com/_sources/list.rst.txt); [CLO API List](https://developer.clo3d.com/list.html)
- **No call removes a seam in MD 2026.0.315.** The bound deletes are `DeletePatternPiece`, `DeleteLine(pattern, line)`, `DeletePoint`, `DeleteFabric`, `DeleteAvatar` and `RemovePin`/`RemoveAllPins`. CLO V11's `MergeSewnPatterns(A, B) -> bool` ("the seam between the two patterns is removed and the outlines are joined") is not in the MD binary. — [MD exe strings](<C:/Program Files/Marvelous Designer Enterprise Network Offline/MarvelousDesigner_Enterprise_Offline.exe>); [CLO API List](https://developer.clo3d.com/list.html)
- **The JSON route.** `ExportPatternJSON(_path) -> bool` / `ImportPatternJSON(_path) -> bool`. The repo measured a clean round trip (names, seam groups, a 360 mm edge exact), with the fabric duplicated and arrangement offsets lost. This is the only candidate route to remove a seam: delete it from `SeamLinePairGroupList`, then re-import. — `docs/md-conventions.md` (Adding a sewing pattern)

#### Layer and clone behaviour and options [Layer, Clone, Lining]
- **Layer-clone and copy calls (`pattern_api`):**
  - `LayerClonePatternPiecePos(_patternIndex, _x, _y, _bUnder) -> None` and `LayerClonePatternPieceMove(_patternIndex, _offsetX, _offsetY, _bUnder) -> None`. `_bUnder` is "the value to 3D phase pattern position", i.e. whether the clone sits under or over the original in 3D. No option suppresses the automatic seams.
  - `CopyPatternPiecePos`/`CopyPatternPieceMove(int, float, float) -> int`
  - `InstancePatternPiece`
  - `SetPatternLayer`/`GetPatternLayer`
  - — [MD API List source](https://developer.marvelousdesigner.com/_sources/list.rst.txt); [MD exe strings](<C:/Program Files/Marvelous Designer Enterprise Network Offline/MarvelousDesigner_Enterprise_Offline.exe>)
- **`SetSimulationLayerBasedCollisionDetection(_bUseLayer: bool)`** (`utility_api`) switches layer-based collision on or off. — [MD API List source](https://developer.marvelousdesigner.com/_sources/list.rst.txt)
- **The repo's layer-clone measurement.** `LayerClonePatternPieceMove(i, dx, dy, True)` creates one seam group per outline line, each with `bIsTurned: true` and `FoldData.iAngle: 180`. — `docs/sessions/2026-10-11-vampire-cape.md` (Measured this session)

#### Geometry readback [Cloth, Position, Vertex, Mesh, Bound, Get]
- **`GetClothPositions(positions: list[float]) -> None`** (`utility_api`) is documented as "Each three array has x, y, z position of each vertex". Its C++ signature is `void UtilityAPI::GetClothPositions(std::vector<float>&)`. The repo measured that it cannot fill a Python list. — [MD API List source](https://developer.marvelousdesigner.com/_sources/list.rst.txt); [MD exe strings](<C:/Program Files/Marvelous Designer Enterprise Network Offline/MarvelousDesigner_Enterprise_Offline.exe>); `src/md_mcp/notes.py`
- **Other per-piece readbacks (`pattern_api`):**
  - `GetMeshCountByType(_patternIndex | _patternName) -> map[str,str]`, the face and vertex count for the mesh type set by `SetMeshType(i, "Triangle"|"Quad")`
  - `GetPatternPieceArea`
  - `GetLineLength`
  - `GetPatternIndexFrom3DView(_scenePosX: float, _scenePosY: float) -> int`
  - — [MD API List source](https://developer.marvelousdesigner.com/_sources/list.rst.txt)
- **`GetGraphicStylePosition(_graphicIndex: int, _is2D: bool) -> map[int, list[list[float]]]`** (`utility_api`, bound). It returns graphic positions per pattern index; per CLO, "_is2D … if False, returns the value based on the 3D window". `AddGraphicToPattern`/`AddGraphicStyleToPattern` place graphics. — [MD API List](https://developer.marvelousdesigner.com/list.html); [CLO API List](https://developer.clo3d.com/list.html)
- **File exports** are the measured 3D route: `ExportOBJ(path, ImportExportOption)` (measured), `ExportAlembic(path, options)`, `ExportFBX(path, options)`, `ExportUSD(path, options, usdOptions)` and `ExportZPac`. — [MD API List](https://developer.marvelousdesigner.com/list.html); `docs/md-conventions.md`
- **CLO V11 geometry getters, all absent from the MD binary:** `GetPatternOutlinePoints(i) -> list[list[float]]` (mm, top-left origin, y down), `GetPatternLineInfo(i) -> str` (lines with length, orientation, location, start/end/mid and the bounding box) and `GetIntersectionPoints`. — [CLO API List](https://developer.clo3d.com/list.html)

#### 2D board position and bounding box [Pattern, Bound, Position, 2D]
- **Position calls (`pattern_api`):**
  - `GetPatternPiecePos(_patternIndex) -> list[float]`, the "world position (x, y)"; the repo measured this as the bounding-box centre
  - `SetPatternPiecePos(_patternIndex, _x: float, _y: float) -> None`, "Set Pattern piece's world position to (_x, _y)"
  - `SetPatternPieceMove(_patternIndex, _offsetX: float, _offsetY: float) -> None`, "(CurrentPosition.x + _offsetX, CurrentPosition.y + _offsetY)"
  - `GetPatternIndexFrom2DView(x, y) -> int`
  - — [MD API List source](https://developer.marvelousdesigner.com/_sources/list.rst.txt); `docs/md-conventions.md` (Units and coordinates)
- **`GetBoundingBoxOfPattern`** has three forms:
  - `() -> list[map[str,str]]`, "BoundingBox Size width, height, pattern index, pattern count"
  - `(_patternIndex) -> map[str,str]`
  - `(_patternName) -> map[str,str]`
  - These give **size only, not position**.
  - — [MD API List source](https://developer.marvelousdesigner.com/_sources/list.rst.txt)
- **Nesting calls exist in C++ but are not bound:** `GetNestingFixedPatternPiecePos(int) -> pair<int,int>`, `SetNestingFixedPatternPiecePos(int,int,int)`, `StartNesting` and `GetNestingBufferSpacing`. — [MD exe strings](<C:/Program Files/Marvelous Designer Enterprise Network Offline/MarvelousDesigner_Enterprise_Offline.exe>)
- **`ExportSnapshot2D(_filePath, SnapShotMode: int) -> list[str]`** is undocumented in MD. CLO gives "0: Captures the 2D Window in Simulation Mode, 1: … Print Layout Mode", with an empty vector on error. The repo saw `[]` for mode 0. — [CLO API List](https://developer.clo3d.com/list.html); `docs/sessions/2026-10-11-vampire-cape.md`

#### Pins, tack and freeze [Pin, Tack, Freeze]
- **Pin calls:** `GetPinListSize() -> int`, "Count the number of pins from patterns"; `RemovePin() -> bool`, "Removes Pin one by one"; `RemoveAllPins() -> bool`; and `AddPinsForFabricValidation(size1..4: float) -> bool`, "Adjust lengths and adjust pin locations", used by the fabric kit. **There is no add-pin-at-point call and no tack call.** — [MD API List source](https://developer.marvelousdesigner.com/_sources/list.rst.txt)
- **Piece-state setters (`pattern_api`):**
  - `SetPatternFreeze(i, bool)`, with no getter
  - `SetPatternLock(i, bool)`
  - `SetPatternHide3D(i, bool)`
  - `SetPatternStrengthen(i, bool)`
  - `SetPatternArchiveState(i, bool)` / `GetPatternArchiveState(i) -> bool`, bound, documented by CLO
  - C++ signatures are `void PatternAPI::SetPatternFreeze(int,bool)` and so on.
  - — [MD API List source](https://developer.marvelousdesigner.com/_sources/list.rst.txt); [CLO API List](https://developer.clo3d.com/list.html); [MD exe strings](<C:/Program Files/Marvelous Designer Enterprise Network Offline/MarvelousDesigner_Enterprise_Offline.exe>)
- **`GetPatternsAttachedToAvatarMeasures() -> list[map[str,str]]`** (bound) lists patterns "currently attached (pinned/tacked) to avatar measures". **`SetGarmentDisplayProperties(_option, _show)`**: option 7 is "Pins", and 2 is "Seamlines". — [CLO API List](https://developer.clo3d.com/list.html); [MD API List source](https://developer.marvelousdesigner.com/_sources/list.rst.txt)

#### Error and message retrieval [Error, Message, Log, Warning, Last]
- **No getter for MD's messages exists:** no `GetLastError`, no message or log reader. The only message call is `DisplayMessageBox(str)`/`DisplayMessageBoxW`, which is modal and shows a message rather than reading one. — [MD exe strings](<C:/Program Files/Marvelous Designer Enterprise Network Offline/MarvelousDesigner_Enterprise_Offline.exe>); [MD API List](https://developer.marvelousdesigner.com/list.html)
- **Documented "errors" are return values.** For example, "If an error occurs, return empty string" for `ExportZPac`/`ExportZPrj`, "return infoMap" for `GetBoundingBoxOfPattern`/`GetMeshCountByType`, and an empty vector for `GetPatternPiecePos`. — [MD API List source](https://developer.marvelousdesigner.com/_sources/list.rst.txt)
- **Possible indirect signals:**
  - `GetMeshCountByType` before and after an edit (the meshing warning sits next to "mesh structure has been changed from rectangular mesh to triangular mesh")
  - `CheckZPRJForUnsavedChanges() -> bool`
  - `GetParticleDistanceOfPattern`, since the warning says "decrease the particle distance"
  - — [MD API List source](https://developer.marvelousdesigner.com/_sources/list.rst.txt); [MD exe strings](<C:/Program Files/Marvelous Designer Enterprise Network Offline/MarvelousDesigner_Enterprise_Offline.exe>)

#### Checkpoint, save and import [Save, Zprj, Import, Project]
- **Save and load calls:**
  - `ExportZPrj()`, `(path)` and `(path, _bCreateThumbnail)` -> str, plus `ExportZPrjW`
  - `ImportZprj(path, loadOption: ImportZPRJOption) -> bool`, documented as "Load zprj File without the dialog"
  - `ExportZPac(path)` / `ImportZpac(path, ImportExportOption) -> bool`
  - `ImportFile(path)` / `(path, options)`, "This function will show up the dialog per the file type", i.e. potentially modal
  - — [MD API List source](https://developer.marvelousdesigner.com/_sources/list.rst.txt)
- **Project state calls (`utility_api`):** `CheckZPRJForUnsavedChanges() -> bool`, `GetProjectFilePath() -> str` ("NULL" if none), `GetProjectName()`, `NewProject()` and `SaveCLOFileThumbnail(filePath, outputPath, thumbnailIndex) -> bool`. — [MD API List](https://developer.marvelousdesigner.com/list.html)
- **The repo's checkpoint measurement.** `ExportZPrj` → `ImportZprj` preserved names, fabric indices, layers, 133 seam groups and the fabric list on a 56-piece garment. — `docs/md-conventions.md`

#### Fabric physics and two-sided colour [Fabric, Property, Physical, Side, Color]
- **The JSON fabric route is bound and documented:**
  - `AddFabric(inputFilePath) -> int`: "'zfab'(MD file) path or **'jfab'(json) path**"
  - `ExportFabric(filePath[, index]) -> str`: "Export the selected fabric to the file(**.jfab** or .zfab)"
  - `ReplaceFabric(_fabricIndex, _inputFilePath) -> bool`, for a `.zfab`
  - `GetAPIMetaDataFromFile(path)` for `.zfab`/`.jfab`
  - — [MD API List source](https://developer.marvelousdesigner.com/_sources/list.rst.txt)
- **`CombineZfab(_filePath, _baseZfabPath, _targetZfabPath) -> None`**: "_baseZfabPath: the Path to the zfab file on which the physical properties will be based; _targetZfabPath: … on which the image will be based". **`CreateZfabFromTextures(...) -> bool`**. — [MD API List source](https://developer.marvelousdesigner.com/_sources/list.rst.txt)
- **Two-sided colour (`fabric_api`):**
  - `SetFabricPBRMaterialBaseColor(fabricIndex, materialFace, r, g, b, a) -> bool` and `(fabricIndex, materialFace, _colorName: str)`, with faces "Front", "Back", "Side"
  - `GetFabricPBRMaterialBaseColor(fabricIndex, materialFace) -> tuple`
  - `SetUseSameColorAsFront(fabricIndex, materialFace (1 Back, 2 Side), _useSameColor) -> None` / `GetUseSameColorAsFront`
  - `SetUseSameMaterialAsFront` / `GetUseSameMaterialAsFront`
  - `ImportSubstanceFileAsFaceType(index, path, "Front"|"Back"|"Side")`
  - The C++ overloads also include colourway-indexed versions: `(uint, uint, uint, float×4)`, `(uint, uint, int, bool)`.
  - — [MD API List source](https://developer.marvelousdesigner.com/_sources/list.rst.txt); [MD exe strings](<C:/Program Files/Marvelous Designer Enterprise Network Offline/MarvelousDesigner_Enterprise_Offline.exe>)
- **The repo on this call.** It records "`SetFabricPBRMaterialBaseColor(_fabric, _colorway, r, g, b, a)`" in one place and "a side index of 1 ended the MD process" in another. The docs name the second argument `materialFace`, not colourway. — `docs/md-conventions.md` (Fabric and materials); contradicted on the argument name by [MD API List source](https://developer.marvelousdesigner.com/_sources/list.rst.txt)
- **CLO V11 material setters, absent from MD.** CLO V11's `SetMaterialProperties` / `GetMaterialPropertiesAsJson` (`MaterialPropertyOptions` with `physical_type`, `physical_preset`, `physical_thickness_mm` and front/back/side stacks) is not in MD 2026.0.315. — [CLO optiontype source](https://developer.clo3d.com/_sources/optiontype.rst.txt); [MD exe strings](<C:/Program Files/Marvelous Designer Enterprise Network Offline/MarvelousDesigner_Enterprise_Offline.exe>)

#### Camera and snapshot [Cam, Snapshot, View]
- **`SetCamViewPoint(_viewPointIndex)`**, documented: "0: bottom, 1: 3/4 right, 2: front, 3: 3/4 left, 4: right, 5: top, 6: left, 7: focus zoom, 8: back, 9: zoom extents all". **`SetViewPoint(_index)`** is "View Point in render property window (0: Current View, 1: Custom View)". — [MD API List source](https://developer.marvelousdesigner.com/_sources/list.rst.txt)
- **The docs settle a repo contradiction.** The docs' table matches the cape session's by-eye reading (7 ≈ 6, 8 and 9 back). It contradicts `md-conventions.md`/`notes.py`, which say "0..7 give eight distinct views … round the back". — `docs/sessions/2026-10-11-vampire-cape.md`; `src/md_mcp/notes.py`
- **Camera readback and control (`utility_api`):**
  - `GetCustomViewInformation() -> str`: JSON `customViewList[{cameraMatrix: 12 floats ("row vector 0,1,2 of local to world camera matrix"), fov}]`
  - `GenerateZcmrFrom3DWindow(_filePath, _addToList) -> list[str]`
  - `LoadCustomViewIn3DWindow(_index)`
  - `SetViewControlDefaults(_xAngle, _yPosition, _cameraDistance)`
  - `SetZoomView()`
  - `ExportZCMR(folder)`
  - — [MD API List source](https://developer.marvelousdesigner.com/_sources/list.rst.txt)
- **Measured snapshot behaviour.** `ExportSnapshot3D(path) -> list[list[str]]` works. `ExportTurntableImages` and `ExportCustomViewSnapshot` return `[]`, and `ExportStdViewImage`/`ExportMultiViewImages` are compiled but not bound. — `docs/md-conventions.md`; [MD exe strings](<C:/Program Files/Marvelous Designer Enterprise Network Offline/MarvelousDesigner_Enterprise_Offline.exe>)

### Inferences
- **P3 placement.** The untested candidates, in rough order of promise:
  1. `SetArrangementPosition` re-tested as a 4-int call. Its `positionX/positionY` are probably the GUI's point-on-volume Position X/Y and `offset` the distance from the volume, not millimetres in world space. It may also only apply on the next `SetArrangement`/`ReDrape3DArrangement`.
  2. `ReDrape3DArrangement(ImportExportOption())` after arranging. Per CLO it needs a CLO avatar or a fitting suit, and `bAutoCreateFittingSuit` exists on the option.
  3. Garment-wide translation via `ImportZpac`/`ImportZprj(bAppend=True, translationValue*)`. This can only move a whole garment, so per-piece use would need a piece isolated in its own `.zpac`.
  None gives a per-piece rotation or translation call.
- **P1.4 2D board.** Board state is computable: centre from `GetPatternPiecePos` plus size from `GetBoundingBoxOfPattern(i)` gives each piece's 2D rectangle, which allows overlap checks. Layout is writable with `SetPatternPiecePos`/`SetPatternPieceMove`.
- **P1.1 and P2.4 seams.** `GetAllStitchProperty()` and `GetLinkedPatternLists()` are the two unmeasured calls most likely to give a structured seam or layer-clone ledger without decoding pattern JSON. Seam removal has no direct call; JSON edit and re-import is the only route, and CLO only added `MergeSewnPatterns` in August 2026.
- **P1.2 geometry.** Adding a small graphic to each piece and reading `GetGraphicStylePosition(g, False)` could give a cheap 3D anchor per piece without an OBJ export. Unmeasured.
- **P4 materials.**
  - Physics may be settable by exporting a preset to `.jfab` (`ExportFabric`), editing it, and loading it with `AddFabric`.
  - `CombineZfab` mixes one preset's physics with another's look.
  - For two-sided colour, the documented order to test, after saving, is `SetUseSameColorAsFront(f, 1, False)` and then `SetFabricPBRMaterialBaseColor(f, 1, r, g, b, a)`. The recorded crash may come from writing a face that still shares the front colour; this is a hypothesis.
- **P0.2 errors.** No route through the API. Detection has to be inferred from read-backs, e.g. a mesh count or type change, piece count, seam count, or a snapshot.

### Gaps
- None of the following has been measured on 2026.0.315:
  - 4-int `SetArrangementPosition` semantics and ordering;
  - `ReDrape3DArrangement`;
  - `ImportZprj`/`ImportZpac` translation fields;
  - the `GetAllStitchProperty` JSON;
  - `GetLinkedPatternLists`;
  - `GetGraphicStylePosition(…, False)`;
  - `.jfab` export and editing;
  - `CombineZfab`;
  - `SetUseSameColorAsFront`-first colour writes;
  - `SetPatternArchiveState`;
  - `GetBoundingBoxOfPattern`'s exact keys.
  MD was closed, and mutation was out of scope.
- Whether MD's GUI-only operations (Fold Arrangement, Reset 2D/3D Arrangement, Superimpose, Tack, the gizmo) have hidden bindings under other names. Not found in the bound name list.

## Q5. Pattern JSON schema (ExportPatternJSON/ImportPatternJSON)

### Takeaway
No official or community schema for MD/CLO pattern JSON was found. MD and CLO document the two calls in a single line each. The only description is the repo's own reverse-engineered field list in `docs/md-conventions.md`.

### Cited Findings
- **The official description in full:** `ExportPatternJSON(_path: str) -> bool`, "Export pattern information by JSON format", and `ImportPatternJSON(_path: str) -> bool`, "Import pattern information of JSON file". CLO's entries are identical. — [MD API List source](https://developer.marvelousdesigner.com/_sources/list.rst.txt); [CLO API List](https://developer.clo3d.com/list.html)
- **CLO's related calls.** CLO adds `ConvertPatternJSONToCPX(_jsonPath, _cpxPath) -> bool` ("Protobuf .cpx binary format", removed in V10.0.3 and re-introduced in V11.0.0). Neither MD nor CLO documents the fields. — [CLO API List](https://developer.clo3d.com/list.html); [CLO changelog](https://developer.clo3d.com/changelog.html)
- **The repo's measured field list:**
  - Top level: `formatVersion`, `Unit` ("mm"), `generator`
  - `FabricList`
  - `PatternList[]`: `ID`, `Name`, `CurrentFabricUUID`, `fGrainlineAngle`, `IsClosed`, `IsHalfSymmetric`, `ShapeInfo.LineList[].PointList[]` (ID, PointType "Straight"/"Polyline", GradingRuleID, Position{x,y}), `InternalLineList`, `NotchList`, `OuterSeamAllowanceList`, `ButtonHeadList`, `ButtonHoleList`, `AnnotationList`, and `ArrangementPointDataMap{PointName, fOffSetX, fOffSetY, fAngle}`
  - `SeamLinePairGroupList[]`: `Name`, `bIsTurned`, `FoldData{iAngle, iStrength}`, `PairList[]{First, Second}`, each side having `ShapeID`, `LengthParam{fStart, fEnd}` and `Direction`
  - `GradingRuleTableList`, `SymmetricDataList`, `InstanceDataList`, `POMList`
  - **No 3D positions.**
  - — `docs/md-conventions.md` (Adding a sewing pattern)
- **The repo on `LengthParam`.** `fStart`/`fEnd` are fractions of the outline perimeter of `ShapeID`: the back's left shoulder read 0.784–0.807, which matched the line lengths. — `docs/sessions/2026-10-11-vampire-cape.md`
- **Searches returned nothing.** Web searches for "SeamLinePairGroupList" and "ArrangementPointDataMap"/"ExportPatternJSON" returned no MD or CLO material. — web search, 2026-10-11

### Inferences
- The schema is internal and unversioned in public. md-mcp's decoding (P1.1) has to be built from exported samples and re-checked whenever `formatVersion` changes.
- 3D state is not in this document. Any placement route through pattern JSON is closed for this build: the offsets in `ArrangementPointDataMap` are discarded on import (repo measurement).

### Gaps
- The meaning of `FoldData.iStrength`, `First`/`Second` ordering versus the direction flags, and `formatVersion` values across builds. No source found.

## Q6. Community usage: working recipes for placement, seams and readback

### Takeaway
Public code using MD's Python API is almost non-existent. No GitHub repository calling `pattern_api`/`utility_api` for placement or seams was found. The visible community items are a layer-management script post for MD 2026.0.259 (inaccessible, 403), a CLO3D MCP server with no placement, seam or readback tools, and a CLO forum thread on the `.jfab` round trip.

### Cited Findings
- **Searches.** Searches for `github "pattern_api" "utility_api"`, `"import pattern_api" marvelous designer` and "marvelous designer MCP server github" found no MD API code repositories. — web searches, 2026-10-11
- **A community layer-management post.** "Marvelous Designer - Python Scripts to Manage Layers Quickly - MD v2026.0.259" (Matt Waggle, ArtStation) shares Plugin Manager scripts, including a "Promote_All" script that "looks at the name of the pattern and assigns it to a Layer". Fetching the page returned HTTP 403, so the exact calls are unverified. — [ArtStation blog (search result)](https://mattwaggle.artstation.com/blog/category/1)
- **A CLO3D MCP server.** "clo3d-mcp" (Lilbonner) exposes 14 tools: ping, shutdown, import project/avatar, auto_hang, simulate, add/assign fabric, set_fabric_color, colourway tools, render_image, export_zprj and pattern_count. It has **no arrangement, reset, seam-readback, geometry or error tools**, with defaults `CLO_MCP_PORT` 5005 and `CLO_MCP_TIMEOUT` 600 s. — [glama.ai clo3d-mcp schema](https://glama.ai/mcp/servers/Lilbonner/clo3d-mcp/schema)
- **A CLO forum thread on the `.jfab` round trip.** A CLO plug-in developer uses `ExportFabric` → edit `.jfab` → `ChangeFabricWithJson` to recolour fabrics. They report that CLO-SET fabrics cannot be exported to JSON ("this fabric has been downloaded from CLO-SET or CLOSET CONNECT, which can't be exported to Json Fabric(.json)"). No answer was posted. — [CLO forum post](https://support.clo3d.com/hc/zh-cn/community/posts/6876906333977-Clo3D-automation-using-API)
- **An MD support thread on access.** According to a search-result summary, a support thread on API access reports users unable to find documentation, and support pointing them to the developer site. — [MD support community (search result)](https://support.marvelousdesigner.com/hc/ja/community/posts/48267759477401-API-access)

### Inferences
- md-mcp's own measured notes (`notes.py`, `md-conventions.md`, the session report) are already the most detailed public record of how MD's API behaves. There are no external recipes to borrow for placement or seam removal. Measurement on the live build is the only way forward.

### Gaps
- GitHub code search requires authentication and `gh` is not installed, so a thorough code search for `import utility_api` / `fabric_api` was not possible. Unauthenticated web search may miss repositories.
