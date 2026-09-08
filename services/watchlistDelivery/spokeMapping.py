# spoke_mapping.py
from typing import Dict, Any

# THE DYNAMIC PROJECTION MAP
# If you add a new spoke layout or exception tomorrow, you ONLY edit this dictionary. 
# The main synchronization engine remains completely untouched.
SPOKE_MAPPING: Dict[str, Any] = {
    "core.member_name": {
        "json_array": "Names",
        "alias": "n",
        "columns": {
            "name_type": "n->>'NameType'",
            "name": "n->>'Name'",
            "first_name": "n->>'FirstName'",
            "middle_name": "n->>'Middle Name'",
            "last_name": "n->>'Last Name'",
            "normalized_name": "n->>'Normalized_Name'",
            "phonetic_key": "n->'Phonetic_Key'->>0",
            "phonetic_key_alt": "n->'Phonetic_Key'->>1",
            # Converts '["rust", "closed"]' to ' rust   closed '
            "search_tokens": "TRANSLATE(n->>'Search_Tokens', '[]\",', '    ')",
            "language": "n->>'Language'",
            # Evaluates your CSV logic: 'If Names[].NameType = "Primary Name" Then 1 Else 0'
            "is_primary": "CASE WHEN n->>'NameType' = 'Primary Name' THEN true ELSE false END"
        }
    },
    "core.member_alias": {
        "json_array": "Aliases",
        "alias": "a",
        "columns": {
            "alias_type": "a->>'AliasType'",
            "alias": "NULLIF(a->>'Alias', '')",
            "normalized_alias": "a->>'Normalized_Alias'",
            "phonetic_key": "a->'Phonetic_Key'->>0",
            "phonetic_key_alt": "a->'Phonetic_Key'->>1",
            # Converts '["rust", "closed"]' to ' rust   closed '
            "search_tokens": "TRANSLATE(a->>'Search_Tokens', '[]\",', '    ')"
        }
    },
    "core.member_identifier": {
        "json_array": "Identifiers",
        "alias": "i",
        "columns": {
            "identifier_type": "NULLIF(i->>'Type', '')",
            "identifier_value": "NULLIF(i->>'Number', '')",
            "normalized_identifier": "i->>'Normalized_Number'",
            "issuing_country": "i->>'IssuingCountry'"
        }
    },
    "core.member_date": {
        "json_array": "Dates",
        "alias": "d",
        "columns": {
            "date_type": "d->>'Type'",
            # SAFEGUARDED: Converted empty strings to NULL before casting to int
            "year": "(NULLIF(d->>'Year', ''))::int",
            "month": "(NULLIF(d->>'Month', ''))::int",
            "day": "(NULLIF(d->>'Day', ''))::int",
            "is_approximate": "COALESCE((NULLIF(d->>'IsApproximate', ''))::boolean, false)"
        }
    },
    "core.member_country": {
        "json_array": "Countries",
        "alias": "c",
        "columns": {
            "country_type": "NULLIF(c->>'CountryType', '')",
            "country_code": "c->>'CountryCode'",
            "country_name": "NULLIF(c->>'CountryName', '')"
        }
    },
    "core.member_address": {
        "json_array": "Addresses",
        "alias": "ad",
        "columns": {
            "country_name": "ad->>'Country'",
            "state_province": "ad->>'Province'",
            "city": "ad->>'City'",
            "postal_code": "ad->>'PostalCode'",
            "full_address": "ad->>'FullAddress'"
        }
    },
    "core.member_relationship": {
        "json_array": "Relationships",
        "alias": "r",
        "columns": {
            "relationship_type": "NULLIF(r->>'RelationType', '')",
            # SAFEGUARDED: Handles empty string relationships gracefully
            "related_watchlist_member_id": "(NULLIF(r->>'RelatedEntityID', ''))::bigint",
            "related_normalized_name": "COALESCE(r->>'related_normalized_name',r->>'RelatedEntityName')"
        }
    },
    "core.member_program": {
        "json_array": "Programs",
        "alias": "p",
        "columns": {
            "program_type": "p->>'ProgramType'",
            "authority": "p->>'Authority'",
            "program_name": "NULLIF(p->>'Program', '')"
        }
    },
    "core.member_contact": {
        "json_array": "Contacts",
        "alias": "ct",
        "columns": {
            "contact_type": "NULLIF(ct->>'Type', '')",
            "contact_value": "NULLIF(ct->>'Value', '')"
        }
    },
    
    # --- THE EXCEPTION ROUTER ---
    # This acts as a separate mapping but routes data to the same table                                                                   
    "vessel_call_signs": {
        "target_table": "core.member_contact", 
        "json_array": "VesselDetails",
        "alias": "vd",
        "columns": {
            "contact_type": "'Call Sign'", 
            "contact_value": "vd->>'CallSign'"
        }
    }
    
    #example of adding new hardcoded value
	#"aircraft_tail_numbers": {
    #    "target_table": "core.member_identifier", # Tells it the real physical table
    #    "json_array": "AircraftDetails",          # The array it looks for in the JSON
    #    "alias": "air",                           # A short alias for the SQL join
    #    "columns": {
    #        "identifier_type": "'Tail Number'",   # Hardcoded SQL string
    #        "identifier_value": "air->>'TailNumber'" # Extracting from JSON
    #    }
    #}   
    
}