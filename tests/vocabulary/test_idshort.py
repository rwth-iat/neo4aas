"""The two derived idShort forms.

These are *storage* properties, like ``shape_key``: a change to how they are
computed does not fail a test somewhere, it silently changes what every stored
``:Shape`` is keyed on for matching. The golden table below is real corpus data
and is the specification — read it as the contract, not as examples.

``Backpressureresistantversion`` is the negative case: it has no recoverable word
boundary and must pass through untouched — a compound splitter would mis-cut German.

Umlauts expand to two characters (``Handbücher`` -> ``handbuecher``) rather than
being kept or stripped, because the corpus writes one name several ways: Festo
ships ``Baugroeße`` — its own half-transliteration of ``Baugröße`` — and a query
typed on an ASCII keyboard has to reach both.
"""

import pytest

from neo4aas.vocabulary.extract import Shape, shape_key
from neo4aas.vocabulary.idshort import id_short_base, normalize_id_short

SAME = None  # base == norm

#: ``(raw, id_short_norm, id_short_base or SAME)`` — real idShorts from the corpus.
GOLDEN: tuple[tuple[str, str, str | None], ...] = (
    ("AddressOfAdditionalLink", "address of additional link", SAME),
    ("ACvoltage", "ac voltage", SAME),
    ("IP65Rating", "ip 65 rating", SAME),
    ("ValueID", "value id", SAME),
    ("ValueId", "value id", SAME),
    ("CityTown", "city town", SAME),
    ("City_Town", "city town", SAME),
    ("MaxLineCrossSection_Flexible", "max line cross section flexible", SAME),
    ("DFM–CleanRoomClassWithPlain_BearingGuideGF",
     "dfm clean room class with plain bearing guide gf", SAME),
    ("AAQ672-Connector__00__", "aaq 672 connector 00", "connector"),
    ("ContactInformation01", "contact information 01", "contact information"),
    ("Document01_BedienungsanleitungDE",
     "document 01 bedienungsanleitung de", "document 01 bedienungsanleitung"),
    ("CPX-AP-I-4IOL-M12", "cpx ap i 4 iol m 12", SAME),
    ("Handbücher", "handbuecher", SAME),
    ("Backpressureresistantversion", "backpressureresistantversion", SAME),
    ("Coding_head1_", "coding head 1", "coding head"),
)


@pytest.mark.parametrize("raw,expected", [(r, n) for r, n, _ in GOLDEN])
def test_normalize_golden(raw, expected):
    assert normalize_id_short(raw) == expected


@pytest.mark.parametrize("raw,expected", [(r, b if b is not SAME else n) for r, n, b in GOLDEN])
def test_base_golden(raw, expected):
    assert id_short_base(raw) == expected


# -- the acronym lexicon, and the damage it must not do -------------------------


def test_lexicon_splits_an_acronym_a_camel_rule_cannot():
    """``ACvoltage`` is why the lexicon exists.

    Purely lexical splitting sees ``A`` followed by ``Cv`` and yields ``a cvoltage``.
    No regex recovers that; only knowing ``AC`` is a unit does.
    """
    assert normalize_id_short("ACvoltage") == "ac voltage"
    assert normalize_id_short("DCcurrent") == "dc current"
    assert normalize_id_short("IOlink") == "io link"


def test_the_lexicon_is_case_sensitive_and_that_is_the_whole_guard():
    """An acronym glued to a lowercase word splits — including when it should not.

    ``ISOlation`` is the documented residual risk of the lexicon. No idShort in the
    corpus is written that way, and normal capitalisation does not trigger it.
    """
    assert normalize_id_short("ISOlation") == "iso lation"
    assert normalize_id_short("Isolation") == "isolation"


