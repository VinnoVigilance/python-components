"""Decide flagged (unit, label) candidates in context with a local LLM: P(yes) from the first answer token."""
from pathlib import Path

import subprocess
import time

import openpyxl
import torch

RESEARCH = Path(__file__).resolve().parents[1]
MODEL_ID = "Qwen/Qwen3-4B-Instruct-2507"
PROMPT_VERSION = "v6"
SYSTEM = ("You are an anti-money-laundering analyst. You decide whether a piece of text from a watchlist record "
          "supports a risk label for the listed person or entity. Answer with one word: yes or no.")
QUESTION = """Risk label: {label}
{definition}
Typical indicators: {indicators}{note}

Watchlist record field: {field}
Text to judge: "{unit}"
Surrounding text (context only): "{before} [...] {after}"

Does the text to judge, read in its context, support assigning this label to the listed person or entity, following the definition and boundary above?
It does not count if the listed person is a victim or missing person, an official or body fighting or investigating this conduct, or if the text only names the authority that publishes the list, a law or a sanctions programme. A sentence saying who listed or designated the person is about the list, not about the person.
One event can support several labels at once; judge only this label.
Answer yes or no."""
QUESTION_V6 = QUESTION.replace(
    "A sentence saying who listed or designated the person is about the list, not about the person.",
    "A sentence saying who listed or designated the person is about the list, not about the person. A word that is only "
    "part of a name (of a person, company, vessel, place or organisation) is not evidence.")
