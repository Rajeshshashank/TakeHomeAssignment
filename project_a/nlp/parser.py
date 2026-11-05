"""Rule-based plain-language parser for salary-tax descriptions.

Design, and why each part is there:

* **Sentence segmentation** on ``. ;`` and newlines, but never inside a number
  (``1.5 lakh``, ``18,00,000``), keeps a deduction figure from being matched to
  a keyword in the next sentence.
* **Amount-first assignment.** Each monetary mention takes the nearest field
  keyword in its own sentence. Amount-first (not keyword-first) is what makes
  ``80C 1.5L, home loan 2L`` come out right: 1.5L sits next to 80C and 2L sits
  next to the home-loan keyword.
* **Plausibility ceilings.** A deduction is never larger than a statutory cap,
  so an oversized number near a deduction keyword is rejected and left to be
  claimed as the salary. This is what stops ``I earn 11 lakh ... 1.5 lakh in
  PPF`` from putting 11 lakh under 80C.
* **Salary = largest unclaimed amount.** A salary is an order of magnitude
  larger than any deduction, so "largest unclaimed" beats "nearest keyword".
* **80D splitter.** ``80D for self 50000 and for senior parents 50000`` has two
  amounts and one 80D keyword; the self/parent nouns route them.

Anything missing or contradicted is reported so the caller asks rather than
guesses.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from ..models import TaxpayerProfile

ONES = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
MULTIPLIERS = {
    "k": 1_000, "thousand": 1_000, "hazaar": 1_000, "hazar": 1_000, "hajar": 1_000,
    "lakh": 100_000, "lakhs": 100_000, "lac": 100_000, "lacs": 100_000, "l": 100_000,
    "crore": 10_000_000, "crores": 10_000_000, "cr": 10_000_000,
}
MONTHLY = ["per month", "monthly", "a month", "each month", "every month", "p.m",
           "pm", "mahine", "mahina", "har mahine", "month", "/month", "per mnth", "mth"]
ANNUAL = ["per year", "per annum", "annually", "annual", "yearly",
          "lpa", "saal", "salana", "each year", "/year", "a year", "year", "annum"]
METRO_CITIES = ["mumbai", "delhi", "kolkata", "calcutta", "chennai", "madras"]
NON_METRO = ["pune", "jaipur", "bangalore", "bengaluru", "hyderabad", "ahmedabad",
             "lucknow", "chandigarh", "indore", "kochi", "nagpur", "surat", "noida",
             "gurgaon", "gurugram", "coimbatore", "bhopal", "patna", "vadodara"]

FIELD_PATTERNS: list[tuple[str, str]] = [
    ("sec_80ccd_1b", r"80\s?ccd\s?\(?1b\)?|\bnps\b|national pension"),
    ("sec_80c", r"80\s?c\b|\bppf\b|\belss\b|\blic\b|\bpf\b|provident fund|sukanya|tuition fee|principal repayment"),
    ("sec_80d", r"80\s?d\b|medical insurance|mediclaim|health insurance|health premium"),
    ("home_loan_interest", r"home loan interest|housing loan interest|house loan interest|\b24\s?\(?b\)?\b|loan ka interest|home loan"),
    ("professional_tax", r"professional tax|ptax|profession tax"),
    ("hra_received", r"\bhra\b|house rent allowance|ghar bhatta|rent allowance"),
    ("rent_paid", r"\brent\b|\bkiraya\b"),
    ("basic_salary", r"\bbasic\b|basic pay|basic salary"),
    ("dearness_allowance", r"\bda\b|dearness allowance"),
]
SALARY_WORDS = (r"\bsalar(?:y|ies|ied)\b|\bctc\b|\bpackage\b|annual income|\bi earn\b|"
                r"\bi make\b|\bincome\b|\bearnings?\b|\btankhwah\b|\bgross\b|\bpension\b|"
                r"\bper annum\b|\bannually\b|\ba year\b|\bper year\b|\bannual\b")
# Used only for the "two different salaries?" check. Deliberately narrower than
# SALARY_WORDS: 'annual' or 'per year' near a deduction figure is normal, so
# those would create false ambiguity.
STRONG_SALARY_WORDS = (r"\bsalar(?:y|ies|ied)\b|\bctc\b|\bpackage\b|\bincome\b|"
                       r"\bearn|\btankhwah\b|\bgross\b")

CONTRADICTION_MARKERS = ["actually", "sorry", "i mean", "wait", "correction",
                         "not really", "on second thought", "scratch that", "no no"]

_NUM_RE = re.compile(
    r"(?<![\d.])(?P<num>\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<mult>lakhs?|lacs?|crores?|cr|k|l|thousand|hazaar|hazar|hajar)?(?![\w])",
    re.IGNORECASE,
)
_WORD_NUM_RE = re.compile(
    r"\b(?P<words>(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|"
    r"forty|fifty|sixty|seventy|eighty|ninety)(?:[ -](?:one|two|three|four|five|six|"
    r"seven|eight|nine))?(?:[ -]point[ -](?:one|two|three|four|five|six|seven|eight|nine))?)"
    r"\s*(?P<mult>lakhs?|lacs?|crores?|cr|thousand|hazaar|hazar|hajar)\b",
    re.IGNORECASE,
)
_AGE_RE = re.compile(
    r"\b\d{2}\s*(?:years?|yrs?|saal|year old|yo\b)"
    r"|\b(?:age[d]?|i am|i'm|umar)\s+\d{2}\b", re.IGNORECASE)
_WORD_AGE_RE = re.compile(
    r"\b(?:i am|i'm|age[d]?|umar)\s+"
    r"(?P<words>(?:twenty|thirty|forty|fifty|sixty|seventy|eighty)"
    r"(?:[ -](?:one|two|three|four|five|six|seven|eight|nine))?)\b", re.IGNORECASE)

DEDUCTION_CEILING = {
    "sec_80c": 300_000, "sec_80ccd_1b": 150_000, "sec_80d_self": 100_000,
    "sec_80d_parents": 100_000, "home_loan_interest": 500_000,
    "professional_tax": 10_000, "hra_received": 2_000_000, "rent_paid": 3_000_000,
    "basic_salary": 5_000_000, "dearness_allowance": 2_000_000,
}
MIN_SALARY = 100_000


@dataclass
class Amount:
    value: float
    start: int
    end: int
    period: str
    segment: int

    @property
    def annual(self) -> float:
        return self.value * 12 if self.period == "monthly" else self.value


@dataclass
class ParseResult:
    profile: TaxpayerProfile
    missing: list[str] = field(default_factory=list)
    ambiguous: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    evidence: dict[str, str] = field(default_factory=dict)
    matched_fields: list[str] = field(default_factory=list)

    @property
    def needs_clarification(self) -> bool:
        return bool(self.questions)

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile.to_dict(),
            "missing": self.missing,
            "ambiguous": self.ambiguous,
            "questions": self.questions,
            "evidence": self.evidence,
            "matched_fields": self.matched_fields,
        }


def _words_to_number(words: str) -> float:
    words = words.lower().replace("-", " ").strip()
    if " point " in words:
        head, tail = words.split(" point ", 1)
        return float(ONES.get(head.strip(), 0)) + ONES.get(tail.strip(), 0) / 10.0
    return float(sum(ONES[tok] for tok in words.split() if tok in ONES))


def _segments(text: str) -> list[tuple[int, int]]:
    bounds = [0]
    for m in re.finditer(r"(?:(?<!\d)\.|\.(?!\d))|[;\n]", text):
        bounds.append(m.end())
    bounds.append(len(text))
    bounds = sorted(set(bounds))
    return [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]


def _segment_index(segments: list[tuple[int, int]], pos: int) -> int:
    for i, (s, e) in enumerate(segments):
        if s <= pos < e:
            return i
    return len(segments) - 1


def _period_at(text: str, start: int, end: int, seg: tuple[int, int]) -> str:
    """Period from the amount's *own clause* only.

    Looking outside the clause is wrong: in "75k a month. 80C 150000 annually"
    a wider left window would see "month" and mislabel the 80C figure.
    """
    s, e = seg
    after = text[end:min(e, end + 24)].lower()
    before = text[max(s, start - 26):start].lower()

    def period_of(txt: str) -> Optional[str]:
        m = any(k in txt for k in MONTHLY)
        a = any(k in txt for k in ANNUAL)
        if m and not a:
            return "monthly"
        if a and not m:
            return "annual"
        return None

    return (period_of(after) or period_of(before)
            or ("ambiguous" if (any(k in after or k in before for k in MONTHLY)
                                and any(k in after or k in before for k in ANNUAL))
                else "unspecified"))


def _find_amounts(text: str, segments: list[tuple[int, int]]) -> list[Amount]:
    out: list[Amount] = []
    age_spans = [m.span() for m in _AGE_RE.finditer(text)]
    for m in _NUM_RE.finditer(text):
        if any(s <= m.start() < e for s, e in age_spans):
            continue
        mult = (m.group("mult") or "").lower()
        val = float(m.group("num").replace(",", ""))
        if mult:
            val *= MULTIPLIERS[mult]
        out.append(Amount(val, m.start(), m.end(), _period_at(text, m.start(), m.end(), segments[_segment_index(segments, m.start())]),
                          _segment_index(segments, m.start())))
    for m in _WORD_NUM_RE.finditer(text):
        val = _words_to_number(m.group("words")) * MULTIPLIERS[m.group("mult").lower()]
        out.append(Amount(val, m.start(), m.end(), _period_at(text, m.start(), m.end(), segments[_segment_index(segments, m.start())]),
                          _segment_index(segments, m.start())))
    out.sort(key=lambda a: a.start)
    return out


def _trigger_list(text: str, segments: list[tuple[int, int]]) -> list[tuple[str, int, int, int]]:
    out: list[tuple[str, int, int, int]] = []
    for fname, pattern in FIELD_PATTERNS:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            field_name = fname
            if fname == "sec_80d":
                near = text[max(0, m.start() - 35): m.start() + 45].lower()
                field_name = ("sec_80d_parents"
                              if re.search(r"parent|father|mother|maa|mata|pita", near)
                              else "sec_80d_self")
            out.append((field_name, m.start(), m.end(), _segment_index(segments, m.start())))
    for m in re.finditer(SALARY_WORDS, text, re.IGNORECASE):
        out.append(("gross_salary", m.start(), m.end(), _segment_index(segments, m.start())))
    out.sort(key=lambda t: t[1])
    return out


def _split_80d(text: str, segments: list[tuple[int, int]], amounts: list[Amount],
               assigned: dict[str, Amount], used: set[int]) -> None:
    """Route two amounts in one 80D clause to self vs parents by the nearest noun."""
    for seg_i, (s, e) in enumerate(segments):
        clause = text[s:e]
        if not re.search(r"80\s?d\b|mediclaim|medical insurance", clause, re.IGNORECASE):
            continue
        in_clause = [(i, a) for i, a in enumerate(amounts) if a.segment == seg_i]
        if len(in_clause) < 2:
            continue
        self_kw = [s + m.start() for m in re.finditer(r"\bself\b|myself|mera\b|mere\b", clause, re.IGNORECASE)]
        par_kw = [s + m.start() for m in re.finditer(r"parent|father|mother|maa|mata|pita", clause, re.IGNORECASE)]
        if not self_kw or not par_kw:
            continue
        for key, kws in (("sec_80d_self", self_kw), ("sec_80d_parents", par_kw)):
            best = None
            for i, a in in_clause:
                dist = min(abs(a.start - k) for k in kws)
                if best is None or dist < best[0]:
                    best = (dist, i, a)
            if best:
                assigned[key] = best[2]
                used.add(best[1])


def _assign(text: str, segments: list[tuple[int, int]]):
    amounts = _find_amounts(text, segments)
    triggers = _trigger_list(text, segments)
    assigned: dict[str, Amount] = {}
    used: set[int] = set()

    for ai, amt in enumerate(amounts):
        best: Optional[tuple[int, int, str]] = None
        for fname, ts, te, seg in triggers:
            if fname in assigned or seg != amt.segment:
                continue
            if amt.end <= ts:
                dist, prefer = ts - amt.end, 1
            elif amt.start >= te:
                dist, prefer = amt.start - te, 0
            else:
                dist, prefer = 0, 0
            if dist > 40:
                continue
            if amt.value > DEDUCTION_CEILING.get(fname, 10 ** 12):
                continue
            key = (dist, prefer, fname)
            if best is None or key < best:
                best = key
        if best is not None:
            assigned[best[2]] = amt
            used.add(ai)

    _split_80d(text, segments, amounts, assigned, used)

    evidence = {k: text[max(0, v.start - 30): v.end + 30].strip() for k, v in assigned.items()}
    leftover = [a for i, a in enumerate(amounts) if i not in used and a.value > 0]
    return assigned, evidence, leftover, amounts


def parse_description(text: str) -> ParseResult:
    t = " " + text.lower().replace("\u20b9", " rs ").replace("rupees", " rs ") + " "
    segments = _segments(t)
    profile = TaxpayerProfile()
    ambiguous: list[str] = []
    matched: list[str] = []

    age_m = re.search(r"\b(?:i am|i'm|age[d]?|umar)\D{0,10}(\d{2})\b", t)
    if not age_m:
        age_m = re.search(r"\b(\d{2})\s*(?:years?|yrs?|saal)\b", t)
    if age_m:
        profile.age = int(age_m.group(1))
        matched.append("age")
    else:
        wa = _WORD_AGE_RE.search(t)
        if wa:
            profile.age = int(_words_to_number(wa.group("words")))
            matched.append("age")
        elif re.search(r"\bsuper\s*senior\b", t):
            profile.age = 82
            matched.append("age")
        elif re.search(r"\bsenior\s*citizen\b|\bsenior\b", t):
            profile.age = 62
            matched.append("age")
        elif re.search(r"\bretired\b|\bpensioner\b", t):
            profile.age = 62
            matched.append("age")

    if any(c in t for c in METRO_CITIES):
        profile.is_metro = True
        matched.append("is_metro")
    elif any(c in t for c in NON_METRO):
        profile.is_metro = False
        matched.append("is_metro")

    if re.search(r"parents?[^.]{0,35}(senior|60|sixty|elderly|65|70)|senior parents|parents are senior", t):
        profile.parents_are_senior = True
        matched.append("parents_are_senior")

    contradiction = next((m for m in CONTRADICTION_MARKERS if m in t), None)
    assigned, evidence, leftover, amounts = _assign(t, segments)

    for fname, amt in assigned.items():
        setattr(profile, fname, amt.annual)
        matched.append(fname)
        if amt.period == "ambiguous":
            ambiguous.append(fname)

    # Salary: the largest unclaimed amount that is salary-sized once annualised.
    if "gross_salary" not in assigned and leftover:
        cands = [a for a in leftover if a.annual >= MIN_SALARY]
        if cands:
            cand = max(cands, key=lambda a: a.annual)
            profile.gross_salary = cand.annual
            evidence["gross_salary"] = "largest unclaimed amount"
            matched.append("gross_salary")
            leftover = [a for a in leftover if a is not cand]

    if contradiction:
        if "gross_salary" in assigned:
            tail = t[t.find(contradiction):]
            claimed = {round(a.annual) for k, a in assigned.items() if k != "gross_salary"}
            tail_amounts = [a for a in _find_amounts(tail, [(0, len(tail))])
                            if a.annual >= MIN_SALARY and round(a.annual) not in claimed]
            if tail_amounts:
                # The corrected figure is the *last* salary-sized number after
                # the marker; a trailing deduction figure is not a correction.
                last = max(tail_amounts, key=lambda a: a.start)
                if abs(last.annual - assigned["gross_salary"].annual) > 1:
                    profile.gross_salary = last.annual
                    evidence["gross_salary"] = tail.strip()[:80]
        ambiguous.append("gross_salary" if "gross_salary" in matched else "age")

    if contradiction is None and "gross_salary" in matched:
        claimed = {round(a.annual) for a in assigned.values()}
        candidates = {round(profile.gross_salary)}
        for a in amounts:
            if round(a.annual) in claimed or a.annual < 300_000:
                continue
            window = t[max(0, a.start - 14): a.end + 14]
            if re.search(STRONG_SALARY_WORDS, window, re.IGNORECASE):
                candidates.add(round(a.annual))
        if len(candidates) >= 2:
            ambiguous.append("gross_salary")

    if profile.rent_paid and not profile.hra_received and re.search(r"\bhra\b", t):
        ambiguous.append("hra_received")

    missing: list[str] = []
    if not profile.gross_salary:
        missing.append("gross_salary")

    ambiguous = sorted(set(ambiguous))
    profile.missing_fields = missing
    profile.ambiguous_fields = ambiguous
    questions = _build_questions(profile, missing, ambiguous)
    return ParseResult(profile=profile, missing=missing, ambiguous=ambiguous,
                       questions=questions, evidence=evidence, matched_fields=sorted(set(matched)))


def _build_questions(profile: TaxpayerProfile, missing: list[str], ambiguous: list[str]) -> list[str]:
    qs: list[str] = []
    if "gross_salary" in missing:
        qs.append("What is your total annual salary (cost to company) before any deductions?")
    if "gross_salary" in ambiguous:
        qs.append("I saw more than one salary figure, or the period was unclear. Is that annual or "
                  "monthly, and which figure is current?")
    if "age" in ambiguous:
        qs.append("You corrected your age partway through. Please confirm your age.")
    if "hra_received" in ambiguous:
        qs.append("You mentioned rent but I could not find the HRA you receive. What is your annual HRA?")
    if "sec_80c" in ambiguous:
        qs.append("How much did you invest under Section 80C this year (annual amount)?")
    if "rent_paid" in ambiguous:
        qs.append("Is the rent you mentioned monthly or annual?")
    return qs
