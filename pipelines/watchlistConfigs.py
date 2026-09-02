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
            "challenge": "cloudflare",
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
            "challenge": "cloudflare",
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

    "INTERPOL-RED-NOTICES": {
        "source_name": "INTERPOL",
        "list_name": "INTERPOL-RED-NOTICES",
        "date_order": "DMY",
        "download_method": "API",
        "url": "https://ws-public.interpol.int/notices/v1/red",
        "file_type": "jsonl",
        "external_id_path": "source_record_id",
        "schedule": "daily",
        "versioning_strategy": "continuous",
        "preprocessing": [
            {
                # Join each overview stub to its saved profile, matched by
                # entity_id -> attachments/members/{id}.json, into the crawler's
                # list_detail shape ({source_record_id, list, detail}). General
                # handler; any "one primary file + key-matched attachments"
                # source reuses it with a rule row.
                "handler": "enrich_from_attachment",
                "level": "record",
                "relative_path_fields": ["attachments_dir"],
                "config": {
                    "attachments_dir": "attachments/members",
                    "key_field": "entity_id",
                },
            },
        ],
        "api_config": {
            "transport": "browser",
            "bypass_config": {
                "headless": False,
                "warmup_url": "https://www.interpol.int/How-we-work/Notices/Red-Notices/View-Red-Notices",
                "timeout_seconds": 90,
                # The warrant facet probes ~250 countries per over-cap slice --
                # a burst that rate-limited us ("Failed to fetch"). Pace every
                # request and back off hard so the burst self-throttles and any
                # limit self-heals instead of aborting the run.
                "min_request_interval": 0.2,
                "fetch_retries": 6,
                "fetch_retry_delay": 1.0,
                "fetch_backoff": 2.0,
                "fetch_max_delay": 30.0,
            },
            "pagination": {
                "type": "page",
                "page_param": "page",
                "size_param": "resultPerPage",
                "page_size": 160,
                "start_page": 1,
            },
            "faceting": {
                "enabled": True,
                "cap": 160,
                "total_path": "total",
                "facets": [
                    {
                        "type": "enum",
                        "param": "sexId",
                        "values": ["M", "F", "U"],
                    },
                    {
                        "type": "range",
                        "min_param": "ageMin",
                        "max_param": "ageMax",
                        "low": 0,
                        "high": 120,
                        # Some notices have no date of birth (so no age) and the
                        # API has no filter that selects them, so age ranges
                        # cannot cover everyone. complete: False hands the
                        # age-less remainder down to the next facets so those
                        # records are still fetched (recovered 4 confirmed here).
                        "complete": False,
                    },
                    {
                        # PRIMARY splitter (runs before the country facets):
                        # split by single-letter PRESENCE ("forename contains
                        # A".."Z"). Single letters are position-independent
                        # (suffix-safe), and every non-empty forename contains
                        # some letter, so the alphabet covers every named record.
                        # This 26x26 name grid is a COMPLETE catch-all that
                        # shrinks almost any slice cheaply -- so the ~249-code
                        # country sweeps below are reached only for the rare
                        # record the name grid can't place, instead of running on
                        # every slice (which is what made planning take hours).
                        # complete: False so records with NO forename fall
                        # through to the surname pass.
                        "type": "substring",
                        "param": "forename",
                        "max_depth": 1,
                        "complete": False,
                    },
                    {
                        # Second grid axis: same single-letter presence on the
                        # surname. Together the two form the forename x surname
                        # grid, leaving no named record uncovered. complete: False
                        # so a record with no surname either falls through to the
                        # country fallbacks below rather than being dropped.
                        "type": "substring",
                        "param": "name",
                        "max_depth": 1,
                        "complete": False,
                    },
                    {
                        # Fallback, reached only for a record the name grid can't
                        # place (no forename AND no surname) or a name-cell still
                        # over cap: split by nationality. A person can hold several
                        # nationalities, so the value-slices OVERLAP -- NOT a clean
                        # partition. disjoint: False turns off the early-stop
                        # (which double-counts dual nationals, hits the slice total
                        # too soon, and drops the untouched tail of countries) and
                        # probes every code instead.
                        "type": "enum",
                        "param": "nationality",
                        "values_ref": "country_codes",
                        "disjoint": False,
                        # Some notices carry no nationality at all and the API has
                        # no value that selects them; complete: False hands that
                        # leftover down to the warrant fallback. (Named records
                        # with null nationality are already covered above by the
                        # name grid, so this null-handling is only a backstop for
                        # the rare no-name record.)
                        "complete": False,
                    },
                    {
                        # Deepest fallback: split by the country whose arrest
                        # warrant drives the notice ("wanted by"). Reached only for
                        # a slice still over cap after sex+age+name+nationality. A
                        # person can be wanted by several countries, so these
                        # slices overlap -> disjoint: False (probe every value, no
                        # early-stop); the collector's dedup drops any record that
                        # lands under two warrant countries.
                        "type": "enum",
                        "param": "arrestWarrantCountryId",
                        "values_ref": "country_codes",
                        "disjoint": False,
                        # Not every notice names a warrant country either, so this
                        # facet cannot cover everyone: a record with none is
                        # flagged unresolved rather than silently dropped.
                        "complete": False,
                    },
                ],
            },
            "items_path": "_embedded.notices",
            "detail": {
                "url_path": "_links.self.href",
                "concurrency": 10,
            },
            "record_shape": {
                # list_detail uses only id_path -- to name each profile file
                # attachments/members/{entity_id}.json and to dedup the overview.
                "id_path": "entity_id",
                "id_field": "source_record_id",
                "list_field": "list",
                "detail_field": "detail",
            },
            "dedup_path": "source_record_id",
            "throttle_delay": 0.3,
            # CFTC-style two-phase output: a primary listing JSONL of unique
            # notices + one raw profile per person under attachments/members/.
            "write_mode": "list_detail",
        },
    },
}