"""Integration tests for Neo4jObjectStore CRUD operations.

Covers add / commit (edit) / remove / discard for:
  - AssetAdministrationShell
  - Submodel (including submodel-element mutation)
  - SubmodelElement (via submodel commit after element change)

Requires a live Neo4j; skipped automatically when unreachable.
"""
import pytest

from basyx.aas import model
from basyx.aas.model import datatypes

from aas_mapping.aas_neo4j_adapter.neo_aas_object_store import Neo4jObjectStore

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get(ns, id_short: str):
    """Get element from NamespaceSet by id_short."""
    return ns.get("id_short", id_short)


def _make_aas(aas_id: str = "urn:aas/1", id_short: str = "AAS1") -> model.AssetAdministrationShell:
    return model.AssetAdministrationShell(
        id_=aas_id,
        id_short=id_short,
        asset_information=model.AssetInformation(
            asset_kind=model.AssetKind.INSTANCE,
            global_asset_id=f"urn:asset/{id_short}",
        ),
    )


def _make_submodel(sm_id: str = "urn:sm/1", id_short: str = "SM1") -> model.Submodel:
    sm = model.Submodel(id_=sm_id, id_short=id_short)
    sm.submodel_element.add(
        model.Property(id_short="Prop1", value_type=datatypes.String, value="initial")
    )
    return sm


def _make_store(aas_client) -> Neo4jObjectStore:
    return Neo4jObjectStore(aas_client)


# ---------------------------------------------------------------------------
# AAS CRUD
# ---------------------------------------------------------------------------

class TestAasCrud:
    def test_add_aas(self, aas_client):
        store = _make_store(aas_client)
        aas = _make_aas()
        store.add(aas)

        fetched = store.get_identifiable("urn:aas/1")
        assert fetched.id == "urn:aas/1"
        assert fetched.id_short == "AAS1"
        assert len(store) == 1

    def test_add_duplicate_aas_raises(self, aas_client):
        store = _make_store(aas_client)
        store.add(_make_aas())
        with pytest.raises(KeyError):
            store.add(_make_aas())

    def test_add_aas_reflected_in_contains(self, aas_client):
        store = _make_store(aas_client)
        aas = _make_aas()
        assert aas.id not in store
        store.add(aas)
        assert aas.id in store

    def test_commit_aas_updates_id_short(self, aas_client):
        store = _make_store(aas_client)
        aas = _make_aas()
        store.add(aas)

        aas.id_short = "AAS1_renamed"
        store.commit(aas)

        fetched = store.get_identifiable("urn:aas/1")
        assert fetched.id_short == "AAS1_renamed"

    def test_commit_aas_updates_description(self, aas_client):
        store = _make_store(aas_client)
        aas = _make_aas()
        store.add(aas)

        aas.description = model.MultiLanguageTextType({"en": "A pump unit"})
        store.commit(aas)

        fetched = store.get_identifiable("urn:aas/1")
        assert fetched.description["en"] == "A pump unit"

    def test_commit_nonexistent_aas_raises(self, aas_client):
        store = _make_store(aas_client)
        aas = _make_aas()
        with pytest.raises(KeyError):
            store.commit(aas)

    def test_remove_aas(self, aas_client):
        store = _make_store(aas_client)
        aas = _make_aas()
        store.add(aas)
        assert len(store) == 1

        store.remove(aas)
        assert "urn:aas/1" not in store
        assert len(store) == 0

    def test_remove_nonexistent_aas_raises(self, aas_client):
        store = _make_store(aas_client)
        aas = _make_aas()
        with pytest.raises(KeyError):
            store.remove(aas)

    def test_discard_aas(self, aas_client):
        store = _make_store(aas_client)
        aas = _make_aas()
        store.add(aas)
        store.discard(aas)
        assert "urn:aas/1" not in store

    def test_discard_nonexistent_aas_is_silent(self, aas_client):
        store = _make_store(aas_client)
        aas = _make_aas()
        store.discard(aas)  # must not raise

    def test_aas_with_submodel_reference(self, aas_client):
        store = _make_store(aas_client)
        sm = _make_submodel()
        store.add(sm)

        aas = _make_aas()
        aas.submodel.add(model.ModelReference.from_referable(sm))
        store.add(aas)

        fetched = store.get_identifiable("urn:aas/1")
        refs = list(fetched.submodel)
        assert len(refs) == 1
        assert refs[0].key[0].value == "urn:sm/1"

    def test_remove_aas_does_not_delete_submodel(self, aas_client):
        store = _make_store(aas_client)
        sm = _make_submodel()
        store.add(sm)

        aas = _make_aas()
        aas.submodel.add(model.ModelReference.from_referable(sm))
        store.add(aas)
        store.remove(aas)

        assert "urn:sm/1" in store
        assert "urn:aas/1" not in store


