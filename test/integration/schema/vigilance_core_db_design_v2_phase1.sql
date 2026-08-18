-- =====================================================================
-- SCHEMAS
-- =====================================================================
CREATE EXTENSION IF NOT EXISTS "citext";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "fuzzystrmatch";

CREATE SCHEMA common;
CREATE SCHEMA raw;
CREATE SCHEMA core;
CREATE SCHEMA delivery;
CREATE SCHEMA customer;

-- =====================================================================
-- SEQUENCE
-- =====================================================================
CREATE SEQUENCE core.vv_member_id_seq START 1263450;

-- =====================================================================
-- FUNCTION
-- =====================================================================

--for Screening UI search
CREATE OR REPLACE FUNCTION delivery.search_screening_entities(
    p_search_text TEXT,
    p_similarity_threshold INT DEFAULT 60,
	p_entity_type TEXT DEFAULT NULL, 								 
    p_dob DATE DEFAULT NULL,
    p_nationality TEXT DEFAULT NULL,
    p_id_type TEXT DEFAULT NULL,
    p_id_number TEXT DEFAULT NULL,
    p_limit INT DEFAULT 50,    -- Limits max rows returned
    p_offset INT DEFAULT 0     -- Skips rows for pagination
)
RETURNS TABLE (
    source_type TEXT,
	matched_name TEXT,
    match_score NUMERIC,
    entity_type TEXT,
    source_name TEXT,
    source_logo_path TEXT,
    last_updated TIMESTAMPTZ,
    vv_member_id BIGINT
) 
LANGUAGE plpgsql
AS $$
DECLARE
    v_sim_threshold NUMERIC;
    
    -- 1. Declare BOTH phonetic variables here
    v_search_phonetic_primary TEXT;
    v_search_phonetic_alt TEXT;
BEGIN
    -- Convert UI threshold (0-100) to pg_trgm threshold (0.0 - 1.0)
    v_sim_threshold := p_similarity_threshold::NUMERIC / 100.0;
    
    -- Set the strict pg_trgm limit for index utilization
    PERFORM set_limit(v_sim_threshold::REAL);

    -- 2. Populate BOTH variables on the fly using the user's raw text
    v_search_phonetic_primary := dmetaphone(p_search_text);
    v_search_phonetic_alt := dmetaphone_alt(p_search_text);

    RETURN QUERY
    SELECT 
        si.source_type::TEXT,
		si.display_name::TEXT AS matched_name,
        
        -- Calculate similarity against both exact name and tokens, and take the highest score
        CASE 
            -- If it is a phonetic match, assign a baseline score of 95
            WHEN si.phonetic_key IN (v_search_phonetic_primary, v_search_phonetic_alt)
              OR si.phonetic_key_alt IN (v_search_phonetic_primary, v_search_phonetic_alt)
            THEN 
                GREATEST(
                    95, 
                    ROUND(GREATEST(similarity(si.search_term, p_search_text), similarity(si.search_tokens, p_search_text)) * 100)
                )
            -- Otherwise, rely entirely on the exact/fuzzy text similarity
            ELSE 
                ROUND(GREATEST(similarity(si.search_term, p_search_text), similarity(si.search_tokens, p_search_text)) * 100)
				
		-- !!! BONUS: +10 Points if DOB matches exactly
		-- (this is JUST sample of how to implement and not a good idea,
		-- and should be removed from WHERE if applied, and since the WHERE already filters if no match is found with AND,
		-- so it is meaninigless to use it, i just put it here IF anyone insists) !!!
        + CASE 
            WHEN p_dob IS NOT NULL AND EXISTS (
                SELECT 1 FROM core.member_date md WHERE md.watchlist_member_id = si.source_record_id 
                AND md.year = EXTRACT(YEAR FROM p_dob) AND md.month = EXTRACT(MONTH FROM p_dob) AND md.day = EXTRACT(DAY FROM p_dob)
            ) THEN 10 ELSE 0 
        END
				
				
        END::NUMERIC AS match_score,
        
        si.entity_type::TEXT,
        si.source_name::TEXT,
        si.source_logo_path::TEXT,
        si.last_updated,
        si.vv_member_id
    FROM core.mv_screening_member_search si
    WHERE 
        -- 1. Fuzzy, Phonetic, Tokenized Name Match utilizing the GIN index
        (
            si.search_term % p_search_text 
            OR si.search_tokens % p_search_text
            
            -- 3. Compare the database columns against BOTH on-the-fly variables
            OR si.phonetic_key IN (v_search_phonetic_primary, v_search_phonetic_alt)
            OR si.phonetic_key_alt IN (v_search_phonetic_primary, v_search_phonetic_alt)
        )
        
		-- 2. Advanced Filter: Entity Type (e.g., 'Individual', 'Organization')
        AND (p_entity_type IS NULL OR si.entity_type ILIKE p_entity_type)
        
        -- Advanced Filter: Date of Birth																	
																		 
		
        -- 3. Advanced Filter: Date of Birth
        AND (p_dob IS NULL OR EXISTS (
            SELECT 1 FROM core.member_date md 
            WHERE md.watchlist_member_id = si.source_record_id 
              AND md.date_type = 'Birth Date' 
              AND md.year = EXTRACT(YEAR FROM p_dob)
              AND md.month = EXTRACT(MONTH FROM p_dob)
              AND md.day = EXTRACT(DAY FROM p_dob)
        ))
        
        -- 4. Advanced Filter: Nationality
        AND (p_nationality IS NULL OR EXISTS (
            SELECT 1 FROM core.member_country mc
            WHERE mc.watchlist_member_id = si.source_record_id
              AND mc.country_type IN ('Nationality', 'Citizenship','POB')
              AND mc.country_name ILIKE p_nationality
        ))
        
        -- 5. Advanced Filter: ID Type & Number
        AND (
            (p_id_type IS NULL AND p_id_number IS NULL) OR EXISTS (
                SELECT 1 FROM core.member_identifier mi
                WHERE mi.watchlist_member_id = si.source_record_id
                  AND (p_id_type IS NULL OR mi.identifier_type ILIKE p_id_type)
                  AND (p_id_number IS NULL OR mi.identifier_value ILIKE '%' || p_id_number || '%')
            )
        )
    ORDER BY match_score DESC
    LIMIT p_limit 
    OFFSET p_offset;
END;
$$;

/*
CREATE OR REPLACE FUNCTION gen_random_uuid_v7() 
RETURNS uuid AS $$
DECLARE
    unix_time_ms bytea;
    uuid_bytes bytea;
BEGIN
    -- 1. Get current epoch milliseconds and convert to 6-byte bytea
    unix_time_ms := substring(decode(lpad(to_hex(floor(extract(epoch from clock_timestamp()) * 1000)::bigint), 16, '0'), 'hex') from 3 for 6);
    
    -- 2. Generate 10 random bytes
    uuid_bytes := unix_time_ms || gen_random_bytes(10);
    
    -- 3. Set UUIDv7 version (0111xxx or '7') at byte 7
    uuid_bytes := set_byte(uuid_bytes, 6, (get_byte(uuid_bytes, 6) & 15) | 112);
    
    -- 4. Set variant (10xxxxxx) at byte 9
    uuid_bytes := set_byte(uuid_bytes, 8, (get_byte(uuid_bytes, 8) & 63) | 128);
    
    RETURN encode(uuid_bytes, 'hex')::uuid;
END;
$$ LANGUAGE plpgsql VOLATILE;
*/

