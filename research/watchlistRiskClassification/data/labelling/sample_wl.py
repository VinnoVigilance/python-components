import json, glob, os, random, csv, re
from collections import defaultdict, Counter
SRC_DIR = "data/final"
OUT_DIR = "data/reports/watchlist_labeling"
TARGET = 900
RANDOM_SLICE = 150
ADVERSE = {"ADB_CASE_SUMMARIES","DTI_FAIR_TRADE_PRESS_RELEASES","DTI_PH_FAIR_TRADE_PRESS_RELEASES","NBI_PRESS_RELEASES","PCIJ_CORRUPTION_WATCH","PCIJ_INVESTIGATIVE_REPORTS","PTV_NEWS","SEC_PH_ADVISORIES","UK_GOV_NEWS_COMMUNICATIONS"}
QUOTA = {"DFAT":25,"OFAC-SDN":25,"SECO-SANCTIONS":20,"UKSL":20,"EU-FINANCIAL-SANCTIONS":20,"UN-SANCTIONS":20,
         "INTERPOL-RED-NOTICES":20,"FBI-WANTED":20,"EU-TRAVEL-BAN":15,"OFAC-NON-SDN":12,"US-MARSHALS-PROFILED-FUGITIVES":12,
         "ADB-DEBARMENT-SUSPENSION":10,"AFDB-DEBARRED-ENTITIES":10,"WORLD-BANK-DEBARRED":10,"DMW-RECRUITMENT-AGENCIES":10,
         "GPPB-BLACKLISTED-ENTITIES":10,"CFTC-RED-LIST":10,"COMELEC-2025-SENATORS":8,"DILG-LOCAL-OFFICIALS":8,
         "EU-MOST-WANTED":10,"NCA-MOST-WANTED":8,"ATC-DESIGNATED-TERRORIST-INDIVIDUALS":10,"ATC-DESIGNATED-TERRORIST-GROUPS":5,
         "EU-DESIGNATED-VESSELS":5,"US-STATE-TERRORIST-EXCLUSION":2,"WORLD-BANK-OTHER-SANCTIONS":10,"DNFBP":10,
         "CIA-WORLD-LEADERS-HISTORICAL":15,"PH-HOUSE-MEMBERS":10}
HARD_EXTRA = {"DNFBP":20,"DMW-RECRUITMENT-AGENCIES":10}
HARD_MIN_TYPE = {"Vessel":25,"Aircraft":10}
HARD_FBI_VICTIM = 10
FBI_VICTIM_RE = re.compile(r"seeking information|victim|missing person|kidnapped|unidentified", re.I)

HINTS = {
 "Sanctions": r"sanction|designat|asset freeze|frozen|blocked|travel ban|embargo",
 "PEP": r"head of state|president|prime minister|minister|senator|governor|mayor|ambassador|member of (?:parliament|congress|the house)|representative|judge|chief justice|general|commander|deputy",
 "RCA": r"spouse|wife|husband|son of|daughter of|brother|sister|father|mother|relative|associate of|close associate|family member",
 "Crime-Corruption": r"corrupt|brib|kickback|graft|abuse of (?:public )?office|improper payment|diversion of public funds|misappropriat",
 "Crime-Fraud": r"fraud|ponzi|scam|false representation|deceiv",
 "Crime-Money Laundering": r"launder",
 "Crime-Trafficking & Smuggling": r"traffick|smuggl|narcot|drug|sdntk|kingpin|cocaine|heroin|methamphetamine",
 "Crime-Cybercrime": r"cyber|hack|ransomware|malware|phishing|computer intrusion|unauthorized (?:computer )?access",
 "Crime-Violent Crime": r"murder|homicide|kill|assault|kidnap|hostage|rape|armed robbery|extortion",
 "Crime-Theft & Property Crime": r"theft|robbery|burglar|stolen|embezzl",
 "Crime-Forgery & Counterfeiting": r"forg|counterfeit|falsif|fake (?:identity|document|passport)",
 "Crime-Tax Crime": r"tax evasion|tax fraud|false tax|tax crime|tax offen|evad\w* (?:income )?tax|tax refund",
 "Crime-Securities & Market Crime": r"securities|insider|market manipulation|pump.and.dump|futures|commodit|unregistered",
 "Crime-Organized Crime": r"organi[sz]ed crime|criminal (?:group|organi[sz]ation|enterprise|syndicate)|cartel|mafia|racketeer|\btco\b|gang",
 "Crime-Environmental & Wildlife Crime": r"wildlife (?:traffick|trade|product|crime)|illegal (?:logging|fishing|mining)|poach|environmental crime|exploitation of natural resources",
 "Terrorism / Terrorist Financing": r"terror|sdgt|isil|isis|qa.?ida|taliban|hizb|hamas|abu sayyaf|jihad",
 "Proliferation / Proliferation Financing": r"prolifer|npwmd|weapons of mass|nuclear|ballistic|missile|chemical weapon",
 "Human Rights / Atrocities": r"human rights|genocide|crimes against humanity|war crime|tortur|extrajudicial|disappearance|repression|persecut",
 "Regulatory": r"debar|ineligib|suspend|revok|regulator|non-compliance|license|warning|blacklist|cease",
 "Law Enforcement": r"wanted|fugitive|arrest warrant|warrant|red notice|arrested|charged with|investigation",
 "Judicial / Court": r"convicted|conviction|sentenced|indict|court|trial|verdict|acquitted|appeal|judgment",
 "Jurisdiction Risk": r"fatf|high.risk jurisdiction|increased monitoring|call for action",
 "State-Owned": r"state.owned|government.owned|owned by the (?:state|government)|state enterprise|ministry of|government of",
}
HINT_RE = {k: re.compile(v, re.I) for k, v in HINTS.items()}

