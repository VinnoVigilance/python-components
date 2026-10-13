# PDG-84 labelling rules (text layer)

These rules settle borderline cases for the 15 text-layer labels. They follow the taxonomy
(`data/taxonomy/VV_Risk_Labels_Final_reviewed.xlsx`, sheet "Risk Labels": column C definition / coverage / boundary,
column E sample indicators) and are applied the same way in the checker's questions, in every review and in the
reference labels. Accepted by the user on 2026-10-12. Examples are real texts from the 900-record development set.

General principle for every label: the text must support the label **for the listed person or entity**. Text about
other people, about fighting or investigating the conduct, the name of the authority that publishes the list, a law or a
sanctions programme name is not enough by itself. Words that name the fight against a conduct ("Commission Against
Corruption", "anti-narcotics", "drug control", "counter-terrorism") and a word that is only part of a name (of a person,
company, vessel, place or organisation) are not evidence. One event can support several labels.

## Rule 1 — PEP: holders of a prominent public function, national or local

A PEP holds or held a prominent public function with substantial authority over policy, operations or the use of public
resources (AMLC 2018 IRR test): head of state or government, minister or deputy minister, member of a national
parliament, supreme / constitutional / highest court judge, head or deputy chair of a central state body, senior officer
of a state military, head of a state enterprise, a senior intergovernmental official, and at sub-national level the
elected or appointed head or deputy head of a region, province, state, city or municipality (governor, mayor,
vice-mayor) and members or chairs of a regional, provincial, city or municipal council or legislature, and candidates
for any of these functions.
Not PEP: village / barangay level officials, district or city court judges, prosecutors below the head of the national
prosecution service, deputy heads of a ministry department, colonels and officers below general rank, leaders of
militias, rebel or other non-state armed groups.
Changed 2026-10-12 / 13 (user decisions): sub-national executives and council members were added (before, Rule 1
covered national-level functions only), and candidates are PEP (before, candidates were not PEP).

| | Example | Label |
|---|---|---|
| positive | "Deputy Chairman of the Central Election Commission" (SECO-SANCTIONS) | PEP |
| positive | "Elected local official; … SANGGUNIANG BAYAN MEMBER" (DILG-LOCAL-OFFICIALS) | PEP |
| positive | "SENATOR of PHILIPPINES" with "Candidate for Senator of Philippines in 2025 election" (COMELEC-2025-SENATORS) | PEP |
| negative | "Secretary General and leader for the Coalition nationale du people pour la souveraineté du Congo (CNPSC)" (UN-SANCTIONS; a militia coalition) | not PEP |

## Rule 2 — Terrorism: the terrorism link must be reported

