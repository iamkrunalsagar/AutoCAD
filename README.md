# CAD Automation Tool (Plumbing Production)

This project provides an end-to-end automation tool for turning a messy client ZIP into a structured, permit-ready plumbing CAD package.

## Features

- Extracts input ZIP and organizes files into:
  - `Received From Client/` for architectural PDFs
  - `Working Files/M/` for mechanical DWGs
  - `Working Files/P/` for plumbing DWGs and generated sheets
  - `Working Files/XREF/` for XREF files (`X-FLOOR`, `X-ROOF`, `TitleBlock`)
- Removes junk files (`acad.err`, `*recover*`, `*.bak`, `thumbs.db`)
- Copies plumbing templates from `CAD_MASTER_TEMPLATE`
- Renames template sheets to `{ProjectName}_P-#.dwg`
- Generates:
  - AutoLISP (`plumb_batch_setup.lsp`) for XREF attachment + title block attributes
  - AutoCAD script (`plumb_batch_setup.scr`) to call the LISP routine
  - Project config JSON (`project_config.json`)
- Optionally uses COM (`comtypes` / AutoCAD ActiveX) to execute scripts against DWGs
- Produces final ZIP output for delivery

---

## Installation

### 1) Python dependencies

```bash
pip install pyautocad comtypes openai
```

> Notes:
> - `comtypes` is required for AutoCAD COM/ActiveX automation.
> - `openai` is optional unless `--use-openai-classifier` is enabled.
> - `pyautocad` is listed per requirement and can be used for additional extensions.

### 2) AutoCAD requirements

- Windows machine with AutoCAD installed.
- AutoCAD COM automation enabled (default in most installations).
- Trusted paths in AutoCAD should include your generated automation folder if script loading is blocked.
- If needed, set AutoCAD security settings to allow loading generated `.lsp` files.

---

## CLI Usage

```bash
python cad_automation_tool.py input.zip output_dir/ \
  --project-name "Sunset Medical Center" \
  --project-address "101 Healthcare Ave, Phoenix, AZ" \
  --designer-name "Jordan Lee" \
  --revision "A" \
  --master-template-dir "CAD_MASTER_TEMPLATE"
```

### Optional flags

- `--run-autocad` → attempts live AutoCAD automation via COM.
- `--use-openai-classifier` → uses OpenAI model to classify ambiguous DWG names.
- `--openai-model gpt-4o-mini` → choose model for filename classification.
- `--verbose` → enables debug logging.

---

## Example Output Structure

```text
<ProjectName>/
├── Received From Client/
├── Working Files/
│   ├── M/
│   ├── P/
│   ├── XREF/
│   └── Unsorted/
└── Automation/
    ├── plumb_batch_setup.lsp
    ├── plumb_batch_setup.scr
    ├── project_config.json
    └── run_report.txt
```

And final deliverable:

```text
output_dir/<ProjectName>_permit_ready.zip
```

---

## How AutoCAD automation works

1. Python generates `plumb_batch_setup.lsp`.
2. LISP command `PLUMB_BATCH_SETUP`:
   - Attaches `X-FLOOR.dwg`, `X-ROOF.dwg`, `TitleBlock.dwg` from XREF folder.
   - Updates title block attributes:
     - `PROJECT_NAME`
     - `PROJECT_ADDRESS`
     - `SHEET_NO`
     - `REV`
     - `DESIGNER`
     - `DATE`
3. Python generates `.scr` file to load and run the LISP command.
4. If `--run-autocad` is enabled, Python opens each generated DWG and executes script via COM.

---

## Configuration and Extensibility

- Run parameters are saved in `Automation/project_config.json`.
- You can modify classification heuristics in:
  - `MECH_HINTS`
  - `PLUMB_HINTS`
  - `XREF_KEYWORDS`
- Extend LISP logic if your title block uses different attribute tags.

