# tests/debug_output.py
"""
Debug tool to inspect the final output file and understand why preview shows empty list
"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def inspect_file(file_path):
    """Thoroughly inspect a JSONL file to understand its structure"""
    
    print("=" * 70)
    print(f" INSPECTING: {file_path}")
    print("=" * 70)
    
    file_path = Path(file_path)
    
    if not file_path.exists():
        print(f"[ERROR] File not found: {file_path}")
        return
    
    # File info
    print(f"\n[FILE INFO]")
    print(f"  Path: {file_path}")
    print(f"  Size: {file_path.stat().st_size:,} bytes")
    print(f"  Last modified: {file_path.stat().st_mtime}")
    
    # Try different encodings
    encodings = ['utf-8', 'utf-8-sig', 'latin-1', 'cp1252']
    
    print(f"\n[READING FILE]")
    
    for encoding in encodings:
        try:
            with open(file_path, 'r', encoding=encoding) as f:
                lines = f.readlines()
            
            print(f"\n  ✓ Success with encoding: {encoding}")
            print(f"    Total lines: {len(lines)}")
            
            # Count non-empty lines
            non_empty = sum(1 for line in lines if line.strip())
            print(f"    Non-empty lines: {non_empty}")
            
            if non_empty == 0:
                print(f"    ⚠️ No non-empty lines found!")
                continue
            
            # Show first few lines
            print(f"\n    First 3 lines (showing first 200 chars each):")
            for i, line in enumerate(lines[:3]):
                if line.strip():
                    preview = line.strip()[:200]
                    print(f"    Line {i+1}: {preview}...")
            
            # Try parsing first record
            print(f"\n[PARSING FIRST RECORD]")
            
            for i, line in enumerate(lines):
                if line.strip():
                    try:
                        record = json.loads(line)
                        print(f"\n  ✓ Successfully parsed line {i+1}")
                        print(f"    Top-level keys: {list(record.keys())}")
                        
                        # Show sample fields
                        print(f"\n  Sample fields:")
                        for key in list(record.keys())[:10]:
                            value = record.get(key)
                            if isinstance(value, list):
                                print(f"    {key}: list ({len(value)} items)")
                            elif isinstance(value, dict):
                                print(f"    {key}: dict ({len(value)} keys)")
                            else:
                                val_str = str(value)[:50]
                                print(f"    {key}: {val_str}")
                        
                        # Specifically check Dates
                        dates = record.get("Dates", [])
                        print(f"\n  Dates field:")
                        if dates:
                            print(f"    ✓ Contains {len(dates)} item(s)")
                            for i, date_item in enumerate(dates):
                                if isinstance(date_item, dict):
                                    print(f"      Item {i+1}: {list(date_item.keys())}")
                                    # Show non-empty fields
                                    non_empty_fields = [k for k, v in date_item.items() if v and v != ""]
                                    if non_empty_fields:
                                        print(f"        Non-empty: {non_empty_fields}")
                                    else:
                                        print(f"        All fields are empty!")
                        else:
                            print(f"    ✗ EMPTY list (0 items)")
                        
                        # Check for INDIVIDUAL_DATE_OF_BIRTH in raw structure
                        if "INDIVIDUAL_DATE_OF_BIRTH" in record:
                            dob = record.get("INDIVIDUAL_DATE_OF_BIRTH")
                            print(f"\n  Raw INDIVIDUAL_DATE_OF_BIRTH:")
                            if dob:
                                print(f"    ✓ Exists: {dob}")
                            else:
                                print(f"    ✗ Empty or None")
                        
                        return record
                        
                    except json.JSONDecodeError as e:
                        print(f"    ✗ JSON parse error on line {i+1}: {e}")
                        print(f"    Line preview: {line[:100]}")
                        continue
            
            # If we got here, no valid JSON found
            print(f"\n  ✗ No valid JSON records found in file")
            
        except Exception as e:
            print(f"  ✗ Failed with encoding {encoding}: {e}")
            continue


def compare_raw_and_final(source_name):
    """Compare raw and final files to see what's happening"""
    
    print("\n" + "=" * 70)
    print(f" COMPARING RAW VS FINAL FOR: {source_name}")
    print("=" * 70)
    
    raw_file = PROJECT_ROOT / "data" / "raw" / f"{source_name}_RAW.jsonl"
    final_file = PROJECT_ROOT / "data" / "final" / f"{source_name}_final.jsonl"
    
    print(f"\n[RAW FILE]")
    if raw_file.exists():
        print(f"  ✓ Exists: {raw_file}")
        print(f"  Size: {raw_file.stat().st_size:,} bytes")
        
        # Count records
        with open(raw_file, 'r', encoding='utf-8') as f:
            raw_count = sum(1 for line in f if line.strip())
        print(f"  Records: {raw_count}")
        
        # Check first record
        with open(raw_file, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    record = json.loads(line)
                    print(f"  Sample record keys: {list(record.keys())[:10]}")
                    break
    else:
        print(f"  ✗ Not found")
    
    print(f"\n[FINAL FILE]")
    if final_file.exists():
        print(f"  ✓ Exists: {final_file}")
        print(f"  Size: {final_file.stat().st_size:,} bytes")
        
        # Count records
        with open(final_file, 'r', encoding='utf-8') as f:
            final_count = sum(1 for line in f if line.strip())
        print(f"  Records: {final_count}")
        
        # Check first record
        with open(final_file, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    record = json.loads(line)
                    print(f"  Sample record keys: {list(record.keys())[:10]}")
                    
                    # Check Dates
                    dates = record.get("Dates", [])
                    print(f"  Dates: {len(dates)} items")
                    
                    if dates:
                        print(f"    First Date item:")
                        date_item = dates[0]
                        for key, value in date_item.items():
                            print(f"      {key}: {value if value else '(empty)'}")
                    break
    else:
        print(f"  ✗ Not found")


def main():
    """Main function"""
    
    print_header = lambda x: print("\n" + "=" * 70 + f"\n {x}\n" + "=" * 70)
    
    print_header("OUTPUT FILE DEBUG TOOL")
    
    # Get source name
    source_name = input("Enter source name (e.g., UN-SANCTIONS, or press Enter for UN-SANCTIONS): ").strip()
    if not source_name:
        source_name = "UN-SANCTIONS"
    
    # Get final file path
    final_file = PROJECT_ROOT / "data" / "final" / f"{source_name}_final.jsonl"
    
    # Inspect the file
    inspect_file(final_file)
    
    # Compare with raw
    compare_raw_and_final(source_name)


if __name__ == "__main__":
    main()