Terrorism needs a reported terrorist act, plot, financing or support, or a group the text or a source characterises as
terrorist (e.g. ISIL / Da'esh, an al-Shabaab unit, Red Brigades). Membership of or an office in the Taliban, or the name
of a sanctions programme, is not enough by itself.

| | Example | Label |
|---|---|---|
| positive | "By facilitating fuel, arms and ammunition trade between the former al-Assad regime and various actors including ISIL (Da'esh)…" (SECO-SANCTIONS) | Terrorism |
| negative | "Member of the Taliban Supreme Council as of May 2007." (UN-SANCTIONS) | not Terrorism |

## Rule 3 — Proliferation: a WMD link is required

Proliferation needs involvement in a nuclear, chemical or biological weapons programme, its procurement or its
financing. Missile or UAV transfers without a reported WMD / proliferation link are not enough. "Use of weapons of mass
destruction" in US criminal charges (the US legal term for explosive attacks) is not Proliferation.

| | Example | Label |
|---|---|---|
| positive | "Head of the Atomic Energy Organization of Iran (AEOI) Esfahan Nuclear Technology Center" (EU-FINANCIAL-SANCTIONS) | Proliferation |
| negative | "Therefore, MG Flot LLC is involved in transferring Iran's UAVs or missiles or related technologies or components thereof to Russia…" (SECO-SANCTIONS) | not Proliferation |

## Rule 4 — Judicial / Court: a judicial event, not a warrant alone

Judicial needs a judicial event concerning the listed person: formal criminal charges, indictment, arraignment,
court hearing or trial, conviction, sentence, probation or pre-trial release conditions, appeal, criminal proceedings,
or a civil / family court decision. A bare "an arrest warrant was issued" is Law Enforcement only. Holding a judicial
office (being a judge or prosecutor) is not a judicial event for the person.

| | Example | Label |
|---|---|---|
| positive | "Campbell is being sought on a warrant for Burglary with a firearm charge from an incident that occurred in March 2022." (US-MARSHALS-PROFILED-FUGITIVES) | Judicial |
| negative | "Based on the above, an arrest warrant was issued." (EU-MOST-WANTED) | not Judicial |

## Rule 5 — Violent Crime and Human Rights can both apply

Killing, maiming, abduction, sexual violence or armed attacks on people are Violent Crime. When the same acts are
grave abuses in a human-rights context (attacks on civilians, repression of protesters), Human Rights also applies.
State repression without reported physical violence is Human Rights only.

| | Example | Label |
|---|---|---|
| positive | "Under his command, the IRGC forces in Fars Province have been involved in the violent repression of demonstrations … IRGC forces opened fire on protesters, resulting in deaths and injuries." (EU-FINANCIAL-SANCTIONS) | Violent Crime + Human Rights |
| negative | "…responsible for repression and intimidation led by the State apparatus prior to and following the August 2020 presidential election…" (UKSL) | Human Rights only, not Violent Crime |

## Rule 6 — Victims and missing persons get no crime label

If the listed person is the victim or a missing person (e.g. FBI ViCAP "Homicide Victim", "Kidnappings and Missing
Persons" records), no crime label applies to them, however violent the described event.

| | Example | Label |
|---|---|---|
| positive | "Joquan Antonio Porter is wanted for first-degree murder and related firearms charges…" (US-MARSHALS-PROFILED-FUGITIVES) — the listed person is the suspect | Violent Crime |
| negative | "Homicide Victim, Lynchburg, Virginia, May 2, 2023 … He was shot and killed." (FBI-WANTED) — the listed person is the victim | no crime label |

## Rule 7 — Fraud: deception for money or property

Fraud needs deception by the listed person or entity to obtain money, property or another economic benefit, or that
causes a financial loss: wire, mail, bank, investment, securities, insurance, health-care, procurement, loan or tax
fraud, Ponzi schemes, false invoices, scams, and identity theft used to obtain money. A charge naming one of these
(e.g. "conspiracy to commit wire fraud") counts, including an attempt or conspiracy. A short entry is enough: the word
fraud or fraudulent naming the offence, charge, sanction or debarment ground of the listed person (e.g. "Fraudulent
Practice" on a development-bank debarment list, "Fraud" as a wanted-notice charge) counts without details. Owning, running, controlling or profiting from a scam operation counts.
Not Fraud by itself (these are different from a named fraud offence):
* "Conspiracy to defraud the United States" (18 U.S.C. § 371) or "defrauding the government" when the text reports no
  money or property obtained by deception: in US law this clause covers obstructing a government function (election
  interference, sanctions or export-control evasion). It is Judicial (a charge), and the underlying conduct gets its
  own label only with evidence.
* Election fraud or rigging, false statements to officials, and visa or immigration fraud with no economic benefit
  reported (false documents may be Forgery).
* Building, leasing or supplying a site or service used by scams that other people run, unless the text says the listed
  person or entity takes part in or profits from the scams.
Added 2026-10-13 from the v5 proof errors (user asked Claude to define it).

| | Example | Label |
|---|---|---|
| positive | "Conspiracy to Commit Wire Fraud" (FBI-WANTED) | Fraud (+ Judicial) |
| negative | "Conspiracy to Commit Offense or to Defraud the United States" with no money or property obtained (FBI-WANTED) | not Fraud (Judicial only) |

## Rule 8 — Organized Crime: an organised criminal group must be described

Organized Crime needs membership of, leadership of or work for an organised criminal group or network that the text
describes or alleges: a cartel, mafia, syndicate, criminal organisation, racketeering enterprise, or a gang described as
committing serious crimes for profit in a coordinated way. Leading, founding or organising a gang or criminal group,
crimes committed as part of an organised or armed gang, and charges of criminal association (e.g. "asociación ilícita")
also count. Being called a gang member or "gang-related" with no organised
criminal activity described, several co-defendants, a militia or armed group, and officials dealing with organised
crime are not enough.

| | Example | Label |
|---|---|---|
| positive | "Participation in a criminal organisation" (EU-MOST-WANTED) | Organized Crime |
| negative | "Horton is an alleged gang member." (US-MARSHALS-PROFILED-FUGITIVES, fresh export) | not Organized Crime |

## Rule 9 — Corruption: a corrupt act by the listed person

Corruption needs bribery (giving or taking), kickbacks, embezzlement or misuse of public funds by an official, abuse of
office or power for private gain, influence peddling or nepotism, or a charge naming one of these. Officials or bodies
that fight corruption, simply being a public official, and political repression with no corrupt act described are not
Corruption.

| | Example | Label |
|---|---|---|
| positive | "Receipt of Kickbacks for Patient Referrals" (FBI-WANTED) | Corruption |
| negative | "Chairman of Anti-Corruption Commission" (OFAC-SDN) | not Corruption |

## Rule 10 — Tax Crime: evasion or fraud by the listed person

Tax Crime needs tax evasion, tax fraud, false or unfiled tax returns to evade tax, or VAT, customs-duty or excise fraud
committed by the listed person, or a charge naming one of these. Tax authorities and their officials, people who
prosecute tax cases, tax debts or disputes with no crime described, and tax identification numbers are not Tax Crime.

| | Example | Label |
|---|---|---|
| positive | "Income Tax Evasion" (FBI-WANTED) | Tax Crime |
| negative | "he is responsible for initiating the politically motivated tax evasion case against the media outlet TUT.BY" (SECO-SANCTIONS) — he brought the case | not Tax Crime |