-- =====================================================================
-- PROCEDURE
-- =====================================================================
CREATE OR REPLACE PROCEDURE delivery.generate_watchlist_daily_delta_actions(p_effective_date DATE)
LANGUAGE plpgsql
AS $$
BEGIN
    -- STEP 14 & 18 (Step 8): Standard PL/pgSQL procedures execute within a single transaction automatically.
    -- If any error occurs, PostgreSQL will automatically roll back the entire transaction block.

    -- STEP 13 & 18 (Step 7): Idempotency - Delete any existing records for the requested Effective Date.
    DELETE FROM delivery.watchlist_daily_delta_actions 
    WHERE effective_date = p_effective_date;

    -- STEP 7, 8, 9, 10, & 12: Extract and map the delta records using CTEs.
    INSERT INTO delivery.watchlist_daily_delta_actions (effective_date, action, vv_member_id, watchlist_member_id)
    WITH daily_records AS (
        -- STEP 2 & 7: Retrieve all candidate records created ONLY during the requested business date.
        SELECT 
            id,
            vv_member_id,
            change_type,
            version_no,
            -- STEP 8: Group by vv_member_id and determine chronological order using version_no
            ROW_NUMBER() OVER (PARTITION BY vv_member_id ORDER BY version_no ASC) AS rn_first,
            ROW_NUMBER() OVER (PARTITION BY vv_member_id ORDER BY version_no DESC) AS rn_last
        FROM core.watchlist_member
        WHERE DATE(created_at) = p_effective_date
    ),
    member_transitions AS (
        -- STEP 4: For each logical member, isolate the First Event and Last Event of the day.
        SELECT 
            first_rec.vv_member_id,
            first_rec.change_type AS first_event,
            last_rec.change_type AS last_event,
            -- STEP 9 & 11: The last_rec.id represents the latest version created during the requested date.
            last_rec.id AS latest_watchlist_member_id
        FROM (SELECT * FROM daily_records WHERE rn_first = 1) first_rec
        JOIN (SELECT * FROM daily_records WHERE rn_last = 1) last_rec
          ON first_rec.vv_member_id = last_rec.vv_member_id
    ),
    mapped_actions AS (
        -- STEP 5 & 10: Apply the exact synchronization decision matrix.
        SELECT 
            vv_member_id,
            latest_watchlist_member_id,
			CASE 
                -- Single or intra-day transitions starting with NEW
                WHEN first_event = 'NEW' AND last_event = 'NEW' THEN 'ADD'
                WHEN first_event = 'NEW' AND last_event = 'UPDATED' THEN 'ADD'
                WHEN first_event = 'NEW' AND last_event = 'DELETED' THEN 'IGNORE'
            
                -- Transitions starting with UPDATED
                WHEN first_event = 'UPDATED' AND last_event = 'UPDATED' THEN 'UPDATE'
                WHEN first_event = 'UPDATED' AND last_event = 'DELETED' THEN 'DELETE'
            
                -- Transitions starting with DELETED
                WHEN first_event = 'DELETED' AND last_event = 'NEW' THEN 'UPDATE'
                WHEN first_event = 'DELETED' AND last_event = 'DELETED' THEN 'DELETE'
            
                ELSE 'IGNORE'
            END AS action_type
        FROM member_transitions
    )
    -- STEP 6, 7 & 19: Insert only valid actions. If no records matched the date, 0 rows are inserted.
    SELECT 
        p_effective_date,
        action_type,
        vv_member_id,
        latest_watchlist_member_id
    FROM mapped_actions
    WHERE action_type != 'IGNORE';

    -- STEP 18 (Step 9): Completion is handled when the procedure reaches the end successfully.
	
END;
$$;


CREATE OR REPLACE PROCEDURE common.firstload_lookups()
LANGUAGE plpgsql
AS $$
BEGIN
    -- =====================================================================
    -- 0. WIPE DATA AND RESET SEQUENCES TO 1
    -- =====================================================================
    TRUNCATE TABLE 
        common.lkup_entity_type, 
        common.lkup_source, 
        common.lkup_source_list_type, 
        common.lkup_risk_category 
    RESTART IDENTITY CASCADE;

    -- =====================================================================
    -- 1. SEED ENTITY TYPES
    -- =====================================================================
    INSERT INTO common.lkup_entity_type (id, name, description) VALUES 
        (1, 'Individual', 'A natural person.'),
        (2, 'Entity', 'A legal entity such as a company, government agency, NGO, or association.'),
        (3, 'Vessel', 'A maritime vessel or ship.'),
        (4, 'Aircraft', 'An aircraft or aviation asset.')
    ON CONFLICT (id) DO NOTHING;

    -- =====================================================================
    -- 2. SEED SOURCES
    -- =====================================================================
    INSERT INTO common.lkup_source (id, name, country, authority, base_url, logo_attachment_id, created_at) VALUES 
        (1, 'DFAT', 'Australia', 'Department of Foreign Affairs and Trade', 'https://www.dfat.gov.au', NULL, '2026-07-17 16:41:41.723527+00'),
        (2, 'OFAC', 'United States', 'Office of Foreign Assets Control', 'https://ofac.treasury.gov', NULL, '2026-07-17 16:41:41.723527+00'),
        (3, 'OFSI', 'United Kingdom', 'Office of Financial Sanctions Implementation', 'https://www.gov.uk/government/organisations/office-of-financial-sanctions-implementation', NULL, '2026-07-17 16:41:41.723527+00'),
        (4, 'AMLC', 'Philippines', 'Anti-Money Laundering Council', 'https://www.amlc.gov.ph', NULL, '2026-07-17 16:41:41.723527+00'),
        (5, 'ATC', 'Philippines', 'Anti-Terrorism Council', 'https://atc.gov.ph', NULL, '2026-07-17 16:41:41.723527+00'),
        (6, 'EU', 'European Union', 'Council of the European Union', 'https://www.consilium.europa.eu', NULL, '2026-07-17 16:41:41.723527+00'),
        (7, 'SEC', 'Philippines', 'Securities and Exchange Commission', 'https://www.sec.gov.ph', NULL, '2026-07-17 16:41:41.723527+00'),
        (8, 'UN', NULL, NULL, NULL, NULL, '2026-07-24 06:29:14.7202+00'),
		(9, 'SECO', NULL, NULL, NULL, NULL, '2026-08-03 19:39:17.528428+08')
    ON CONFLICT (id) DO NOTHING;

    -- =====================================================================
    -- 3. SEED SOURCE LIST TYPES
    -- =====================================================================
    INSERT INTO common.lkup_source_list_type (id, source_id, name, code, description) VALUES 
        (1, 1, 'DFAT', 'DFAT', 'DFAT Consolidated Sanctions List.'),
        (2, 2, 'OFAC-SDN', 'OFAC_SDN', 'OFAC Specially Designated Nationals and Blocked Persons List.'),
        (3, 2, 'OFAC-NON-SDN', 'OFAC_NON_SDN', 'OFAC Non-SDN Sanctions List.'),
        (4, 3, 'UKSL', 'UKSL', 'UK Sanctions List maintained by OFSI.'),
        (5, 4, 'DNFBP', 'DNFBP', 'AMLC Designated Non-Financial Businesses and Professions List.'),
        (6, 5, 'ATC-DESIGNATED-TERRORIST-GROUPS', 'ATC_GROUPS', 'ATC Designated Terrorist Groups.'),
        (7, 5, 'ATC-DESIGNATED-TERRORIST-INDIVIDUALS', 'ATC_INDIVIDUALS', 'ATC Designated Terrorist Individuals.'),
        (8, 6, 'EU-DESIGNATED-VESSELS', 'EU_VESSELS', 'European Union Designated Vessels List.'),
        (9, 6, 'EU-TRAVEL-BAN', 'EU_TRAVEL_BAN', 'European Union Travel Ban List.'),
        (10, 6, 'EU-FINANCIAL-SANCTIONS', 'EU_CONS', 'European Union Consolidated List.'),
        (11, 7, 'SEC-ADVISORY', 'SEC_ADVISORY', 'Philippine SEC Investor Advisory List.'),
        (12, 8, 'UN-SANCTIONS', 'UN', NULL),
		(13, 9, 'SECO-SANCTIONS', 'SECO', NULL)
    ON CONFLICT (id) DO NOTHING;

    -- =====================================================================
    -- 4. SEED RISK CATEGORIES
    -- =====================================================================
    INSERT INTO common.lkup_risk_category (id, category, sub_category, description) VALUES 
        (1, 'Sanctions', NULL, 'Subject to official economic/financial/trade sanctions by a government or international authority.'),
        (2, 'PEP (Politically Exposed Person)', NULL, 'Holds or held a prominent public/political/governmental/military/judicial position.'),
        (3, 'RCA (Relative and Close Associate)', NULL, 'Family member or close associate of a PEP.'),
        (4, 'Crime', NULL, 'Associated with financial or non-financial criminal activity.'),
        (5, 'Regulatory', NULL, 'Subject to regulatory actions, penalties, warnings, or enforcement.'),
        (6, 'Ownership', NULL, 'Risk due to ownership structure (e.g. state-owned/controlled).'),
        (7, 'Law Enforcement', NULL, 'Subject to law enforcement, judicial, investigative, or wanted-person actions.'),
        (8, 'Adverse Media', NULL, 'Subject of credible negative news or public reporting linking them to financial crime, misconduct, or other reputational risk.'),
        (9, 'Sanctions', 'Sanctioned', 'Named on an official sanctions list.'),
        (10, 'PEP (Politically Exposed Person)', 'PEP', 'The person themselves holds/held a prominent public position: head of state or government, senior politician or party official, senior government/judicial/military officer, ambassador, or senior executive of a state-owned enterprise. Cues: minister, general, governor, president, senior official.'),
        (11, 'RCA (Relative and Close Associate)', 'RCA', 'A family member or close associate of a PEP — spouse, child, parent, sibling, or a close business partner/front person acting for a PEP. Linked to power through the PEP, not by holding office themselves. Cues: "son/wife/brother of", business partner of, close associate of.'),
        (12, 'Crime', 'Fraud', 'Deceptive schemes for financial gain.'),
        (13, 'Crime', 'Corruption', 'Abuse of public office or position for private gain — bribery, kickbacks, embezzlement or misappropriation of state/public funds, kleptocracy. Not violence. Cues: bribes, embezzlement, misappropriation of state funds, abuse of office.'),
        (14, 'Crime', 'Money Laundering', 'Concealing or disguising the illicit origin of criminal proceeds — moving, layering or integrating dirty money, using shell companies or front persons to hide ownership. Cues: laundering, disguising proceeds, shell companies.'),
        (15, 'Crime', 'Terrorism', 'Involvement in terrorism or terrorist financing — membership in or support for a terrorist/armed-extremist group, planning or carrying out attacks (bombings, IEDs, armed assaults), or providing funds, weapons, recruits or safe haven to terrorists. Cues: named terrorist group, attacks, IEDs, terrorist financing.'),
        (16, 'Crime', 'Organized Crime', 'Organized criminal activity for profit — trafficking (drugs, arms, people), smuggling, extortion, illegal taxation, racketeering, or running an illicit criminal economy. Pick a trafficking indicator below when one applies. Cues: trafficking, smuggling, extortion, illegal taxation, criminal network.'),
        (17, 'Crime', 'Human Rights Abuse', 'War crimes, crimes against humanity, torture, atrocities, or serious human rights violations.'),
        (18, 'Crime', 'Proliferation', 'Involvement in the proliferation of weapons of mass destruction (nuclear, chemical, biological) or their delivery systems, or the financing thereof.'),
        (19, 'Crime', 'Cybercrime', 'Malicious cyber activity — hacking, unauthorized computer intrusion, ransomware or malware, or cyber-attacks on systems/infrastructure, including for theft or sabotage. Cues: cyber, hacking, ransomware, malware, intrusion.'),
        (20, 'Regulatory', 'Regulatory Action', 'Penalty, warning, or enforcement by a regulator.'),
        (21, 'Ownership', 'State-Owned Enterprise', 'State-owned or government-controlled entity.'),
        (22, 'Law Enforcement', 'Wanted / Investigation', 'Subject to law-enforcement or judicial action as a suspect — arrest warrant, indictment, criminal proceedings/trial, fugitive status, or wanted by police, Interpol or the ICC. Cues: arrest warrant, indicted, wanted, fugitive.'),
        (23, 'Adverse Media', 'Negative News', 'General adverse or negative media coverage not yet resolved to a specific crime subcategory.')
    ON CONFLICT (id) DO NOTHING;

