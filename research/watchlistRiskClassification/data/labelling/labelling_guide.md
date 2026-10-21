# Watchlist labelling guide (taxonomy: VV_Risk_Labels_Final_reviewed)

Source: `C:\Users\admin\Downloads\VV_Risk_Labels_Final_reviewed.xlsx`, sheets *Risk Labels* and *Annotation Rules*.
Sections 1–3 condense that workbook. Section 4 is the watchlist adaptation agreed for this project (not in the workbook).

## 1. Output format (one JSON object per line)

```json
{"record_key": "OFAC-SDN:12345",
 "labels": [
   {"label": "Sanctions", "status": "supported_positive",
    "evidence": "<exact quote copied from the record text>",
    "attributes": {"measure": "designation", "authority": "OFAC"},
    "note": ""}
 ],
 "unassessed": ["<label names that might apply but the text is too thin to decide>"],
 "labeller": "<claude-opus | claude-sonnet>",
 "taxonomy_version": "VV_Risk_Labels_Final_reviewed"}
```

- `labels` holds only `supported_positive` labels. Every label NOT in `labels` and NOT in `unassessed` is `assessed_absent` (G04: unknown is not the same as absent).
- `evidence` must be an **exact, character-for-character substring** of the record text (copy it; do not paraphrase, fix spelling or join separate parts). Keep it short: the smallest span that proves the label.
- Use only the 23 exact label names below. There is no bare "Crime" label (G01).
- Subtypes go in `attributes`, never as extra labels (G07).
- `note`: anything uncertain, or wording for an uncovered topic.
- A record with no label: `"labels": []`. Still fill `unassessed` if relevant.

## 2. The 23 labels — when to assign

| Label | Assign when the text reports… | Not enough / goes elsewhere | attributes |
|---|---|---|---|
| Sanctions | an official designation/listing or sanctions measure (asset freeze/blocking, travel/entry ban, financial restriction, sectoral, trade restriction, arms embargo) by a competent authority; lifting/amendment too | proposals only; ordinary seizure/holds; bail travel limits; tariffs; export controls without sanctions basis. **Debarment by a development bank or procurement body = Regulatory, not Sanctions**, even if called a "sanction" | authority, regime/program, measure, status (current/lifted) |
| PEP | a person who holds/held a prominent public function: head of state/government, minister, prominent legislator/political leader, senior judicial/military official, senior state-enterprise executive, senior intergovernmental official | junior/ordinary government staff, NGO membership, fame, citizenship. Unknown seniority / ambiguous title → `unassessed` | role_title, organization, function_jurisdiction, current_or_former |
| RCA | family relation or close known association **with an identified PEP**; both sides identifiable | spouse/relative of a non-PEP; meetings, shared board, proximity | relationship_type (family/close associate), pep_anchor |
| Crime-Corruption | bribery, kickbacks, graft, improper payments, abuse of office/entrusted power, influence peddling | political criticism, rhetorical "corrupt", mention of an official alone | offense_text, stage |
| Crime-Fraud | financial deception / fraud allegation or charge (scams, Ponzi, estafa, loan/insurance/health-care/wire fraud, deceptive identity misuse) | money laundering, bribery, tax or market offences **without** deception; losses; generic "financial crime" | offense_text, stage |
| Crime-Money Laundering | explicit laundering allegation/charge or concealment of identified criminal proceeds | unexplained wealth, ordinary transfers, "layering" alone | offense_text, stage |
| Crime-Trafficking & Smuggling | drug, human, migrant, arms, goods or wildlife trafficking/smuggling | lawful trade/migration, "web traffic" | subtype, commodity |
| Crime-Cybercrime | hacking, unauthorized access, malware/ransomware, phishing, digital identity theft, cyber-enabled fraud | accidental leaks, outages, merely using email/internet | offense_text, role |
| Crime-Violent Crime | murder/attempted murder, assault, **rape / sexual assault** (user decision), kidnapping, hostage-taking, violent robbery (also label Theft & Property Crime), extortion with physical threat | accidents, metaphorical attacks, nonviolent extortion (→ `note`) | offense_text, role |
| Crime-Theft & Property Crime | theft, robbery, embezzlement, misappropriation, stolen property. **Any robbery (armed, violent, "in band", burglary) is always Theft & Property Crime; add Crime-Violent Crime too only when violence is stated** | debt, nonpayment, bankruptcy, disputed title | offense_text, asset |
| Crime-Forgery & Counterfeiting | forged/falsified documents, fake IDs, counterfeit currency/goods/securities, knowing use | errors, authorized copies, "fake news" | item_type |
| Crime-Tax Crime | deliberate tax evasion / tax fraud / false returns / criminal tax charge | tax debt, avoidance, admin errors; **a tax ID number** | tax_type, stage |
| Crime-Securities & Market Crime | insider dealing, market manipulation, securities fraud, fraudulent offerings — criminal context | lawful trading, price moves, purely technical/administrative breaches | offense_text |
| Crime-Organized Crime | membership/participation in an organized criminal group, cartel, mafia, syndicate, criminal enterprise / TCO | several co-defendants, vague "group" | group_name, role |
| Crime-Environmental & Wildlife Crime | illegal logging/fishing/mining, poaching, wildlife trafficking, unlawful resource exploitation | lawful resource use, an environment ministry title, ESG criticism | resource, offense_text |
| Terrorism / Terrorist Financing | terrorist acts/plots, terrorist financing, membership/affiliation with a terrorist group, material/logistical support | generic violence/war, rhetoric, shared religion/ethnicity | subtype (activity/financing/affiliation/support), group |
| Proliferation / Proliferation Financing | WMD (nuclear/chemical/biological) activity, financing or procurement/technical support; missiles only with a proliferation link | peaceful nuclear, conventional arms, dual-use without link | subtype, program |
| Human Rights / Atrocities | genocide, crimes against humanity, war crimes (attributed), or grave abuses: torture, extrajudicial killing, enforced disappearance, severe arbitrary detention, severe persecution, forced displacement | ordinary violence, wartime casualties, generic "rights" wording | subtype, attribution |
| Regulatory | a specific regulatory inquiry, alleged breach, finding or target-specific action: fine, warning, licence suspension/revocation, **debarment / cross-debarment by a development bank (World Bank, ADB, AfDB)**, procurement blacklisting | general guidance, broad market warnings, routine licensing without a specific matter | authority, action, stage |
| Law Enforcement | official criminal wanted/fugitive notice, criminal investigation, arrest/search warrant, arrest, seizure in a criminal case | **victims, witnesses, missing persons, "seeking information"** (not suspects); sanctions blocking | authority, subtype (wanted/investigation/action) |
| Judicial / Court | charges/indictment, trial, conviction, acquittal, sentence, appeal, dismissal, civil lawsuit/judgment | police inquiry alone, regulatory penalty without a court event | court, stage, outcome |
| Jurisdiction Risk | FATF / authority AML-CFT risk status of a jurisdiction, or an entity's exposure explicitly linked to it | country name, nationality, address alone | jurisdiction, list_type |
| State-Owned | explicit state-owned / government-owned entity, or significant stated government equity | state control alone, government contracts/loans/regulation; stake of unknown significance → `unassessed` | owner, share |

