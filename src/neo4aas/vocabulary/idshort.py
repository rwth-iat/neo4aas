"""Two derived forms of an ``idShort``: one for matching, one for grouping.

``shape_key`` hashes the ``idShort`` **verbatim** — deliberately, because
normalising would merge two real vocabularies (see :mod:`neo4aas.vocabulary.extract`).
The cost is that nothing in the graph can match idShorts across vendors: ``ValueID``
and ``ValueId`` are two shapes, ``CityTown`` and ``City_Town`` are two shapes. These
two functions pay that cost back without touching the key.

===================  ===========================================================
``id_short``         verbatim, **never modified**
``id_short_norm``    tokenised — matching, search, embedding
``id_short_base``    identity after variant suffixes are stripped — grouping
===================  ===========================================================

``id_short_base`` is computed **from** ``id_short_norm``, never from the raw
string, and both are pure functions: the same input gives the same output on any
machine and any run, which is what lets them be computed at import and stored.

Every comparison must normalise **both** sides. A raw query term matched against a
normalised property silently returns nothing, and that bug is invisible in a test
that only feeds it normalised input — which is why both functions are public and
why :func:`id_short_base` normalises its own input rather than trusting the caller.

What the corpus says
--------------------

Measured over the 4 510 distinct idShorts of a 3 000-file stratified sample:

* **1 731 (38 %) end in an opaque hex identifier** — ``ContactInformation_0041b5db…``,
  SICK's ``Nameplate_0226303791c8…``. Splitting an MD5 on its letter/digit
  boundaries produces twenty tokens of noise, so a hex run of 8+ characters is kept
  whole by the normaliser and dropped by the base: it is per-product identity, which
  is exactly what a base is meant to strip.
* **701 start with an ECLASS coded name** — ``AAQ326-address_of_additional_link``,
  ``BAB010-…``. The digits split off (``aaq 326``), and the base strips the
  three-letter/three-digit *pair* off the head.
* **digits that are identity rather than enumeration are rare here** (10 of 4 510:
  Festo's ``CPX-AP-I-4IOL-M12``) but common in the dictionaries the semanticIds point
  at — IEC CDD names carry ``IP00``…``IP69`` (70), ``IK00``…``IK10`` (45),
  ``M10``/``M12``/``M16`` (29), ``RS422``/``RS485`` (7). Hence
  :data:`DIGIT_SIGNIFICANT_PREFIXES`: without it ``Rating_IP65`` bases to ``rating ip``.

Two decisions worth stating
---------------------------

**The language *word* is identity; the language *tag* is not.**
``Document01_BedienungsanleitungDE`` bases to ``document 01 bedienungsanleitung``. A
*Bedienungsanleitung* is a different document type from a *Manual*, not a translation
of one, so the German word stays and only the trailing ``de`` tag goes.

**Enumeration is stripped from the tail only.** ``Document07`` -> ``document``,
``AAQ672-Connector__00__`` -> ``connector``, but ``Document01_Bedienungsanleitung``
keeps its ``01`` — position is the only evidence separating a counter from identity,
and stripping digits *anywhere* also strips the ``4`` out of Festo's
``CPX-AP-I-4IOL-M12``. The cost is named rather than hidden: ``Document05_HandbücherDE``
and ``Document29_HandbücherDE`` are one document type counted twice and no longer group,
which the interior rule this replaced did catch. A tail strip after the leading coded
name is removed is what keeps ``AAQ680-AdditionalInformation02`` grouping.

**An all-lowercase run is left alone.** ``Backpressureresistantversion`` has no
recoverable boundary. A dictionary-based compound splitter would mis-cut German, so
it passes through unchanged and the base of it is itself.

**Umlauts expand to two characters; every other diacritic folds to its base letter.**
``Handbücher`` -> ``handbuecher``, ``Baugröße`` -> ``baugroesse``, ``résumé`` ->
``resume``. The corpus is what decides this: Festo ships ``Baugroeße`` — its own
half-transliteration, ``ö`` written ``oe`` and the ``ß`` left standing — beside
``Document01_HandbücherDE``, so one name already exists in the data in several
spellings that only a fold can join. The two-character form is not decoration: it is
what keeps ``Höhe`` (``hoehe``) apart from ``Hohe`` (``hohe``), which a naive
accent-strip would merge.

Symbols are **not** touched. ``I²TTimeMotor`` -> ``i ² t time motor`` and
``RepetitionAccuracyIn±%FS`` -> ``repetition accuracy in±%fs`` — both real Festo
idShorts, both pinned by a test so that deciding otherwise stays a deliberate act.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

#: Separator runs. The en-dash is not decoration — ``DFM–CleanRoomClass`` is real
#: corpus data, and treating it as a letter glues two words together.
_SEPARATORS = re.compile(r"[\s_\-–—./+]+")

#: Acronyms that a lexical split cannot recover. ``ACvoltage`` splits as ``A`` +
#: ``Cvoltage`` under any camelCase rule — only knowing that ``AC`` is a unit fixes
#: it. Longest-match-first, at a word start, **case-sensitive**, and only when at
#: least two lowercase letters follow. All three guards earn their place:
#: case-sensitivity keeps ``Identification`` whole (``Id`` is not ``ID``), the
#: two-letter follower keeps ``LEDs`` whole, and requiring a *lowercase* follower
#: leaves ``IDENTIFIER`` and ``IDShort`` to the ordinary rules. The residual risk is
#: an acronym glued to a lowercase word that is not one — ``ISOlation`` would split —
#: which no idShort in the corpus does, and which normal capitalisation
#: (``Isolation``) does not trigger.
ACRONYMS: tuple[str, ...] = (
    "GTIN", "RFID", "IEC", "ISO", "DIN", "URI", "URL", "EMC", "RPM", "PLC",
    "USB", "LED", "EAN", "MAC", "AC", "DC", "IP", "ID", "IO",
)

#: Tokens after which a number is part of the name, not a counter: ``ip 65`` is a
#: degree of protection, ``m 12`` a thread size, ``rs 485`` a bus. Mined from the
#: IEC CDD preferred names (the counts are in the module docstring), not guessed.
DIGIT_SIGNIFICANT_PREFIXES: frozenset[str] = frozenset({
    "ip", "ipx", "ipk", "ik", "m", "rs", "pt", "ni", "cu",
    "iec", "iso", "din", "en", "ul", "sil", "nema", "class", "type",
})

#: Two-character expansions, applied **after** lowercasing, so only lowercase keys are
#: needed — ``"ẞ".lower()`` is ``"ß"`` and ``"Å".lower()`` is ``"å"``. German first,
#: because the corpus is German-heavy and one vendor already writes ``Baugroeße`` by
#: hand; the Nordic letters follow the same convention and, like ``ß``, have no
#: decomposition for the accent fold below to reach.
TRANSLITERATIONS: dict[str, str] = {
    "ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
    "ø": "oe", "å": "aa", "æ": "ae", "þ": "th",
}

#: Language tags stripped from the tail of a base. Deliberately short — every entry
#: is also a word somewhere, so the list is the ones the corpus actually appends.
LANGUAGE_TAGS: frozenset[str] = frozenset({"de", "en", "fr", "it", "es", "cn", "jp"})

#: Regions of a BCP 47 tag, which rule 2 of the normaliser leaves as a second token
#: (``de-DE`` -> ``de de``). Stripped only when a language tag precedes them.
LANGUAGE_REGIONS: frozenset[str] = frozenset({
    "de", "en", "us", "gb", "at", "ch", "fr", "it", "es", "cn", "jp", "nl", "be",
})

#: An opaque per-product identifier — an MD5, a UUID fragment, a hash suffix. Kept
#: whole by the normaliser (splitting it yields nothing but noise) and dropped by
#: the base. The digit requirement is what stops it swallowing a real word made of
#: ``a``-``f`` letters.
_OPAQUE = re.compile(r"(?=.*\d)[0-9a-fA-F]{8,}")

_INTEGER = re.compile(r"\d+")
_ECLASS_LETTERS = re.compile(r"[a-z]{3}")
_ECLASS_DIGITS = re.compile(r"\d{3}")


def _acronym_at(token: str, i: int) -> int:
    """Length of the acronym starting at ``i``, or 0. Longest match first."""
    for entry in _ACRONYMS_BY_LENGTH:
        end = i + len(entry)
        if not token.startswith(entry, i):
            continue
        if _starts_lower_word(token, end):
            return len(entry)
    return 0


_ACRONYMS_BY_LENGTH = tuple(sorted(ACRONYMS, key=len, reverse=True))


def _is_boundary(token: str, i: int) -> bool:
    """Whether a word starts at ``i``. Character tests, not a regex.

    Python's :mod:`re` has no ``\\p{Lu}``, and ``[A-Z]`` would miss ``Ä``/``Ö``/``Ü``
    in a German-heavy corpus. :meth:`str.isupper` and friends are Unicode-correct.
    """
    prev, cur = token[i - 1], token[i]
    if (prev.islower() or prev.isdigit()) and cur.isupper():
        return True                                    # camelCase
    if prev.isupper() and cur.isupper() and _opens_a_word(token, i + 1):
        return True                                    # acronym then word
    if prev.isalpha() and cur.isdigit():
        return True                                    # Document01
    if prev.isdigit() and cur.isalpha():
        return True                                    # IP65Rating
    return False


def _lower_run(token: str, i: int) -> int:
    """Length of the lowercase-letter run starting at ``i``."""
    n = i
    while n < len(token) and token[n].isalpha() and token[n].islower():
        n += 1
    return n - i


def _starts_lower_word(token: str, i: int) -> bool:
    """At least two lowercase letters from ``i`` — a word, not a plural ``s``."""
    return _lower_run(token, i) >= 2


def _opens_a_word(token: str, i: int) -> bool:
    """Whether the uppercase letter at ``i - 1`` starts a new word.

    Two lowercase letters are the ordinary case (``IDShort``). One is ambiguous, and
    what disambiguates it is what comes *after*: in ``LEDs`` the single ``s`` ends the
    token, so it is the plural of the acronym being split off and ``LE Ds`` would be
    wrong; in ``URIOfTheProduct`` it is followed by another capital, so ``Of`` is a
    two-letter word and refusing to split gives ``uriof the product`` — a real form
    from the corpus, found by reading the report this rule feeds.
    """
    run = _lower_run(token, i)
    if run >= 2:
        return True
    return run == 1 and i + 1 < len(token) and token[i + 1].isupper()


def _split_token(token: str) -> list[str]:
    """One separator-delimited token into words."""
    if _OPAQUE.fullmatch(token):
        return [token]
    words: list[str] = []
    start = i = 0
    while i < len(token):
        if i == start:
            hit = _acronym_at(token, i)
            if hit:
                words.append(token[i:i + hit])
                i = start = i + hit
                continue
        elif _is_boundary(token, i):
            words.append(token[start:i])
            start = i
        i += 1
    words.append(token[start:])
    return [w for w in words if w]


@lru_cache(maxsize=200_000)
def normalize_id_short(value: str | None) -> str | None:
    """The tokenised form of an ``idShort``: ``AAQ672-Connector`` -> ``aaq 672 connector``.

    Empty in, ``None`` out — an ``idShort`` of ``""`` is not a name. Non-ASCII letters
    are folded rather than kept: umlauts expand to two characters
    (``Handbücher`` -> ``handbuecher``), everything else loses its accent. See
    :func:`_fold` for why that order is the only correct one.
    """
    if not value:
        return None
    words: list[str] = []
    for token in _SEPARATORS.split(unicodedata.normalize("NFC", value)):
        words.extend(_split_token(token))
    # .lower(), not .casefold(): casefold maps ß to ss — which _fold does deliberately
    # and per-character — but also rewrites letters no idShort wants rewritten.
    # Folding happens here, after the split, so token boundaries keep exactly the
    # semantics they have for ASCII (_is_boundary reads ö and ß correctly already).
    return " ".join(_fold(w.lower()) for w in words) or None


def _fold(word: str) -> str:
    """Expand the two-character letters, then fold every remaining diacritic.

    The order is the correctness argument. Expansion must come **first**: NFD would
    decompose ``ü`` into ``u`` plus a combining diaeresis, the mark strip would drop
    the mark, and ``Höhe`` would collapse onto ``Hohe`` — two different German words.
    Expanding first means the mark strip only ever sees the diacritics nobody writes a
    two-letter form for (``é``, ``ñ``, ``ç``), which is exactly where dropping the
    accent is the right answer. A letter with no decomposition and no entry in the map
    (``đ``, ``ł``) passes through unchanged rather than being guessed at.
    """
    if word.isascii():
        return word
    expanded = "".join(TRANSLITERATIONS.get(ch, ch) for ch in word)
    decomposed = unicodedata.normalize("NFD", expanded)
    stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return unicodedata.normalize("NFC", stripped)


def _digit_is_significant(tokens: list[str], i: int) -> bool:
    """Whether the number at ``i`` belongs to the name that precedes it."""
    return i > 0 and tokens[i - 1] in DIGIT_SIGNIFICANT_PREFIXES


def _strip_suffixes(tokens: list[str]) -> None:
    """Strip trailing enumeration and language tags, in place, until nothing changes.

    Only the **tail** is touched. An enumeration in the middle of a name is left
    standing: it is as likely to be identity (``4IOL``, ``61987``) as a counter, and
    the position is the only evidence available to tell them apart.
    """
    changed = True
    while changed and tokens:
        changed = False
        last = len(tokens) - 1
        if _INTEGER.fullmatch(tokens[last]) and not _digit_is_significant(tokens, last):
            tokens.pop()
            changed = True
        elif last > 0 and tokens[last] in LANGUAGE_REGIONS and tokens[last - 1] in LANGUAGE_TAGS:
            del tokens[last - 1:]                      # a BCP 47 pair: "de de", "en us"
            changed = True
        elif tokens[last] in LANGUAGE_TAGS and last > 0:
            tokens.pop()
            changed = True


@lru_cache(maxsize=200_000)
def id_short_base(value: str | None) -> str | None:
    """The identity of an ``idShort`` once its variant suffixes are gone.

    ``AAQ672-Connector__00__`` -> ``connector``, ``ContactInformation01`` ->
    ``contact information``. Accepts a raw **or** an already-normalised string —
    it normalises first, which is what makes ``base(base(x)) == base(x)``.

    Never returns the empty string: an ``idShort`` that is nothing but enumeration
    (``__00__``) keeps its normalised form rather than losing its identity entirely.
    """
    norm = normalize_id_short(value)
    if norm is None:
        return None
    tokens = [t for t in norm.split() if not _OPAQUE.fullmatch(t)]

    _strip_suffixes(tokens)                            # 1. enumeration, 2. language

    if (len(tokens) > 2 and _ECLASS_LETTERS.fullmatch(tokens[0])   # 3. leading coded name
            and _ECLASS_DIGITS.fullmatch(tokens[1])):
        del tokens[:2]
        _strip_suffixes(tokens)      # the head may have hidden a trailing enumeration

    return " ".join(tokens) or norm                                # 4. never empty
