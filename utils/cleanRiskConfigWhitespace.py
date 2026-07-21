"""
One-off hygiene tool: strip non-breaking spaces (\\xa0) and zero-width spaces
from every cell of riskClassification.xlsx, replacing them with normal spaces
and trimming/collapsing whitespace.

The risk config loader already normalizes these on read, so this is cosmetic -
it just keeps the source workbook clean for anyone reading it directly.

Usage (close the file in Excel first):
    ./vv-env/Scripts/python.exe utils/cleanRiskConfigWhitespace.py
"""

import re
import sys

import openpyxl

PATH = "data/rules/riskClassification.xlsx"


def clean_text(text: str) -> str:
    text = text.replace("\xa0", " ").replace("​", "")
    return re.sub(r"\s+", " ", text).strip()


def main(path: str = PATH) -> None:
    wb = openpyxl.load_workbook(path)
    changed = 0

    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, str):
                    cleaned = clean_text(cell.value)
                    if cleaned != cell.value:
                        cell.value = cleaned
                        changed += 1

    wb.save(path)
    print(f"Cleaned {changed} cell(s) in {path}")


if __name__ == "__main__":
    try:
        main()
    except PermissionError:
        print(
            "PermissionError: the file is open in Excel. "
            "Close it and run again.",
            file=sys.stderr,
        )
        sys.exit(1)
