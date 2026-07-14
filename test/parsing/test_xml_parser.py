# tests/parsing/test_xml_parser.py
"""
XML Parser Test Suite

Provides two testing modes for the XmlParser:
    1. Single Source Test - Process one XML file with automatic source detection
    2. Batch Test - Process all XML files in the downloads directory

The test suite automatically detects the appropriate watchlist configuration
for each XML file based on filename patterns.
"""

import sys
import json
from pathlib import Path

# Add project root to Python path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from parsing.xmlParser import XmlParser
from pipelines.watchlistConfigs import WATCHLIST_CONFIGS


# ============================================================================
# CONFIGURATION
# ============================================================================

# Directory structure:
# project_root/
#   ├── data/
#   │   ├── downloads/     # Input directory for XML files
#   │   └── test/          # Output directory for JSONL files
#   └── tests/
#       └── parsing/
#           └── test_xml_parser.py

PROJECT_ROOT = Path(__file__).parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
INPUT_DIR = DATA_DIR / "downloads"
OUTPUT_DIR = DATA_DIR / "test"

# Create directories if they don't exist
INPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def get_output_file(xml_filename: str) -> Path:
    """
    Get output file path for an XML file.
    
    Args:
        xml_filename: The full name of the XML file (e.g., "OFAC_20260706_085222.XML")
    
    Returns:
        Path: Path to the output JSONL file with the same base name
    """
    # Remove .xml or .XML extension and add .jsonl
    base_name = xml_filename.rsplit('.', 1)[0]
    return OUTPUT_DIR / f"{base_name}.jsonl"


def count_records(file_path: Path) -> int:
    """Count JSON records in a file."""
    if not file_path.exists():
        return 0
    with open(file_path, 'r', encoding='utf-8') as f:
        return sum(1 for line in f if line.strip())


def get_file_size_mb(file_path: Path) -> float:
    """Get file size in megabytes."""
    if not file_path.exists():
        return 0.0
    return file_path.stat().st_size / (1024 * 1024)


def get_xml_sources() -> dict:
    """Get all XML sources from watchlist configs."""
    return {
        name: config
        for name, config in WATCHLIST_CONFIGS.items()
        if config.get("file_type") == "xml"
    }


def detect_source_for_file(filename: str, xml_sources: dict) -> tuple:
    """
    Detect the appropriate source configuration for an XML file.
    
    Returns:
        tuple: (source_name, config_dict) or (None, None) if no match found
    """
    # Try exact match with source name in filename
    for source_name in xml_sources.keys():
        if source_name in filename.upper():
            return source_name, xml_sources[source_name]
    
    # Try pattern-based matching
    patterns = {
        "OFAC": "OFAC-SDN",
        "OFSI": "UKSL",
        "UKSL": "UKSL",
        "UN": "UN",
        "EU": "EU-TRAVEL-BAN",
        "TRAVEL": "EU-TRAVEL-BAN",
        "SDN": "OFAC-SDN",
        "NON-SDN": "OFAC-NON-SDN",
    }
    
    for pattern, source_name in patterns.items():
        if pattern in filename.upper():
            if source_name in xml_sources:
                return source_name, xml_sources[source_name]
    
    return None, None


# ============================================================================
# UI HELPERS
# ============================================================================

def print_header(title: str, char: str = "=", width: int = 80):
    """Print a formatted header."""
    print("\n" + char * width)
    print(f"{title.center(width)}")
    print(char * width)


def print_subheader(title: str, char: str = "-", width: int = 80):
    """Print a formatted subheader."""
    print(f"\n{title}")
    print(char * width)


def print_success(message: str):
    """Print a success message."""
    print(f"[SUCCESS] {message}")


def print_error(message: str):
    """Print an error message."""
    print(f"[ERROR] {message}")


def print_info(message: str):
    """Print an info message."""
    print(f"[INFO] {message}")


def print_warning(message: str):
    """Print a warning message."""
    print(f"[WARNING] {message}")


# ============================================================================
# TEST FUNCTIONS
# ============================================================================