END;
$$;


CREATE OR REPLACE PROCEDURE common.sync_lookups()
LANGUAGE plpgsql
AS $$
BEGIN
    -- =====================================================================
    -- 1. SYNC ENTITY TYPES
    -- =====================================================================
    INSERT INTO common.lkup_entity_type (id, name, description) VALUES 
        (1, 'Individual', 'A natural person.'),
        (2, 'Entity', 'A legal entity such as a company, government agency, NGO, or association.'),
        (3, 'Vessel', 'A maritime vessel or ship.'),
        (4, 'Aircraft', 'An aircraft or aviation asset.')
    ON CONFLICT (id) DO UPDATE SET 
        name = EXCLUDED.name, 
        description = EXCLUDED.description;

    -- =====================================================================
    -- 2. SYNC SOURCES
    -- =====================================================================
    INSERT INTO common.lkup_source (id, name, country, authority, base_url, logo_attachment_id, created_at) VALUES 
        (1, 'DFAT', 'Australia', 'Department of Foreign Affairs and Trade', 'https://www.dfat.gov.au', NULL, '2026-07-17 16:41:41.723527+00'),
        (2, 'OFAC', 'United States', 'Office of Foreign Assets Control', 'https://ofac.treasury.gov', NULL, '2026-07-17 16:41:41.723527+00'),
        (3, 'OFSI', 'United Kingdom', 'Office of Financial Sanctions Implementation', 'https://www.gov.uk/government/organisations/office-of-financial-sanctions-implementation', NULL, '2026-07-17 16:41:41.723527+00'),
        (4, 'AMLC', 'Philippines', 'Anti-Money Laundering Council', 'https://www.amlc.gov.ph', NULL, '2026-07-17 16:41:41.723527+00'),
        (5, 'ATC', 'Philippines', 'Anti-Terrorism Council', 'https://atc.gov.ph', NULL, '2026-07-17 16:41:41.723527+00'),
        (6, 'EU', 'European Union', 'Council of the European Union', 'https://www.consilium.europa.eu', NULL, '2026-07-17 16:41:41.723527+00'),
        (7, 'SEC', 'Philippines', 'Securities and Exchange Commission', 'https://www.sec.gov.ph', NULL, '2026-07-17 16:41:41.723527+00'),
        (8, 'UN', NULL, NULL, NULL, NULL, '2026-07-24 06:29:14.7202+00'),
		(9, 'SECO', NULL, NULL, NULL, NULL, '2026-08-03 19:39:17.528428+08')
    ON CONFLICT (id) DO UPDATE SET 
        name = EXCLUDED.name, 
        country = EXCLUDED.country, 
        authority = EXCLUDED.authority, 
        base_url = EXCLUDED.base_url,
        logo_attachment_id = EXCLUDED.logo_attachment_id;

    -- =====================================================================
    -- 3. SYNC SOURCE LIST TYPES
    -- =====================================================================
    INSERT INTO common.lkup_source_list_type (id, source_id, name, code, description) VALUES 
        (1, 1, 'DFAT', 'DFAT', 'DFAT Consolidated Sanctions List.'),
        (2, 2, 'OFAC-SDN', 'OFAC_SDN', 'OFAC Specially Designated Nationals and Blocked Persons List.'),
        (3, 2, 'OFAC-NON-SDN', 'OFAC_NON_SDN', 'OFAC Non-SDN Sanctions List.'),
        (4, 3, 'UKSL', 'UKSL', 'UK Sanctions List maintained by OFSI.'),
        (5, 4, 'DNFBP', 'DNFBP', 'AMLC Designated Non-Financial Businesses and Professions List.'),
        (6, 5, 'ATC-DESIGNATED-TERRORIST-GROUPS', 'ATC_GROUPS', 'ATC Designated Terrorist Groups.'),
        (7, 5, 'ATC-DESIGNATED-TERRORIST-INDIVIDUALS', 'ATC_INDIVIDUALS', 'ATC Designated Terrorist Individuals.'),
        (8, 6, 'EU-DESIGNATED-VESSELS', 'EU_VESSELS', 'European Union Designated Vessels List.'),
        (9, 6, 'EU-TRAVEL-BAN', 'EU_TRAVEL_BAN', 'European Union Travel Ban List.'),
        (10, 6, 'EU-FINANCIAL-SANCTIONS', 'EU_CONS', 'European Union Consolidated List.'),
        (11, 7, 'SEC-ADVISORY', 'SEC_ADVISORY', 'Philippine SEC Investor Advisory List.'),
        (12, 8, 'UN-SANCTIONS', 'UN', NULL),
		(13, 9, 'SECO-SANCTIONS', 'SECO', NULL)
    ON CONFLICT (id) DO UPDATE SET 
        source_id = EXCLUDED.source_id, 
        name = EXCLUDED.name, 
        code = EXCLUDED.code, 
        description = EXCLUDED.description;

    -- =====================================================================
    -- 4. SYNC RISK CATEGORIES
    -- =====================================================================
    INSERT INTO common.lkup_risk_category (id, category, sub_category, description) VALUES 
        (1, 'Sanctions', NULL, 'Subject to official economic/financial/trade sanctions by a government or international authority.'),
        (2, 'PEP (Politically Exposed Person)', NULL, 'Holds or held a prominent public/political/governmental/military/judicial position.'),
        (3, 'RCA (Relative and Close Associate)', NULL, 'Family member or close associate of a PEP.'),
        (4, 'Crime', NULL, 'Associated with financial or non-financial criminal activity.'),
        (5, 'Regulatory', NULL, 'Subject to regulatory actions, penalties, warnings, or enforcement.'),
        (6, 'Ownership', NULL, 'Risk due to ownership structure (e.g. state-owned/controlled).'),
        (7, 'Law Enforcement', NULL, 'Subject to law enforcement, judicial, investigative, or wanted-person actions.'),
        (8, 'Adverse Media', NULL, 'Subject of credible negative news or public reporting linking them to financial crime, misconduct, or other reputational risk.'),
        (9, 'Sanctions', 'Sanctioned', 'Named on an official sanctions list.'),
        (10, 'PEP (Politically Exposed Person)', 'PEP', 'The person themselves holds/held a prominent public position: head of state or government, senior politician or party official, senior government/judicial/military officer, ambassador, or senior executive of a state-owned enterprise. Cues: minister, general, governor, president, senior official.'),
        (11, 'RCA (Relative and Close Associate)', 'RCA', 'A family member or close associate of a PEP — spouse, child, parent, sibling, or a close business partner/front person acting for a PEP. Linked to power through the PEP, not by holding office themselves. Cues: "son/wife/brother of", business partner of, close associate of.'),
        (12, 'Crime', 'Fraud', 'Deceptive schemes for financial gain.'),
        (13, 'Crime', 'Corruption', 'Abuse of public office or position for private gain — bribery, kickbacks, embezzlement or misappropriation of state/public funds, kleptocracy. Not violence. Cues: bribes, embezzlement, misappropriation of state funds, abuse of office.'),
        (14, 'Crime', 'Money Laundering', 'Concealing or disguising the illicit origin of criminal proceeds — moving, layering or integrating dirty money, using shell companies or front persons to hide ownership. Cues: laundering, disguising proceeds, shell companies.'),
        (15, 'Crime', 'Terrorism', 'Involvement in terrorism or terrorist financing — membership in or support for a terrorist/armed-extremist group, planning or carrying out attacks (bombings, IEDs, armed assaults), or providing funds, weapons, recruits or safe haven to terrorists. Cues: named terrorist group, attacks, IEDs, terrorist financing.'),
        (16, 'Crime', 'Organized Crime', 'Organized criminal activity for profit — trafficking (drugs, arms, people), smuggling, extortion, illegal taxation, racketeering, or running an illicit criminal economy. Pick a trafficking indicator below when one applies. Cues: trafficking, smuggling, extortion, illegal taxation, criminal network.'),
        (17, 'Crime', 'Human Rights Abuse', 'War crimes, crimes against humanity, torture, atrocities, or serious human rights violations.'),
        (18, 'Crime', 'Proliferation', 'Involvement in the proliferation of weapons of mass destruction (nuclear, chemical, biological) or their delivery systems, or the financing thereof.'),
        (19, 'Crime', 'Cybercrime', 'Malicious cyber activity — hacking, unauthorized computer intrusion, ransomware or malware, or cyber-attacks on systems/infrastructure, including for theft or sabotage. Cues: cyber, hacking, ransomware, malware, intrusion.'),
        (20, 'Regulatory', 'Regulatory Action', 'Penalty, warning, or enforcement by a regulator.'),
        (21, 'Ownership', 'State-Owned Enterprise', 'State-owned or government-controlled entity.'),
        (22, 'Law Enforcement', 'Wanted / Investigation', 'Subject to law-enforcement or judicial action as a suspect — arrest warrant, indictment, criminal proceedings/trial, fugitive status, or wanted by police, Interpol or the ICC. Cues: arrest warrant, indicted, wanted, fugitive.'),
        (23, 'Adverse Media', 'Negative News', 'General adverse or negative media coverage not yet resolved to a specific crime subcategory.')
    ON CONFLICT (id) DO UPDATE SET 
        category = EXCLUDED.category, 
        sub_category = EXCLUDED.sub_category, 
        description = EXCLUDED.description;

    -- =====================================================================
    -- 5. SYNCHRONIZE SEQUENCES (CRITICAL FOR UPSERTS)
    -- =====================================================================
    -- This ensures that the next time a record is added without an ID, 
    -- the database starts counting from the highest ID we just inserted.
    PERFORM setval(pg_get_serial_sequence('common.lkup_entity_type', 'id'), COALESCE((SELECT MAX(id) FROM common.lkup_entity_type), 1));
    PERFORM setval(pg_get_serial_sequence('common.lkup_source', 'id'), COALESCE((SELECT MAX(id) FROM common.lkup_source), 1));
    PERFORM setval(pg_get_serial_sequence('common.lkup_source_list_type', 'id'), COALESCE((SELECT MAX(id) FROM common.lkup_source_list_type), 1));
    PERFORM setval(pg_get_serial_sequence('common.lkup_risk_category', 'id'), COALESCE((SELECT MAX(id) FROM common.lkup_risk_category), 1));