NOTES = {
    "PEP": "PEP means holding or having held a senior public function: head of state or government, minister or deputy minister, member of a national parliament, judge of a supreme, constitutional or highest court, head or deputy chair of a central state body, senior officer of a state military (general rank), head of a state enterprise, or senior intergovernmental official. Not PEP: election candidates or people who only ran for an office, district or city court judges, prosecutors below the head of the national prosecution service, deputy heads of a ministry department, colonels and lower ranks, leaders of militias, rebel or other non-state armed groups.",
    "Terrorism / Terrorist Financing": "A role in a group, or the name of a sanctions programme, is not enough by itself: the text must report a terrorist act, plot, financing or support, or describe the group as terrorist. Membership of or an office in the Taliban alone is not enough.",
    "Proliferation / Proliferation Financing": "A link to nuclear, chemical or biological weapons, their programme, procurement or financing is required. Missile or UAV transfers without such a link do not count. 'Use of weapons of mass destruction' in US criminal charges means an explosive attack and does not count.",
    "Judicial / Court": "Needs a judicial event concerning the listed person: formal criminal charges, indictment, court hearing or trial, conviction, sentence, probation or pre-trial release, appeal, criminal proceedings, or a civil or family court decision. A bare 'an arrest warrant was issued' does not count. Holding a job as judge or prosecutor is not a judicial event for the person.",
    "Crime-Violent Crime": "Killing, maiming, abduction, sexual violence or armed attacks on people count, including when carried out by forces under the person's command; this label can apply together with Human Rights.",
    "Human Rights / Atrocities": "Grave abuses such as attacks on civilians, torture, enforced disappearance or severe repression of civil society and protesters count; this label can apply together with Violent Crime. Private crimes without a human-rights context do not count.",
}
NOTES_V5 = {
    **NOTES,
    "PEP": "PEP means holding or having held a prominent public function: head of state or government, minister or deputy minister, member of a national parliament, judge of a supreme, constitutional or highest court, head or deputy chair of a central state body, senior officer of a state military (general rank), head of a state enterprise, senior intergovernmental official, and at sub-national level the head or deputy head of a region, province, state, city or municipality (governor, mayor, vice-mayor) or a member or chair of a regional, provincial, city or municipal council or legislature. Not PEP: election candidates or people who only ran for an office, village or barangay level officials, district or city court judges, prosecutors below the head of the national prosecution service, deputy heads of a ministry department, colonels and lower ranks, leaders of militias, rebel or other non-state armed groups.",
    "Crime-Violent Crime": NOTES["Crime-Violent Crime"] + " Membership of an armed group or 'participation in an illegal armed formation', without a reported act or threat of violence against people, does not count. The listed person's own death or injury does not count.",
    "Crime-Trafficking & Smuggling": "Counts: trafficking, sale, distribution, possession with intent to distribute, import or smuggling of drugs, people, weapons or goods, conspiracy to do so, or membership of a group doing so. Does not count: simple possession or use of drugs with no sale or distribution, officials of anti-drug or anti-trafficking bodies, and witnesses or people holding information about a victim.",
    "Judicial / Court": NOTES["Judicial / Court"] + " A named criminal charge against the listed person counts, including a warrant naming the offence, unlawful flight to avoid prosecution, failure to appear, parole or probation violations and sentences. Answer no only when the listed person is the judge, prosecutor or official handling cases against other people.",
    "Crime-Money Laundering": "Counts: a money laundering charge or allegation against the listed person, or concealing or moving criminal proceeds to hide their origin. Does not count: laundering attributed only to other named persons, or spending proceeds with no laundering or concealment allegation.",
    "Human Rights / Atrocities": NOTES["Human Rights / Atrocities"] + " Murder, kidnapping or torture by private criminals with no state, armed-conflict or civilian-population context is Violent Crime, not Human Rights.",
}
NOTES_V6 = {
    **NOTES_V5,
    "PEP": NOTES_V5["PEP"].replace("Not PEP: election candidates or people who only ran for an office, village", "Candidates for these functions are PEP. Not PEP: village"),
    "Crime-Fraud": "A named fraud offence charged against or attributed to the listed person always counts, including an attempt or conspiracy to commit it (for example 'conspiracy to commit wire fraud', 'mail fraud', 'bank fraud', 'fraudulent practices', 'estafa'). A short entry is enough: the word fraud or fraudulent naming the offence, charge, sanction or debarment ground of the listed person counts without details of the scheme. Fraud is deception to obtain money, property or another economic benefit, or that causes a financial loss. Counts: investment, Ponzi or pyramid schemes; scams and romance or online fraud; wire, mail, bank, loan, insurance, health-care, procurement or tax fraud; false invoices or false claims to obtain payment; identity theft used to obtain money; a charge naming one of these (for example 'conspiracy to commit wire fraud'); owning, running or profiting from a scam operation. Does not count by itself (these are different from a named fraud offence): 'conspiracy to defraud the United States' or 'defrauding the government' when no money or property is obtained (it means obstructing a government function, such as election interference or sanctions evasion); election fraud or rigging; false statements to officials or visa fraud with no economic benefit; building, leasing or servicing premises that other people use for scams.",
    "Crime-Organized Crime": "Counts: membership of, leadership of or work for an organised criminal group or network described or alleged in the text, such as a cartel, mafia, syndicate, criminal organisation, racketeering enterprise, or a gang described as committing serious crimes for profit in a coordinated way. Also counts: leading, founding or organising a gang or criminal group, crimes committed as part of an organised or armed gang, and charges of criminal association or participation in a criminal group (for example 'asociación ilícita', 'organization of a criminal group'). Does not count: being called a gang member or 'gang-related' with no organised criminal activity described, several co-defendants, a militia or armed group, and judges, prosecutors or police dealing with organised crime.",
    "Crime-Corruption": "Counts: bribery (giving or taking), kickbacks, embezzlement or misuse of public funds by an official, abuse of office or power for private gain, influence peddling, nepotism, or a charge naming one of these. Does not count: officials or bodies that fight corruption, being a public official, and sanctions for political repression or undermining democracy with no corrupt act described.",
    "Crime-Tax Crime": "Counts: tax evasion, tax fraud, false or unfiled tax returns to evade tax, VAT, customs-duty or excise fraud, or a charge naming one of these. Does not count: tax authorities or their officials, tax debts or disputes with no crime described, and tax identification numbers.",
}
ROLE_QUESTION = """Watchlist record:
{text}

Does this record explicitly describe the listed person as a victim, a missing person, or a person whose death is being investigated (and not as a suspect or perpetrator)?
Answer yes or no."""


def role_questions(texts, width=1500):
    """One question per record: is the listed person a victim or missing person?"""
    return [ROLE_QUESTION.format(text=t[:width]) for t in texts]


