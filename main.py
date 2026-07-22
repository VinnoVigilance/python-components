from ingestion.downloader.models import DownloadTask
from ingestion.downloader.interface import download
# from parsing.xmlParser import XMLIngestor
from transforms.postNormalization import run_post_normalization, load_jsonl, save_jsonl
from transforms.preNormalization import run_pre_normalization
from utils.valueExplorer import extract_unique_values
# from parsing.xmlParser import run_xml_ingestion
from transforms.fieldMapper import run_mapping
from parsing.pdfParser import PdfParser
import pandas as pd
import json
import re

def test_downloader():
    tasks_data = [
        {
            "url": "https://www.dfat.gov.au/sites/default/files/Australian_Sanctions_Consolidated_List.xlsx",
            "list_name": "sanctions"
        },
        {
            "url": "https://sanctionslist.fcdo.gov.uk/docs/UK-Sanctions-List.xml",
            "list_name": "UKSL_List"
        },
        {
            "url": "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN_ENHANCED.XML",
            "list_name": "OFAC_SDN_List"
        },
        {
            "url": "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/CONS_ENHANCED.XML",
            "list_name": "OFAC_NON-SDN_List"
        },
        {
            "url": "https://www.international.gc.ca/world-monde/assets/office_docs/international_relations-relations_internationales/sanctions/sema-lmes.xml",
            "list_name": "Canadian_Global_Affairs_Economic_Sanctions_List"
        },
        {
            "url": "https://scsanctions.un.org/resources/xml/en/consolidated.xml",
            "list_name": "UN_Consolidated_Sanctions_List"
        }

    ]

    for item in tasks_data:
        task = DownloadTask(**item)
        path = download(task)
        print(path)





def test_xml_ingestor():

    ingestor = XMLIngestor(
        output_dir="test_output"
    )

    xml_file = r"C:\VV_Python_Project\data\downloads\UN_Consolidated_Sanctions_List_2026-05-01_17-55-04_consolidated.xml"

    output_path = ingestor.process_file(
        xml_file
    )

    print("\n====================")
    print("XML INGESTOR TEST")
    print("====================")
    print(f"Input : {xml_file}")
    print(f"Output: {output_path}")
    print("====================\n")

def test_post_normalization():

    input_path = "/Users/mac/Desktop/VV_Python_Project/mapped_OFAC-SDN.jsonl"
    output_path = "finall_ofac_sdn.jsonl"
    rules_path = "/Users/mac/Desktop/VV_Python_Project/postNormalization.xlsx"

    jsonl_data = load_jsonl(input_path)
    rules_df = pd.read_excel(rules_path)
    result = run_post_normalization(jsonl_data, rules_df, {"date_order": "DMY"})

    save_jsonl(result, output_path)

    print("DONE")
    print(f"Input : {input_path}")
    print(f"Output: {output_path}")

def test_value_explorer():
    
    extract_unique_values(
    input_jsonl="/Users/mac/Desktop/VV_Python_Project/data/final/UKSL_final.jsonl",
    json_path="entity_type",
    output_txt="dates.txt"
)
    
def test_pdf_parser():
    PdfParser().convert(
    input_file=r"/Users/mac/Desktop/VV_Python_Project/DNFBPs28Feb26.pdf",
    output_file="DNFBPsRAW.jsonl",
    output_type="jsonl"
)

def test_pre_normalization():
    output_path = run_pre_normalization(
        source="OFAC-SDN",
        input_jsonl_path="/Users/mac/Desktop/VV_Python_Project/offac_sdn_attr.jsonl",
        output_jsonl_path="preNormalized_OFAC_SDN.jsonl",
        prenormalization_path="/Users/mac/Desktop/VV_Python_Project/preNormalization.xlsx",
        source_config_path="/Users/mac/Desktop/VV_Python_Project/sourceConfig.xlsx",
    )

    print("Pre-normalization completed.")
    print(f"Output: {output_path}")

def test_mapping():
    output_path = run_mapping(
        input_file="/Users/mac/Desktop/VV_Python_Project/UK-Sanctions-List_raw.jsonl",
        mapping_file="/Users/mac/Desktop/VV_Python_Project/mapping.xlsx",
        output_file="mapped_UKSL.jsonl",
        source_name="UKSL",
    )

    print("Mapping completed.")
    print(f"Output: {output_path}")
    
def test_tabular_parser():
    
    output_path = run_tabular_ingestion(
        input_file="/Users/mac/Desktop/VV_Python_Project/Australian_Sanctions_Consolidated_List.xlsx"
    )

    print("DONE")
    print(f"Output: {output_path}")
    
def test_xml_parser():
    
    output_path = run_xml_ingestion(
        xml_file="/Users/mac/Desktop/VV_Python_Project/UK-Sanctions-List.xml",
        output_file="UK-Sanctions-List_raw.jsonl",
        root_tag="Designation"
    )

    print("DONE")
    print(f"Output: {output_path}")


if __name__ == "__main__":
    # test_downloader()
    # test_xml_ingestor()
    # test_post_normalization()
    test_value_explorer()
    #test_pdf_parser()
    #test_pre_normalization()
    #test_mapping()
    # test_tabular_parser()
    #test_xml_parser()
