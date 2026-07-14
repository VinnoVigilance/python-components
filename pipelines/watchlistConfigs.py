# pipelines/watchlist_configs.py
WATCHLIST_CONFIGS = {
    
    "DFAT": {
        "source_name": "DFAT",
        "list_name": "DFAT",
        "url": "https://www.dfat.gov.au/sites/default/files/Australian_Sanctions_Consolidated_List.xlsx",
        "file_type": "xlsx",
        "external_id_path":"Reference",
        "schedule": "daily",
        "preprocessing": [
            {
                "handler": "merge_dfat_split_records",
                "level": "dataset"
            }
        ]
    },

    "OFAC-SDN": {
        "source_name": "OFAC",
        "list_name": "OFAC-SDN",
        "url": "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN_ENHANCED.XML",
        "file_type": "xml",
        "root_tags": ["entity"],
        "external_id_path":"id",
        "schedule": "daily",
    },

    "OFAC-NON-SDN": {
        "source_name": "OFAC",
        "list_name": "OFAC-NON-SDN",
        "url": "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/CONS_ENHANCED.XML",
        "file_type": "xml",
        "root_tags": ["entity"],
        "external_id_path":"id",
        "schedule": "daily",
    },

    "UKSL": {
        "source_name": "OFSI",
        "list_name": "UKSL",
        "url": "https://sanctionslist.fcdo.gov.uk/docs/UK-Sanctions-List.xml",
        "file_type": "xml",
        "root_tags": ["Designation"],
        "external_id_path":"UniqueID",
        "schedule": "daily",
        
    },

    "DNFBP": {
    "source_name": "AMLC",
    "list_name": "DNFBP",
    "external_id_path": "unique_id",
    "local_path": "data/downloads/a3923f9e-5afc-4102-9899-8fc5a8f07f41_Registered Designated Non-Financial Businesses and Professions (DNFBPs) as of 31 March 2026.pdf",
    "url": r"https://www.amlc.gov.ph/storage/v1/object/public/random-uploads/documents/a3923f9e-5afc-4102-9899-8fc5a8f07f41_Registered%20Designated%20Non-Financial%20Businesses%20and%20Professions%20(DNFBPs)%20as%20of%2031%20March%202026.pdf",
    "file_type": "pdf",
    "schedule": "daily",

    "preprocessing": [
        {
            "handler": "detect_entity_type",
            "config": {
                "input_field": "INSTITUTION NAME",
                "output_field": "entity_type"
            }
        }
    ]
},
    "ATC-DESIGNATED-TERRORIST-INDIVIDUALS": {
        "source_name": "ATC",
        "list_name": "ATC-DESIGNATED-TERRORIST-INDIVIDUALS",
        "url": "https://atc.gov.ph/individuals/",
        "file_type": "html",
        "external_id_path": "unique_id",
        "schedule": "daily",
        "local_path": "data/downloads/Designated Terrorist Individuals _ Anti-Terrorism Council.html",
        "profile_dir": "data/downloads/profiles",

        "enrichment": [
            {
                "handler": "enrich_atc_profile_data",
                "level": "record",
                "config": {
                    "url_field": "detail_url",
                    "profile_dir": "data/downloads/profiles",
                    "images_dir": "data/downloads/images",
                    "output_field": "profile_data"
                }
            }
        ],

        "preprocessing": [
            {
                "handler": "generate_atc_unique_id",
                "config": {
                    "name_field": "name",
                    "resolution_field": "atc_resolution_no",
                    "output_field": "unique_id",
                    "prefix": "ATC"
                }
            },
            {
                "handler": "split_atc_date_and_place_of_birth",
                "config": {
                    "input_field": "profile_data.profile_fields.Date and Place of Birth",
                    "date_output_field": "atc_birth_date",
                    "place_output_field": "atc_birth_place"
                }
            },
            {
                "handler": "clean_atc_profile_name_fields",
                "config": {
                    "fields": [
                        "Variant/s",
                        "Alias/es"
                    ]
                }
            },
            {
                "handler": "extract_name_from_url",
                "config": {
                    "input_field": "detail_url",
                    "output_field": "profile_slug"
                }
            }
        ]
    },
    "ATC-DESIGNATED-TERRORIST-GROUPS": {
        "source_name": "ATC",
        "list_name": "ATC-DESIGNATED-TERRORIST-GROUPS",
        "url": "https://atc.gov.ph/groups/",
        "file_type": "html",
        "external_id_path": "unique_id",
        "schedule": "daily",
        "local_path": "data/downloads/Designated Terrorist Groups _ Anti-Terrorism Council.html",

        "preprocessing": [
            {
                "handler": "generate_atc_unique_id",
                "config": {
                    "name_field": "name",
                    "resolution_field": "atc_resolution_no",
                    "output_field": "unique_id",
                    "prefix": "ATC"
                }
            }
        ]
    },
    "EU-DESIGNATED-VESSELS": {
        "list_name": "EU-DESIGNATED-VESSELS",
        "source_name": "EU",
        "url": "https://dk9q89lxhn3e0.cloudfront.net/EU+designated+vessels+consolidated.xlsx",
        "file_type": "xlsx",
        "external_id_path": "IMO number",
        "schedule": "daily",

        "enrichment": [
            {
                "handler": "fix_eu_vessel_multiline_rows",
                "level": "dataset"
            }
        ]
    },
    "EU-TRAVEL-BAN": {
        "list_name": "EU-TRAVEL-BAN",
        "source_name": "EU",
        "url": "https://www.sanctionsmap.eu/api/v1/travelbans/file/101",
        "file_type": "xml",
        "external_id_path": "logicalId",
        "root_tags": ["sanctionEntity"],
        "schedule": "daily",
    },
    "UN": {
        "file_type": "xml",
        "root_tags": ["INDIVIDUAL", "ENTITY", "sanctionEntity"],
    }
}