def label_definitions():
    """Per label: English definition, coverage and boundary (taxonomy column C) and sample indicators (column E)."""
    ws = openpyxl.load_workbook(RESEARCH / "data" / "taxonomy" / "VV_Risk_Labels_Final_reviewed.xlsx", read_only=True)["Risk Labels"]
    indicators = lambda cell: "; ".join(l.split(" / ")[0].strip() for l in (cell or "").splitlines() if l.strip())
    return {r[0]: (r[2].strip(), indicators(r[4])) for r in ws.iter_rows(min_row=2, values_only=True) if r[0]}


def questions(units, flags, definitions, width=300, version=PROMPT_VERSION):
    """One prompt per flagged (unit, label): the unit, its field, its neighbouring units and the label definition."""
    out = []
    for k, label, _ in flags:
        field, unit = units[k]
        before = units[k - 1][1][-width:] if k else ""
        after = units[k + 1][1][:width] if k + 1 < len(units) else ""
        definition, indicators = definitions[label]
        notes = NOTES if version == "v4" else NOTES_V5 if version == "v5" else NOTES_V6
        note = f"\n{notes[label]}" if label in notes else ""
        template = QUESTION_V6 if version >= "v6" else QUESTION
        out.append(template.format(label=label, definition=definition, indicators=indicators, note=note, field=field,
                                   unit=unit, before=before, after=after))
    return out


GPU_PAUSE_AT, GPU_RESUME_AT = 76, 68


def gpu_temperature():
    out = subprocess.run(["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True)
    return int(out.stdout.strip().splitlines()[0])


def cool_down(log=RESEARCH / "cache" / "gpu_pauses.log"):
    """Pause the GPU work while the GPU is at or above GPU_PAUSE_AT degrees, until it is back to GPU_RESUME_AT."""
    if gpu_temperature() < GPU_PAUSE_AT:
        return
    t0 = time.time()
    while gpu_temperature() > GPU_RESUME_AT:
        time.sleep(10)
    with open(log, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} paused {time.time() - t0:.0f} s to cool down\n")


class Checker:
    """Qwen3-4B-Instruct in 4-bit on the GPU; scores prompts by the probability of answering 'yes'."""

    def __init__(self, model_id=MODEL_ID):
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
        self.model_id = model_id
        self.tok = AutoTokenizer.from_pretrained(model_id, padding_side="left")
        quant = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.bfloat16)
        self.model = AutoModelForCausalLM.from_pretrained(model_id, quantization_config=quant, device_map="cuda:0").eval()
        ids = lambda words: [self.tok.encode(w, add_special_tokens=False)[0] for w in words]
        self.yes, self.no = ids(["yes", "Yes"]), ids(["no", "No"])

    def chat(self, prompt):
        msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]
        return self.tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True, enable_thinking=False)

    @torch.no_grad()
    def p_yes(self, prompts, batch_size=4):
        """P(yes) / (P(yes) + P(no)) of the first answer token, for every prompt."""
        out = []
        order = sorted(range(len(prompts)), key=lambda i: len(prompts[i]))
        for s in range(0, len(order), batch_size):
            if s % (batch_size * 25) == 0:
                cool_down()
            idx = order[s:s + batch_size]
            enc = self.tok([self.chat(prompts[i]) for i in idx], return_tensors="pt", padding=True).to("cuda:0")
            logits = self.model(**enc, logits_to_keep=1).logits[:, -1].float()
            y, n = torch.logsumexp(logits[:, self.yes], 1), torch.logsumexp(logits[:, self.no], 1)
            out += list(zip(idx, torch.sigmoid(y - n).cpu().tolist()))
        return [p for _, p in sorted(out)]

    @torch.no_grad()
    def explain(self, prompt, max_new_tokens=60):
        """A one-sentence reason, for showing evidence on a sample."""
        text = self.chat(prompt.replace("Answer yes or no.", "Answer yes or no, then give a one-sentence reason."))
        enc = self.tok(text, return_tensors="pt").to("cuda:0")
        gen = self.model.generate(**enc, max_new_tokens=max_new_tokens, do_sample=False)
        return self.tok.decode(gen[0, enc["input_ids"].shape[1]:], skip_special_tokens=True).strip()
