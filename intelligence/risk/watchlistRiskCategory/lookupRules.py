"""Decided lookup rules: list / measure / programme / FBI category -> risk labels (single source for workbook and labels)."""
import re
from pipelines.watchlistConfigs import WATCHLIST_CONFIGS

SAN, PEP, REG, LE = "Sanctions", "PEP", "Regulatory", "Law Enforcement"
TER, PRO, HR = "Terrorism / Terrorist Financing", "Proliferation / Proliferation Financing", "Human Rights / Atrocities"
CYB, TRF, ORG = "Crime-Cybercrime", "Crime-Trafficking & Smuggling", "Crime-Organized Crime"
COR, VIO, FRA = "Crime-Corruption", "Crime-Violent Crime", "Crime-Fraud"

GUIDE, DATA = "guide", "data review 2026-10-11"
TEXT = "text decides"

DATASET_CATEGORY_LABELS = {
    "Sanctions": SAN,
    "PEP": PEP,
    "Regulatory": REG,
    "Law Enforcement": LE,
}

LISTS = {
    **{l: ([SAN], GUIDE, "Official sanctions list") for l in (
        "OFAC-SDN", "UN-SANCTIONS", "EU-FINANCIAL-SANCTIONS", "EU-TRAVEL-BAN", "UKSL", "SECO-SANCTIONS", "DFAT",
        "CANADA_CONSOLIDATED_SANCTIONS", "EU-DESIGNATED-VESSELS")},
    "OFAC-NON-SDN": ([SAN], DATA, "Official US restrictive measures (sectoral, investment, menu-based) fit the Sanctions definition"),
    "ATC-DESIGNATED-TERRORIST-INDIVIDUALS": ([SAN, TER], GUIDE, "Philippine ATC terrorist designation"),
    "ATC-DESIGNATED-TERRORIST-GROUPS": ([SAN, TER], GUIDE, "Philippine ATC terrorist designation"),
    "US-STATE-TERRORIST-EXCLUSION": ([TER], GUIDE, "Terrorist Exclusion List; not Sanctions"),
    **{l: ([REG], GUIDE, "Development-bank / regulator debarment, blacklist or warning") for l in (
        "ADB-DEBARMENT-SUSPENSION", "AFDB-DEBARRED-ENTITIES", "WORLD-BANK-DEBARRED", "WORLD-BANK-OTHER-SANCTIONS",
        "GPPB-BLACKLISTED-ENTITIES", "CFTC-RED-LIST")},
    **{l: ([LE], GUIDE, "Wanted / red-notice list: every record is a wanted person") for l in (
        "INTERPOL-RED-NOTICES", "US-MARSHALS-PROFILED-FUGITIVES", "EU-MOST-WANTED", "NCA-MOST-WANTED")},
    "FBI-WANTED": ([], DATA, "Mixes wanted persons with victims and missing persons: the FBI category decides"),
    "CIA-WORLD-LEADERS-HISTORICAL": ([PEP], DATA, "Heads of state and cabinet members; every sampled record is PEP"),
    "PH-HOUSE-MEMBERS": ([PEP], GUIDE, "Members of the House of Representatives"),
    "DILG-LOCAL-OFFICIALS": ([PEP], DATA, "Only mayors, vice-mayors, governors and municipal / city / provincial council members; no barangay officials"),
    "COMELEC-2025-SENATORS": ([PEP], DATA, "Dataset category is PEP; national senate candidates are PEP (user decision 2026-10-13)"),
    "DNFBP": ([], GUIDE, "Register of designated non-financial businesses; not adverse, excluded from risk labels"),
    "DMW-RECRUITMENT-AGENCIES": ([], DATA, "Licence register: the licence status decides"),
}

REGULATORY_MEASURES = {"Debarment", "Regulatory Warning", "Blacklisting", "Letter of Reprimand", "Conditional Non-Debarment"}

DMW_ADVERSE = re.compile(r"Cancelled|Banned|Suspen|Revoked|Denied Renewal", re.I)

PROGRAM_RULES = [
    (r"^ICC-EO14203$", [], DATA, "Targets ICC officials; no crime implied"),
    (r"^NS-PLC$", [PEP], DATA, "Palestinian Legislative Council list: members are legislators"),
    (r"^(SDGT|FTO|SDT)$|terror|ISIL|Da.esh|Al-Qa|Al-Kaida|^1267|^(DAESH|CTG|LTG|ATTG)$", [TER], GUIDE, "Terrorism programme"),
    (r"^(TAQA|EUAQ)$", [TER], DATA, "EU code for the ISIL/Al-Qaida regime (same regime the guide accepts by name)"),
    (r"^TERR$", [TER], DATA, "EU terrorist list (persons involved in terrorist acts)"),
    (r"^HAM$|Hamas", [TER], DATA, "Regime targets support for violent actions by Hamas / PIJ; Canada names the same regime 'Hamas Terrorist Attacks'"),
    (r"^1373|Taliban|^1988|Afghanistan|^AFG$", [], GUIDE, "No conduct label from the name alone"),
    (r"Al-Shabaab|^2713", [], DATA, "Somalia peace-and-security criteria, not terrorism by definition; text decides"),
    (r"^IRGC$", [], DATA, "Tag covers proliferation, terrorism and other bases; text decides"),
    (r"NPWMD|Non-?proliferation|proliferation", [PRO], GUIDE, "Non-proliferation programme"),
    (r"^CHEM$|Chemical Weapons", [PRO], DATA, "Chemical-weapons regime; chemical weapons are WMD"),
    (r"\(Nuclear\)", [], DATA, "Designees include conventional military and financial bodies; text decides"),
    (r"DPRK|Nordkorea|Korea|^PRK$|^1718|^1737|^IFSR$", [], GUIDE, "Country regime; needs WMD wording in the text"),
    (r"^CYBER\d?$|^CYB$|Cyber|cyber-attacks", [CYB], DATA, "Cyber regime (EO 13694 / 14144 / 14306, EU and UK cyber regimes)"),
    (r"SDNTK|^SDNT$|ILLICIT-DRUGS|Trafficking in Persons", [TRF], DATA, "Narcotics / human-trafficking programme"),
    (r"^TCO$", [ORG], GUIDE, "Transnational criminal organisation"),
    (r"^GLOMAG$|JVCFOR|NHRAA", [], GUIDE, "Magnitsky-type regime: corruption and/or human rights, text decides"),
    (r"Human Rights|-HR$|^HR$|^HRIT|^MAGNIT$|^UHRPA$", [HR], DATA, "Human-rights regime"),
    (r"Anti-Corruption|Thematic . Corruption", [COR], GUIDE, "Anti-corruption regime"),
    (r"Extremist Settler Violence|^HOSTAGES", [VIO], DATA, "Regime defined by violent conduct (settler violence, hostage-taking)"),
]