def test_single_file():
    """
    Test a single XML file with automatic source detection.
    
    The user selects an XML file from the input directory, and the system
    automatically detects the appropriate watchlist configuration.
    """
    
    # Get available XML files
    xml_files = list(INPUT_DIR.glob("*.xml")) + list(INPUT_DIR.glob("*.XML"))
    xml_files = sorted(set(xml_files), key=lambda x: x.name.lower())
    
    if not xml_files:
        print_error(f"No XML files found in: {INPUT_DIR}")
        return False
    
    # Display available files
    print_subheader("Available XML Files", "-", 80)
    print(f"\nDirectory: {INPUT_DIR}\n")
    
    for i, file_path in enumerate(xml_files, 1):
        size_mb = get_file_size_mb(file_path)
        print(f"  [{i:2d}] {file_path.name:<40} ({size_mb:.2f} MB)")
    
    # Get user selection
    while True:
        try:
            choice = input("\nSelect file number (or 0 to cancel): ").strip()
            if choice == "0":
                return False
            
            idx = int(choice) - 1
            if 0 <= idx < len(xml_files):
                selected_file = xml_files[idx]
                break
            print_error("Invalid selection. Please try again.")
        except ValueError:
            print_error("Please enter a valid number.")
    
    # Detect source configuration
    xml_sources = get_xml_sources()
    source_name, config = detect_source_for_file(selected_file.name, xml_sources)
    
    if not source_name or not config:
        print_error(f"Could not detect source configuration for: {selected_file.name}")
        return False
    
    # Prepare processing
    root_tags = config.get("root_tags", ["Designation"])
    output_file = get_output_file(selected_file.name)
    
    # Display processing details
    print_subheader("Processing Details", "-", 80)
    print(f"\n  Input File:   {selected_file.name}")
    print(f"  Source:       {source_name}")
    print(f"  Root Tags:    {', '.join(root_tags)}")
    print(f"  Output File:  {output_file.name}")
    print(f"  Output Path:  {OUTPUT_DIR}")
    
    # Run parser
    parser = XmlParser()
    
    try:
        print_info("Processing XML file...")
        
        result = parser.run_xml_ingestion(
            xml_file=str(selected_file),
            output_file=str(output_file),
            root_tags=root_tags
        )
        
        # Display results
        record_count = count_records(output_file)
        file_size_mb = get_file_size_mb(output_file)
        
        print_subheader("Processing Results", "-", 80)
        print(f"\n  Status:       SUCCESS")
        print(f"  Output File:  {output_file.name}")
        print(f"  Records:      {record_count}")
        print(f"  File Size:    {file_size_mb:.2f} MB")
        
        if record_count > 0:
            print_info(f"Output saved to: {output_file}")
        else:
            print_warning("No records were extracted. Check the XML structure and root tags.")
        
        return True
        
    except Exception as e:
        print_error(f"Processing failed: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def test_all_files():
    """
    Process all XML files in the input directory.
    
    Each file is automatically matched to its appropriate watchlist
    configuration based on filename patterns.
    Each XML file produces its own JSONL output file.
    """
    
    print_header("BATCH XML PROCESSING", "=", 80)
    
    # Get XML sources
    xml_sources = get_xml_sources()
    
    if not xml_sources:
        print_error("No XML sources found in watchlist configurations.")
        return False
    
    # Get unique XML files
    xml_files = list(INPUT_DIR.glob("*.xml")) + list(INPUT_DIR.glob("*.XML"))
    xml_files = sorted(set(xml_files), key=lambda x: x.name.lower())
    
    if not xml_files:
        print_error(f"No XML files found in: {INPUT_DIR}")
        return False
    
    # Display file list
    print_subheader("Files to Process", "-", 80)
    print(f"\nFound {len(xml_files)} XML file(s) in: {INPUT_DIR}\n")
    
    for file_path in xml_files:
        size_mb = get_file_size_mb(file_path)
        print(f"  * {file_path.name:<45} ({size_mb:.2f} MB)")
    
    # Process each file
    parser = XmlParser()
    results = {}
    total_records = 0
    
    print_subheader("Processing Files", "-", 80)
    
    for xml_file in xml_files:
        # Detect source
        source_name, config = detect_source_for_file(xml_file.name, xml_sources)
        
        if not source_name:
            print_warning(f"Skipping {xml_file.name} - no matching source config found")
            results[xml_file.name] = {"status": "skipped", "reason": "No matching source config"}
            continue
        
        # Get configuration
        root_tags = config.get("root_tags", ["Designation"])
        output_file = get_output_file(xml_file.name)
        
        print(f"\nProcessing: {xml_file.name}")
        print(f"  Source:     {source_name}")
        print(f"  Root Tags:  {', '.join(root_tags)}")
        print(f"  Output:     {output_file.name}")
        
        try:
            # Run parser
            parser.run_xml_ingestion(
                xml_file=str(xml_file),
                output_file=str(output_file),
                root_tags=root_tags
            )
            
            # Get results
            record_count = count_records(output_file)
            total_records += record_count
            
            results[xml_file.name] = {
                "status": "success",
                "source": source_name,
                "records": record_count,
                "output": output_file.name
            }
            
            print(f"  Status:     SUCCESS - {record_count} records extracted")
            
        except Exception as e:
            results[xml_file.name] = {
                "status": "error",
                "source": source_name,
                "error": str(e)
            }
            print(f"  Status:     ERROR - {str(e)}")
    
    # Display summary
    print_header("PROCESSING SUMMARY", "=", 80)
    
    success_count = sum(1 for r in results.values() if r["status"] == "success")
    error_count = sum(1 for r in results.values() if r["status"] == "error")
    skipped_count = sum(1 for r in results.values() if r["status"] == "skipped")
    
    print(f"\n  Total Files:   {len(results)}")
    print(f"  Successful:    {success_count}")
    print(f"  Errors:        {error_count}")
    print(f"  Skipped:       {skipped_count}")
    print(f"  Total Records: {total_records}")
    
    # Show per-source totals
    if success_count > 0:
        print_subheader("Records by Source", "-", 80)
        
        source_totals = {}
        for result in results.values():
            if result["status"] == "success":
                source = result["source"]
                source_totals[source] = source_totals.get(source, 0) + result["records"]
        
        for source, count in sorted(source_totals.items()):
            print(f"  {source:<20} {count:>8} records")
    
    # Show output files
    print_subheader("Generated Output Files", "-", 80)
    
    output_files = list(OUTPUT_DIR.glob("*.jsonl"))
    if output_files:
        print()
        for file_path in sorted(output_files):
            records = count_records(file_path)
            size_mb = get_file_size_mb(file_path)
            print(f"  * {file_path.name:<50} ({records:>6} records, {size_mb:.2f} MB)")
    else:
        print_warning("No output files were generated.")
    
    return success_count > 0


# ============================================================================
# MAIN MENU
# ============================================================================

def main():
    """Main entry point with interactive menu."""
    
    print_header("XML PARSER TEST SUITE", "=", 80)
    
    # Display directories
    print(f"\n  Input Directory:   {INPUT_DIR}")
    print(f"  Output Directory:  {OUTPUT_DIR}")
    
    # Display available sources
    xml_sources = get_xml_sources()
    if xml_sources:
        print_subheader("Available XML Sources", "-", 80)
        for name, config in xml_sources.items():
            root_tags = ", ".join(config.get("root_tags", ["Designation"]))
            print(f"  * {name:<20} -> root tags: {root_tags}")
    
    # Display menu
    while True:
        print_header("MAIN MENU", "-", 80)
        print("\n  [1] Process Single File (auto-detect source)")
        print("  [2] Process All Files (batch mode)")
        print("  [0] Exit")
        print("-" * 80)
        
        try:
            choice = input("\nEnter your selection: ").strip()
            
            if choice == "0":
                print_info("Exiting test suite.")
                sys.exit(0)
            elif choice == "1":
                test_single_file()
            elif choice == "2":
                test_all_files()
            else:
                print_error("Invalid selection. Please enter 0, 1, or 2.")
                
        except KeyboardInterrupt:
            print("\n\n[INFO] Exiting test suite.")
            sys.exit(0)
        except Exception as e:
            print_error(f"An unexpected error occurred: {str(e)}")


if __name__ == "__main__":
    main()