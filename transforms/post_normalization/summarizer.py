"""Fast, verbatim news highlights for VV post-normalization.

Standard library only. Bounded TF-IDF graph centrality, lead anchoring,
domain cues and redundancy-aware selection. No model downloads, rewritten
clauses or invented names. Each selected sentence has an exact source span
in ``cleaned_text``. ``AUTO_CANDIDATE`` means structural checks passed, not
that a human has verified every fact or selected sentence.
"""
from __future__ import annotations

import hashlib
import html
import math
import re
from collections import Counter
from dataclasses import dataclass, field

VERSION = "5.1.0"
RE_SPACE = re.compile(r"\s+")
RE_TOKEN = re.compile(r"[^\W_]+(?:['’\-][^\W_]+)*", re.UNICODE)
RE_HTML_META = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.I | re.S)
RE_TAG = re.compile(r"<[^<>]{1,500}>")
RE_CSS = re.compile(r"(?:\.[A-Za-z][\w-]*|#[\w-]+)\s*\{[^{}]{0,1000}\}", re.S)
RE_URL = re.compile(r"(?:https?://|www\.)[^\s<>]+|\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", re.I)
RE_ABBR = re.compile(r"(?<!\w)(?:a\.k\.a\.|e\.g\.|i\.e\.|Ph\.D\.|U\.S\.|U\.K\.|R\.A\.|G\.R\.|D\.O\.J\.|P\.O\.|a\.m\.|p\.m\.|Mr\.|Mrs\.|Ms\.|Dr\.|Prof\.|Atty\.|Engr\.|Hon\.|Sen\.|Rep\.|Gov\.|Pres\.|Sec\.|Rev\.|Fr\.|Ma\.|No\.|Nos\.|Art\.|Ret\.|Jr\.|Sr\.|Inc\.|Ltd\.|Corp\.|Co\.|approx\.|vs\.|Jan\.|Feb\.|Mar\.|Apr\.|Jun\.|Jul\.|Aug\.|Sep\.|Sept\.|Oct\.|Nov\.|Dec\.|St\.|Gen\.|Dept\.|Reg\.|Brgy\.)", re.I)
RE_EVENT = re.compile(r"\b(?:arrest\w*|apprehend\w*|charg(?:e|ed|es)|indict\w*|convict\w*|sentenc\w*|acquit\w*|debar\w*|rescu\w*|seiz\w*|confiscat\w*|warn\w*|sanction\w*|suspend\w*|revok\w*|recall\w*|appoint\w*|announc\w*|launch\w*|introduc\w*|approv\w*|investigat\w*|recover\w*|repatriat\w*|extradit\w*|issued|ordered|filed|signed|opened|released|received|found|raided|settled|fined)\b", re.I)
RE_CRIME = re.compile(r"\b(?:fraud\w*|corrupt\w*|brib\w*|launder\w*|terroris\w*|traffick\w*|smuggl\w*|counterfeit\w*|forger\w*|falsif\w*|cybercrime|theft|robbery|murder|kidnap\w*|extort\w*|violat\w*|illicit|illegal|alleg\w*|victim\w*|exploitat\w*|underage|minors|unsafe|unlicensed)\b", re.I)
RE_DISPOSITION = re.compile(r"\b(?:sentenced|convicted|acquitted|debarred|found\s+(?:not\s+)?guilty|cleared\s+of|exonerated|dismissed\s+(?:the\s+)?(?:case|charges|complaint)|(?:case|charges|complaint)\s+(?:against\b.{0,80}?)?(?:was|were|has\s+been|have\s+been)\s+(?:dismissed|dropped)|pleaded\s+guilty)\b", re.I)
RE_REGISTRATION = re.compile(r"\b(?:not\s+(?:a\s+)?(?:duly\s+)?registered|unregistered|no\s+(?:valid\s+)?registration)\b", re.I)
RE_AUTHORIZATION = re.compile(r"\b(?:not\s+(?:duly\s+)?(?:authori[sz]ed|licensed)|not\s+licensed|without\s+(?:a\s+|the\s+|proper\s+)?(?:license|authority)|no\s+(?:secondary\s+)?licen[cs]e|has\s+not\s+(?:secured|obtained)\b.{0,60}\blicen[cs]e)\b", re.I)
RE_WARNING = re.compile(r"\b(?:not\s+to\s+invest|not\s+invest|stop\s+investing|avoid\s+investing|advised\s+not|warned\s+(?:the\s+)?public)\b", re.I)
RE_RESCUE = re.compile(r"\b(?:rescued|liberated|freed\s+the\s+victims|safe\s+(?:recovery|release)|victims?\b.{0,80}\b(?:turned\s+over|shelter|protective\s+custody))\b", re.I)
RE_EVIDENCE = re.compile(r"\b(?:forensic|tested\s+positive|investigation\s+(?:found|revealed|established)|evidence\s+(?:revealed|gathered)|laboratory\s+(?:test|analysis)|confirmed\s+that|verified\s+that)\b", re.I)
RE_LEGAL_PROCESS = re.compile(r"\b(?:presented\b.{0,80}\binquest|charges?\s+(?:were\s+|was\s+|have\s+been\s+)?filed|filed\s+(?:criminal\s+)?charges|warrants?\s+of\s+arrest|plea.bargain|prosecutor\b.{0,70}\binquest)\b", re.I)
RE_PLEA = re.compile(r"\b(?:initially|previously|pleaded\s+not\s+guilty|plea.bargain)\b", re.I)
RE_SAFETY = re.compile(r"\b(?:should\s+not\s+stop\s+taking|no.one\s+should\s+stop\s+taking|without\s+(?:first\s+)?consulting\s+(?:a|their)\s+doctor)\b", re.I)
RE_PROMO = re.compile(r"\b(?:look\s+forward|proud\s+of|commended|congratulat\w*|thanked|reaffirmed\s+(?:the\s+)?commitment|unwavering\s+commitment|we\s+are\s+committed|relentless\s+in\s+pursuing|sends\s+a\s+clear\s+message|remains\s+unyielding)\b", re.I)
RE_CONTACT = re.compile(r"\b(?:media\s+contact|media\s+enquiries|press\s+enquiries|contact\s+(?:us|details|information)|follow\s+(?:us|the\s+\w+)\s+on|like\s+this|all\s+rights\s+reserved|share\s+this|subscribe\s+to|telephone|fax)\b|\[email\s+protected\]", re.I)
RE_BOILER = re.compile(r"\b(?:may\s+be\s+held\s+(?:criminally\s+)?liable|shall\s+be\s+(?:punished|penalized)|for\s+the\s+guidance\s+of\s+the\s+public|maximum\s+(?:fine|penalty)\s+of)\b", re.I)
RE_PRONOUN = re.compile(r"^(?:It|They|Their|Its|He|She|This|These|The\s+(?:firm|company|subject|suspect|accused))\b", re.I)
RE_IMPORTANT = re.compile(r"\b(?:mandatory|processing\s+time|no\s+corroborating\s+evidence|not\s+convincing|letters?\s+of\s+inquiry|show\s+cause\s+order|notices?\s+of\s+violation|malfunction\w*|not\s+used\s+at\s+all|has\s+closed|replaces\s+it)\b", re.I)
RE_FRAMING = re.compile(r"\b(?:Share\s+on\s+(?:Facebook|LinkedIn|X)|Opens\s+in\s+new\s+window|Photo\s+(?:by|courtesy)|proudly\s+shows|Notes\s+to\s+editors|Page\s+\d+\s+of\s+\d+)\b", re.I)
RE_NON_ENGLISH = re.compile(r"\b(?:inaresto|suspek|paglabag|kinasuhan|pulisya|sinabi|naniniwala|ang\s+aming|para\s+sa|ng\s+mga)\b", re.I)
STOP = frozenset("the a an and or but in on at to of with for by from after before into as is was were be been being has have had that which who whom this those these its their his her it they them there here said say he she will would could should about over under during while through than then based both also against such via nor each any all including report reported can may must shall not no".split())
FACT_PATTERNS = {"DISPOSITION": RE_DISPOSITION, "REGISTRATION": RE_REGISTRATION,
    "AUTHORIZATION": RE_AUTHORIZATION, "WARNING": RE_WARNING, "RESCUE": RE_RESCUE,
    "EVIDENCE": RE_EVIDENCE, "LEGAL_PROCESS": RE_LEGAL_PROCESS,
    "SAFETY": RE_SAFETY, "EVENT": RE_EVENT, "CRIME": RE_CRIME, "IMPORTANT": RE_IMPORTANT}