@pytest.mark.parametrize("raw,expected", [
    ("Identification", "identification"),   # not "id entification" — case-sensitive match
    ("Idle", "idle"),
    ("IDENTIFIER", "identifier"),           # follower is uppercase: lexicon must not fire
    ("LEDs", "leds"),                       # a plural acronym, not "LE Ds"
    ("Isolation", "isolation"),             # normal capitalisation never hits the lexicon
    ("IDShort", "id short"),                # the acronym-then-word rule already handles it
    ("URIOfTheProduct", "uri of the product"),   # a two-letter word, not a plural
    ("IDs", "ids"),
])
def test_lexicon_does_not_over_split(raw, expected):
    assert normalize_id_short(raw) == expected


# -- non-ASCII letters: expanded, not kept and not stripped ---------------------


@pytest.mark.parametrize("raw,expected", [
    ("Handbücher", "handbuecher"),
    ("Handbuecher", "handbuecher"),      # the ASCII spelling meets the umlaut one
    ("Baugröße", "baugroesse"),
    ("Baugroeße", "baugroesse"),         # verbatim from the corpus: half-transliterated
    ("Baugroesse", "baugroesse"),
    ("Handbu\u0308cher", "handbuecher"),  # decomposed input folds like composed input
    ("ÜBERDRUCK", "ueberdruck"),
    ("Größe", "groesse"),
])
def test_umlauts_expand_to_two_characters(raw, expected):
    """One name, several spellings, one normalised form. That is the whole point."""
    assert normalize_id_short(raw) == expected


def test_the_expansion_is_what_keeps_two_real_words_apart():
    """A naive accent-strip would merge these; a two-character expansion does not."""
    assert normalize_id_short("Höhe") == "hoehe"
    assert normalize_id_short("Hohe") == "hohe"


@pytest.mark.parametrize("raw,expected", [
    ("Målestørrelse", "maalestoerrelse"),   # Nordic letters expand too
    ("Æther", "aether"),
    ("résumé", "resume"),                   # everything else folds to its base letter
    ("español", "espanol"),
    ("Bürkert_Ventilgröße", "buerkert ventilgroesse"),
])
def test_nordic_expands_and_other_diacritics_fold(raw, expected):
    assert normalize_id_short(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("I²TTimeMotor", "i ² t time motor"),
    ("RepetitionAccuracyIn±%FS", "repetition accuracy in±%fs"),
])
def test_symbols_are_left_verbatim(raw, expected):
    """Pinned, not aspirational: symbols are out of scope, and this says so.

    ``"²".isdigit()`` is True, so the letter-digit rule splits it out as its own
    token; ``re``'s ``\d`` does not match it, so ``id_short_base`` never mistakes it
    for an enumeration counter. Both strings are real Festo idShorts.
    """
    assert normalize_id_short(raw) == expected


def test_a_real_corpus_document_name_keeps_its_german_word():
    """The language *word* is identity, the language *tag* is not — after folding."""
    assert normalize_id_short("Document01_HandbücherDE") == "document 01 handbuecher de"
    assert id_short_base("Document01_HandbücherDE") == "document 01 handbuecher"


# -- digits: which are identity, which are enumeration --------------------------


def test_an_enumeration_bases_the_same_whether_it_is_zero_padded_or_not():
    """``Document7`` and ``Document07`` are one name, whatever the padding.

    A rule that stripped only zero-padded numbers based them differently, which
    defeats the grouping the property exists for.
    """
    assert id_short_base("Document7") == id_short_base("Document07") == "document"


def test_an_enumeration_that_is_not_at_the_tail_is_kept():
    """The stated cost of the tail-only rule, pinned so that changing it is deliberate.

    ``Document05_HandbuecherDE`` and ``Document29_HandbuecherDE`` are one document type
    counted twice, and the tail-only rule does not join them: the ``05`` is followed by
    a word, and nothing at that position distinguishes it from the ``4`` in Festo's
    ``CPX-AP-I-4IOL-M12``, which is identity. Both strings are real Festo idShorts.
    """
    assert id_short_base("Document05_HandbücherDE") == "document 05 handbuecher"
    assert id_short_base("Document29_HandbücherDE") == "document 29 handbuecher"
    assert id_short_base("CPX-AP-I-4IOL-M12") == "cpx ap i 4 iol m 12"


