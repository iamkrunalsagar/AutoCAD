#!/usr/bin/env python3
"""
CAD Automation Tool for Plumbing Drawing Production

This script automates intake, cleanup, file structuring, template generation,
AutoCAD automation script generation, and final packaging for permit-ready
plumbing CAD sets.

Usage:
    python cad_automation_tool.py input.zip output_dir/ \
        --project-name "Acme Tower" \
        --project-address "100 Main St, Springfield" \
        --designer-name "A. Designer"
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import logging
import os
import re
import shutil
import sys
import zipfile
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Optional OpenAI dependency for advanced file classification (disabled by default).
try:
    from openai import OpenAI
except Exception:  # pragma: no cover - optional dependency
    OpenAI = None

# Optional AutoCAD COM dependencies.
try:
    import comtypes.client
except Exception:  # pragma: no cover - optional dependency
    comtypes = None


JUNK_PATTERNS = [
    "acad.err",
    "*recover*",
    "*.bak",
    "thumbs.db",
]

PLUMBING_TEMPLATE_FILES = ["P-0.0.dwt", "P-1.0.dwt", "P-2.0.dwt", "P-3.0.dwt", "P-4.0.dwt"]
XREF_KEYWORDS = ["X-FLOOR", "X-ROOF", "TITLEBLOCK"]
MECH_HINTS = ["M-", "MECH", "MECHANICAL", "HVAC"]
PLUMB_HINTS = ["P-", "PLUMB", "PLUMBING"]


@dataclass
class ProjectConfig:
    project_name: str
    project_address: str
    designer_name: str
    revision: str
    issue_date: str
    master_template_dir: str
    output_root: str


def setup_logging(verbose: bool = False) -> None:
    """Configure application logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="[%(levelname)s] %(message)s")


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for automation run."""
    parser = argparse.ArgumentParser(description="Automate plumbing CAD production workflow.")
    parser.add_argument("input_zip", type=Path, help="Client input ZIP file path")
    parser.add_argument("output_dir", type=Path, help="Directory to place generated project package")
    parser.add_argument("--project-name", required=True, help="Project name for title blocks")
    parser.add_argument("--project-address", required=True, help="Project address for title blocks")
    parser.add_argument("--designer-name", required=True, help="Designer name for title blocks")
    parser.add_argument("--revision", default="0", help="Revision value for title blocks")
    parser.add_argument("--issue-date", default=datetime.now().strftime("%Y-%m-%d"), help="Issue date")
    parser.add_argument(
        "--master-template-dir",
        default="CAD_MASTER_TEMPLATE",
        help="Directory containing template files (e.g., P-1.0.dwt)",
    )
    parser.add_argument(
        "--use-openai-classifier",
        action="store_true",
        help="Use OpenAI API for uncertain DWG classification",
    )
    parser.add_argument(
        "--openai-model",
        default="gpt-4o-mini",
        help="OpenAI model used when --use-openai-classifier is enabled",
    )
    parser.add_argument(
        "--run-autocad",
        action="store_true",
        help="Attempt live AutoCAD COM automation after generating LISP/script",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return parser.parse_args()


def ensure_dir(path: Path) -> None:
    """Create a directory recursively if it does not exist."""
    path.mkdir(parents=True, exist_ok=True)


def extract_zip(input_zip: Path, extract_to: Path) -> None:
    """Extract client ZIP contents to a working location."""
    logging.info("Extracting ZIP: %s", input_zip)
    with zipfile.ZipFile(input_zip, "r") as zf:
        zf.extractall(extract_to)


def is_junk_file(file_path: Path) -> bool:
    """Return True when file matches known junk patterns."""
    lower_name = file_path.name.lower()
    return any(fnmatch.fnmatch(lower_name, pattern.lower()) for pattern in JUNK_PATTERNS)


def remove_junk_files(root: Path) -> List[Path]:
    """Delete junk files from extracted workspace and return removed list."""
    removed: List[Path] = []
    for path in root.rglob("*"):
        if path.is_file() and is_junk_file(path):
            path.unlink(missing_ok=True)
            removed.append(path)
    logging.info("Removed %d junk files", len(removed))
    return removed


def classify_dwg_basic(file_name: str) -> str:
    """Classify DWG into mechanical/plumbing/xref/unknown based on naming conventions."""
    name_up = file_name.upper()
    if any(keyword in name_up for keyword in XREF_KEYWORDS):
        return "xref"
    if any(name_up.startswith(hint) or hint in name_up for hint in MECH_HINTS):
        return "mechanical"
    if any(name_up.startswith(hint) or hint in name_up for hint in PLUMB_HINTS):
        return "plumbing"
    if name_up.startswith("X-"):
        return "xref"
    return "unknown"


def classify_with_openai(file_name: str, model: str) -> str:
    """Use OpenAI model to classify ambiguous DWGs into known buckets."""
    if OpenAI is None:
        raise RuntimeError("OpenAI SDK not installed; cannot use AI classifier.")

    client = OpenAI()
    prompt = (
        "Classify this CAD filename into one category: mechanical, plumbing, xref, unknown. "
        f"Filename: {file_name}. Return only one word."
    )
    response = client.responses.create(model=model, input=prompt)
    result = (response.output_text or "unknown").strip().lower()
    return result if result in {"mechanical", "plumbing", "xref", "unknown"} else "unknown"


def prepare_project_structure(project_root: Path) -> Dict[str, Path]:
    """Create required folder hierarchy and return canonical paths."""
    folders = {
        "received": project_root / "Received From Client",
        "working_m": project_root / "Working Files" / "M",
        "working_p": project_root / "Working Files" / "P",
        "working_xref": project_root / "Working Files" / "XREF",
        "unsorted": project_root / "Working Files" / "Unsorted",
        "automation": project_root / "Automation",
    }
    for p in folders.values():
        ensure_dir(p)
    return folders


def move_and_sort_files(
    extracted_root: Path,
    folders: Dict[str, Path],
    use_openai_classifier: bool,
    openai_model: str,
) -> Dict[str, List[Path]]:
    """Sort extracted files into destination folders based on extension and naming."""
    report: Dict[str, List[Path]] = {k: [] for k in ["pdf", "mechanical", "plumbing", "xref", "unsorted"]}

    for path in extracted_root.rglob("*"):
        if not path.is_file():
            continue

        suffix = path.suffix.lower()
        dest: Optional[Path] = None

        if suffix == ".pdf":
            dest = folders["received"] / path.name
            report["pdf"].append(dest)
        elif suffix in {".dwg", ".dwt"}:
            category = classify_dwg_basic(path.name)
            if category == "unknown" and use_openai_classifier:
                try:
                    category = classify_with_openai(path.name, model=openai_model)
                except Exception as exc:
                    logging.warning("OpenAI classification failed for %s: %s", path.name, exc)

            if category == "mechanical":
                dest = folders["working_m"] / path.name
                report["mechanical"].append(dest)
            elif category == "plumbing":
                dest = folders["working_p"] / path.name
                report["plumbing"].append(dest)
            elif category == "xref":
                dest = folders["working_xref"] / path.name
                report["xref"].append(dest)
            else:
                dest = folders["unsorted"] / path.name
                report["unsorted"].append(dest)
        else:
            dest = folders["unsorted"] / path.name
            report["unsorted"].append(dest)

        ensure_dir(dest.parent)
        shutil.move(str(path), str(dest))

    return report


def copy_and_rename_templates(master_template_dir: Path, working_p_dir: Path, project_name: str) -> List[Path]:
    """Copy predefined plumbing templates and rename as project sheet DWGs."""
    generated: List[Path] = []
    safe_project = re.sub(r"[^A-Za-z0-9_-]+", "_", project_name.strip())

    for template_name in PLUMBING_TEMPLATE_FILES:
        source = master_template_dir / template_name
        if not source.exists():
            logging.warning("Missing template file: %s", source)
            continue

        sheet_id = template_name.replace(".dwt", "").replace("P-", "")
        target = working_p_dir / f"{safe_project}_P-{sheet_id}.dwg"
        shutil.copy2(source, target)
        generated.append(target)

    logging.info("Generated %d plumbing sheets from master template", len(generated))
    return generated


def generate_autolisp(output_path: Path) -> None:
    """Generate AutoLISP routine to attach XREFs and populate title block attributes."""
    lsp = r'''; Auto-generated plumbing CAD batch setup LISP
(defun _safe-getvar (name fallback)
  (if (vl-catch-all-error-p (setq v (vl-catch-all-apply 'getvar (list name)))) fallback v)
)

(defun _insert-or-update-titleblock-attribs (proj_name proj_addr sheet_no rev des_name issue_date / ent obj tag)
  (vl-load-com)
  (setq ms (vla-get-ModelSpace (vla-get-ActiveDocument (vlax-get-acad-object))))
  (vlax-for obj ms
    (if (and (= (vla-get-ObjectName obj) "AcDbBlockReference")
             (= :vlax-true (vla-get-HasAttributes obj)))
      (progn
        (foreach att (vlax-invoke obj 'GetAttributes)
          (setq tag (strcase (vla-get-TagString att)))
          (cond
            ((= tag "PROJECT_NAME") (vla-put-TextString att proj_name))
            ((= tag "PROJECT_ADDRESS") (vla-put-TextString att proj_addr))
            ((= tag "SHEET_NO") (vla-put-TextString att sheet_no))
            ((= tag "REV") (vla-put-TextString att rev))
            ((= tag "DESIGNER") (vla-put-TextString att des_name))
            ((= tag "DATE") (vla-put-TextString att issue_date))
          )
        )
      )
    )
  )
)

(defun _attach-xref (xref_path block_name /)
  (if (findfile xref_path)
    (command "_.-XREF" "A" xref_path "0,0" "1" "1" "0")
    (princ (strcat "\n[WARN] XREF not found: " xref_path))
  )
)

(defun c:PLUMB_BATCH_SETUP (proj_name proj_addr sheet_no rev des_name issue_date xref_dir / floor roof title)
  (setq floor (strcat xref_dir "\\X-FLOOR.dwg"))
  (setq roof  (strcat xref_dir "\\X-ROOF.dwg"))
  (setq title (strcat xref_dir "\\TitleBlock.dwg"))

  (_attach-xref floor "X-FLOOR")
  (_attach-xref roof "X-ROOF")
  (_attach-xref title "TitleBlock")
  (_insert-or-update-titleblock-attribs proj_name proj_addr sheet_no rev des_name issue_date)

  (command "_.QSAVE")
  (princ "\nPLUMB_BATCH_SETUP complete.")
  (princ)
)
(princ)
'''
    output_path.write_text(lsp, encoding="utf-8")


def generate_script(output_path: Path, lsp_path: Path, cfg: ProjectConfig, xref_dir: Path) -> None:
    """Generate AutoCAD .scr script to load LISP and run batch setup command."""
    safe_proj = cfg.project_name.replace('"', "")
    safe_addr = cfg.project_address.replace('"', "")
    safe_designer = cfg.designer_name.replace('"', "")

    script = f'''FILEDIA 0
CMDECHO 0
(load "{lsp_path.as_posix()}")
; Run per drawing with sheet-specific number argument
PLUMB_BATCH_SETUP "{safe_proj}" "{safe_addr}" "P-1.0" "{cfg.revision}" "{safe_designer}" "{cfg.issue_date}" "{xref_dir.as_posix()}"
QSAVE
FILEDIA 1
'''
    output_path.write_text(script, encoding="utf-8")


def run_autocad_com_batch(working_p_dir: Path, script_path: Path) -> Tuple[bool, str]:
    """Optional: Execute generated script in AutoCAD using COM automation."""
    if comtypes is None:
        return False, "comtypes not installed; skipping AutoCAD COM automation"

    dwgs = sorted(working_p_dir.glob("*.dwg"))
    if not dwgs:
        return False, "No DWGs in Working Files/P to automate"

    try:
        acad = comtypes.client.CreateObject("AutoCAD.Application")
        acad.Visible = True
    except Exception as exc:
        return False, f"Unable to launch AutoCAD via COM: {exc}"

    try:
        for dwg in dwgs:
            logging.info("Automating drawing: %s", dwg.name)
            doc = acad.Documents.Open(str(dwg))
            doc.SendCommand(f'_SCRIPT "{script_path}"\n')
            doc.Save()
            doc.Close()
    except Exception as exc:
        return False, f"AutoCAD automation failed: {exc}"

    return True, "AutoCAD COM automation completed"


def save_config(config_path: Path, cfg: ProjectConfig) -> None:
    """Save a JSON snapshot of run configuration for reproducibility."""
    config_path.write_text(json.dumps(asdict(cfg), indent=2), encoding="utf-8")


def zip_output(project_root: Path, output_zip_path: Path) -> None:
    """Create final ZIP archive for permit-ready package delivery."""
    with zipfile.ZipFile(output_zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in project_root.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(project_root))


def main() -> int:
    """Entrypoint: orchestrate full CAD automation workflow."""
    args = parse_args()
    setup_logging(args.verbose)

    if not args.input_zip.exists():
        logging.error("Input ZIP does not exist: %s", args.input_zip)
        return 1

    output_root = args.output_dir / re.sub(r"[^A-Za-z0-9_-]+", "_", args.project_name)
    extracted_root = output_root / "_extracted"
    ensure_dir(output_root)
    ensure_dir(extracted_root)

    cfg = ProjectConfig(
        project_name=args.project_name,
        project_address=args.project_address,
        designer_name=args.designer_name,
        revision=args.revision,
        issue_date=args.issue_date,
        master_template_dir=str(Path(args.master_template_dir).resolve()),
        output_root=str(output_root.resolve()),
    )

    try:
        extract_zip(args.input_zip, extracted_root)
        remove_junk_files(extracted_root)

        folders = prepare_project_structure(output_root)
        sort_report = move_and_sort_files(
            extracted_root,
            folders,
            use_openai_classifier=args.use_openai_classifier,
            openai_model=args.openai_model,
        )

        generated_sheets = copy_and_rename_templates(
            Path(args.master_template_dir),
            folders["working_p"],
            cfg.project_name,
        )

        lsp_path = folders["automation"] / "plumb_batch_setup.lsp"
        scr_path = folders["automation"] / "plumb_batch_setup.scr"
        cfg_path = folders["automation"] / "project_config.json"

        generate_autolisp(lsp_path)
        generate_script(scr_path, lsp_path, cfg, folders["working_xref"])
        save_config(cfg_path, cfg)

        status_txt = folders["automation"] / "run_report.txt"
        status_txt.write_text(
            "\n".join(
                [
                    f"Sorted PDFs: {len(sort_report['pdf'])}",
                    f"Mechanical files: {len(sort_report['mechanical'])}",
                    f"Plumbing files: {len(sort_report['plumbing'])}",
                    f"XREF files: {len(sort_report['xref'])}",
                    f"Unsorted files: {len(sort_report['unsorted'])}",
                    f"Generated sheets: {len(generated_sheets)}",
                ]
            ),
            encoding="utf-8",
        )

        if args.run_autocad:
            ok, message = run_autocad_com_batch(folders["working_p"], scr_path)
            logging.info("AutoCAD batch status: %s", message)
            if not ok:
                logging.warning("Proceeding without live AutoCAD execution.")

        final_zip = args.output_dir / f"{re.sub(r'[^A-Za-z0-9_-]+', '_', args.project_name)}_permit_ready.zip"
        zip_output(output_root, final_zip)
        shutil.rmtree(extracted_root, ignore_errors=True)

        logging.info("Complete. Final package: %s", final_zip)
        return 0

    except Exception as exc:
        logging.exception("Workflow failed: %s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