def normalize(text: str) -> str:
    return RE_SPACE.sub(" ", text).strip()


def clean_text(body: str) -> tuple[str, list[str]]:
    """Remove markup/known framing, never paraphrase article text."""
    text = html.unescape(body)
    notes = []
    text, n = RE_HTML_META.subn(" ", text)
    if n:
        notes.append("HTML_SCRIPT_STYLE_REMOVED")
    text, n = RE_CSS.subn(" ", text)
    if n:
        notes.append("CSS_REMOVED")
    text = re.sub(r"</?(?:p|div|li|h[1-6]|br)\b[^>]*>", "\n\n", text, flags=re.I)
    text = RE_TAG.sub(" ", text)
    text, n = re.subn(r"^\s*(?:Share on (?:Facebook|X|LinkedIn) \(Opens in new window\)\s*(?:Facebook|X|LinkedIn)?\s*)+", "", text, flags=re.I)
    text, m = re.subn(r"^\s*By [A-Z][A-Za-z .'-]{2,65}/Philippine News Agency\s*", "", text)
    text, k = re.subn(r"^\s*(?:ENFORCEMENT AND INVESTOR PROTECTION DEPARTMENT\s*)?(?:S\s*E\s*C\s+ADVISORY|A\s+D\s+V\s+I\s+S\s+O\s+R\s+Y)\s*", "", text, flags=re.I)
    if n or m or k:
        notes.append("WEBSITE_OR_ADVISORY_FRAMING_REMOVED")
    # Separate common website headers without touching the first narrative clause.
    text, n = re.subn(r"^\s*PRESS\s+STATEMENT\s+[A-Za-z]+\s+\d{1,2},?\s+\d{4}\s*", "", text, flags=re.I)
    text, m = re.subn(r"^\s*PRESS\s+RELEASE\s*\|\s*", "", text, flags=re.I)
    if n or m:
        notes.append("PRESS_HEADER_REMOVED")
    # An all-capital NBI heading before a dateline is layout, not a sentence.
    text, n = re.subn(r"^\s*NBI\s+[A-Z0-9\s,;:()&/'’\-]{15,240}?(?=PASAY(?:\s+CITY)?[,\s])", "", text)
    if n:
        notes.append("SHOUTING_TITLE_REMOVED")
    # Recognizable trailing page widgets and media-contact blocks only.
    markers = list(re.finditer(r"\b(?:Like\s+this\s*:|Media\s+(?:Contact|enquiries)\s*:|Press\s+enquiries\s*:|All\s+rights\s+reserved)", text, re.I))
    for marker in markers:
        if marker.start() > len(text)*.55:
            text = text[:marker.start()]
            notes.append("TRAILING_WIDGET_REMOVED")
            break
    # Keep paragraph boundaries for sentence splitting. Other spacing is normalized later.
    text = re.sub(r"[\t\r\xa0]+", " ", text)
    text = re.sub(r"\n[ \t]*\n+", "\n\n", text)
    return text.strip(), notes


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """Sentence offsets in cleaned text; masks punctuation without changing bytes."""
    protected = set()
    for match in RE_ABBR.finditer(text):
        dots = [i for i in range(match.start(), match.end()) if text[i] == "."]
        # Inc./Ltd./Corp. and country abbreviations can also end a sentence.
        terminal = re.fullmatch(r"(?:Inc|Ltd|Corp|Co|Jr|Sr|U\.S|U\.K)\.", match.group(), re.I)
        if match.group() == "SEC.":
            terminal = True
        if terminal and re.match(r"\s+[\"“‘]*(?:The|This|These|It|They|An|A|In|On|However|For)\b", text[match.end():]):
            dots = dots[:-1]
        protected.update(dots)
    for match in re.finditer(r"\b[A-Z]\.(?=\s+(?:[A-Z][\w’'-]+|[A-Z]\.))", text):
        protected.add(match.end()-1)
    for match in re.finditer(r"\d\.\d", text):
        protected.add(match.start()+1)
    for match in RE_URL.finditer(text):
        end = match.end()
        # Terminal punctuation after a URL is still punctuation.
        if text[end-1:end] in ".!?":
            end -= 1
        protected.update(i for i in range(match.start(), end) if text[i] in ".!?")
    boundaries = set()
    # Numbered/lettered list items are complete source statements. Delimit the
    # item labels rather than emitting fragments such as 'Reporting Format: a.'.
    for match in re.finditer(r"(?:;\s*(?:and,\s*)?|:\s*)(?:[a-z]|[IVX]{1,4})\.\s+(?=[A-Z])", text):
        boundaries.add(match.start()+1)
        boundaries.add(match.end())
    for match in re.finditer(r"(?<!\w)(?:[a-z]|[IVX]{1,4})\.(?=\s+[A-Z])", text):
        protected.add(match.end()-1)
    for match in re.finditer(r"[.!?]+[\"”’')\]]*(?=\s|$)", text):
        if match.start() in protected:
            continue
        # A decimal, abbreviation or ellipsis inside a sentence is not a boundary.
        if match.group().startswith("..."):
            continue
        boundaries.add(match.end())
    for match in re.finditer(r"\n\s*\n", text):
        boundaries.add(match.start())
        boundaries.add(match.end())
    spans, start = [], 0
    for end in sorted(boundaries | {len(text)}):
        left, right = start, end
        while left < right and text[left].isspace():
            left += 1
        while right > left and text[right-1].isspace():
            right -= 1
        if right > left:
            spans.append((left, right))
        start = end
    return spans