def test_the_tail_is_stripped_again_after_a_leading_coded_name_goes():
    """``aaq 680 additional information 02``: both ends are variant, neither is the name.

    The head strip runs after the tail strip, so a name whose enumeration only becomes
    trailing once the coded name is gone needs the tail pass to run a second time.
    """
    assert id_short_base("AAQ680-AdditionalInformation02") == "additional information"
    assert id_short_base("AAQ672-Connector__00__") == "connector"


@pytest.mark.parametrize("raw,expected_base", [
    ("Rating_IP65", "rating ip 65"),        # a degree of protection, not a counter
    ("Connector_M12", "connector m 12"),    # a thread size
    ("Sensor_PT100", "sensor pt 100"),
    ("Bus_RS485", "bus rs 485"),
    ("CleanroomClassToISO14644", "cleanroom class to iso 14644"),  # a standard, not a counter
    ("Document07", "document"),             # a counter
    ("Contact_2", "contact"),
])
def test_digit_significant_prefixes_survive_the_base(raw, expected_base):
    assert id_short_base(raw) == expected_base


# -- properties every caller relies on ------------------------------------------


@pytest.mark.parametrize("raw", [r for r, _, _ in GOLDEN])
def test_idempotent(raw):
    """Every comparison normalises both sides, so a normalised input must be a fixpoint."""
    norm = normalize_id_short(raw)
    base = id_short_base(raw)
    assert normalize_id_short(norm) == norm
    assert id_short_base(base) == base
    assert id_short_base(norm) == base


@pytest.mark.parametrize("value", [None, "", "   ", "___", "--"])
def test_empty_is_none(value):
    assert normalize_id_short(value) is None
    assert id_short_base(value) is None


def test_base_never_empties_a_name():
    """All-enumeration input keeps its normalised form rather than becoming nothing."""
    assert id_short_base("01") == "01"
    assert id_short_base("__00__") == "00"


# -- the key is untouched -------------------------------------------------------


def test_shape_exposes_both_forms_without_changing_its_key():
    """The derived forms are *beside* ``id_short``; the key stays a hash of the verbatim tuple."""
    shape = Shape.of({"idShort": "AAQ672-Connector__00__", "modelType": "SubmodelElementCollection"})
    assert shape.id_short == "AAQ672-Connector__00__"
    assert shape.id_short_norm == "aaq 672 connector 00"
    assert shape.id_short_base == "connector"
    assert shape.key == shape_key("AAQ672-Connector__00__", None,
                                  "SubmodelElementCollection", None)


def test_two_spellings_stay_two_shapes():
    """Normalisation is for matching, never for merging: ``ValueID`` and ``ValueId`` differ."""
    one = Shape.of({"idShort": "ValueID", "modelType": "Property"})
    other = Shape.of({"idShort": "ValueId", "modelType": "Property"})
    assert one.key != other.key
    assert one.id_short_norm == other.id_short_norm


def test_none_id_short_derives_nothing():
    shape = Shape.of({"modelType": "Property"})
    assert shape.id_short_norm is None and shape.id_short_base is None


# -- against real corpus idShorts, not only the table ---------------------------


def test_idempotent_over_the_corpus(json_files, xml_files):
    from neo4aas.vocabulary.extract import extract_paths

    records = extract_paths(json_files, vendor="festo")
    records.merge(extract_paths(xml_files, vendor="sick"))
    seen = {rec.shape.id_short for rec in records.shapes.values() if rec.shape.id_short}
    assert len(seen) > 50, "corpus sample too small to prove anything"
    for raw in seen:
        norm = normalize_id_short(raw)
        base = id_short_base(raw)
        assert normalize_id_short(norm) == norm, raw
        assert id_short_base(base) == base, raw
        assert norm and base, raw