NOISE = {"Language Spoken","Height","Height (in)","Weight","Hair","Eyes","Distinguishing Marks","Scars and Marks",
         "Race","Ethnic Origin","Complexion","Build","Age Range","Reporting Phone","National Reference","Warrant Case Number"}
def vals(items, keys):
    out = []
    for x in items or []:
        if isinstance(x, dict):
            v = " ; ".join(str(x.get(k, "")).strip() for k in keys if str(x.get(k, "")).strip())
            if v: out.append(v)
    return out

def render(r, src):
    s0 = (r.get("Sources") or [{}])[0]
    lines = [f"Source list: {src}",
             f"Entity type: {r.get('EntityType','')}"]
    sections = [("Programs", vals(r.get("Programs"), ["ProgramType","Program","Authority","Note"])),
                ("Measures", vals(r.get("Measures"), list((r.get("Measures") or [{}])[0].keys()) if r.get("Measures") else [])),
                ("Titles", vals(r.get("Titles"), ["Title","Note"])),
                ("Occupations", vals(r.get("Occupations"), list((r.get("Occupations") or [{}])[0].keys()) if r.get("Occupations") else [])),
                ("Relationships", vals(r.get("Relationships"), list((r.get("Relationships") or [{}])[0].keys()) if r.get("Relationships") else [])),
                ("Additional info", vals([a for a in r.get("additionalInfo") or [] if a.get("Type") not in NOISE], ["Type","Value"])),
                ("Source note", [s0.get("Note","").strip()] if s0.get("Note","").strip() else []),
                ("Remarks", vals(r.get("Comments"), ["type","text"]))]
    for name, items in sections:
        seen = []
        for i in items:
            if i not in seen: seen.append(i)
        if seen: lines.append(f"{name}: " + " || ".join(seen))
    return "\n".join(lines)

def stratum(r):
    p = (r.get("Programs") or [{}])[0]
    return f"{r.get('EntityType','')}|{p.get('Program','') or p.get('ProgramType','')}"

def hints(text):
    body = text.split("\n", 1)[1] if "\n" in text else text
    return [k for k, rx in HINT_RE.items() if rx.search(body)]

rng = random.Random(20261007)
os.makedirs(OUT_DIR, exist_ok=True)

pool = {}
for f in sorted(glob.glob(f"{SRC_DIR}/*_final.jsonl")):
    src = os.path.basename(f)[:-12]
    if src in ADVERSE or src not in QUOTA: continue
    by_text = {}
    for line in open(f, encoding="utf-8"):
        r = json.loads(line)
        by_text.setdefault(render(r, src), r)
    items = [{"record_key": f"{src}:{r.get('EntityId')}", "source": src, "entity_type": r.get("EntityType",""),
              "stratum": stratum(r), "text": t, "hint_labels": hints(t)} for t, r in by_text.items()]
    rng.shuffle(items)
    pool[src] = items