FBI = {
    "Ten Most Wanted Fugitives": [LE], "Most Wanted Terrorists": [LE, TER], "Domestic Terrorism": [LE, TER],
    "Cyber's Most Wanted": [LE, CYB], "Most Wanted Fraudster": [LE, FRA], "Criminal Enterprise Investigations": [LE, ORG],
    "Human Trafficking": [LE, TRF], "Violent Crime - Murders": [LE, VIO], "Additional Violent Crimes": [LE, VIO],
    "Crimes Against Children": [LE], "Counterintelligence": [LE], "Iran": [LE], "Transnational Repression": [LE],
    "Law Enforcement Assistance": [LE], "Parental Kidnapping": [LE], "White-Collar Crime": [LE], "While-Collar Crime": [LE],
}


def list_rule(lst):
    """Labels, basis and reason for a list."""
    return LISTS.get(lst, ([], TEXT, "List not reviewed yet"))


def measure_rule(lst, mtype, note=""):
    """Labels, basis and reason for one measure on one list."""
    if lst == "US-STATE-TERRORIST-EXCLUSION":
        return [], GUIDE, "Travel exclusion; not Sanctions"
    if lst == "DMW-RECRUITMENT-AGENCIES":
        if DMW_ADVERSE.search(note or ""):
            return [REG], DATA, "Licence cancelled, revoked, suspended, banned or renewal denied"
        return [], DATA, "Valid, expired, inactive, ceased or delisted licence: no adverse action stated"
    if mtype in REGULATORY_MEASURES:
        return [REG], GUIDE, "Debarment / blacklist / regulatory action"
    return [SAN], GUIDE, "Sanctions measure"


def program_rule(lst, ptype, program):
    """Labels, basis and reason for one programme value; None when the value is not a lookup value."""
    if lst == "FBI-WANTED":
        if program in FBI:
            return FBI[program], DATA, "FBI category of wanted persons"
        return [], DATA, "Victim, missing-person or information notice: text decides"
    if ptype not in ("Sanctions", "Counter-Terrorism"):
        return None
    base = [SAN] if ptype == "Sanctions" else [SAN, TER]
    for pattern, labels, basis, reason in PROGRAM_RULES:
        if re.search(pattern, program or "", re.I):
            return base + [l for l in labels if l not in base], basis, reason
    return base, GUIDE, "Country / situation regime: Sanctions only"


def record_rule_labels(lst, record):
    """Return source-backed labels and their provenance for one record."""
    out = {}
    config = WATCHLIST_CONFIGS.get(lst, {})
    if config.get("use_dataset_category_as_risk_label", 1) not in (0, 1):
        raise ValueError(f"Invalid use_dataset_category_as_risk_label for {lst}")
    for source in (record.get("Sources") or []) if config.get("use_dataset_category_as_risk_label", 1) else []:
        if not isinstance(source, dict):
            continue
        category = source.get("DatasetCategory")
        categories = category if isinstance(category, list) else [category]
        for category in categories:
            if not isinstance(category, str):
                continue
            category = category.strip()
            label = DATASET_CATEGORY_LABELS.get(category)
            if label:
                out.setdefault(label, f"dataset category {category}")
    for label in list_rule(lst)[0]:
        out.setdefault(label, f"list {lst}")
    for m in record.get("Measures") or []:
        for label in measure_rule(lst, m.get("MeasureType") or "", m.get("Note") or "")[0]:
            out.setdefault(label, f"measure {m.get('MeasureType')}")
    for p in record.get("Programs") or []:
        rule = program_rule(lst, p.get("ProgramType"), p.get("Program"))
        for label in (rule[0] if rule else []):
            out.setdefault(label, f"programme {p.get('Program')}")
    for mapping in config.get("structured_risk_mappings", []):
        field = mapping["field"]
        for item in record.get(field) or []:
            if not isinstance(item, dict):
                continue
            type_key = mapping.get("type_key")
            if type_key and item.get(type_key) != mapping["type_value"]:
                continue
            raw_value = item.get(mapping["value_key"])
            if not isinstance(raw_value, str):
                continue
            value = raw_value.strip()
            label = mapping["labels"].get(value)
            if label:
                provenance = f"{field}.{mapping.get('type_value', mapping['value_key'])}: {value}"
                out.setdefault(label, provenance)
    return out