# ---------------------------------------------------------------------------
# Submodel CRUD
# ---------------------------------------------------------------------------

class TestSubmodelCrud:
    def test_add_submodel(self, aas_client):
        store = _make_store(aas_client)
        sm = _make_submodel()
        store.add(sm)

        fetched = store.get_identifiable("urn:sm/1")
        assert fetched.id == "urn:sm/1"
        assert fetched.id_short == "SM1"

    def test_add_submodel_preserves_elements(self, aas_client):
        store = _make_store(aas_client)
        sm = _make_submodel()
        store.add(sm)

        fetched = store.get_identifiable("urn:sm/1")
        prop = _get(fetched.submodel_element, "Prop1")
        assert prop is not None
        assert prop.value == "initial"

    def test_commit_submodel_updates_element_value(self, aas_client):
        store = _make_store(aas_client)
        sm = _make_submodel()
        store.add(sm)

        _get(sm.submodel_element, "Prop1").value = "updated"
        store.commit(sm)

        fetched = store.get_identifiable("urn:sm/1")
        assert _get(fetched.submodel_element, "Prop1").value == "updated"

    def test_commit_submodel_adds_new_element(self, aas_client):
        store = _make_store(aas_client)
        sm = _make_submodel()
        store.add(sm)

        sm.submodel_element.add(
            model.Property(id_short="Prop2", value_type=datatypes.Int, value=42)
        )
        store.commit(sm)

        fetched = store.get_identifiable("urn:sm/1")
        assert _get(fetched.submodel_element, "Prop1") is not None
        assert _get(fetched.submodel_element, "Prop2") is not None
        assert _get(fetched.submodel_element, "Prop2").value == 42

    def test_commit_submodel_removes_element(self, aas_client):
        store = _make_store(aas_client)
        sm = _make_submodel()
        store.add(sm)

        sm.submodel_element.remove(_get(sm.submodel_element, "Prop1"))
        store.commit(sm)

        fetched = store.get_identifiable("urn:sm/1")
        assert len(list(fetched.submodel_element)) == 0

    def test_remove_submodel(self, aas_client):
        store = _make_store(aas_client)
        sm = _make_submodel()
        store.add(sm)
        store.remove(sm)
        assert "urn:sm/1" not in store

    def test_discard_submodel(self, aas_client):
        store = _make_store(aas_client)
        sm = _make_submodel()
        store.add(sm)
        store.discard(sm)
        assert "urn:sm/1" not in store

    def test_len_decrements_on_remove(self, aas_client):
        store = _make_store(aas_client)
        store.add(_make_submodel("urn:sm/1", "SM1"))
        store.add(_make_submodel("urn:sm/2", "SM2"))
        assert len(store) == 2
        store.remove(store.get_identifiable("urn:sm/1"))
        assert len(store) == 1


# ---------------------------------------------------------------------------
# SubmodelElement CRUD (via submodel commit)
# ---------------------------------------------------------------------------

