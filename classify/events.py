"""Headline event detection and figure parsing (regex; free tier, no model calls).

Ported from physical-ai-tracker classify/events.py. Materials types added: result (a paper or discovery), launch,
deployment (production, shipments, installs), license, pilot (samples, trials, scale-up), correction (retraction,
critique, dispute). Financial types (LEDGER) can be auto-accepted from tier 1; STAGE types never are: every stage,
evidence or dispute change goes through review.
"""
import re

LEDGER = ["funding", "valuation", "listing", "acquisition", "status"]
STAGE = ["correction", "result", "deployment", "launch", "license", "pilot"]

PATTERNS = {
    "listing": r"\b(IPO|go(?:es|ing)? public|SPAC|direct listing|files? (?:confidentially )?for (?:an )?IPO|delist\w*|"
               r"(?:Nasdaq|NYSE) debut|public listing)\b",
    "acquisition": r"\b(acquires?|acquired|acquisition|to buy|buys|merg(?:e|er|es|ing) with|takeover)\b",
    "status": r"\b(shut(?:s|ting)? down|bankrupt\w*|Chapter 11|winds? down|ceases? operations|insolven\w*|"
              r"lay(?:s|ing)? off|layoffs?)\b",
    "funding": r"\b(rais(?:e|es|ed|ing)|funding|financing|Series [A-H]\b|seed round|pre-seed|investment|invests?|"
               r"backed by|round)\b",
    "valuation": r"\b(valu(?:ed|ation)|unicorn)\b",
    "correction": r"\b(retract\w*|correction|erratum|corrigendum|critique|disput\w*|flawed|overstat\w*|"
                  r"questions? (?:the )?(?:claims?|results?)|not (?:actually )?new)\b",
    "result": r"\b(discover\w*|publish\w*|paper|study|journal|Nature|Science|preprint|arXiv|peer[- ]reviewed|"
              r"breakthrough|record|outperform\w*|new (?:material|alloy|catalyst|electrolyte|polymer|compound))\b",
    "deployment": r"\b(deploy\w*|ships?|shipping|shipped|in production|mass production|production line|"
                  r"commerciali[sz]\w*|install\w*|poured|in use|supply agreement|offtake|plant|factory)\b",
    "launch": r"\b(launch\w*|unveil\w*|introduc\w+|debuts?|now available|commercially available|announces? new)\b",
    "license": r"\b(licens\w*)\b",
    "pilot": r"\b(pilot\w*|samples?|sampling|trials?|qualif\w*|scale[- ]up|scales? up|validat\w*|tested by|"
             r"evaluation)\b",
    "partnership": r"\b(partner(?:s|ship|ing)?|collaborat\w+|teams? up|alliance|joint development)\b",
    "contract": r"\b(contract|awarded|grant|award|purchase order|orders?)\b",
}
RX = {k: re.compile(v, re.I) for k, v in PATTERNS.items()}

MONEY = re.compile(r"(?:US)?[$€£]\s?(\d+(?:\.\d+)?)\s?(billion|bn|b|million|mn|m)\b", re.I)
SCALE = {"b": 1e9, "bn": 1e9, "billion": 1e9, "m": 1e6, "mn": 1e6, "million": 1e6}
ROUND = re.compile(r"\b(pre-seed|seed(?: prime| extension)?|Series [A-H](?:[- ]?\d|\+)?(?: extension)?|bridge round|"
                   r"growth round)\b", re.I)
VALUATION = re.compile(r"(?:valu(?:ed|ation)\s+(?:of\s+|at\s+)?(?:[a-z]+\s+){0,3}(?:US)?\$\s?(\d+(?:\.\d+)?)\s?"
                       r"(billion|bn|b|million|mn|m)\b)|"
                       r"(?:(?:US)?\$\s?(\d+(?:\.\d+)?)\s?(billion|bn|b|million|mn|m)\b\s+valuation)", re.I)
REPORTED = re.compile(r"\b(in talks|talks|seeks?|seeking|reportedly|report(?:s|ed)? that|eyes|eyeing|nears?|"
                      r"considering|discuss\w*|plan(?:s|ning|ned)?|looking to|could|said to|sources say|"
                      r"according to|explores?|exploring|weighs?|in discussions|raising|aims? to|set to|"
                      r"expected to|would|may)\b", re.I)
CLOSED = re.compile(r"\b(rais(?:es|ed)|clos(?:es|ed)|secur(?:es|ed)|lands?|landed|grabs?|nabs?|bags?|snags?|"
                    r"announces? \$|completes?|completed|Series [A-H]|seed round|seed prime|pre-seed)\b", re.I)
NOT_EQUITY = re.compile(r"\b(debt|loan|credit facility|grant|award\w*|contract|government|program|invests? (?:in|into)|"
                        r"investment (?:in|into))\b", re.I)
NON_USD = re.compile(r"[€£]")


def events(title):
    return [k for k in PATTERNS if RX[k].search(title)]


def _usd(num, unit):
    return int(round(float(num) * SCALE[unit.lower()]))


def valuation(title):
    m = VALUATION.search(title)
    if not m:
        return None
    num, unit = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
    return _usd(num, unit)


def money(title):
    """Dollar amounts in the headline, valuation figures removed. Euro and pound figures are returned too; the
    acceptance rules send any non-USD headline to review (non_usd)."""
    v = VALUATION.search(title)
    rest = title[:v.start()] + title[v.end():] if v else title
    return [_usd(n, u) for n, u in MONEY.findall(rest)]


def non_usd(title):
    return bool(NON_USD.search(title))


def round_of(title):
    m = ROUND.search(title)
    return m.group(1).title().replace("Pre-Seed", "Pre-seed") if m else ""


def reported(title):
    return bool(REPORTED.search(title))


def clean_round(title):
    """A headline that states a closed equity round: closing verb, and no debt, grant or third-party investment."""
    return bool(CLOSED.search(title)) and not NOT_EQUITY.search(title)


def qualifier(title):
    t = title.lower()
    if re.search(r"\b(at least|more than|over|north of|upwards of)\s+(?:us)?\$", t):
        return "at_least"
    if re.search(r"\b(up to|as much as)\s+(?:us)?\$", t):
        return "up_to"
    if re.search(r"\b(about|around|nearly|roughly|almost|some|approximately)\s+(?:us)?\$|~\$", t):
        return "approx"
    return ""
