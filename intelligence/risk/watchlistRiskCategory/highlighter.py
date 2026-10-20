"""Split watchlist records into evidence units and flag candidate (unit, label) pairs with trigger words."""
import re
import unicodedata

SKIP_FIELD = re.compile(r"^(source list|entity type|website|email|tel|fax|gender|.*\bnumber|.*\bid|date of .*)$", re.I)
FIELD = re.compile(r"^([A-Z][A-Za-z ]{1,40}?) ?: ")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"“(])")
HAS_LETTERS = re.compile(r"[^\W\d_]{3,}")
SKIP_LABEL_FIELDS = {"PEP": {"Programs"}}
NAME_ONLY_PROGRAMS = {"OFAC-SDN", "OFAC-NON-SDN", "UN-SANCTIONS", "EU-FINANCIAL-SANCTIONS", "EU-TRAVEL-BAN", "UKSL",
                      "SECO-SANCTIONS", "DFAT", "EU-DESIGNATED-VESSELS", "ATC-DESIGNATED-TERRORIST-INDIVIDUALS",
                      "ATC-DESIGNATED-TERRORIST-GROUPS", "US-STATE-TERRORIST-EXCLUSION", "FBI-WANTED"}
BOILERPLATE_FIELDS = {"US-STATE-TERRORIST-EXCLUSION": {"Additional info"}, "PH-HOUSE-MEMBERS": {"Additional info"}}
BOILERPLATE_UNIT = re.compile(r"Rewards For Justice|^\s*(?:Council (?:Decision|Regulation|Implementing)|Executive Order)", re.I)
FIGHTING_BEFORE = re.compile(r"\b(?:anti|counter|countering|against|combat(?:ing|ting)|prevention of)\s+(?:\w+\s+)?$")
FIGHTING_AFTER = re.compile(r"^\s+(?:control\b|enforcement\s+(?:administration|agenc|authorit|unit|department|officer))")

TAXONOMY_TRIGGERS = {
    "PEP": ["head of state", "minister*", "senior judicial official", "senior military official", "prominent public function*",
            "intergovernmental official", "politically exposed"],
    "RCA": ["spouse", "child of", "family member*", "business associate*", "close associate*", "beneficial owner*"],
    "Crime-Corruption": ["brib*", "kickback*", "graft", "abuse of office", "abuse of public office", "improper payment*",
                         "corrupt*", "diversion of public funds"],
    "Crime-Fraud": ["fraud*", "ponzi", "scam*", "false financial representation*"],
    "Crime-Money Laundering": ["launder*", "concealment of criminal funds", "criminal proceeds", "illicit proceeds",
                               "shell compan*"],
    "Crime-Trafficking & Smuggling": ["traffick*", "smuggl*", "illicit goods"],
    "Crime-Cybercrime": ["computer access", "hacking", "ransomware", "malware", "phishing", "cyber*", "data theft"],
    "Crime-Violent Crime": ["murder*", "assault*", "kidnap*", "hostage*", "physical harm", "violent*", "extortion"],
    "Crime-Theft & Property Crime": ["theft*", "robber*", "embezzl*", "misappropriat*", "stolen property",
                                     "unlawful appropriation"],
    "Crime-Forgery & Counterfeiting": ["forged", "forgery", "forgeries", "counterfeit*", "falsified", "fake identity",
                                       "fake document*"],
    "Crime-Tax Crime": ["tax evasion", "tax fraud", "tax return*", "taxable income", "tax refund*", "tax charge*"],
    "Crime-Securities & Market Crime": ["insider dealing", "market manipulation", "securities fraud", "pump and dump",
                                        "sham trade*", "securities offering*"],
    "Crime-Organized Crime": ["organized criminal group*", "organised criminal group*", "criminal syndicate*", "mafia",
                              "criminal enterprise*", "criminal network*", "trafficking network*"],
    "Crime-Environmental & Wildlife Crime": ["illegal logging", "illegal fishing", "illegal mining", "wildlife", "poach*",
                                             "environmental offen*"],
    "Terrorism / Terrorist Financing": ["terror*"],
    "Proliferation / Proliferation Financing": ["wmd", "prolifera*", "nuclear", "chemical weapon*"],
    "Human Rights / Atrocities": ["genocide", "crimes against humanity", "war crime*", "tortur*", "extrajudicial",
                                  "enforced disappearance*", "human rights", "forced displacement", "persecut*"],
    "Judicial / Court": ["indict*", "arraign*", "trial*", "hearing*", "convict*", "guilty", "acquit*", "sentenc*",
                         "dismissed", "appeal*", "lawsuit*", "judgment*", "judgement*"],
    "State-Owned": ["state owned", "government owned", "government ownership", "state ownership", "government shareholder*"],
}

