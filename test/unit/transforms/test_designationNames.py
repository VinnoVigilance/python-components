import pytest

from transforms.preNormalization import DesignationNamesHandler


pytestmark = pytest.mark.unit


def test_shared_amendment_date_and_former_name():
    result = DesignationNamesHandler().normalize(
        "ISIS (formerly al-Qa’ida in Iraq)"
        "— Islamic State of Iraq Amendment (January 26, 2012)"
        "— al-Hayat Media Center and Amaq News Agency Amendments (March 22, 2019)", ""
    )
    assert result["primary_name"] == "ISIS"
    assert [(a["type"], a["value"]) for a in result["aliases"]] == [
        ("Former Name", "al-Qa’ida in Iraq"),
        ("AKA", "Islamic State of Iraq"),
        ("AKA", "al-Hayat Media Center"),
        ("AKA", "Amaq News Agency"),
    ]
    assert result["aliases"][0]["note"] == ""
    assert "January 26, 2012" in result["aliases"][1]["note"]
    assert result["aliases"][2]["note"] == result["aliases"][3]["note"]
    assert "March 22, 2019" in result["aliases"][3]["note"]


@pytest.mark.parametrize("text,primary,aliases", [
    ("Lashkar-e-Tayyiba (LeT)", "Lashkar-e-Tayyiba", [("AKA", "LeT")]),
    ("al-Mulathamun Battalion, aka al-Murabitoun", "al-Mulathamun Battalion", [("AKA", "al-Murabitoun")]),
    ("Ansar al-Shari’a in Benghazi", "Ansar al-Shari’a in Benghazi", []),
    ("Revolutionary Armed Forces of Colombia – People’s Army (FARC-EP)",
     "Revolutionary Armed Forces of Colombia – People’s Army", [("AKA", "FARC-EP")]),
    ("Shining Path (Sendero Luminoso, SL)", "Shining Path", [("AKA", "Sendero Luminoso"), ("AKA", "SL")]),
])
def test_base_names(text, primary, aliases):
    result = DesignationNamesHandler().normalize(text, "")
    assert result["primary_name"] == primary
    assert [(a["type"], a["value"]) for a in result["aliases"]] == aliases


def test_same_name_amendment_keeps_date_without_duplicate_alias():
    result = DesignationNamesHandler().normalize(
        "Jaysh al-Adl (formerly Jundallah)— Jaysh al-Adl Amendment (July 2, 2019)", ""
    )
    assert len(result["aliases"]) == 1
    assert "July 2, 2019" in result["primary_note"]


def test_unrecognized_amendment_is_rejected():
    with pytest.raises(ValueError, match="Unsupported designation amendment"):
        DesignationNamesHandler().normalize("Group— Something changed", "")