def sentence_split(text: str) -> list[str]:
    return [normalize(text[a:b]) for a, b in sentence_spans(text)]


def words(text: str) -> list[str]:
    return [t for t in RE_TOKEN.findall(text.lower()) if len(t)>2 and t not in STOP]


def features(text: str) -> set[str]:
    return {key for key, pattern in FACT_PATTERNS.items() if pattern.search(text)}


def multi_event(text: str) -> bool:
    if len(text) < 6000:
        return False
    starts = len(re.findall(r"\bOn\s+[A-Z][a-z]+\s+\d{1,2},?\s+20\d\d.{0,180}?\b(?:arrested|seized|operatives|agents)", text))
    return starts >= 4 or len(re.findall(r"\b(?:in another operation|in a separate operation|another case|different case)\b", text, re.I)) >= 3


def target_sentences(wordcount: int, requested: int = 0) -> int:
    if requested:
        return requested
    for threshold, target in [(80, 1), (180, 2), (350, 3), (650, 4), (1100, 5), (1800, 6), (3500, 7)]:
        if wordcount <= threshold:
            return target
    return 8


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(value*b.get(term, 0.) for term, value in a.items())


def normalized_vector(counts: Counter, idf: dict[str, float]) -> dict[str, float]:
    vec = {term: (1+math.log(count))*idf.get(term, 1.) for term, count in counts.items()}
    norm = math.sqrt(sum(v*v for v in vec.values())) or 1.
    return {term: value/norm for term, value in vec.items()}