END;
$$;

-- =====================================================================
-- LEVEL 1: Independent Tables (No Dependencies)
-- =====================================================================

CREATE TABLE raw.attachment (
  id bigserial PRIMARY KEY,
  storage_path text,
  file_name text,
  file_type text,
  mime_type text,
  file_size bigint,
  file_hash text UNIQUE NOT NULL,
  source_url text,
  downloaded_at timestamptz DEFAULT (now())
);
COMMENT ON COLUMN raw.attachment.id IS 'Unique identifier of the extracted attachment.';
COMMENT ON COLUMN raw.attachment.storage_path IS 'Internal storage location of the attachment.';
COMMENT ON COLUMN raw.attachment.file_name IS 'Original attachment file name.';
COMMENT ON COLUMN raw.attachment.file_type IS 'File format (e.g., PDF, JPG, PNG, DOCX, XLSX, CSV, HTML).';
COMMENT ON COLUMN raw.attachment.mime_type IS 'MIME type of the attachment.';
COMMENT ON COLUMN raw.attachment.file_size IS 'File size in bytes.';
COMMENT ON COLUMN raw.attachment.file_hash IS 'Unique hash value used to detect duplicate attachments.';
COMMENT ON COLUMN raw.attachment.source_url IS 'Original URL from which the attachment was downloaded, if available.';
COMMENT ON COLUMN raw.attachment.downloaded_at IS 'Timestamp when the attachment was downloaded.';


CREATE TABLE common.lkup_entity_type (
  id bigserial PRIMARY KEY,
  name citext UNIQUE NOT NULL,
  description text
);
COMMENT ON COLUMN common.lkup_entity_type.id IS 'Unique identifier of the entity type.';
COMMENT ON COLUMN common.lkup_entity_type.name IS 'Entity type name. Case-insensitive and globally unique. Expected values include Individual, Organization, Vessel, Aircraft, High Risk Country, and Dual Use Goods.';
COMMENT ON COLUMN common.lkup_entity_type.description IS 'Detailed description of the entity type.';