## 3. Shared rules (G01–G15, condensed)

- **G03** The label set is the union of supported facts about the record's subject; a fact about another person (e.g. the victim of a crime) does not label the subject.
- **G05** An attributed allegation counts even if denied. Bare negation or hypotheticals do not.
- **G06** Historical and current status are both labelled; note "former"/"lifted" in attributes.
- **G08** Co-labels each need their own evidence: Fraud + Cybercrime (digital deception), Fraud + Tax (tax fraud), Fraud + Securities (securities fraud), Trafficking + Environmental (wildlife trafficking), Theft + Corruption (embezzlement by abuse of office). Never add a label just because a related one is present.
- **G09** Investigation is not proof; victim/witness involvement is not suspect status.
- **G11** A keyword alone is not enough ("asset freeze", "support", "association" are clues only).
- **G14** Uncovered topics get no label; put the wording in `note`: non-trafficking child sexual exploitation, nonviolent extortion, maritime piracy, state-controlled / opaque ownership.

## 4. Watchlist adaptation (project decision, not in the workbook)

- The **list itself is evidence**. Being on an official list is a reported status. Quote the line that shows it — prefer the `Programs:` / `Measures:` line; use `Source list: <NAME>` only when nothing more specific exists.
- Typical list-derived labels (still check the text — exceptions exist):

