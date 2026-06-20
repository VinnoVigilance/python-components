import json
from pathlib import Path
import sys
import time


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

    output = generate(
        prompt=prompt,
        model_name=model_name,
        schema=schema
    )

    output = output.replace("```json", "").replace("```", "").strip()

    return json.loads(output)


if __name__ == "__main__":
    
    total_start = time.perf_counter()

    pdf_path = r"C:\VV_Python_Project\2026_Advisory-zild.pdf"

    prompt_path = ROOT_DIR / "data" / "prompts" / "advisory_extraction.txt"
    schema_path = ROOT_DIR / "data" / "schemas" / "organization_schema.json"

    parser = PdfParser()

    t1 = time.perf_counter()
    pdf_text = parser.parse_text(pdf_path)
    print(f"PDF parse time: {time.perf_counter() - t1:.2f} seconds")

    t2 = time.perf_counter()
    result = extract_with_llm(
        text=pdf_text,
        prompt_path=prompt_path,
        schema_path=schema_path,
        model_name="qwen2.5:14b"
    )
    print(f"LLM extraction time: {time.perf_counter() - t2:.2f} seconds")

    print(json.dumps(result, ensure_ascii=False, indent=2))

    print(f"Total time: {time.perf_counter() - total_start:.2f} seconds")