CREATE TABLE common.lkup_risk_category (
  id bigserial PRIMARY KEY,
  category citext NOT NULL,
  sub_category citext,
  description text,
  CONSTRAINT uq_category_sub_category UNIQUE (category, sub_category)
);
COMMENT ON TABLE common.lkup_risk_category IS 'Centralized system taxonomy mapping risk classification levels and their operational profiles.';
COMMENT ON COLUMN common.lkup_risk_category.id IS 'Unique identifier of the risk configuration item.';
COMMENT ON COLUMN common.lkup_risk_category.category IS 'High-level risk classification bucket.';
COMMENT ON COLUMN common.lkup_risk_category.sub_category IS 'Granular specific sub-type classification.';
COMMENT ON COLUMN common.lkup_risk_category.description IS 'Detailed guidance and screening textual criteria.';

-- =====================================================================
-- LEVEL 2: First-Tier Dependencies
-- =====================================================================

CREATE TABLE common.lkup_source (
  id bigserial PRIMARY KEY,
  name citext UNIQUE NOT NULL,
  country citext,
  authority text,
  base_url text,
  logo_attachment_id bigint REFERENCES raw.attachment (id) DEFERRABLE INITIALLY IMMEDIATE,
  created_at timestamptz DEFAULT (now())
);
COMMENT ON COLUMN common.lkup_source.id IS 'Unique identifier of the source.';
COMMENT ON COLUMN common.lkup_source.name IS 'Official source name. Case-insensitive and globally unique (e.g., OFAC SDN, UN Sanctions List, SEC Advisory).';
COMMENT ON COLUMN common.lkup_source.country IS 'Country or jurisdiction that owns or publishes the source.';
COMMENT ON COLUMN common.lkup_source.authority IS 'Publishing authority or organization responsible for maintaining the source.';
COMMENT ON COLUMN common.lkup_source.base_url IS 'Official website or root URL of the source.';
COMMENT ON COLUMN common.lkup_source.logo_attachment_id IS 'Reference to the source logo file stored in object storage.';
COMMENT ON COLUMN common.lkup_source.created_at IS 'Timestamp when the source record was created.';


-- =====================================================================
-- LEVEL 3: Second-Tier Dependencies
-- =====================================================================

CREATE TABLE common.lkup_source_list_type (
  id bigserial PRIMARY KEY,
  source_id bigint REFERENCES common.lkup_source (id) DEFERRABLE INITIALLY IMMEDIATE,
  name citext NOT NULL,
  code citext UNIQUE NOT NULL,
  description text
);
COMMENT ON COLUMN common.lkup_source_list_type.id IS 'Unique identifier of the source list type.';
COMMENT ON COLUMN common.lkup_source_list_type.source_id IS 'Reference to the source that owns this list type.';
COMMENT ON COLUMN common.lkup_source_list_type.name IS 'Display name of the source list type (e.g., Sanctions, PEP, Advisory, Wanted List).';
COMMENT ON COLUMN common.lkup_source_list_type.code IS 'Unique short code representing the source list type (e.g., SANCTION, PEP, FRAUD, ADVISORY).';
COMMENT ON COLUMN common.lkup_source_list_type.description IS 'Detailed description of the source list type and its intended purpose.';


-- =====================================================================
-- LEVEL 4: Third-Tier Dependencies
-- =====================================================================

CREATE TABLE raw.watchlist_file (
  id bigserial PRIMARY KEY,
  source_id bigint NOT NULL REFERENCES common.lkup_source (id) DEFERRABLE INITIALLY IMMEDIATE,
  list_type_id bigint REFERENCES common.lkup_source_list_type (id) DEFERRABLE INITIALLY IMMEDIATE,
  list_url text,
  storage_path text,
  file_name text,
  file_type text,
  mime_type text,
  file_size bigint,
  file_hash text UNIQUE NOT NULL,
  file_version text,
  downloaded_at timestamptz DEFAULT (now()),
  parsed_at timestamptz,
  status text DEFAULT 'DOWNLOADED',
  download_method text
);
COMMENT ON COLUMN raw.watchlist_file.id IS 'Unique identifier of the downloaded source file.';
COMMENT ON COLUMN raw.watchlist_file.source_id IS 'Reference to the source from which the file was downloaded.';
COMMENT ON COLUMN raw.watchlist_file.list_type_id IS 'Reference to the source list type associated with the file.';
COMMENT ON COLUMN raw.watchlist_file.list_url IS 'Exact URL from which the file was downloaded.';
COMMENT ON COLUMN raw.watchlist_file.storage_path IS 'Internal storage location of the downloaded file.';
COMMENT ON COLUMN raw.watchlist_file.file_name IS 'Original downloaded file name.';
COMMENT ON COLUMN raw.watchlist_file.file_type IS 'File format (e.g., JSON, JSONL, XML, CSV, XLSX, HTML, PDF).';
COMMENT ON COLUMN raw.watchlist_file.mime_type IS 'MIME type of the downloaded file.';
COMMENT ON COLUMN raw.watchlist_file.file_size IS 'File size in bytes.';
COMMENT ON COLUMN raw.watchlist_file.file_hash IS 'Unique hash value used to detect file-level changes and prevent duplicate downloads.';
COMMENT ON COLUMN raw.watchlist_file.file_version IS 'Source file version identifier or release version provided by the publisher.';
COMMENT ON COLUMN raw.watchlist_file.downloaded_at IS 'Timestamp when the file was downloaded.';
COMMENT ON COLUMN raw.watchlist_file.parsed_at IS 'Timestamp when parsing completed successfully.';
COMMENT ON COLUMN raw.watchlist_file.status IS 'Current processing status (e.g., DOWNLOADED, PARSED, FAILED, ARCHIVED).';


-- =====================================================================
-- LEVEL 5: Core Processing & Remaining Raw Tables
-- =====================================================================

CREATE TABLE raw.watchlist_file_log (
  id bigserial PRIMARY KEY,
  file_id bigint NOT NULL REFERENCES raw.watchlist_file (id) DEFERRABLE INITIALLY IMMEDIATE,
  event_time timestamptz DEFAULT (now()),
  step text NOT NULL,
  status text NOT NULL,
  message text,
  error_code text,
  error_details text,
  duration_ms bigint
);
COMMENT ON COLUMN raw.watchlist_file_log.id IS 'Unique processing log entry.';
COMMENT ON COLUMN raw.watchlist_file_log.file_id IS 'Reference to the processed file.';
COMMENT ON COLUMN raw.watchlist_file_log.event_time IS 'Timestamp when the processing event occurred.';
COMMENT ON COLUMN raw.watchlist_file_log.step IS 'Pipeline step (e.g., DOWNLOAD, VALIDATION, PARSING, RAW_INSERT, STAGING, CORE_INSERT).';
COMMENT ON COLUMN raw.watchlist_file_log.status IS 'Processing result (e.g., STARTED, SUCCESS, FAILED, SKIPPED).';
COMMENT ON COLUMN raw.watchlist_file_log.message IS 'Processing message or summary.';
COMMENT ON COLUMN raw.watchlist_file_log.error_code IS 'Application or parser error code, if any.';
COMMENT ON COLUMN raw.watchlist_file_log.error_details IS 'Detailed error or exception message.';
COMMENT ON COLUMN raw.watchlist_file_log.duration_ms IS 'Execution time of the processing step in milliseconds.';


CREATE TABLE raw.unparsed_watchlist_payload (
  id bigserial PRIMARY KEY,
  watchlist_file_id bigint NOT NULL REFERENCES raw.watchlist_file (id) DEFERRABLE INITIALLY IMMEDIATE,
  external_id text NOT NULL,
  raw_json jsonb NOT NULL,
  inserted_at timestamptz DEFAULT (now())
);
COMMENT ON COLUMN raw.unparsed_watchlist_payload.id IS 'Unique identifier of the raw source record.';
COMMENT ON COLUMN raw.unparsed_watchlist_payload.watchlist_file_id IS 'Reference to the source file.';
COMMENT ON COLUMN raw.unparsed_watchlist_payload.external_id IS 'Original unique identifier assigned by the source.';
COMMENT ON COLUMN raw.unparsed_watchlist_payload.raw_json IS 'Original source record in its native format without transformation.';
COMMENT ON COLUMN raw.unparsed_watchlist_payload.inserted_at IS 'Timestamp when the raw record was stored.';