EXTRA_TRIGGERS = {
    "PEP": ["president*", "prime minister", "senator*", "congressman", "congresswoman", "parliament*", "governor*",
            "mayor*", "ambassador*", "deputy", "min", "secretary", "judge*", "chief justice", "central bank",
            "head of government", "representative*", "vice mayor", "councilor*", "councillor*", "official*", "government"],
    "RCA": ["relative*", "brother*", "sister*", "father", "mother", "son", "daughter", "wife", "husband", "nephew",
            "niece", "cousin", "in law", "family", "associate*", "associated with", "partner*"],
    "Crime-Corruption": ["public funds", "abuse of power", "abuse of authority", "influence peddling",
                         "trading in influence", "cohecho", "soborno", "corrupcion", "corrupcao", "bestechung",
                         "korruption", "peculado", "nepotism"],
    "Crime-Fraud": ["deceit*", "deception", "deceptive", "swindl*", "false pretences", "false pretenses",
                    "misrepresent*", "false statement*", "estafa", "fraude", "betrug", "escroquerie", "defraud*",
                    "cheat*", "identity theft"],
    "Crime-Money Laundering": ["blanqueo", "lavado", "lavagem", "blanchiment", "geldwasche", "money mule*",
                               "proceeds of crime", "proceeds", "structuring"],
    "Crime-Trafficking & Smuggling": ["narcotic*", "drug*", "cocaine", "heroin", "methamphetamine", "fentanyl",
                                      "marijuana", "cannabis", "controlled substance*", "psychotropic", "narcotrafico",
                                      "trafico", "trafic", "contraband", "illegal immigration", "people smuggling",
                                      "estupefacientes", "stupefiants", "drogas", "betaubungsmittel*",
                                      "sexual exploitation", "forced labor", "forced labour", "prostitution"],
    "Crime-Cybercrime": ["computer intrusion", "computer fraud", "unauthorized access", "unauthorised access", "botnet*",
                         "ddos", "carding", "hacker*", "hacked", "spyware", "data breach*"],
    "Crime-Violent Crime": ["kill*", "homicid*", "shoot*", "shot", "stab*", "rape*", "raping", "sexual assault",
                            "armed", "firearm*", "weapon*", "bodily injur*", "bodily harm", "manslaughter", "abduct*",
                            "carjack*", "homicidio", "asesinato", "secuestro", "meurtre", "assassinat*", "enlevement",
                            "mord", "totschlag", "lesiones", "violence", "arson", "threat*", "attack*", "bomb*",
                            "explosive*"],
    "Crime-Theft & Property Crime": ["steal*", "stole", "stolen", "burglar*", "larcen*", "pickpocket*", "robo", "hurto",
                                     "vol", "diebstahl", "raub", "peculado", "malversacion", "breach of trust",
                                     "fraudulent conversion", "loot*"],
    "Crime-Forgery & Counterfeiting": ["forg*", "false document*", "falsif*", "fake", "falsificacion", "falsificacao",
                                       "faux", "falschung", "urkundenfalschung", "document fraud", "passport fraud"],
    "Crime-Tax Crime": ["tax", "taxes", "taxation", "evasion fiscal", "fraude fiscal", "defraudacion",
                        "steuerhinterziehung", "vat", "customs dut*", "excise", "fiscal"],
    "Crime-Securities & Market Crime": ["insider trading", "securities", "stock manipulation", "market abuse",
                                        "manipulat*", "investment adviser*"],
    "Crime-Organized Crime": ["organized crime", "organised crime", "criminal organization*", "criminal organisation*",
                              "cartel*", "gang*", "racketeer*", "rico", "criminal group*", "criminal association*",
                              "asociacion ilicita", "organizacion criminal", "association de malfaiteurs",
                              "kriminelle vereinigung", "crime ring", "crime family", "clan", "ndrangheta", "camorra",
                              "cosa nostra", "yakuza", "triad*", "syndicate*"],
    "Crime-Environmental & Wildlife Crime": ["environmental", "deforestation", "ivory", "rhino*", "pangolin*",
                                             "endangered species", "protected species", "fisheries", "pollution",
                                             "toxic waste", "hazardous waste", "timber", "natural resources"],
    "Terrorism / Terrorist Financing": ["extremis*", "jihad*", "isis", "isil", "daesh", "al qaida", "al qaeda",
                                        "hizballah", "hezbollah", "hamas", "taliban", "boko haram", "al shabaab",
                                        "bomb*", "explosive*", "suicide", "terrorismo", "terrorisme", "militant*"],
    "Proliferation / Proliferation Financing": ["missile*", "ballistic", "enrichment", "uranium", "dual use",
                                                "weapons program*", "biological weapon*", "weapons of mass destruction",
                                                "atomic"],
    "Human Rights / Atrocities": ["atrocit*", "massacre*", "ethnic cleansing", "repression", "repress*", "crackdown",
                                  "arbitrary detention", "political prisoner*", "protester*", "civil society",
                                  "child soldier*", "sexual violence", "disappear*", "ill treatment"],
    "Judicial / Court": ["court*", "tribunal*", "verdict*", "prison*", "imprison*", "jail*", "charg*", "complaint*",
                         "warrant*", "proceeding*", "prosecut*", "condena*", "juzgado", "proces", "urteil*",
                         "verurteil*", "gericht*", "plea*", "pleaded", "probation", "parole"],
    "State-Owned": ["state run", "state controlled", "government controlled", "state company", "state enterprise*",
                    "state corporation*", "state concern", "unitary enterprise", "owned by the state",
                    "owned by the government", "national oil company", "state owned enterprise*"],
}


