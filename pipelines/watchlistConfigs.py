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
        "download_method": "BYPASS",
        "url": "https://atc.gov.ph/individuals/",
        "file_type": "html",
        "external_id_path": "unique_id",
        "schedule": "daily",
        "versioning_strategy": "continuous",
        "bypass_config": {
            # Declare the challenge; the collector picks the engine that
            # clears it (cloudflare -> stealth browser). No runtime detection.
            "challenge": "cloudflare",
            # False = run a VISIBLE browser window (often needed so the
            # anti-bot challenge clears); set True only on a headless server.
            "headless": False,
            "timeout_seconds": 90,
            "success_criteria": ["Designated Terrorist Individuals"],

            "actions": [
                {
                    "action": "navigate",
                    "url": "{url}"
                },
                {
                    "action": "wait",
                    "type": "selector",
                    "selector": "table.tablepress",
                    "timeout": 60
                },
                {
                    "action": "save_html",
                    "filename_pattern": "{source}_{list}_{timestamp}.html"
                }
            ],
            
            "validation": {
                "required_content": [
                    "Designated Terrorist",
                    "tablepress",
                    "Anti-Terrorism Council"
                ],
                "min_size_bytes": 10000
            }
        },
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
        "download_method": "BYPASS",
        "versioning_strategy": "continuous",
        "url": "https://atc.gov.ph/groups/",
        "file_type": "html",
        "external_id_path": "unique_id",
        "schedule": "daily",
        "bypass_config": {
            # Declare the challenge; the collector picks the engine that
            # clears it (cloudflare -> stealth browser). No runtime detection.
            "challenge": "cloudflare",
            # False = run a VISIBLE browser window (often needed so the
            # anti-bot challenge clears); set True only on a headless server.
            "headless": False,
            "timeout_seconds": 90,
            "success_criteria": ["Designated Terrorist Groups"],

            "actions": [
                {
                    "action": "navigate",
                    "url": "{url}"
                },
                {
                    "action": "wait",
                    "type": "selector",
                    "selector": "table.tablepress",
                    "timeout": 60
                },
                {
                    "action": "save_html",
                    "filename_pattern": "{source}_{list}_{timestamp}.html"
                }
            ],

            "validation": {
                "required_content": [
                    "Designated Terrorist",
                    "tablepress",
                    "Anti-Terrorism Council"
                ],
                "min_size_bytes": 10000
            }
        },
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

    "FBI-WANTED": {
        "source_name": "FBI",
        "list_name": "FBI-WANTED",
        "date_order": "MDY",
        "download_method": "API",
        "url": "https://api.fbi.gov/wanted/v1/list",
        "file_type": "jsonl",
        "external_id_path": "uid",
        "schedule": "daily",
        "versioning_strategy": "continuous",
        "api_config": {
            "pagination": {
                "type": "page",
                "page_param": "page",
                "size_param": "pageSize",
                "page_size": 50,
                "start_page": 1,
            },
            "items_path": "items",
            "write_mode": "single_jsonl",
            "throttle_delay": 0.3,
        },
    },

    "DILG-LOCAL-OFFICIALS": {
        "source_name": "DILG",
        "date_order": "DMY",
        "list_name": "DILG-LOCAL-OFFICIALS",

        "download_method": "HTTPS",

        "url": (
            r"https://region5.dilg.gov.ph/wp-content/uploads/2026/05/Masterlist-of-Local-Officials-2025-2028.pdf"
        ),

        "url_resolver": {
            "type": "link_text",
            "source_page_url": (
                "https://region5.dilg.gov.ph/lgus/"
            ),
            "value": "Masterlist of Local Officials",
        },

        "file_type": "pdf",

        "external_id_path": "unique_id",

        "schedule": "daily",

        "versioning_strategy": "continuous",

        "parser_config": {
            "expected_headers": [
                "REGION",
                "PROVINCE",
                "P/C/M",
                "POSITION",
                "NAME",
            ],
        },

        "preprocessing": [
            {
                "handler": "generate_composite_id",
                "level": "record",
                "config": {
                    "fields": [
                        "REGION",
                        "PROVINCE",
                        "P/C/M",
                        "POSITION",
                        "NAME",
                    ],
                    "output_field": "unique_id",
                    "prefix": "DILG",
                },
            },
        ],
    },

        "CFTC-RED-LIST": {
        "source_name": "CFTC",
        "date_order": "MDY",
        "list_name": "CFTC-RED-LIST",
        "download_method": "CRAWLER",
        "url": "https://www.cftc.gov/LearnAndProtect/Resources/Check/redlist.htm",
        "file_type": "html",
        "external_id_path": "source_record_id",
        "schedule": "daily",
        "versioning_strategy": "continuous",
        "source_config": "config/watchlistSources/cftc_red_list.yaml",
        "attachments": [
            {
                "scope": "member",
                "attachment_type": "DOCUMENT",
                "local_path_field": "detail_file_path",
                "source_url_field": "detail_url",
            },
        ],
        "preprocessing": [
            {
                "handler": "set_constant_field",
                "level": "record",
                "config": {
                    "output_field": "entity_type",
                    "value": "Entity",
                },
            }
        ],
    },
    "GPPB-BLACKLISTED-ENTITIES": {
        "source_name": "GPPB",
        "date_order": "YMD",
        "list_name": "GPPB-BLACKLISTED-ENTITIES",
        "download_method": "API",
        "url": "https://onlineblacklistingportal.gppb.gov.ph/obp-backend/cbr/cbr_public/",
        "file_type": "jsonl",
        "external_id_path": "unique_id",
        "schedule": "daily",
        "versioning_strategy": "continuous",
        "api_config": {
            "pagination": {
                "type": "none",
            },
            "items_path": "",
            "param_variants": [
                {"category": "BLACKLISTED_ENTITIES"},
                {"category": "PERMANENT_BLACKLISTED_ENTITIES"},
                {"category": "TEMPORARY_REMOVED_BLACKLISTED_ENTITIES"},
            ],
            "write_mode": "single_jsonl",
        },
        "preprocessing": [
            {
                "handler": "generate_composite_id",
                "level": "record",
                "config": {
                    "fields": [
                        "blacklisted_entity",
                        "procuring_entity",
                        "project",
                        "start_date",
                    ],
                    "output_field": "unique_id",
                    "prefix": "GPPB",
                },
            },
            {
                "handler": "set_constant_field",
                "level": "record",
                "config": {
                    "output_field": "entity_type",
                    "value": "Entity",
                },
            },
        ],
    },

    "DMW-RECRUITMENT-AGENCIES": {
        "source_name": "DMW",
        "date_order": "YMD",
        "list_name": "DMW-RECRUITMENT-AGENCIES",
        "download_method": "API",
        "url": "https://master-api.dmw.gov.ph/api/v1/public/licensed-agencies",
        "file_type": "jsonl",
        "external_id_path": "unique_id",
        "schedule": "daily",
        "versioning_strategy": "continuous",
        "api_config": {
            "pagination": {
                "type": "page",
                "page_param": "page",
                "start_page": 1,
            },
            "items_path": "data",
            "headers": {
                "x-api-key": "RTA0X0lOWFcycm9KU29WTlZxNDUzSDY5enc5OWFxY2ktWkxVdkFwZjEyMjkwNTA2MTE",
                "x-requested-with": "XMLHttpRequest",
                "referer": "https://dmw.gov.ph/",
                "origin": "https://dmw.gov.ph",
                "accept": "application/json",
                "user-agent": "Mozilla/5.0",
            },
            "throttle_delay": 0.3,
            "write_mode": "single_jsonl",
        },
        "preprocessing": [
            {
                "handler": "generate_composite_id",
                "level": "record",
                "config": {
                    "fields": [
                        "name",
                        "address",
                        "license_status_date",
                    ],
                    "output_field": "unique_id",
                    "prefix": "DMW",
                },
            },
            {
                "handler": "set_constant_field",
                "level": "record",
                "config": {
                    "output_field": "entity_type",
                    "value": "Entity",
                },
            },
        ],
    },

    "PH-HOUSE-MEMBERS": {
        "source_name": "CONGRESS-PH",
        "date_order": "MDY",
        "list_name": "PH-HOUSE-MEMBERS",

        "download_method": "BYPASS",
        "extraction_method": "SAVED_HTML_SPIDER",

        "url": (
            "https://www.congress.gov.ph/"
            "house-members"
        ),

        "file_type": "html",
        "external_id_path": "source_record_id",

        "schedule": "daily",
        "versioning_strategy": "continuous",

        "source_config": (
            "config/watchlistSources/"
            "ph_house_members.yaml"
        ),

        "minimum_record_count": 250,

        "bypass_config": {
            "challenge": "cloudflare",
            "headless": False,
            "timeout_seconds": 120,

            "success_criteria": [
                "House Members",
            ],

            "actions": [
                {
                    "action": "wait",
                    "type": "selector",
                    "selector": (
                        "a[href*='/house-members/view/']"
                    ),
                    "timeout": 90,
                },
                {
                    "action": "save_html",
                    "filename_pattern": (
                        "{source}_{list}_"
                        "{timestamp}.html"
                    ),
                },
            ],

            "validation": {
                "required_content": [
                    "Full Name",
                    "Representing",
                    "/house-members/view/",
                ],
                "min_size_bytes": 10000,
            },
        },

        "preprocessing": [
            {
                "handler": "set_constant_field",
                "level": "record",
                "config": {
                    "output_field": "entity_type",
                    "value": "Individual",
                },
            },
            {
                "handler": "set_constant_field",
                "level": "record",
                "config": {
                    "output_field": (
                        "jurisdiction_country"
                    ),
                    "value": "Philippines",
                },
            },
            {
                "handler": "set_constant_field",
                "level": "record",
                "config": {
                    "output_field": (
                        "jurisdiction_code"
                    ),
                    "value": "PH",
                },
            },
            {
                "handler": "set_constant_field",
                "level": "record",
                "config": {
                    "output_field": "congress",
                    "value": "20th Congress",
                },
            },
        ],
    },
}