CREATE TABLE raw.list_attachment (
  id bigserial PRIMARY KEY,
  raw_file_id bigint NOT NULL REFERENCES raw.watchlist_file (id) DEFERRABLE INITIALLY IMMEDIATE,
  attachment_id bigint NOT NULL REFERENCES raw.attachment (id) DEFERRABLE INITIALLY IMMEDIATE,
  created_at timestamptz DEFAULT (now())
);
COMMENT ON COLUMN raw.list_attachment.id IS 'Unique identifier of the list attachment.';
COMMENT ON COLUMN raw.list_attachment.raw_file_id IS 'Reference to the source file that contains this attachment.';
COMMENT ON COLUMN raw.list_attachment.attachment_id IS 'Reference to the extracted attachment.';
COMMENT ON COLUMN raw.list_attachment.created_at IS 'Timestamp when the attachment mapping was created.';


CREATE TABLE raw.member_attachment (
  id bigserial PRIMARY KEY,
  external_id text NOT NULL,
  attachment_id bigint NOT NULL REFERENCES raw.attachment (id) DEFERRABLE INITIALLY IMMEDIATE,
  attachment_type text,
  created_at timestamptz DEFAULT (now())
);
COMMENT ON COLUMN raw.member_attachment.external_id IS 'Original source entity identifier.';
COMMENT ON COLUMN raw.member_attachment.attachment_type IS 'PHOTO, PASSPORT, DOCUMENT, ADVISORY, COURT_ORDER, WANTED_POSTER, EVIDENCE, OTHER.';
COMMENT ON COLUMN raw.member_attachment.created_at IS 'Timestamp when the attachment mapping was created.';


CREATE TABLE core.watchlist_member (
  id bigserial PRIMARY KEY,
  raw_file_id bigint NOT NULL REFERENCES raw.watchlist_file (id) DEFERRABLE INITIALLY IMMEDIATE,
  raw_member_id bigint REFERENCES raw.unparsed_watchlist_payload (id) DEFERRABLE INITIALLY IMMEDIATE,
  vv_member_id bigint NOT NULL DEFAULT nextval('core.vv_member_id_seq'),
  source_id bigint NOT NULL REFERENCES common.lkup_source (id) DEFERRABLE INITIALLY IMMEDIATE,
  list_type_id bigint NOT NULL REFERENCES common.lkup_source_list_type (id) DEFERRABLE INITIALLY IMMEDIATE,
  external_id text,
  entity_type_id bigint NOT NULL REFERENCES common.lkup_entity_type (id) DEFERRABLE INITIALLY IMMEDIATE,
  version_no int NOT NULL DEFAULT 1,
  is_current boolean NOT NULL DEFAULT true,
  record_hash text NOT NULL,
  valid_from timestamptz NOT NULL DEFAULT (now()),
  valid_to timestamptz,
  change_type text DEFAULT 'NEW',
  full_payload jsonb NOT NULL,
  created_at timestamptz DEFAULT (now())
);
CREATE UNIQUE INDEX uq_watchlist_member_vv_version ON core.watchlist_member (vv_member_id, version_no);
CREATE UNIQUE INDEX uq_watchlist_member_source_external_version ON core.watchlist_member (list_type_id, external_id, version_no);
COMMENT ON COLUMN core.watchlist_member.id IS 'Unique internal identifier of the watchlist member version.';
COMMENT ON COLUMN core.watchlist_member.raw_file_id IS 'Reference to the source file from which this version originated.';
COMMENT ON COLUMN core.watchlist_member.raw_member_id IS 'Reference to the raw source record used to create this version.';
COMMENT ON COLUMN core.watchlist_member.vv_member_id IS 'Unique internal identifier shared by all versions of the same logical watchlist member.';
COMMENT ON COLUMN core.watchlist_member.source_id IS 'Reference to the source that published the member.';
COMMENT ON COLUMN core.watchlist_member.list_type_id IS 'Reference to the source list type.';
COMMENT ON COLUMN core.watchlist_member.external_id IS 'Stable identifier assigned by the source system.';
COMMENT ON COLUMN core.watchlist_member.entity_type_id IS 'Reference to the member entity type.';
COMMENT ON COLUMN core.watchlist_member.version_no IS 'Sequential version number. Starts at 1 and increments for each update.';
COMMENT ON COLUMN core.watchlist_member.is_current IS 'Indicates whether this is the current active version of the member.';
COMMENT ON COLUMN core.watchlist_member.record_hash IS 'Hash of the normalized record used for change detection.';
COMMENT ON COLUMN core.watchlist_member.valid_from IS 'Timestamp when this version became active.';
COMMENT ON COLUMN core.watchlist_member.valid_to IS 'Timestamp when this version was superseded or deleted. NULL indicates the current version.';
COMMENT ON COLUMN core.watchlist_member.change_type IS 'Member synchronization status (e.g., NEW, UPDATED, DELETED).';
COMMENT ON COLUMN core.watchlist_member.full_payload IS 'Complete standardized member record stored in the VV canonical schema.';
COMMENT ON COLUMN core.watchlist_member.created_at IS 'Timestamp when this version was created.';


-- =====================================================================
-- LEVEL 6: Core Member Details (Dependent on Watchlist Member)
-- =====================================================================

CREATE TABLE core.member_name (
  id bigserial PRIMARY KEY,
  vv_member_id bigint NOT NULL,
  watchlist_member_id bigint NOT NULL REFERENCES core.watchlist_member (id) DEFERRABLE INITIALLY IMMEDIATE,
  name_type citext,
  name citext,
  first_name citext,
  middle_name citext,
  last_name citext,
  normalized_name citext,
  phonetic_key citext,
  phonetic_key_alt citext,
  search_tokens citext,
  language text,
  is_primary boolean NOT NULL DEFAULT false
);
CREATE INDEX ON core.member_name (normalized_name);
COMMENT ON COLUMN core.member_name.id IS 'Unique identifier of the entity name record.';
COMMENT ON COLUMN core.member_name.watchlist_member_id IS 'Reference to the current active watchlist member.';
COMMENT ON COLUMN core.member_name.name_type IS 'Type of name (e.g., Primary, AKA, Legal Name, Native Name).';
COMMENT ON COLUMN core.member_name.name IS 'Complete entity name.';
COMMENT ON COLUMN core.member_name.first_name IS 'Given name. Mainly applicable to individuals.';
COMMENT ON COLUMN core.member_name.middle_name IS 'Middle name.';
COMMENT ON COLUMN core.member_name.last_name IS 'Surname or family name.';
COMMENT ON COLUMN core.member_name.normalized_name IS 'Normalized name generated by VVIF for exact, fuzzy, phonetic, and transliteration matching.';
COMMENT ON COLUMN core.member_name.language IS 'Language of the name.';
COMMENT ON COLUMN core.member_name.is_primary IS 'Indicates whether this is the primary display name.';


CREATE TABLE core.member_alias (
  id bigserial PRIMARY KEY,
  vv_member_id bigint NOT NULL,
  watchlist_member_id bigint NOT NULL REFERENCES core.watchlist_member (id) DEFERRABLE INITIALLY IMMEDIATE,
  alias_type citext,
  alias citext NOT NULL,
  normalized_alias citext,
  phonetic_key citext,
  phonetic_key_alt citext,
  search_tokens citext
);
CREATE INDEX ON core.member_alias (normalized_alias);
COMMENT ON COLUMN core.member_alias.id IS 'Unique identifier of the alias record.';
COMMENT ON COLUMN core.member_alias.watchlist_member_id IS 'Reference to the current active watchlist member.';
COMMENT ON COLUMN core.member_alias.alias_type IS 'Type of alias (e.g., AKA, Former Name, Weak Alias, Strong Alias, Native Name).';
COMMENT ON COLUMN core.member_alias.alias IS 'Alias value.';
COMMENT ON COLUMN core.member_alias.normalized_alias IS 'Normalized alias generated by VVIF for exact, fuzzy, phonetic, and transliteration matching.';