def fold(text):
    """Lower-case, strip accents and turn hyphens into spaces, so 'Evasión' matches 'evasion' and 'state-owned' 'state owned'."""
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).lower().replace("-", " ").replace("’", "'")


def compile_triggers(*lexicons):
    """One whole-word regex per label; a trailing * allows any word ending."""
    merged = {}
    for lex in lexicons:
        for label, terms in lex.items():
            merged.setdefault(label, []).extend(terms)
    pattern = lambda t: r"\b" + r"\s+".join(map(re.escape, t.rstrip("*").split())) + (r"\w*" if t.endswith("*") else r"\b")
    return {label: re.compile("|".join(pattern(t) for t in terms)) for label, terms in merged.items()}


def split_units(text):
    """Evidence units of one record: (field, unit text), split at lines, '||', ';' and sentences; ID fields dropped."""
    units, field = [], ""
    for line in text.split("\n"):
        m = FIELD.match(line)
        if m:
            field, line = m.group(1).strip(), line[m.end():]
        if SKIP_FIELD.match(field):
            continue
        for part in re.split(r"\|\||;", line):
            for unit in SENTENCE_END.split(part.strip()):
                unit = unit.strip(" \"“”")
                if HAS_LETTERS.search(unit):
                    units.append((field, unit))
    return units


def is_boilerplate(source, field, unit):
    """v5: programme / category names, a list's standard legal text and act titles are never evidence."""
    return ((field == "Programs" and source in NAME_ONLY_PROGRAMS) or field in BOILERPLATE_FIELDS.get(source, ())
            or bool(BOILERPLATE_UNIT.search(unit)))


def fighting(u, m):
    """v6: the matched word names the fight against the conduct ('anti-corruption', 'drug control'), not the conduct."""
    return bool(FIGHTING_BEFORE.search(u[:m.start()]) or FIGHTING_AFTER.match(u[m.end():]))


def flag_units(units, triggers, source=None, version="v5"):
    """Candidate (unit index, label, matched words) for every unit where a label's trigger occurs."""
    out = []
    for k, (field, unit) in enumerate(units):
        if source is not None and is_boilerplate(source, field, unit):
            continue
        u = fold(unit)
        for label, rx in triggers.items():
            if field in SKIP_LABEL_FIELDS.get(label, ()):
                continue
            hits = sorted({m.group(0) for m in rx.finditer(u) if version < "v6" or not fighting(u, m)})
            if hits:
                out.append((k, label, hits))
    return out