class TestSubmodelElementCrud:
    def test_add_collection_with_nested_property(self, aas_client):
        store = _make_store(aas_client)
        sm = model.Submodel(id_="urn:sm/1", id_short="SM1")
        sec = model.SubmodelElementCollection(id_short="Section")
        sec.value.add(model.Property(id_short="Inner", value_type=datatypes.Float, value=3.14))
        sm.submodel_element.add(sec)
        store.add(sm)

        fetched = store.get_identifiable("urn:sm/1")
        inner = _get(_get(fetched.submodel_element, "Section").value, "Inner")
        assert abs(inner.value - 3.14) < 1e-6

    def test_edit_nested_property_value(self, aas_client):
        store = _make_store(aas_client)
        sm = model.Submodel(id_="urn:sm/1", id_short="SM1")
        sec = model.SubmodelElementCollection(id_short="Section")
        sec.value.add(model.Property(id_short="Inner", value_type=datatypes.Float, value=1.0))
        sm.submodel_element.add(sec)
        store.add(sm)

        _get(_get(sm.submodel_element, "Section").value, "Inner").value = 99.9
        store.commit(sm)

        fetched = store.get_identifiable("urn:sm/1")
        assert abs(_get(_get(fetched.submodel_element, "Section").value, "Inner").value - 99.9) < 1e-6

    def test_add_element_to_existing_collection(self, aas_client):
        store = _make_store(aas_client)
        sm = model.Submodel(id_="urn:sm/1", id_short="SM1")
        sm.submodel_element.add(model.SubmodelElementCollection(id_short="Section"))
        store.add(sm)

        _get(sm.submodel_element, "Section").value.add(
            model.Property(id_short="New", value_type=datatypes.String, value="added")
        )
        store.commit(sm)

        fetched = store.get_identifiable("urn:sm/1")
        assert _get(_get(fetched.submodel_element, "Section").value, "New").value == "added"

    def test_remove_element_from_collection(self, aas_client):
        store = _make_store(aas_client)
        sm = model.Submodel(id_="urn:sm/1", id_short="SM1")
        sec = model.SubmodelElementCollection(id_short="Section")
        sec.value.add(model.Property(id_short="ToRemove", value_type=datatypes.String, value="x"))
        sec.value.add(model.Property(id_short="ToKeep", value_type=datatypes.String, value="y"))
        sm.submodel_element.add(sec)
        store.add(sm)

        _get(_get(sm.submodel_element, "Section").value, "ToRemove").parent = None
        _get(sm.submodel_element, "Section").value.discard(
            _get(sm.submodel_element, "Section").value.get("id_short", "ToRemove")
        )
        store.commit(sm)

        fetched = store.get_identifiable("urn:sm/1")
        section_ids = {se.id_short for se in _get(fetched.submodel_element, "Section").value}
        assert "ToKeep" in section_ids
        assert "ToRemove" not in section_ids

    def test_change_element_value_type(self, aas_client):
        """Commit replaces entire submodel, so value-type change must round-trip."""
        store = _make_store(aas_client)
        sm = model.Submodel(id_="urn:sm/1", id_short="SM1")
        sm.submodel_element.add(
            model.Property(id_short="P", value_type=datatypes.String, value="text")
        )
        store.add(sm)

        sm.submodel_element.remove(_get(sm.submodel_element, "P"))
        sm.submodel_element.add(
            model.Property(id_short="P", value_type=datatypes.Int, value=7)
        )
        store.commit(sm)

        fetched = store.get_identifiable("urn:sm/1")
        prop = _get(fetched.submodel_element, "P")
        assert prop.value_type is datatypes.Int
        assert prop.value == 7

    def test_multi_language_property_round_trip(self, aas_client):
        store = _make_store(aas_client)
        sm = model.Submodel(id_="urn:sm/1", id_short="SM1")
        mlp = model.MultiLanguageProperty(
            id_short="Label",
            value=model.MultiLanguageTextType({"en": "Hello", "de": "Hallo"}),
        )
        sm.submodel_element.add(mlp)
        store.add(sm)

        fetched = store.get_identifiable("urn:sm/1")
        label = _get(fetched.submodel_element, "Label")
        assert label.value["en"] == "Hello"
        assert label.value["de"] == "Hallo"

    def test_edit_multi_language_property(self, aas_client):
        store = _make_store(aas_client)
        sm = model.Submodel(id_="urn:sm/1", id_short="SM1")
        sm.submodel_element.add(
            model.MultiLanguageProperty(
                id_short="Label",
                value=model.MultiLanguageTextType({"en": "Old"}),
            )
        )
        store.add(sm)

        _get(sm.submodel_element, "Label").value = model.MultiLanguageTextType(
            {"en": "New", "fr": "Nouveau"}
        )
        store.commit(sm)

        fetched = store.get_identifiable("urn:sm/1")
        label = _get(fetched.submodel_element, "Label")
        assert label.value["en"] == "New"
        assert label.value["fr"] == "Nouveau"

    def test_submodel_element_list_order_preserved(self, aas_client):
        """SubmodelElementList order must survive add→commit round-trip."""
        store = _make_store(aas_client)
        sm = model.Submodel(id_="urn:sm/1", id_short="SM1")
        sml = model.SubmodelElementList(
            id_short="List",
            type_value_list_element=model.Property,
            value_type_list_element=datatypes.String,
        )
        # Items in SubmodelElementList must NOT have an id_short (AASd-120).
        for val in ["alpha", "beta", "gamma"]:
            sml.value.append(
                model.Property(id_short=None, value_type=datatypes.String, value=val)
            )
        sm.submodel_element.add(sml)
        store.add(sm)

        fetched = store.get_identifiable("urn:sm/1")
        values = [el.value for el in _get(fetched.submodel_element, "List").value]
        assert values == ["alpha", "beta", "gamma"]
