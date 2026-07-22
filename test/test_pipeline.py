# tests/test_pipeline.py
"""
Watchlist Pipeline Test Harness

Allows testing individual watchlist sources or the full pipeline
without modifying the main code. Supports both XML parsing and
full pipeline execution.
"""

import sys
import json
import time
from pathlib import Path
from datetime import datetime
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from pipelines.watchlistPipline import WatchlistPipeline
from pipelines.watchlistConfigs import WATCHLIST_CONFIGS
from ingestion.downloader import interface as downloader
from transforms.preNormalization import PreNormalizationEngine
from transforms.fieldMapper import load_rules, MappingEngine
from transforms.postNormalization import PostNormalizationEngine
from parsing.xmlParser import XmlParser
from parsing.tabularParser import TabularParser
from parsing.pdfParser import PdfParser
from parsing.htmlParser import HtmlParser


# ============================================================================
# CONFIGURATION
# ============================================================================

DATA_DIR = PROJECT_ROOT / "data"
DOWNLOAD_DIR = DATA_DIR / "downloads"
RAW_DIR = DATA_DIR / "raw"
FINAL_DIR = DATA_DIR / "final"
RULES_DIR = DATA_DIR / "rules"

for directory in [DOWNLOAD_DIR, RAW_DIR, FINAL_DIR, RULES_DIR]:
    directory.mkdir(parents=True, exist_ok=True)


# ============================================================================
# HELPERS
# ============================================================================

def print_header(title, width=80):
    print("\n" + "=" * width)
    print(title.center(width))
    print("=" * width)


def print_subheader(title, width=80):
    print("\n" + title)
    print("-" * width)


def print_info(message):
    print(f"[INFO] {message}")


def print_success(message):
    print(f"[SUCCESS] {message}")


def print_error(message):
    print(f"[ERROR] {message}")


def print_warning(message):
    print(f"[WARNING] {message}")


def count_records(file_path):
    """Count number of records in a JSONL file"""
    if not file_path.exists():
        return 0
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
    except UnicodeDecodeError:
        # Fallback to utf-8-sig if BOM is present
        with open(file_path, "r", encoding="utf-8-sig") as f:
            return sum(1 for line in f if line.strip())


def preview_record(file_path, max_fields=10):
    """Preview the first record in a JSONL file"""
    if not file_path.exists():
        return None
    
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    return json.loads(line)
    except UnicodeDecodeError:
        with open(file_path, "r", encoding="utf-8-sig") as f:
            for line in f:
                if line.strip():
                    return json.loads(line)
    return None



def get_file_size_mb(file_path):
    if not file_path.exists():
        return 0.0
    return file_path.stat().st_size / (1024 * 1024)


def get_available_sources():
    """Return list of available source names from config"""
    return sorted(WATCHLIST_CONFIGS.keys())


def get_source_by_file_type(file_type):
    """Get sources that match a file type"""
    return [
        name for name, config in WATCHLIST_CONFIGS.items()
        if config.get("file_type") == file_type
    ]


# tests/test_pipeline.py
# Add this after the imports and before the helper functions

# Explicit file-to-source mapping for files that don't match patterns well
FILE_TO_SOURCE_MAP = {
    "EU_20260430-FULL-1_1(xsd).xml": "EU-FINANCIAL-SANCTIONS",
    "UN_consolidatedLegacyByPRN.xml": "UN-SANCTIONS",
    # Add more explicit mappings as needed
}


def detect_source_for_file(filename):
    """Detect source from filename pattern"""
    filename_upper = filename.upper()
    
    # First check explicit mapping
    if filename in FILE_TO_SOURCE_MAP:
        return FILE_TO_SOURCE_MAP[filename]
    
    # Pattern-based detection (existing logic)
    patterns = {
        "DFAT": ["DFAT"],
        "OFAC-SDN": ["SDN", "OFAC_"],
        "OFAC-NON-SDN": ["NON-SDN", "CONS_"],
        "UKSL": ["UKSL", "OFSI"],
        "DNFBP": ["DNFBP"],
        "ATC-DESIGNATED-TERRORIST-INDIVIDUALS": ["INDIVIDUALS", "TERRORIST INDIVIDUALS"],
        "ATC-DESIGNATED-TERRORIST-GROUPS": ["GROUPS", "TERRORIST GROUPS"],
        "EU-DESIGNATED-VESSELS": ["VESSEL"],
        "EU-TRAVEL-BAN": ["EU_", "TRAVEL"],
        "UN-SANCTIONS": ["UN_", "CONSOLIDATED"],
        "EU-FINANCIAL-SANCTIONS": ["FINANCIAL-SANCTIONS", "FINANCIAL"],
    }
    
    for source_name, patterns_list in patterns.items():
        for pattern in patterns_list:
            if pattern in filename_upper:
                if source_name in WATCHLIST_CONFIGS:
                    return source_name
    
    return None