CREATE TABLE core.member_identifier (
  id bigserial PRIMARY KEY,
  vv_member_id bigint NOT NULL,
  watchlist_member_id bigint NOT NULL REFERENCES core.watchlist_member (id) DEFERRABLE INITIALLY IMMEDIATE,
  identifier_type citext NOT NULL,
  identifier_value citext NOT NULL,
  normalized_identifier citext,
  issuing_country citext
);
CREATE INDEX ON core.member_identifier (identifier_value);
CREATE INDEX ON core.member_identifier (normalized_identifier);
COMMENT ON COLUMN core.member_identifier.id IS 'Unique identifier of the identifier record.';
COMMENT ON COLUMN core.member_identifier.watchlist_member_id IS 'Reference to the current active watchlist member.';
COMMENT ON COLUMN core.member_identifier.identifier_type IS 'Type of identifier (e.g., Passport, National ID, IMO Number, Registration Number, Tax ID).';
COMMENT ON COLUMN core.member_identifier.identifier_value IS 'Identifier value.';
COMMENT ON COLUMN core.member_identifier.normalized_identifier IS 'Normalized identifier generated by VVIF for standardized matching.';
COMMENT ON COLUMN core.member_identifier.issuing_country IS 'Country that issued the identifier.';


CREATE TABLE core.member_date (
  id bigserial PRIMARY KEY,
  vv_member_id bigint NOT NULL,
  watchlist_member_id bigint NOT NULL REFERENCES core.watchlist_member (id) DEFERRABLE INITIALLY IMMEDIATE,
  date_type citext NOT NULL,
  year int,
  month int,
  day int,
  is_approximate boolean NOT NULL DEFAULT false,
  note text
);
COMMENT ON COLUMN core.member_date.id IS 'Unique identifier of the entity date record.';
COMMENT ON COLUMN core.member_date.watchlist_member_id IS 'Reference to the current active watchlist member.';
COMMENT ON COLUMN core.member_date.date_type IS 'Type of date (e.g., Birth, Incorporation, Registration, Manufacture, Built, Expiry).';
COMMENT ON COLUMN core.member_date.year IS 'Year component of the date.';
COMMENT ON COLUMN core.member_date.month IS 'Month component of the date.';
COMMENT ON COLUMN core.member_date.day IS 'Day component of the date.';
COMMENT ON COLUMN core.member_date.is_approximate IS 'Indicates whether the date is approximate.';
COMMENT ON COLUMN core.member_date.note IS 'Additional notes regarding the date.';


CREATE TABLE core.member_country (
  id bigserial PRIMARY KEY,
  vv_member_id bigint NOT NULL,
  watchlist_member_id bigint NOT NULL REFERENCES core.watchlist_member (id) DEFERRABLE INITIALLY IMMEDIATE,
  country_type citext NOT NULL,
  country_code citext,
  country_name citext NOT NULL
);
COMMENT ON COLUMN core.member_country.id IS 'Unique identifier of the country record.';
COMMENT ON COLUMN core.member_country.watchlist_member_id IS 'Reference to the current active watchlist member.';
COMMENT ON COLUMN core.member_country.country_type IS 'Type of country (e.g., Nationality, Citizenship, Registration, Flag, Residence).';
COMMENT ON COLUMN core.member_country.country_code IS 'ISO 3166-1 alpha-2 or alpha-3 country code, if available.';
COMMENT ON COLUMN core.member_country.country_name IS 'Country name.';


CREATE TABLE core.member_address (
  id bigserial PRIMARY KEY,
  vv_member_id bigint NOT NULL,
  watchlist_member_id bigint NOT NULL REFERENCES core.watchlist_member (id) DEFERRABLE INITIALLY IMMEDIATE,
  country_name citext,
  state_province citext,
  city citext,
  postal_code citext,
  full_address citext
);
COMMENT ON COLUMN core.member_address.id IS 'Unique identifier of the address record.';
COMMENT ON COLUMN core.member_address.watchlist_member_id IS 'Reference to the current active watchlist member.';
COMMENT ON COLUMN core.member_address.country_name IS 'Country name.';
COMMENT ON COLUMN core.member_address.state_province IS 'State, province, or region.';
COMMENT ON COLUMN core.member_address.city IS 'City or locality.';
COMMENT ON COLUMN core.member_address.postal_code IS 'Postal or ZIP code.';
COMMENT ON COLUMN core.member_address.full_address IS 'Complete formatted address.';


CREATE TABLE core.member_relationship (
  id bigserial PRIMARY KEY,
  vv_member_id bigint NOT NULL,
  watchlist_member_id bigint NOT NULL REFERENCES core.watchlist_member (id) DEFERRABLE INITIALLY IMMEDIATE,
  relationship_type citext NOT NULL,
  related_watchlist_member_id bigint REFERENCES core.watchlist_member (id) DEFERRABLE INITIALLY IMMEDIATE,
  related_normalized_name citext
);
COMMENT ON COLUMN core.member_relationship.id IS 'Unique identifier of the relationship record.';
COMMENT ON COLUMN core.member_relationship.watchlist_member_id IS 'Reference to the current active watchlist member.';
COMMENT ON COLUMN core.member_relationship.relationship_type IS 'Type of relationship (e.g., Owner, Director, Parent, Subsidiary, Associate, Relative).';
COMMENT ON COLUMN core.member_relationship.related_watchlist_member_id IS 'Reference to the current watchlist member record representing the related entity.';
COMMENT ON COLUMN core.member_relationship.related_normalized_name IS 'Related entity name as provided by the source.';


CREATE TABLE core.member_program (
  id bigserial PRIMARY KEY,
  vv_member_id bigint NOT NULL,
  watchlist_member_id bigint NOT NULL REFERENCES core.watchlist_member (id) DEFERRABLE INITIALLY IMMEDIATE,
  program_type citext,
  authority citext,
  program_name citext NOT NULL
);
COMMENT ON COLUMN core.member_program.id IS 'Unique identifier of the program record.';
COMMENT ON COLUMN core.member_program.watchlist_member_id IS 'Reference to the current active watchlist member.';
COMMENT ON COLUMN core.member_program.program_type IS 'Type of program (e.g., Sanctions, Regulatory, Watchlist).';
COMMENT ON COLUMN core.member_program.authority IS 'Authority responsible for the program.';
COMMENT ON COLUMN core.member_program.program_name IS 'Program name.';


CREATE TABLE core.member_contact (
  id bigserial PRIMARY KEY,
  vv_member_id bigint NOT NULL,
  watchlist_member_id bigint NOT NULL REFERENCES core.watchlist_member (id) DEFERRABLE INITIALLY IMMEDIATE,
  contact_type citext NOT NULL,
  contact_value citext NOT NULL
);
COMMENT ON COLUMN core.member_contact.id IS 'Unique identifier of the contact record.';
COMMENT ON COLUMN core.member_contact.watchlist_member_id IS 'Reference to the current active watchlist member.';
COMMENT ON COLUMN core.member_contact.contact_type IS 'Type of contact (e.g., Email, Phone, Website, Social Media, Fax).';
COMMENT ON COLUMN core.member_contact.contact_value IS 'Contact value.';


CREATE TABLE core.member_risk_category (
    id bigserial PRIMARY KEY,
    vv_member_id bigint NOT NULL,
    watchlist_member_id bigint NOT NULL REFERENCES core.watchlist_member(id) DEFERRABLE INITIALLY IMMEDIATE,
    version_no int NOT NULL,
    risk_details jsonb NOT NULL DEFAULT '{}'::jsonb,
    risk_details_hash text NOT NULL,
    valid_from timestamptz NOT NULL DEFAULT NOW(),
    valid_to timestamptz,
    is_current boolean NOT NULL DEFAULT TRUE,
    created_at timestamptz NOT NULL DEFAULT NOW()
);

-- Create the indexes
CREATE INDEX idx_member_risk_category_watchlist_current 
    ON core.member_risk_category (watchlist_member_id, is_current);

CREATE INDEX idx_member_risk_category_vv_current 
    ON core.member_risk_category (vv_member_id, is_current);

CREATE INDEX idx_member_risk_category_hash 
    ON core.member_risk_category (risk_details_hash);