| Lists | Usual label from the list |
|---|---|
| OFAC-SDN, OFAC-NON-SDN, DFAT, SECO-SANCTIONS, UKSL, EU-FINANCIAL-SANCTIONS, UN-SANCTIONS, EU-TRAVEL-BAN, EU-DESIGNATED-VESSELS | Sanctions |
| ATC-DESIGNATED-TERRORIST-* | Terrorism / Terrorist Financing **and** Sanctions (ATC designation is a sanctions designation) |
| US-STATE-TERRORIST-EXCLUSION | Terrorism / Terrorist Financing only — **never Sanctions** (immigration exclusion) |
| INTERPOL-RED-NOTICES, FBI-WANTED (suspects only), US-MARSHALS, EU-MOST-WANTED, NCA-MOST-WANTED | Law Enforcement |
| WORLD-BANK-DEBARRED, WORLD-BANK-OTHER-SANCTIONS, ADB-DEBARMENT-SUSPENSION, AFDB-DEBARRED-ENTITIES, GPPB-BLACKLISTED-ENTITIES | Regulatory (never Sanctions) |
| CFTC-RED-LIST | Regulatory (regulator's target-specific warning) |
| CIA-WORLD-LEADERS-HISTORICAL, PH-HOUSE-MEMBERS, DILG-LOCAL-OFFICIALS | PEP. **Elected local officials (e.g. city/municipal council members, mayors, governors) are PEP.** |
| COMELEC-2025-SENATORS | PEP only if the text shows the person holds/held office. **An election candidate alone is NOT a PEP** (assessed_absent). |
| DNFBP, DMW-RECRUITMENT-AGENCIES | usually none — a registered/licensed business is not a risk event unless the text reports an action (e.g. licence cancelled → Regulatory) |

- **Conduct labels come from the text, not the list.** The reason for listing (e.g. "SDGT", "narcotics trafficking", "charged with fraud") supports the conduct label with its own quote.
- **Official program names/codes count as facts in the text** (approved). Quote the code as evidence. Allowed mappings (use only these; any other code gives no conduct label by itself):

| Program code / name in the text | Label |
|---|---|
| SDGT, FTO, SDT, "Counter-Terrorism", "1267 (ISIL (Da'esh) and Al-Qaida)", "ISIL", "Al-Qaida", Counter-Terrorism regulations | Terrorism / Terrorist Financing |
| NPWMD, "Non-proliferation", "Non-Proliferation", DPRK/Nordkorea/"Democratic People's Republic of Korea" regimes **only when the text also mentions WMD/nuclear/missile/proliferation** | Proliferation / Proliferation Financing |
| SDNTK, ILLICIT-DRUGS, "Narcotics Trafficking", "Illicit trafficking in narcotic drugs" | Crime-Trafficking & Smuggling |
| TCO, "Transnational Criminal Organization" | Crime-Organized Crime |
| CYBER, CYBER2, "Cyber-Related" | Crime-Cybercrime |
| "Global Anti-Corruption" regime | Crime-Corruption |
| "Global Human Rights" regime | Human Rights / Atrocities |
| GLOMAG, "Global Magnitsky" | decide from the text (corruption and/or human rights); if text gives neither → `unassessed` |
| "Global Irregular Migration and Trafficking in Persons" regime | Crime-Trafficking & Smuggling |
| **Any program/regime name that itself contains the word "Terrorism"/"Terrorist"** (e.g. "EO 13224 (Terrorism)", "Counter-Terrorism (Sanctions)") | Terrorism / Terrorist Financing |
| **Any program/regime name that itself contains "Human Rights" or the suffix "-HR"** (e.g. "Autonomous (Thematic - Human Rights)", IRAN-HR, SYRIA-HR) | Human Rights / Atrocities |
| "Thematic - Cyber" | Crime-Cybercrime |
| "Thematic - Corruption", "Serious Corruption" | Crime-Corruption |
| NOT on this list (need text that says it): "1373", "Taliban", "1988", "1518", country regimes (Iran, Syria, Russia, Belarus…) | no conduct label from the name alone |

- **Unsure labels:** list them in `unassessed` but do not spend effort on them; only confident labels are used.
- **Wanted / fugitive lists:** an arrest warrant or wanted status is **Law Enforcement**. Add **Judicial / Court** only when a court-process event is stated: "charged with", "indicted", "convicted", "sentenced", "complaint filed".
- **Entity type matters**: a vessel or aircraft gets Sanctions from its listing; it is not a PEP and has no conduct of its own unless the text says so (e.g. "used for smuggling").
- When the text is only a list name and an entity type with nothing else, label what the list proves and leave the rest absent.
- **No outside knowledge — facts in the text only.** Do not use what you know about a named group, person or country. "links to Hamas", "Taliban commander", "attacks on forces" do NOT give Terrorism unless the text itself says terrorist/terrorism (or the program is explicitly a terrorism program, see the code list above). Put such cases in `unassessed` with a note.
- **RCA needs the PEP status in the text.** "Family member of <name>" where the text does not say that person holds a public function → no RCA (assessed_absent), not unassessed.