chosen, keys = [], set()
def take(item, part):
    if item["record_key"] in keys: return False
    keys.add(item["record_key"]); chosen.append({**item, "part": part}); return True

def free(src):
    return [x for x in pool[src] if x["record_key"] not in keys]

for src, items in pool.items():
    groups = defaultdict(list)
    for x in items: groups[x["stratum"]].append(x)
    order = sorted(groups, key=lambda k: -len(groups[k]))
    n, i = 0, 0
    while n < QUOTA[src] and any(groups.values()):
        g = groups[order[i % len(order)]]
        if g and take(g.pop(), "A"): n += 1
        i += 1

total_quota = sum(QUOTA[s] for s in pool)
for src in pool:
    n = max(1, round(RANDOM_SLICE * QUOTA[src] / total_quota))
    for x in free(src)[:n]: take(x, "D")

for src, n in HARD_EXTRA.items():
    for x in free(src)[:n]: take(x, "C")
for et, need in HARD_MIN_TYPE.items():
    have = sum(1 for c in chosen if c["entity_type"] == et)
    cands = [x for s in pool for x in free(s) if x["entity_type"] == et]
    rng.shuffle(cands)
    for x in cands[:max(0, need - have)]: take(x, "C")
for x in [x for x in free("FBI-WANTED") if FBI_VICTIM_RE.search(x["text"])][:HARD_FBI_VICTIM]: take(x, "C")

label_count = Counter(h for c in chosen for h in c["hint_labels"])
label_src = defaultdict(Counter)
for c in chosen:
    for h in c["hint_labels"]: label_src[h][c["source"]] += 1
cands = defaultdict(lambda: defaultdict(list))
for s in pool:
    for x in free(s):
        for h in x["hint_labels"]: cands[h][s].append(x)
while len(chosen) < TARGET:
    live = [h for h in HINTS if any(any(x["record_key"] not in keys for x in v) for v in cands[h].values())]
    if not live: break
    h = min(live, key=lambda k: (label_count[k], k))
    srcs = [s for s, v in cands[h].items() if any(x["record_key"] not in keys for x in v)]
    s = min(srcs, key=lambda k: (label_src[h][k], k))
    x = next(x for x in cands[h][s] if x["record_key"] not in keys)
    take(x, "B")
    for k in x["hint_labels"]:
        label_count[k] += 1; label_src[k][s] += 1

PART_NAME = {"A": "A - List coverage", "B": "B - Rare-label top-up", "C": "C - Hard case", "D": "D - Random check"}
fields = ["record_key","source","entity_type","part","stratum","hint_labels","text"]
rows = [{**{k: c[k] for k in fields}, "part": PART_NAME[c["part"]]} for c in chosen]
with open(f"{OUT_DIR}/watchlist_label_sample_{TARGET}.jsonl", "w", encoding="utf-8") as o:
    for r in rows: o.write(json.dumps(r, ensure_ascii=False) + "\n")
with open(f"{OUT_DIR}/watchlist_label_sample_{TARGET}.csv", "w", encoding="utf-8-sig", newline="") as o:
    w = csv.DictWriter(o, fieldnames=fields); w.writeheader()
    w.writerows({**r, "hint_labels": ";".join(r["hint_labels"])} for r in rows)

per_src = defaultdict(Counter)
for c in chosen: per_src[c["source"]][c["part"]] += 1
print(f"{'source':40}{'distinct':>9}{'A':>5}{'B':>5}{'C':>5}{'D':>5}{'total':>7}")
for s in pool:
    p = per_src[s]
    print(f"{s:40}{len(pool[s]):>9}{p['A']:>5}{p['B']:>5}{p['C']:>5}{p['D']:>5}{sum(p.values()):>7}")
print("parts:", dict(Counter(c["part"] for c in chosen)), "TOTAL", len(chosen))
print("entity types:", dict(Counter(c["entity_type"] for c in chosen)))
print("hint-label coverage (sampling hints only, not labels):")
for h in HINTS: print(f"  {h:42}{label_count[h]:>5}  lists={len(label_src[h])}")
