# pipelines/watchlist_configs.py

WATCHLIST_CONFIGS = {
    "DFAT": {
        "source_name": "DFAT",
        "date_order": "DMY",
        "list_name": "DFAT",
        "download_method": "HTTPS",
        "url": ("https://www.dfat.gov.au/sites/default/files/Australian_Sanctions_Consolidated_List.xlsx"),
        "file_type": "xlsx",
        "external_id_path": "Reference",
        "schedule": "daily",
        "versioning_strategy": "continuous",
        "preprocessing": [
            {
                "handler": "merge_dfat_split_records",
                "level": "dataset",
            },
        ],
    },

    "OFAC-SDN": {
        "source_name": "OFAC",
        "date_order": "DMY",
        "list_name": "OFAC-SDN",
        "download_method": "HTTPS",
        "url": (
            "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN_ENHANCED.XML"
        ),
        "file_type": "xml",
        "root_tags": ["entity"],
        "external_id_path": "id",
        "schedule": "daily",
        "versioning_strategy": "continuous",
    },

    "OFAC-NON-SDN": {
        "source_name": "OFAC",
        "date_order": "DMY",
        "list_name": "OFAC-NON-SDN",
        "download_method": "HTTPS",
        "versioning_strategy": "continuous",
        "url": (
            "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/CONS_ENHANCED.XML"
        ),
        "file_type": "xml",
        "root_tags": ["entity"],
        "external_id_path": "id",
        "schedule": "daily",
    },

    "UKSL": {
        "source_name": "OFSI",
        "date_order": "DMY",
        "list_name": "UKSL",
        "download_method": "HTTPS",
        "versioning_strategy": "continuous",
        "url": (
            "https://sanctionslist.fcdo.gov.uk/docs/UK-Sanctions-List.xml"
        ),
        "file_type": "xml",
        "root_tags": ["Designation"],
        "external_id_path": "UniqueID",
        "schedule": "daily",
        "filename_aliases": ["UK-Sanctions-List"],
    },


    "DNFBP": {
        "source_name": "AMLC",
        "date_order": "MDY",
        "list_name": "DNFBP",
        "download_method": "Manual",
        "external_id_path": "INSTITUTION CODE",
        "versioning_strategy": "continuous",
        "local_path": (
            "data/downloads/a3923f9e-5afc-4102-9899-8fc5a8f07f41_Registered Designated Non-Financial Businesses and Professions (DNFBPs) as of 31 March 2026.pdf"
        ),
        "url": (
            "https://www.amlc.gov.ph/storage/v1/object/public/"
            "random-uploads/documents/"
            "a3923f9e-5afc-4102-9899-8fc5a8f07f41_"
            "Registered%20Designated%20Non-Financial%20"
            "Businesses%20and%20Professions%20(DNFBPs)%20"
            "as%20of%2031%20March%202026.pdf"
        ),
        "file_type": "pdf",
        "schedule": "daily",
        "preprocessing": [
            {
                "handler": "detect_entity_type",
                "level": "record",
                "config": {
                    "input_field": "INSTITUTION NAME",
                    "output_field": "entity_type",
                },
            },
        ],
    },

    "ATC-DESIGNATED-TERRORIST-INDIVIDUALS": {
        "source_name": "ATC",
        "date_order": "DMY",
        "list_name": (
            "ATC-DESIGNATED-TERRORIST-INDIVIDUALS"
        ),
        "download_method": "Manual",
        "url": "https://atc.gov.ph/individuals/",
        "file_type": "html",
        "external_id_path": "unique_id",
        "schedule": "daily",
        "versioning_strategy": "continuous",
        "local_path": (
            "data/downloads/ATC/"
            "ATC-DESIGNATED-TERRORIST-INDIVIDUALS/"
            "year=2026/month=07/day=20/"
            "Designated Terrorist Individuals _ "
            "Anti-Terrorism Council.html"
        ),
        "profile_dir": "data/downloads/profiles",
        "attachments": [
            {
                "scope": "member",
                "attachment_type": "DOCUMENT",
                "local_path_field": (
                    "profile_data.profile_file"
                ),
                "source_url_field": "detail_url",
            },
            {
                "scope": "member",
                "attachment_type": "PHOTO",
                "local_path_field": (
                    "profile_data.local_images"
                ),
                "source_url_field": (
                    "profile_data.image_urls"
                ),
            },
        ],
        "preprocessing": [
           {
                "handler": "enrich_atc_profile_data",
                "level": "record",
                "relative_path_fields": [
                    "profile_dir",
                    "images_dir",
                ],
                "config": {
                    "profile_dir": "attachments/profiles",
                    "images_dir": "attachments/images",
                },
            },
            {
                "handler": "generate_atc_unique_id",
                "level": "record",
                "config": {
                    "name_field": "name",
                    "resolution_field": "atc_resolution_no",
                    "output_field": "unique_id",
                    "prefix": "ATC",
                },
            },
            {
                "handler": (
                    "split_atc_date_and_place_of_birth"
                ),
                "level": "record",
                "config": {
                    "input_field": (
                        "profile_data.profile_fields."
                        "Date and Place of Birth"
                    ),
                    "date_output_field": "atc_birth_date",
                    "place_output_field": "atc_birth_place",
                },
            },
            {
                "handler": "clean_atc_profile_name_fields",
                "level": "record",
                "config": {
                    "fields": [
                        "Variant/s",
                        "Alias/es",
                    ],
                },
            },
            {
                "handler": "extract_name_from_url",
                "level": "record",
                "config": {
                    "input_field": "detail_url",
                    "output_field": "profile_slug",
                },
            },
        ],
    },

    "ATC-DESIGNATED-TERRORIST-GROUPS": {
        "source_name": "ATC",
        "date_order": "DMY",
        "list_name": "ATC-DESIGNATED-TERRORIST-GROUPS",
        "download_method": "Manual",
        "versioning_strategy": "continuous",
        "url": "https://atc.gov.ph/groups/",
        "file_type": "html",
        "external_id_path": "unique_id",
        "schedule": "daily",
        "local_path": (
            "data/downloads/ATC/"
            "ATC-DESIGNATED-TERRORIST-GROUPS/"
            "year=2026/month=07/day=21/"
            "Designated Terrorist Groups _ "
            "Anti-Terrorism Council.html"
        ),
        "preprocessing": [
            {
                "handler": "generate_atc_unique_id",
                "level": "record",
                "config": {
                    "name_field": "name",
                    "resolution_field": "atc_resolution_no",
                    "output_field": "unique_id",
                    "prefix": "ATC",
                },
            },
        ],
    },

    "EU-DESIGNATED-VESSELS": {
        "source_name": "EU",
        "date_order": "DMY",
        "list_name": "EU-DESIGNATED-VESSELS",
        "download_method": "HTTPS",
        "url": (
            "https://dk9q89lxhn3e0.cloudfront.net/"
            "EU+designated+vessels+consolidated.xlsx"
        ),
        "file_type": "xlsx",
        "external_id_path": "IMO number",
        "schedule": "daily",
        "versioning_strategy": "continuous",
        "preprocessing": [
            {
                "handler": "fix_eu_vessel_multiline_rows",
                "level": "dataset",
            },
        ],
    },

    "EU-TRAVEL-BAN": {
        "source_name": "EU",
        "date_order": "DMY",
        "list_name": "EU-TRAVEL-BAN",
        "download_method": "HTTPS",
        "versioning_strategy": "continuous",
        "url": (
            "https://www.sanctionsmap.eu/"
            "api/v1/travelbans/file/101"
        ),
        "file_type": "xml",
        "external_id_path": "logicalId",
        "root_tags": ["sanctionEntity"],
        "schedule": "daily",
        "filename_aliases": ["TRAVEL"],
        "preprocessing": [
            {
                "handler": "filter_missing_required_field",
                "level": "dataset",
                "config": {
                    "field": "logicalId",
                },
            },
        ],
    },
    "UN-SANCTIONS": {
        "source_name": "UN",
        "list_name": "UN-SANCTIONS",
        "date_order": "DMY",
        "url": "https://scsanctions.un.org/resources/xml/en/consolidated.xml",
        "file_type": "xml",
        "root_tags": ["INDIVIDUAL", "ENTITY"],
        "external_id_path": "DATAID",
        "filename_aliases": ["UN"],
        "download_method": "HTTPS",
        "versioning_strategy": "continuous",

    },
    "EU-FINANCIAL-SANCTIONS": {
        "source_name": "EU",
        "list_name": "EU-FINANCIAL-SANCTIONS",
        "date_order": "DMY",
        "url": "https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw",
        "file_type": "xml",
        "root_tags": ["sanctionEntity"],
        "external_id_path": "logicalId",
        "filename_aliases": ["EU"],
        "download_method": "HTTPS",
        "versioning_strategy": "continuous",
    },

    "SECO-SANCTIONS": {
        "source_name": "SECO",
        "date_order": "DMY",
        "list_name": "SECO-SANCTIONS",
        "download_method": "HTTPS",
        "url": (
            "https://www.sesam.search.admin.ch/sesam-search-web/pages/"
            "search.xhtml?action=generateExcelAction&lang=de"
        ),
        "file_type": "xlsx",
        "external_id_path": "SSID",
        "schedule": "daily",
        "versioning_strategy": "continuous",
    },
}