def graph_rank(pool: list[int], vectors: list[dict[str, float]]) -> dict[int, float]:
    """Bounded weighted LexRank-style graph with damping and fixed iteration cap."""
    n = len(pool)
    if n <= 1:
        return {i: 1. for i in pool}
    adjacency = [[] for _ in pool]
    degrees = [0.]*n
    for a in range(n):
        for b in range(a+1, n):
            sim = cosine(vectors[pool[a]], vectors[pool[b]])
            if sim >= .10:
                adjacency[a].append((b, sim))
                adjacency[b].append((a, sim))
                degrees[a] += sim
                degrees[b] += sim
    rank = [1/n]*n
    for _ in range(30):
        dangling = sum(rank[i] for i in range(n) if not degrees[i])
        next_rank = [(1-.85)/n + .85*dangling/n]*n
        for i in range(n):
            if degrees[i]:
                scale = .85*rank[i]/degrees[i]
                for j, similarity in adjacency[i]:
                    next_rank[j] += scale*similarity
        delta = sum(abs(a-b) for a, b in zip(rank, next_rank))
        rank = next_rank
        if delta < 1e-5:
            break
    maximum = max(rank) or 1.
    return {index: rank[i]/maximum for i, index in enumerate(pool)}


@dataclass
class Result:
    summary: str
    status: str
    flags: list[str]
    selected_indices: list[int]
    sentences_available: int
    target: int
    engine: str
    removed_noise: list[str]
    source_sha256: str
    facts_in_source: list[str]
    facts_in_summary: list[str]
    selected_sentences: list[str] = field(default_factory=list)
    source_spans: list[tuple[int, int]] = field(default_factory=list)
    cleaned_text: str = ""
    sentence_count_valid: int = 0
    source_word_count: int = 0
    version: str = VERSION

    def to_dict(self):
        return {**self.__dict__, "flags": "; ".join(self.flags),
                "selected_indices": ",".join(map(str, self.selected_indices)),
                "removed_noise": "; ".join(self.removed_noise),
                "facts_in_source": "; ".join(self.facts_in_source),
                "facts_in_summary": "; ".join(self.facts_in_summary)}


