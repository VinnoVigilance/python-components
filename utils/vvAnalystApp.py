import streamlit as st
import json
import tempfile
import os
import base64
from pathlib import Path
import sys
from typing import Any

# Add project root to path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

from valueExplorer import extract_values
from parsing.xmlParser import XmlParser
from parsing.tabularParser import TabularParser
from schemaExtractor import extract_schema, schema_to_rows

# Import watchlist configs
from pipelines.watchlistConfigsOld import WATCHLIST_CONFIGS

DATA_DIR = ROOT_DIR / "data"
SEC_DATA_DIR = DATA_DIR / "sec"


# ============================================================================
# PAGE CONFIGURATION
# ============================================================================

st.set_page_config(
    page_title="VPAT",
    layout="wide"
)

st.markdown(
    """
    <style>
        h1 {font-size: 26px !important; margin-bottom: 4px !important;}
        h2 {font-size: 21px !important;}
        h3 {font-size: 17px !important;}
        .stCaption, .stMarkdown, label, p, div {
            font-size: 13px;
        }
        .stButton button {
            padding: 4px 8px;
            min-height: 30px;
            font-size: 14px;
        }
        .stSelectbox div, .stTextInput input, .stTextArea textarea {
            font-size: 13px;
        }
        section[data-testid="stSidebar"] {
            width: 250px !important;
        }
        .block-container {
            padding-top: 1.2rem;
            padding-bottom: 1rem;
        }
    </style>
    """,
    unsafe_allow_html=True
)

st.title("VV Production Analyst Tools - VPAT")
st.caption("Production helper tools for analysts")


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def ensure_sec_directories(base_dir: Path = SEC_DATA_DIR) -> None:
    for sub_dir in [
        "pdf/raw",
        "json/extracted",
        "json/approved",
        "logs"
    ]:
        (base_dir / sub_dir).mkdir(parents=True, exist_ok=True)