-- Apply column documentation/comments
COMMENT ON COLUMN core.member_risk_category.id IS 'Unique identifier of the member risk classification record.';
COMMENT ON COLUMN core.member_risk_category.vv_member_id IS 'Business identifier of the unified member.';
COMMENT ON COLUMN core.member_risk_category.watchlist_member_id IS 'FK to watchlist member.';
COMMENT ON COLUMN core.member_risk_category.version_no IS 'Version inherited from watchlist member.';
COMMENT ON COLUMN core.member_risk_category.risk_details IS 'Risk classification details including category, subcategory, evidence, contributing sources, confidence, reviewer information, and any future extensible attributes.';
COMMENT ON COLUMN core.member_risk_category.risk_details_hash IS 'SHA-256 hash of the canonical risk_details JSON used for change detection.';
COMMENT ON COLUMN core.member_risk_category.valid_from IS 'Timestamp when this version became effective.';
COMMENT ON COLUMN core.member_risk_category.valid_to IS 'Timestamp when this version expired. NULL indicates the current version.';
COMMENT ON COLUMN core.member_risk_category.is_current IS 'TRUE for the active version; FALSE for historical versions.';
COMMENT ON COLUMN core.member_risk_category.created_at IS 'Timestamp when this record was created.';

-- Apply index documentation/comments
COMMENT ON INDEX core.idx_member_risk_category_watchlist_current IS 'Retrieve current risk classifications for a member.';
COMMENT ON INDEX core.idx_member_risk_category_vv_current IS 'Lookup current classifications by business member ID.';
COMMENT ON INDEX core.idx_member_risk_category_hash IS 'Detect changes in risk classification details.';


CREATE TABLE delivery.watchlist_daily_delta_actions (
  id bigserial PRIMARY KEY,
  effective_date date NOT NULL,
  action text NOT NULL,
  vv_member_id bigint NOT NULL,
  watchlist_member_id bigint NOT NULL REFERENCES core.watchlist_member (id) DEFERRABLE INITIALLY IMMEDIATE
);
COMMENT ON COLUMN delivery.watchlist_daily_delta_actions.id IS 'Unique identifier of the delta record.';
COMMENT ON COLUMN delivery.watchlist_daily_delta_actions.effective_date IS 'Effective date of the delta record.';
COMMENT ON COLUMN delivery.watchlist_daily_delta_actions.action IS 'Delta operation type (ADD, UPDATE, DELETE).';
COMMENT ON COLUMN delivery.watchlist_daily_delta_actions.watchlist_member_id IS 'Reference to the current active watchlist member.';


CREATE TABLE core.spoke_run_log (
    run_date DATE PRIMARY KEY,
    status VARCHAR(50),
    completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);



-- Search-optimized materialized view
--it refereshs after delivery.generate_watchlist_daily_delta_actions by "REFRESH MATERIALIZED VIEW CONCURRENTLY core.mv_screening_member_search" command
CREATE MATERIALIZED VIEW core.mv_screening_member_search AS
SELECT DISTINCT ON (source_type, source_record_id, search_term)
    source_type,
    source_record_id,
    vv_member_id,
    search_term,
    phonetic_key,
    phonetic_key_alt,
    search_tokens,
    display_name,
    entity_type,
    source_name,
    source_logo_path,
    last_updated
FROM (

SELECT 
    'Watchlist'::TEXT AS source_type,
	wm.id AS source_record_id,
    wm.vv_member_id,
    mn.normalized_name AS search_term,
    mn.phonetic_key,
	mn.phonetic_key_alt,
    mn.search_tokens,
    mn.name AS display_name,
    et.name AS entity_type,
    src.name AS source_name,
    att.storage_path AS source_logo_path,
    wm.created_at AS last_updated
FROM core.watchlist_member wm
JOIN core.member_name mn ON wm.id = mn.watchlist_member_id
JOIN common.lkup_entity_type et ON wm.entity_type_id = et.id
JOIN common.lkup_source src ON wm.source_id = src.id
LEFT JOIN raw.attachment att ON src.logo_attachment_id = att.id
WHERE wm.is_current = true

UNION ALL

SELECT 
    'Watchlist'::TEXT AS source_type,
	wm.id AS source_record_id,
    wm.vv_member_id,
    ma.normalized_alias AS search_term,
    ma.phonetic_key,
	ma.phonetic_key_alt,
    ma.search_tokens,
    ma.alias AS display_name,
    et.name AS entity_type,
    src.name AS source_name,
    att.storage_path AS source_logo_path,
    wm.created_at AS last_updated
FROM core.watchlist_member wm
JOIN core.member_alias ma ON wm.id = ma.watchlist_member_id
JOIN common.lkup_entity_type et ON wm.entity_type_id = et.id
JOIN common.lkup_source src ON wm.source_id = src.id
LEFT JOIN raw.attachment att ON src.logo_attachment_id = att.id
WHERE wm.is_current = true

) combined_results
ORDER BY source_type, source_record_id, search_term, last_updated DESC;

-- The critical GIN index for rapid fuzzy searching and token
CREATE INDEX idx_mv_member_search_term_trgm ON core.mv_screening_member_search USING GIN (search_term gin_trgm_ops);
CREATE INDEX idx_mv_member_search_tokens_trgm ON core.mv_screening_member_search USING GIN (search_tokens gin_trgm_ops);

-- The two B-Tree indexes for instant phonetic lookups
CREATE INDEX idx_mv_member_search_phonetic ON core.mv_screening_member_search (phonetic_key);
CREATE INDEX idx_mv_member_search_phonetic_alt ON core.mv_screening_member_search (phonetic_key_alt);

-- Unique index required to allow CONCURRENTLY refreshing
CREATE UNIQUE INDEX idx_mv_member_search_unique ON core.mv_screening_member_search (source_type, source_record_id, search_term);

-- =====================================================================
-- CUSTOMER Schema
-- =====================================================================
CREATE TABLE customer.customer (
  id SERIAL PRIMARY KEY,
  customer_name VARCHAR(255) NOT NULL,
  customer_code CITEXT UNIQUE NOT NULL,
  contact_email VARCHAR(255),
  status VARCHAR(50) DEFAULT 'ACTIVE',
  created_at TIMESTAMP DEFAULT (now())
);
COMMENT ON TABLE customer.customer IS 'Central system profile organization directory managing customer master profiles and organizational units.';
COMMENT ON COLUMN customer.customer.id IS 'Primary serial unique identifier key representing distinct tenant organizations.';
COMMENT ON COLUMN customer.customer.customer_name IS 'Official commercial legal registered title designation of the customer.';
COMMENT ON COLUMN customer.customer.customer_code IS 'Unique alphanumeric abbreviation code representing the client organization (e.g., BOC, HSBC).';
COMMENT ON COLUMN customer.customer.contact_email IS 'Primary administrative destination address used for corporate communication metrics.';
COMMENT ON COLUMN customer.customer.status IS 'Active lifecycle status configuration indicator toggle. Expected: ACTIVE, SUSPENDED, TERMINATED.';
COMMENT ON COLUMN customer.customer.created_at IS 'Definitive database track record capturing instantiation date profiles.';


-- 1. Entity Type Export Filter Table
CREATE TABLE customer.filter_entity_type (
  id bigserial PRIMARY KEY,
  customer_id int NOT NULL REFERENCES customer.customer(id) ON DELETE CASCADE,
  entity_type_id bigint NOT NULL REFERENCES common.lkup_entity_type(id),
  created_at timestamptz DEFAULT now(),
  CONSTRAINT uq_cust_entity_filter UNIQUE (customer_id, entity_type_id)
);

-- 2. List Type Export Filter Table
CREATE TABLE customer.filter_list_type (
  id bigserial PRIMARY KEY,
  customer_id int NOT NULL REFERENCES customer.customer(id) ON DELETE CASCADE,
  list_type_id bigint NOT NULL REFERENCES common.lkup_source_list_type(id),
  created_at timestamptz DEFAULT now(),
  CONSTRAINT uq_cust_list_filter UNIQUE (customer_id, list_type_id)
);

-- 3. Risk Category Export Filter Table
CREATE TABLE customer.filter_risk_category (
  id bigserial PRIMARY KEY,
  customer_id int NOT NULL REFERENCES customer.customer(id) ON DELETE CASCADE,
  risk_category_id bigint NOT NULL REFERENCES common.lkup_risk_category(id),
  created_at timestamptz DEFAULT now(),
  CONSTRAINT uq_cust_risk_filter UNIQUE (customer_id, risk_category_id)
);