class Summarizer:
    def __init__(self, *, engine="hybrid", sentences=0, max_input_chars=140000):
        # 'native' is a backward-compatible name for V5's dependency-free engine.
        if engine not in {"hybrid", "native", "sumy"}:
            raise ValueError("engine must be hybrid, native or sumy")
        if not isinstance(sentences, int) or not 0 <= sentences <= 10:
            raise ValueError("sentences must be 0..10; 0 chooses adaptively")
        if max_input_chars < 1:
            raise ValueError("max_input_chars must be positive")
        self.engine, self.sentences, self.max_input_chars = engine, sentences, max_input_chars

    def summarize(self, body: str | None, *, title: str = "") -> Result:
        if body is None:
            body = ""
        if not isinstance(body, str):
            raise TypeError("body must be a string or None")
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        result = Result("", "MISSING_BODY", [], [], 0, 0, self.engine, [], digest, [], [])
        if not body.strip():
            return result
        result.status = "MANUAL_REVIEW"
        if len(body) > self.max_input_chars:
            result.flags.append("OVERSIZE_INPUT")
            return result
        clean, result.removed_noise = clean_text(body)
        spans = sentence_spans(clean)
        sentences = [normalize(clean[a:b]) for a, b in spans]
        result.cleaned_text = clean
        result.sentences_available = len(sentences)
        result.source_word_count = len(normalize(clean).split())
        fs = [features(s) for s in sentences]
        valid = []
        for i, sentence in enumerate(sentences):
            tokens = words(sentence)
            noise = RE_CONTACT.search(sentence) or RE_FRAMING.search(sentence) or (RE_URL.search(sentence) and not fs[i].intersection({"EVENT", "CRIME", "AUTHORIZATION", "WARNING"}))
            heading = not fs[i] and (len(tokens) < 4 or sentence.endswith(":") or (sentence.isupper() and len(tokens) < 30))
            heading = heading or bool(re.match(r"^(?:On this note|Here(?:'s| is)|Last of .* Parts|For strict compliance)", sentence, re.I) and not fs[i])
            fragment = re.search(r"\b(?:and|or|of|the|with|from|at|for|to|by|under|that|which)\s*[.!?]?$", sentence, re.I)
            if noise or heading or fragment or len(tokens)<2:
                continue
            valid.append(i)
        result.sentence_count_valid = len(valid)
        if not valid:
            result.flags.append("NO_USABLE_SENTENCES")
            return result
        all_features = set().union(*(fs[i] for i in valid))
        result.facts_in_source = sorted(all_features)
        desired = target_sentences(result.source_word_count, self.sentences)
        # Highlights should not select nearly every sentence of a short article.
        maximum = len(valid) if self.sentences else max(1, math.ceil(len(valid)*.6))
        target = min(desired, maximum, len(valid))
        result.target = target
        token_counts = [Counter(words(s)) for s in sentences]
        df = Counter(t for i in valid for t in token_counts[i])
        idf = {t: 1+math.log((len(valid)+1)/(count+1)) for t, count in df.items()}
        vectors = [normalized_vector(c, idf) for c in token_counts]
        center_counts = sum((token_counts[i] for i in valid), Counter())
        center = normalized_vector(center_counts, idf)
        title_vec = normalized_vector(Counter(words(title)), idf) if title else {}
        # Anchor the first substantive sentence. Named narrative leads need not
        # contain a risk term; ordinary/non-adverse news is also supported.
        lead_candidates = [i for i in valid[:8] if (not RE_PRONOUN.search(sentences[i]) or fs[i])
                           and (fs[i] or re.search(r"\b(?:is|are|was|were|has|have|will|said|gave|reported|earned|marked|recorded|began|remains|remained|started|ended|grew|showed|provides)\b", sentences[i], re.I) or bool(re.search(r"\d", sentences[i])))
                           and not sentences[i].endswith("?")
                           and not (RE_PROMO.search(sentences[i]) and not fs[i].intersection({"DISPOSITION", "AUTHORIZATION", "REGISTRATION"}))]
        lead = lead_candidates[0] if lead_candidates else valid[0]
        # A short report may still need two sentences: e.g. arrest followed by
        # acquittal, or company registration followed by no investment licence.
        if not self.sentences and target == 1 and len(valid) >= 2:
            if any(key in all_features and key not in fs[lead] for key in ["DISPOSITION", "AUTHORIZATION", "SAFETY", "IMPORTANT"]):
                target = result.target = 2
        scores = {}
        for position, i in enumerate(valid):
            s, flags = sentences[i], fs[i]
            score = .8*cosine(vectors[i], center)+1.1*cosine(vectors[i], title_vec)
            score += 1.2/(1+position*.45)
            score += .65*("EVENT" in flags)+.65*("CRIME" in flags)
            score += 2.7*("DISPOSITION" in flags)+2.2*("AUTHORIZATION" in flags)
            score += 1.8*("REGISTRATION" in flags)+1.5*("WARNING" in flags)
            score += 1.9*("RESCUE" in flags)+.9*("EVIDENCE" in flags)+2.0*("SAFETY" in flags)
            score += 1.0*("LEGAL_PROCESS" in flags)
            score += 1.6*("IMPORTANT" in flags)
            if re.search(r"(?:[£$₱]|\b(?:Php|PhP|PHP|P)\s*\d|\d\s*%)", s):
                score += .35
            if i == lead:
                score += 1.8
            if RE_PROMO.search(s) and not flags.intersection({"DISPOSITION", "AUTHORIZATION", "REGISTRATION", "RESCUE"}):
                score -= 3.0
            if RE_BOILER.search(s):
                score -= 2.0
            if i != lead and re.search(r"\b(?:said|told|stated|emphasized|underscored|lauded|welcomes|welcome|pleasing|great news)\b", s, re.I) and not flags.intersection({"DISPOSITION", "AUTHORIZATION", "REGISTRATION", "RESCUE", "IMPORTANT"}):
                score -= 1.2
            if re.search(r"\b(?:we (?:remain|are) committed|one of kind|we are fortunate|grateful for|for their unwavering)\b", s, re.I):
                score -= 2.0
            if s.endswith("?"):
                score -= 1.6
            if RE_PLEA.search(s) and "DISPOSITION" not in flags:
                score -= .7
            if len(s.split()) > 110:
                score -= 1.0
            if re.search(r"\b(?:Curriculum vitae|Year Role|Full name:|Related posts|Photo credit)\b", s, re.I):
                score -= 3.0
            scores[i] = score
        # Bound graph size without discarding access to late source facts.
        if len(valid) <= 72:
            pool = valid[:]
        else:
            pool = sorted(set(valid[:20]+[valid[round(k*(len(valid)-1)/19)] for k in range(20)]
                              +sorted(valid, key=lambda i: (-scores[i], i))[:32]))
        if self.engine == "sumy":
            # Optional real Sumy LexRank ranking; custom tokenizer avoids Punkt downloads.
            from sumy.models.dom import Sentence, Paragraph, ObjectDocumentModel
            from sumy.summarizers.lex_rank import LexRankSummarizer
            class WordTokenizer:
                @staticmethod
                def to_words(text):
                    return tuple(RE_TOKEN.findall(text))
            objects = [Sentence(sentences[i], WordTokenizer()) for i in pool]
            ranker = LexRankSummarizer()
            ranker.stop_words = STOP
            doc = ObjectDocumentModel([Paragraph(objects)])
            chosen_objects = {id(s) for s in ranker(doc, min(len(objects), target*3))}
            ranks = {i: float(id(obj) in chosen_objects) for i, obj in zip(pool, objects)}
        else:
            ranks = graph_rank(pool, vectors)
        for i in valid:
            scores[i] += .75*ranks.get(i, 0.)
        chosen = [lead]
        def overlap(i):
            return max((cosine(vectors[i], vectors[j]) for j in chosen), default=0.)
        def merit(i):
            return scores[i]-2.5*overlap(i)
        # Different legal findings are not interchangeable. Reserve a slot for
        # outcome before evidence, then distinct authorization/rescue findings.
        for fact in ["DISPOSITION", "AUTHORIZATION", "REGISTRATION", "RESCUE", "WARNING", "SAFETY", "IMPORTANT", "LEGAL_PROCESS"]:
            if len(chosen) >= target:
                break
            if any(fact in fs[i] for i in chosen):
                continue
            options = [i for i in valid if i not in chosen and fact in fs[i]
                       and not RE_BOILER.search(sentences[i]) and overlap(i) < .88]
            if options:
                chosen.append(max(options, key=lambda i: (merit(i), -i)))
        while len(chosen) < target:
            options = [i for i in valid if i not in chosen and overlap(i) < .84]
            if not options:
                break
            best = max(options, key=lambda i: (merit(i), -i))
            if scores[best] < -.5:
                break
            chosen.append(best)
        chosen.sort()
        # Keep nearby antecedents intact instead of changing pronouns or names.
        context_added = []
        for i in chosen[:]:
            previous = next((j for j in reversed(valid) if j < i), None)
            if previous is None or previous in chosen or i-previous > 2:
                continue
            needs_context = bool(RE_PRONOUN.search(sentences[i]) and not any(0 < i-j <= 2 for j in chosen))
            needs_context |= bool(re.search(r"\breplaces it\b", sentences[i], re.I))
            needs_context |= bool(re.match(r"^The (?:anti.graft )?court\b.*\b(?:case|charges)\b", sentences[i], re.I)
                                  and RE_EVENT.search(sentences[previous]))
            if needs_context:
                limit = min(10, target+2) if not self.sentences else target
                if len(chosen) < limit:
                    chosen.append(previous)
                    context_added.append(previous)
                else:
                    result.flags.append("ANTECEDENT_NOT_SELECTED")
        chosen.sort()
        if context_added:
            result.target = max(target, len(chosen))
            result.removed_noise.append("ANTECEDENT_CONTEXT_INCLUDED")
        selected = [sentences[i] for i in chosen]
        result.selected_indices = chosen
        result.selected_sentences = selected
        result.source_spans = [spans[i] for i in chosen]
        result.summary = " ".join(selected)
        if (result.summary.count('“') != result.summary.count('”')
                or result.summary.count('"') % 2):
            result.flags.append("UNBALANCED_QUOTE_EXCERPT")
        kept = set().union(*(fs[i] for i in chosen))
        result.facts_in_summary = sorted(kept)
        # Verify independently of scoring: every excerpt must occur in the
        # original normalized body as well as in its exact cleaned-source span.
        original = normalize(html.unescape(body))
        if any(s not in original or normalize(clean[a:b]) != s for s, (a, b) in zip(selected, result.source_spans)):
            result.flags.append("SOURCE_SPAN_MISMATCH")
        if multi_event(clean):
            result.flags.append("MULTI_EVENT_NEEDS_REVIEW")
        if RE_NON_ENGLISH.search(clean):
            result.flags.append("LANGUAGE_RULES_LIMITED")
        if "DISPOSITION" in all_features and "DISPOSITION" not in kept:
            result.flags.append("DISPOSITION_NOT_SELECTED")
        if "AUTHORIZATION" in all_features and "AUTHORIZATION" not in kept:
            result.flags.append("AUTHORIZATION_NOT_SELECTED")
        if any("\ufffd" in s or re.search(r"[\x00-\x08\x0b\x0e-\x1f]", s) for s in selected):
            result.flags.append("SOURCE_ENCODING_OR_CONTROL_CHARACTERS")
        if any(len(s.split()) > 160 for s in selected):
            result.flags.append("VERY_LONG_SOURCE_SENTENCE")
        if len(result.summary.split()) > .85*max(1, result.source_word_count) and len(valid) >= 4:
            result.flags.append("HIGHLIGHTS_TOO_LONG")
        # Informational counts are not completeness promises for a full abstract.
        if len(valid) != len(sentences):
            result.removed_noise.append("LAYOUT_OR_CONTACT_SENTENCES_EXCLUDED")
        result.status = "MANUAL_REVIEW" if result.flags else "AUTO_CANDIDATE"
        return result