def load_json_file(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json_file(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def show_pdf(path: Path) -> None:
    with open(path, "rb") as f:
        pdf_bytes = f.read()
    base64_pdf = base64.b64encode(pdf_bytes).decode("utf-8")
    st.markdown(
        f"""
        <iframe
            src="data:application/pdf;base64,{base64_pdf}"
            width="100%"
            height="850"
            type="application/pdf">
        </iframe>
        """,
        unsafe_allow_html=True
    )


def display_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False)
        return "—" if text.strip() in ["{}", "[]"] else text
    if value is None:
        return "—"
    text = str(value).strip()
    return "—" if text == "" else text


def edit_value(original_value: Any, edited_text: str) -> Any:
    if isinstance(original_value, (dict, list)):
        return json.loads(edited_text)
    if original_value is None:
        return None if edited_text.strip() == "" else edited_text
    if isinstance(original_value, bool):
        return edited_text.lower() in ["true", "1", "yes"]
    if isinstance(original_value, int):
        try:
            return int(edited_text)
        except ValueError:
            return edited_text
    if isinstance(original_value, float):
        try:
            return float(edited_text)
        except ValueError:
            return edited_text
    return edited_text


def normalize_entities(data: Any) -> list[dict]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    return []


def get_entity_display_name(entity: dict, index: int) -> str:
    names = entity.get("Names", [])
    if isinstance(names, list) and names:
        first_name = names[0]
        if isinstance(first_name, dict):
            name = first_name.get("Name", "")
            if name:
                return f"{index + 1}. {name}"
    return f"{index + 1}. Entity {index + 1}"


def parse_root_tags(input_value: str) -> list:
    """
    Parse root tags from comma-separated input.
    Returns a list of trimmed tags.
    """
    if not input_value or not input_value.strip():
        return ["Designation"]
    
    # Split by comma and clean each tag
    tags = [tag.strip() for tag in input_value.split(",") if tag.strip()]
    return tags if tags else ["Designation"]

def get_xml_sources():
    return {
        name: config
        for name, config in WATCHLIST_CONFIGS.items()
        if config.get("file_type") == "xml"
    }


def detect_source_for_file(filename: str, xml_sources: dict):
    # Try exact match
    for source_name in xml_sources.keys():
        if source_name in filename.upper():
            return source_name, xml_sources[source_name]

    # Pattern-based matching
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

    return None, {}

def normalize_dataframe_data(records: list) -> list:
    """
    Normalize records to ensure all values of the same column have consistent types.
    Converts list values to strings for display.
    """
    normalized = []
    for record in records:
        normalized_record = {}
        for key, value in record.items():
            if isinstance(value, list):
                # Convert list to JSON string for display
                normalized_record[key] = json.dumps(value, ensure_ascii=False)
            elif isinstance(value, dict):
                # Convert dict to JSON string for display
                normalized_record[key] = json.dumps(value, ensure_ascii=False)
            elif value is None:
                normalized_record[key] = ""
            else:
                normalized_record[key] = value
        normalized.append(normalized_record)
    return normalized

# ============================================================================
# SIDEBAR - TOOL SELECTION
# ============================================================================

tool = st.sidebar.selectbox(
    "Select Tool",
    [
        "Source File Parser",
        "Schema Explorer",
        "Value Explorer",
        "SEC PDF JSON Review"
    ]
)


# ============================================================================
# TOOL 1: SOURCE FILE PARSER
# ============================================================================

if tool == "Source File Parser":

    st.header("Source File Parser")
    st.caption("Convert source files such as XML, Excel or CSV into raw JSONL records")

    uploaded_file = st.file_uploader(
        "Upload source file",
        type=["xml", "xlsx", "xls", "csv"]
    )

    if uploaded_file:
        suffix = os.path.splitext(uploaded_file.name)[1].lower()
        st.info(f"Selected file: {uploaded_file.name}")

        file_type = st.selectbox(
            "Source file type",
            ["Auto Detect", "XML", "Excel / CSV"]
        )

        xml_sources = get_xml_sources()
        _, config = detect_source_for_file(uploaded_file.name, xml_sources)

        if config is None:
            config = {}

        # ====================================================================
        # XML CONFIGURATION - SIMPLE ROOT TAGS INPUT
        # ====================================================================
        
        if file_type == "XML" or suffix == ".xml":
            
            st.subheader("XML Configuration")
            
            root_tags_input = st.text_input(
                "Root Tags (Optional - leave empty to use watchlist config/default)",
                value="",
                help="Example: Designation, sanctionEntity, entity"
            )

            root_tags = (
                parse_root_tags(root_tags_input)
                if root_tags_input.strip()
                else None
            )

        # ====================================================================
        # EXCEL / CSV CONFIGURATION
        # ====================================================================
        
        if file_type == "Excel / CSV" or suffix in [".xlsx", ".xls", ".csv"]:
            sheet_name = st.text_input(
                "Sheet Name / Index",
                value="0"
            )
            try:
                config["sheet_name"] = int(sheet_name)
            except ValueError:
                config["sheet_name"] = sheet_name

        # ====================================================================
        # PARSE BUTTON
        # ====================================================================
        
        if st.button("Parse File"):

            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(uploaded_file.getbuffer())
                input_path = tmp.name

            try:
                records = []

                if file_type == "Auto Detect":
                    if suffix == ".xml":
                        parser = XmlParser()
                        records = list(
                            parser.parse(
                                input_path,
                                config=config,
                                root_tags=root_tags
                            )
                        )
                    elif suffix in [".xlsx", ".xls", ".csv"]:
                        parser = TabularParser(output_dir="output")
                        records = list(parser.parse(input_path, config))
                    else:
                        st.error(f"Unsupported file type: {suffix}")

                elif file_type == "XML":
                    parser = XmlParser()
                    records = list(
                        parser.parse(
                            input_path,
                            config=config,
                            root_tags=root_tags
                        )
                    )

                elif file_type == "Excel / CSV":
                    parser = TabularParser(output_dir="output")
                    records = list(parser.parse(input_path, config))

                if records:
                    st.success(f"Parsed {len(records)} records successfully.")

                    # Normalize the records for display
                    normalized_records = normalize_dataframe_data(records[:50])
                    st.dataframe(
                        normalized_records,
                        width="stretch"
                    )

                    jsonl_output = "\n".join(
                        json.dumps(record, ensure_ascii=False)
                        for record in records
                    )

                    st.download_button(
                        label="Download JSONL",
                        data=jsonl_output,
                        file_name=f"{Path(uploaded_file.name).stem}_raw.jsonl",
                        mime="application/jsonl"
                    )
                else:
                    st.warning("No records found.")

            except Exception as e:
                st.error(f"Error while parsing file: {e}")


# ============================================================================
# TOOL 2: SCHEMA EXPLORER
# ============================================================================

elif tool == "Schema Explorer":

    st.header("Schema Explorer")
    st.caption("Extract schema fields from a JSONL file")

    uploaded_file = st.file_uploader(
        "Upload JSONL file",
        type=["jsonl"]
    )

    if uploaded_file and st.button("Extract Schema"):

        with tempfile.NamedTemporaryFile(delete=False, suffix=".jsonl") as tmp:
            tmp.write(uploaded_file.getbuffer())
            input_path = tmp.name

        try:
            final_schema, total_records, errors = extract_schema(input_path)
            schema_rows = schema_to_rows(final_schema)

            st.success(
                f"Schema extracted successfully. Records: {total_records}, Fields: {len(schema_rows)}"
            )

            st.dataframe(
                schema_rows,
                width="stretch"
            )

            schema_json = json.dumps(
                final_schema,
                ensure_ascii=False,
                indent=4
            )

            st.download_button(
                label="Download Schema JSON",
                data=schema_json,
                file_name=f"{Path(uploaded_file.name).stem}_schema.json",
                mime="application/json"
            )

            if errors:
                with st.expander("Errors"):
                    st.write(errors)

        except Exception as e:
            st.error(f"Error while extracting schema: {e}")


# ============================================================================
# TOOL 3: VALUE EXPLORER
# ============================================================================

elif tool == "Value Explorer":

    st.header("Value Explorer")
    st.caption("Extract unique values from a JSONL file by JSON path")

    uploaded_file = st.file_uploader(
        "Upload JSONL file",
        type=["jsonl"]
    )

    json_path = st.text_input(
        "JSON Path",
        placeholder="Example: names.name"
    )

    if uploaded_file and json_path:
        path_parts = json_path.split(".")
        unique_values = set()
        errors = []

        for i, line in enumerate(uploaded_file):
            try:
                data = json.loads(line.decode("utf-8"))
                values = extract_values(data, path_parts)

                for value in values:
                    if value is None:
                        continue
                    if isinstance(value, (dict, list)):
                        continue
                    clean_value = str(value).strip()
                    if clean_value:
                        unique_values.add(clean_value)

            except Exception as e:
                errors.append(f"Line {i + 1}: {e}")

        sorted_values = sorted(unique_values)

        st.success(f"Found {len(sorted_values)} unique values.")

        st.dataframe(
            [{"value": value} for value in sorted_values],
            width="stretch"
        )

        st.download_button(
            label="Download TXT",
            data="\n".join(sorted_values),
            file_name="unique_values.txt",
            mime="text/plain"
        )

        if errors:
            with st.expander("Errors"):
                st.write(errors)


# ============================================================================
# TOOL 4: SEC PDF JSON REVIEW
# ============================================================================

elif tool == "SEC PDF JSON Review":

    st.header("SEC PDF JSON Review")
    st.caption("Review extracted SEC advisory JSON entity by entity and save approved JSON")

    ensure_sec_directories()

    pdf_root_dir = SEC_DATA_DIR / "pdf" / "raw"
    extracted_root_dir = SEC_DATA_DIR / "json" / "extracted"
    approved_root_dir = SEC_DATA_DIR / "json" / "approved"

    year_folders = sorted(
        [p for p in pdf_root_dir.iterdir() if p.is_dir()],
        key=lambda p: int(p.name),
        reverse=True
    )

    if not year_folders:
        st.warning(f"No year folders found in: {pdf_root_dir}")

    else:
        selected_year = st.selectbox(
            "Select Year",
            [p.name for p in year_folders]
        )

        selected_year_pdf_dir = pdf_root_dir / selected_year
        selected_year_extracted_dir = extracted_root_dir / selected_year
        selected_year_approved_dir = approved_root_dir / selected_year

        pdf_files = sorted(
            selected_year_pdf_dir.glob("*.pdf"),
            key=lambda p: p.name.lower()
        )

        if not pdf_files:
            st.warning(f"No PDF files found for year: {selected_year}")

        else:
            pdf_options = {}

            for pdf_path in pdf_files:
                extracted_json_path = selected_year_extracted_dir / f"{pdf_path.stem}.json"
                approved_json_path = selected_year_approved_dir / f"{pdf_path.stem}.json"

                if approved_json_path.exists():
                    label = f"[Approved] {pdf_path.name}"
                elif extracted_json_path.exists():
                    label = f"[Extracted] {pdf_path.name}"
                else:
                    label = f"[New] {pdf_path.name}"

                pdf_options[label] = pdf_path

            selected_pdf_label = st.selectbox(
                "Select PDF",
                list(pdf_options.keys())
            )

            selected_pdf_path = pdf_options[selected_pdf_label]

            extracted_json_path = (
                selected_year_extracted_dir
                / f"{selected_pdf_path.stem}.json"
            )

            approved_json_path = (
                selected_year_approved_dir
                / f"{selected_pdf_path.stem}.json"
            )

            if approved_json_path.exists():
                st.success("This PDF already has approved JSON.")

            if not extracted_json_path.exists():
                st.error("Extracted JSON not found for this PDF.")
                st.code(str(extracted_json_path), language="text")

            else:
                original_json = load_json_file(extracted_json_path)
                entities = normalize_entities(original_json)

                if not entities:
                    st.error("Extracted JSON is not a valid object or array.")
                    st.stop()

                pdf_key = f"{selected_year}_{selected_pdf_path.stem}"

                values_key = f"{pdf_key}_values"
                approved_key = f"{pdf_key}_approved"
                editing_key = f"{pdf_key}_editing"

                if values_key not in st.session_state:
                    st.session_state[values_key] = entities

                if approved_key not in st.session_state:
                    st.session_state[approved_key] = {}

                if editing_key not in st.session_state:
                    st.session_state[editing_key] = {}

                current_entities = st.session_state[values_key]

                st.markdown("---")

                entity_labels = [
                    get_entity_display_name(entity, i)
                    for i, entity in enumerate(current_entities)
                ]

                selected_entity_index = st.selectbox(
                    "Select Entity",
                    list(range(len(current_entities))),
                    format_func=lambda i: entity_labels[i]
                )

                current_json = current_entities[selected_entity_index]

                st.caption(
                    f"Entity {selected_entity_index + 1} of {len(current_entities)}"
                )

                if not isinstance(current_json, dict):
                    st.error("Selected entity is not a valid JSON object.")
                    st.stop()

                left_col, right_col = st.columns([1.15, 1])

                with left_col:
                    st.subheader("PDF Preview")
                    show_pdf(selected_pdf_path)

                with right_col:
                    st.subheader("Field Review")

                    entity_key = f"{pdf_key}_entity_{selected_entity_index}"

                    with st.container(height=850):

                        for field_name, field_value in current_json.items():

                            field_key = f"{entity_key}_{field_name}"

                            approved = st.session_state[approved_key].get(
                                field_key,
                                False
                            )

                            editing = st.session_state[editing_key].get(
                                field_key,
                                False
                            )

                            bg_color = "#e9f7ef" if approved else "#ffffff"
                            border_color = "#94d3a2" if approved else "#dddddd"
                            status = "Approved" if approved else "Pending"

                            row_col, edit_col, approve_col = st.columns(
                                [10, 0.75, 0.75],
                                vertical_alignment="center"
                            )

                            with row_col:
                                st.markdown(
                                    f"""
                                    <div style="
                                        background-color:{bg_color};
                                        border:1px solid {border_color};
                                        border-radius:7px;
                                        padding:7px 9px;
                                        margin-bottom:2px;
                                    ">
                                        <div style="
                                            font-size:11px;
                                            color:#777;
                                            margin-bottom:2px;
                                        ">
                                            {status}
                                        </div>
                                        <div style="
                                            font-size:13px;
                                            line-height:1.45;
                                        ">
                                            <b>{field_name}</b>:
                                            <span style="
                                                font-family:monospace;
                                                color:#444;
                                                font-size:12px;
                                            ">
                                                {display_value(field_value)}
                                            </span>
                                        </div>
                                    </div>
                                    """,
                                    unsafe_allow_html=True
                                )

                            with edit_col:
                                if st.button(
                                    "✏️",
                                    key=f"edit_{field_key}",
                                    width="stretch"
                                ):
                                    st.session_state[editing_key][field_key] = True
                                    st.session_state[approved_key][field_key] = False
                                    st.rerun()

                            with approve_col:
                                if st.button(
                                    "✅",
                                    key=f"approve_{field_key}",
                                    width="stretch"
                                ):
                                    st.session_state[approved_key][field_key] = True
                                    st.session_state[editing_key][field_key] = False
                                    st.rerun()

                            if editing:
                                if isinstance(field_value, (dict, list)):
                                    edited_text = st.text_area(
                                        "Edit value",
                                        value=json.dumps(
                                            field_value,
                                            ensure_ascii=False,
                                            indent=4
                                        ),
                                        key=f"edit_input_{field_key}",
                                        label_visibility="collapsed"
                                    )
                                else:
                                    edited_text = st.text_input(
                                        "Edit value",
                                        value="" if field_value is None else str(field_value),
                                        key=f"edit_input_{field_key}",
                                        label_visibility="collapsed"
                                    )

                                save_col, cancel_col = st.columns([1, 1])

                                with save_col:
                                    if st.button(
                                        "Save",
                                        key=f"save_{field_key}",
                                        width="stretch"
                                    ):
                                        try:
                                            new_value = edit_value(
                                                field_value,
                                                edited_text
                                            )

                                            current_entities[selected_entity_index][field_name] = new_value
                                            st.session_state[values_key] = current_entities
                                            st.session_state[editing_key][field_key] = False
                                            st.session_state[approved_key][field_key] = False
                                            st.rerun()

                                        except Exception as e:
                                            st.error(f"Invalid value: {e}")

                                with cancel_col:
                                    if st.button(
                                        "Cancel",
                                        key=f"cancel_{field_key}",
                                        width="stretch"
                                    ):
                                        st.session_state[editing_key][field_key] = False
                                        st.rerun()

                            st.markdown(
                                "<div style='height:4px;'></div>",
                                unsafe_allow_html=True
                            )

                    analyst_name = st.text_input(
                        "Analyst Name",
                        value="",
                        key=f"analyst_{pdf_key}"
                    )

                    review_note = st.text_area(
                        "Review Note",
                        value="",
                        height=70,
                        key=f"note_{pdf_key}"
                    )

                    final_col1, final_col2 = st.columns(2)

                    with final_col1:
                        if st.button(
                            "Save Final Approved JSON",
                            type="primary",
                            key=f"final_approve_{pdf_key}",
                            width="stretch"
                        ):
                            save_json_file(
                                approved_json_path,
                                current_entities
                            )

                            st.success("Final approved JSON saved successfully.")
                            st.code(str(approved_json_path), language="text")

                    with final_col2:
                        st.download_button(
                            label="Download Current Edited JSON",
                            data=json.dumps(
                                current_entities,
                                ensure_ascii=False,
                                indent=4
                            ),
                            file_name=f"{selected_pdf_path.stem}.json",
                            mime="application/json",
                            width="stretch"
                        )