def get_parser(file_type):
    """Get appropriate parser for file type"""
    parsers = {
        "xml": XmlParser(),
        "pdf": PdfParser(),
        "html": HtmlParser(),
        "csv": TabularParser(),
        "xlsx": TabularParser(),
        "xls": TabularParser(),
    }
    return parsers.get(file_type)


# ============================================================================
# TEST FUNCTIONS
# ============================================================================

def test_parse_only(source_name, input_file):
    """
    Test only the parsing step (no pre-normalization, mapping, or post-normalization)
    """
    print_header(f"PARSE ONLY: {source_name}")
    
    if source_name not in WATCHLIST_CONFIGS:
        print_error(f"Source '{source_name}' not found in configuration")
        return False
    
    config = WATCHLIST_CONFIGS[source_name]
    file_type = config.get("file_type", "")
    parser = get_parser(file_type)
    
    if not parser:
        print_error(f"No parser available for file type: {file_type}")
        return False
    
    input_path = Path(input_file)
    if not input_path.exists():
        print_error(f"Input file not found: {input_path}")
        return False
    
    print_info(f"Input: {input_path}")
    print_info(f"File type: {file_type}")
    print_info(f"Parser: {parser.__class__.__name__}")
    
    try:
        start_time = time.perf_counter()
        
        records = list(parser.parse(
            file_path=str(input_path),
            config=config
        ))
        
        elapsed = time.perf_counter() - start_time
        
        print_info(f"Parsed {len(records)} records in {elapsed:.2f}s")
        
        if records:
            output_file = RAW_DIR / f"{source_name}_RAW.jsonl"
            with open(output_file, "w") as f:
                for record in records:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
            
            print_success(f"Output saved to: {output_file}")
            
            # Show first record
            print_subheader("First Record Preview")
            preview = records[0]
            for key, value in list(preview.items())[:8]:
                if isinstance(value, (dict, list)):
                    print(f"  {key}: {type(value).__name__} ({len(value)} items)")
                else:
                    val_str = str(value)[:60]
                    print(f"  {key}: {val_str}")
            if len(records) > 1:
                print(f"  ... and {len(records) - 1} more records")
        
        return True
        
    except Exception as e:
        print_error(f"Parse failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_full_pipeline(source_name, input_file=None, skip_download=False):
    """
    Test the full pipeline for a single source
    """
    print_header(f"FULL PIPELINE: {source_name}")
    
    if source_name not in WATCHLIST_CONFIGS:
        print_error(f"Source '{source_name}' not found in configuration")
        available = ", ".join(get_available_sources())
        print_info(f"Available sources: {available}")
        return False
    
    config = WATCHLIST_CONFIGS[source_name].copy()
    
    # Handle input file
    if input_file:
        input_path = Path(input_file)
        if not input_path.exists():
            print_error(f"Input file not found: {input_path}")
            return False
        config["local_path"] = str(input_path)
        skip_download = True
    
    if skip_download and "local_path" not in config:
        print_error("Skip download requested but no local_path provided")
        return False
    
    print_info(f"Source: {source_name}")
    print_info(f"URL: {config.get('url', 'N/A')}")
    print_info(f"File type: {config.get('file_type', 'N/A')}")
    
    if skip_download:
        print_info(f"Mode: Using local file: {config['local_path']}")
    else:
        print_info(f"Mode: Download from URL")
    
    # Load rules
    try:
        print_info("Loading rules...")
        prenorm_df = pd.read_excel(RULES_DIR / "preNormalization.xlsx")
        source_config_df = pd.read_excel(RULES_DIR / "sourceConfig.xlsx")
        post_rules_df = pd.read_excel(RULES_DIR / "postNormalization.xlsx")
        
        mapping_rules = load_rules(
            mapping_file=RULES_DIR / "mapping.xlsx",
            source_name=config.get("list_name", config["source_name"]),
        )
        print_success(f"Loaded {len(mapping_rules)} mapping rules")
        
    except Exception as e:
        print_error(f"Failed to load rules: {e}")
        return False
    
    # Initialize engines
    pre_normalizer = PreNormalizationEngine(prenorm_df, source_config_df)
    mapper = MappingEngine(mapping_rules)
    post_normalizer = PostNormalizationEngine(post_rules_df, config)
    
    # Create and run pipeline
    pipeline = WatchlistPipeline(
        config=config,
        downloader=downloader,
        pre_normalizer=pre_normalizer,
        mapper=mapper,
        post_normalizer=post_normalizer,
    )
    
    try:
        start_time = time.perf_counter()
        
        pipeline.run()
        
        elapsed = time.perf_counter() - start_time
        
        list_name = config.get("list_name", source_name)
        final_file = FINAL_DIR / f"{list_name}_final.jsonl"
        
        print_success(f"Pipeline completed in {elapsed:.2f}s")
        
        if final_file.exists():
            record_count = count_records(final_file)
            file_size = get_file_size_mb(final_file)
            
            print_subheader("Output Summary")
            print(f"  Output file: {final_file}")
            print(f"  Records: {record_count}")
            print(f"  File size: {file_size:.2f} MB")
            
            if record_count > 0:
                preview = preview_record(final_file)
                if preview:
                    print_subheader("First Record Preview")
                    for key, value in list(preview.items())[:8]:
                        if isinstance(value, (dict, list)):
                            print(f"  {key}: {type(value).__name__} ({len(value)} items)")
                        else:
                            val_str = str(value)[:60]
                            print(f"  {key}: {val_str}")
                    if len(preview) > 8:
                        print(f"  ... and {len(preview) - 8} more fields")
            
            return True
        else:
            print_error(f"Output file not found: {final_file}")
            return False
            
    except Exception as e:
        print_error(f"Pipeline failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_mapping_only(source_name, input_file):
    """Test only the mapping step on an existing raw JSONL file"""
    print_header(f"MAPPING ONLY: {source_name}")
    
    if source_name not in WATCHLIST_CONFIGS:
        print_error(f"Source '{source_name}' not found")
        return False
    
    config = WATCHLIST_CONFIGS[source_name]
    input_path = Path(input_file)
    
    if not input_path.exists():
        print_error(f"Input file not found: {input_path}")
        return False
    
    print_info(f"Input: {input_path}")
    
    # Load mapping rules
    try:
        mapping_rules = load_rules(
            mapping_file=RULES_DIR / "mapping.xlsx",
            source_name=config.get("list_name", config["source_name"]),
        )
        print_success(f"Loaded {len(mapping_rules)} mapping rules")
        
        # Show sample rules
        print_subheader("Sample Mapping Rules")
        for rule in mapping_rules[:5]:
            if rule.source_type:
                print(f"  {rule.entity_type} -> {rule.target_path}")
                print(f"    [{rule.source_type}]: {rule.source_value}")
        if len(mapping_rules) > 5:
            print(f"  ... and {len(mapping_rules) - 5} more rules")
            
    except Exception as e:
        print_error(f"Failed to load mapping rules: {e}")
        return False
    
    # Initialize mapper
    mapper = MappingEngine(mapping_rules)
    
    # Process records
    try:
        start_time = time.perf_counter()
        
        mapped_records = []
        raw_count = 0
        
        # Use utf-8 encoding for reading
        with open(input_path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                raw_count += 1
                raw = json.loads(line)
                mapped = mapper.map_record(raw)
                mapped_records.append(mapped)
        
        elapsed = time.perf_counter() - start_time
        
        print_success(f"Processed {raw_count} records in {elapsed:.2f}s")
        
        if mapped_records:
            output_file = FINAL_DIR / f"{source_name}_mapped_only.jsonl"
            # Use utf-8 encoding for writing
            with open(output_file, "w", encoding="utf-8") as f:
                for record in mapped_records:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
            
            print_success(f"Output saved to: {output_file}")
            
            # Show first record
            print_subheader("First Mapped Record Preview")
            preview = mapped_records[0]
            for key, value in list(preview.items())[:8]:
                if isinstance(value, (dict, list)):
                    print(f"  {key}: {type(value).__name__} ({len(value)} items)")
                else:
                    val_str = str(value)[:60]
                    print(f"  {key}: {val_str}")
            if len(mapped_records) > 1:
                print(f"  ... and {len(mapped_records) - 1} more records")
        
        return True
        
    except Exception as e:
        print_error(f"Mapping failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def list_available_files(directory=DOWNLOAD_DIR):
    """List available files in directory"""
    files = []
    for ext in ["*.xml", "*.XML", "*.xlsx", "*.xls", "*.csv", "*.pdf", "*.html"]:
        files.extend(directory.glob(ext))
    
    return sorted(set(files), key=lambda x: x.name.lower())


# ============================================================================
# INTERACTIVE MENU
# ============================================================================

def main_menu():
    """Main interactive menu"""
    
    print_header("WATCHLIST PIPELINE TEST HARNESS")
    
    print_info(f"Project root: {PROJECT_ROOT}")
    print_info(f"Data directory: {DATA_DIR}")
    
    # Show available sources
    sources = get_available_sources()
    print_subheader("Available Sources")
    for i, source in enumerate(sources, 1):
        config = WATCHLIST_CONFIGS[source]
        file_type = config.get("file_type", "unknown")
        print(f"  {i:2d}. {source:<30} ({file_type})")
    
    # Show available files
    files = list_available_files()
    if files:
        print_subheader("Available Data Files")
        for file_path in files:
            size_mb = get_file_size_mb(file_path)
            detected = detect_source_for_file(file_path.name)
            source_str = f"-> {detected}" if detected else "-> unknown"
            print(f"  * {file_path.name:<45} ({size_mb:.2f} MB) {source_str}")
    
    # Menu options
    while True:
        print_subheader("Options")
        print("  [1] Run full pipeline for a specific source")
        print("  [2] Run full pipeline using a specific file")
        print("  [3] Parse only (no normalization/mapping)")
        print("  [4] Test mapping only (requires raw JSONL)")
        print("  [5] Run pipeline for all XML sources")
        print("  [0] Exit")
        
        try:
            choice = input("\nEnter selection: ").strip()
            
            if choice == "0":
                print_info("Exiting.")
                sys.exit(0)
            
            elif choice == "1":
                # Run full pipeline for specific source
                print_subheader("Available Sources")
                for i, source in enumerate(sources, 1):
                    print(f"  {i}. {source}")
                
                try:
                    idx = int(input("\nSelect source number: ")) - 1
                    if 0 <= idx < len(sources):
                        source = sources[idx]
                        skip = input("Skip download? (y/n): ").strip().lower() == "y"
                        test_full_pipeline(source, skip_download=skip)
                    else:
                        print_error("Invalid selection")
                except ValueError:
                    print_error("Please enter a valid number")
            
            elif choice == "2":
                # Run full pipeline using specific file
                files = list_available_files()
                if not files:
                    print_error("No files found in downloads directory")
                    continue
                
                print_subheader("Available Files")
                for i, file_path in enumerate(files, 1):
                    detected = detect_source_for_file(file_path.name)
                    source_str = f" (detected: {detected})" if detected else " (unknown source)"
                    print(f"  {i}. {file_path.name}{source_str}")
                
                try:
                    idx = int(input("\nSelect file number: ")) - 1
                    if 0 <= idx < len(files):
                        file_path = files[idx]
                        detected = detect_source_for_file(file_path.name)
                        
                        if detected:
                            test_full_pipeline(detected, input_file=file_path, skip_download=True)
                        else:
                            print_warning("Source could not be auto-detected")
                            print_info("Available sources: " + ", ".join(sources))
                            source = input("Enter source name: ").strip()
                            if source in sources:
                                test_full_pipeline(source, input_file=file_path, skip_download=True)
                            else:
                                print_error(f"Source '{source}' not found")
                    else:
                        print_error("Invalid selection")
                except ValueError:
                    print_error("Please enter a valid number")
            
            elif choice == "3":
                # Parse only
                files = list_available_files()
                if not files:
                    print_error("No files found in downloads directory")
                    continue
                
                print_subheader("Available Files")
                for i, file_path in enumerate(files, 1):
                    print(f"  {i}. {file_path.name}")
                
                try:
                    idx = int(input("\nSelect file number: ")) - 1
                    if 0 <= idx < len(files):
                        file_path = files[idx]
                        detected = detect_source_for_file(file_path.name)
                        
                        if detected:
                            test_parse_only(detected, file_path)
                        else:
                            print_warning("Source could not be auto-detected")
                            source = input("Enter source name: ").strip()
                            if source in sources:
                                test_parse_only(source, file_path)
                            else:
                                print_error(f"Source '{source}' not found")
                    else:
                        print_error("Invalid selection")
                except ValueError:
                    print_error("Please enter a valid number")
            
            elif choice == "4":
                # Mapping only
                raw_files = list(RAW_DIR.glob("*.jsonl"))
                if not raw_files:
                    print_error("No raw JSONL files found in data/raw/")
                    continue
                
                print_subheader("Available Raw Files")
                for i, file_path in enumerate(raw_files, 1):
                    print(f"  {i}. {file_path.name}")
                
                try:
                    idx = int(input("\nSelect file number: ")) - 1
                    if 0 <= idx < len(raw_files):
                        file_path = raw_files[idx]
                        # Extract source name from filename
                        source_name = file_path.stem.replace("_RAW", "")
                        if source_name in sources:
                            test_mapping_only(source_name, file_path)
                        else:
                            print_warning(f"Could not determine source from: {file_path.name}")
                            source = input("Enter source name: ").strip()
                            if source in sources:
                                test_mapping_only(source, file_path)
                            else:
                                print_error(f"Source '{source}' not found")
                    else:
                        print_error("Invalid selection")
                except ValueError:
                    print_error("Please enter a valid number")
            
            elif choice == "5":
                # Run all XML sources
                xml_sources = get_source_by_file_type("xml")
                if not xml_sources:
                    print_error("No XML sources found")
                    continue
                
                print_subheader("Running Pipeline for XML Sources")
                results = {}
                
                for source in xml_sources:
                    print(f"\nProcessing: {source}")
                    success = test_full_pipeline(source, skip_download=False)
                    results[source] = "PASSED" if success else "FAILED"
                
                print_subheader("Results Summary")
                for source, status in results.items():
                    print(f"  {source:<30} {status}")
            
            else:
                print_error("Invalid selection. Please enter 0, 1, 2, 3, 4, or 5.")
                
        except KeyboardInterrupt:
            print("\n")
            print_info("Exiting.")
            sys.exit(0)
        except Exception as e:
            print_error(f"Unexpected error: {e}")
            import traceback
            traceback.print_exc()


# ============================================================================
# COMMAND LINE INTERFACE
# ============================================================================

def main():
    """Main entry point with CLI support"""
    
    # Check for command line arguments
    if len(sys.argv) > 1:
        parser = argparse.ArgumentParser(
            description="Test the watchlist pipeline"
        )
        parser.add_argument(
            "source",
            nargs="?",
            help="Source name to test"
        )
        parser.add_argument(
            "--file",
            help="Input file path (overrides download)"
        )
        parser.add_argument(
            "--skip-download",
            action="store_true",
            help="Skip download and use local file"
        )
        parser.add_argument(
            "--parse-only",
            action="store_true",
            help="Only parse, skip normalization and mapping"
        )
        parser.add_argument(
            "--mapping-only",
            action="store_true",
            help="Only test mapping (requires raw JSONL)"
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Test all sources"
        )
        parser.add_argument(
            "--list",
            action="store_true",
            help="List available sources"
        )
        
        args = parser.parse_args()
        
        if args.list:
            sources = get_available_sources()
            print("Available sources:")
            for source in sources:
                config = WATCHLIST_CONFIGS[source]
                print(f"  {source} ({config.get('file_type', 'unknown')})")
            sys.exit(0)
        
        if args.all:
            print_header("Testing All Sources")
            for source in get_available_sources():
                print(f"\n--- {source} ---")
                if args.parse_only:
                    # Would need file for parse only
                    print_warning("Parse-only mode requires --file")
                else:
                    test_full_pipeline(source, skip_download=args.skip_download)
            sys.exit(0)
        
        if args.source:
            if args.parse_only:
                if not args.file:
                    print_error("--parse-only requires --file")
                    sys.exit(1)
                test_parse_only(args.source, args.file)
            elif args.mapping_only:
                if not args.file:
                    print_error("--mapping-only requires --file")
                    sys.exit(1)
                test_mapping_only(args.source, args.file)
            else:
                test_full_pipeline(args.source, args.file, args.skip_download)
            sys.exit(0)
    
    # Interactive mode
    main_menu()


if __name__ == "__main__":
    main()