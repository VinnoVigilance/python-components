import json
from pathlib import Path
import sys
import time
from datetime import datetime


ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

from intelligence.llm import generate
from parsing.pdfParser import PdfParser


def extract_with_llm(text, prompt_path, model_name, schema_path=None):

    prompt = Path(prompt_path).read_text(encoding="utf-8")

    schema = None
    schema_text = ""

    if schema_path:
        schema_text = Path(schema_path).read_text(encoding="utf-8")
        schema = json.loads(schema_text)

    prompt = prompt.replace("{{schema}}", schema_text)
    prompt = prompt.replace("{{text}}", text)

    # -----------------------------
    # LLM
    # -----------------------------
    start = time.perf_counter()

    output = generate(
        prompt=prompt,
        model_name=model_name,
        schema=schema
    )

    print(f"LLM time: {time.perf_counter() - start:.2f}s")

    # -----------------------------
    # JSON Parse
    # -----------------------------
    start = time.perf_counter()

    output = output.replace("```json", "").replace("```", "").strip()
    result = json.loads(output)

    if isinstance(result, dict):
        result = [result]

    print(f"JSON parse time: {time.perf_counter() - start:.2f}s")
    print(f"Extracted entities count: {len(result)}")

    return result


def run_single_pdf(pdf_path):

    total_start = time.perf_counter()

    pdf_path = Path(pdf_path)

    prompt_path = ROOT_DIR / "data" / "prompts" / "advisory_extraction.txt"
    schema_path = ROOT_DIR / "data" / "schemas" / "organization_schema.json"

    parser = PdfParser()

    start_datetime = datetime.now()

    print()
    print("=" * 80)
    print(f"Processing PDF: {pdf_path}")
    print(f"Start time : {start_datetime.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

    # -----------------------------
    # PDF Parse
    # -----------------------------
    start = time.perf_counter()

    pdf_text = parser.parse_text(str(pdf_path))

    print(f"PDF parse time: {time.perf_counter() - start:.2f}s")

    # -----------------------------
    # Extraction
    # -----------------------------
    result = extract_with_llm(
        text=pdf_text,
        prompt_path=prompt_path,
        schema_path=schema_path,
        model_name="qwen2.5:14b"
    )

    output_dir = (
        ROOT_DIR
        / "data"
        / "sec"
        / "json"
        / "extracted"
        / pdf_path.parent.name
    )

    output_dir.mkdir(parents=True, exist_ok=True)

    output_file = output_dir / f"{pdf_path.stem}.json"

    # -----------------------------
    # Save
    # -----------------------------
    start = time.perf_counter()

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(
            result,
            f,
            ensure_ascii=False,
            indent=4
        )

    print(f"Save time: {time.perf_counter() - start:.2f}s")

    print(f"JSON saved successfully: {output_file}")

    end_datetime = datetime.now()

    print(f"End time   : {end_datetime.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Total PDF time: {time.perf_counter() - total_start:.2f}s")


def run_all_pdfs():

    total_start = time.perf_counter()

    pdf_root = (
        ROOT_DIR
        / "data"
        / "sec"
        / "pdf"
        / "raw"
    )

    year_folders = sorted(
        [p for p in pdf_root.iterdir() if p.is_dir()],
        key=lambda p: int(p.name),
        reverse=True
    )

    for year_folder in year_folders:

        print()
        print("#" * 80)
        print(f"Processing year: {year_folder.name}")
        print("#" * 80)

        pdf_files = sorted(year_folder.glob("*.pdf"))

        for pdf_path in pdf_files:

            output_file = (
                ROOT_DIR
                / "data"
                / "sec"
                / "json"
                / "extracted"
                / year_folder.name
                / f"{pdf_path.stem}.json"
            )

            if output_file.exists():
                print(f"Skipped, already exists: {output_file}")
                continue

            file_start = time.perf_counter()

            try:
                run_single_pdf(pdf_path)

            except Exception as e:

                print()
                print(f"ERROR while processing PDF: {pdf_path}")
                print(str(e))
                print(
                    f"Failed PDF time: "
                    f"{time.perf_counter() - file_start:.2f}s"
                )

    print()
    print("=" * 80)
    print(f"All done. Total time: {time.perf_counter() - total_start:.2f}s")
    print("=" * 80)


if __name__ == "__main__":

    # run_single_pdf(
    #     ROOT_DIR
    #     / "data"
    #     / "sec"
    #     / "pdf"
    #     / "raw"
    #     / "2026"
    #     / "2026_Advisory-zild.pdf"
    # )

    run_all_pdfs()