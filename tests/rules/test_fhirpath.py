import datetime
from decimal import Decimal

import pytest
from fhirpathpy import evaluate

from fhirsync.rules import FHIR_MODEL


def fhirpath(resource, expression):
    # evaluated like the rule engine does, with the FHIR R4 model
    return evaluate(resource, expression, {}, FHIR_MODEL)


# docs/rules.md 4.2, table "Navigating"
@pytest.mark.parametrize("expression, expected", [
    ("status", ["active"]),
    ("patient.display", ["Pseudonym 1000000001"]),
    ("organization[0].display", ["MII"]),
    ("organization.first().display", ["MII"]),
    ("policy.uri", ["2.16.840.1.113883.3.1937.777.24.2.184"]),
    ("category.coding.code", ["57016-8", "consent-status", "2.16.840.1.113883.3.1937.777.24.2.184"]),
    ("provision.provision.count()", [27]),
])
def test_navigating(gics_consent, expression, expected):
    assert fhirpath(gics_consent, expression) == expected


# docs/rules.md 4.2: an element that doesn't exist is an empty collection, not an error
def test_missing_element(gics_consent):
    assert fhirpath(gics_consent, "doesNotExist.at.all") == []


# docs/rules.md 4.2, table "Filtering with where"
def test_where_on_gics_patient(gics_patient):
    expression = "identifier.where(system = 'https://ths-greifswald.de/fhir/gics/identifiers/Pseudonym').value"
    assert fhirpath(gics_patient, expression) == ["1000000001"]


# docs/rules.md 4.2, table "Filtering with where": counting the permitted provisions
def test_where_on_consent(gics_consent):
    assert fhirpath(gics_consent, "provision.provision.where(type = 'permit').count()") == [15]


# docs/rules.md 4.2, table "Filtering with where": the LOINC code of an Observation
def test_where_on_observation(observation):
    assert fhirpath(observation, "code.coding.where(system = 'http://loinc.org').code.first()") == ["718-7"]


# docs/rules.md 4.2, table "Conditions"
@pytest.mark.parametrize("expression, expected", [
    ("status = 'active'", [True]),
    ("status != 'active'", [False]),
    ("status.exists()", [True]),
    ("status.exists().not()", [False]),
    ("extension.where(url = 'x').empty()", [True]),
    ("category.coding.where(system = 'http://loinc.org' and code = '57016-8').exists()", [True]),
    ("status = 'active' and policy.exists()", [True]),
    ("status = 'x' or policy.exists()", [True]),
    ("(status = 'x').not()", [True]),
])
def test_conditions(gics_consent, expression, expected):
    assert fhirpath(gics_consent, expression) == expected


# docs/rules.md 4.2, table "Conditions": in with a list of values
def test_in(patient):
    assert fhirpath(patient, "gender in ('female' | 'male')") == [True]


# docs/rules.md 4.2, table "Conditions": comparing numbers
def test_comparing_numbers(observation):
    assert fhirpath(observation, "valueQuantity.value > 10") == [True]


# docs/rules.md 4.2, table "Text functions"
@pytest.mark.parametrize("expression, expected", [
    ("patient.display.substring(10)", ["1000000001"]),
    ("patient.display.replace('Pseudonym ', '')", ["1000000001"]),
    ("patient.display.startsWith('Pseudonym')", [True]),
    ("patient.display.endsWith('0001')", [True]),
    ("patient.display.contains('Pseudo')", [True]),
    ("patient.display.matches('^Pseudonym [0-9]{10}$')", [True]),
    ("patient.display.split(' ')", ["Pseudonym", "1000000001"]),
])
def test_text_functions_on_consent(gics_consent, expression, expected):
    assert fhirpath(gics_consent, expression) == expected


# docs/rules.md 4.2, table "Text functions": the examples on a Patient
@pytest.mark.parametrize("expression, expected", [
    ("birthDate.toString().substring(0, 4)", ["1980"]),
    ("name.family.upper()", ["MUSTER"]),
    ("name.family.lower()", ["muster"]),
    ("' x '.trim()", ["x"]),
    ("name.family.length()", [6]),
])
def test_text_functions_on_patient(patient, expression, expected):
    assert fhirpath(patient, expression) == expected


# docs/rules.md 4.2, table "Text functions": the id part of a reference
def test_replace_in_reference(observation):
    assert fhirpath(observation, "subject.reference.replace('Patient/', '')") == ["p-4711"]


# docs/rules.md 4.2, table "Conversions and choice elements"
@pytest.mark.parametrize("expression, expected", [
    ("valueQuantity.value.toString()", ["13.2"]),
    ("value.ofType(Quantity).value", [Decimal("13.2")]),
    ("effective.ofType(dateTime)", ["2026-03-01T10:00:00+01:00"]),
    ("valueQuantity.value", [Decimal("13.2")]),
    ("effectiveDateTime", ["2026-03-01T10:00:00+01:00"]),
])
def test_conversions_and_choice_elements(observation, expression, expected):
    assert fhirpath(observation, expression) == expected


# docs/rules.md 4.2, table "Conversions and choice elements": toInteger() drops leading zeros
def test_to_integer(patient):
    # the hospital number is '0012345'
    assert fhirpath(patient, "identifier.value.toInteger()") == [12345]


# docs/rules.md 4.2, table "Conversions and choice elements": the date part of a dateTime
def test_date_part_of_a_datetime(gics_consent):
    # dateTime is '2026-10-14T21:56:04+02:00'
    assert fhirpath(gics_consent, "dateTime.toString().substring(0, 10)") == ["2026-10-14"]


# docs/rules.md 4.2, table "Conversions and choice elements": extensions by their url
def test_extension_by_url(gics_consent):
    expression = ("extension('http://fhir.de/ConsentManagement/StructureDefinition/DomainReference')"
                  ".extension('domain').value.reference")
    assert fhirpath(gics_consent, expression) == ["ResearchStudy/468b895a-c809-11f1-8d48-d6456abb6135"]


# docs/rules.md 4.2: iif on a Consent
def test_iif_on_consent(gics_consent):
    assert fhirpath(gics_consent, "iif(status = 'active', 'aktiv', 'inaktiv')") == ["aktiv"]


# docs/rules.md 4.2: iif on an Observation (also used in cookbook 7.11)
def test_iif_on_observation(observation):
    expression = "iif(code.coding.where(system = 'http://loinc.org').exists(), 'loinc-coded', 'local-code')"
    assert fhirpath(observation, expression) == ["loinc-coded"]


# docs/rules.md 4.2: & treats a missing value as empty text, + gives nothing (name.prefix doesn't exist)
def test_ampersand_and_plus(patient):
    assert fhirpath(patient, "name.family & '-' & name.prefix") == ["Muster-"]
    assert fhirpath(patient, "name.family + '-' + name.prefix") == []


# docs/rules.md 4.2: today() and now() at the time the rule runs
def test_today_and_now(patient):
    today = datetime.date.today().isoformat()

    assert fhirpath(patient, "today()") == [today]
    assert fhirpath(patient, "now()")[0].startswith(today)


# docs/rules.md 5: malformed expressions are read leniently, name..family like name.family
def test_double_dot_is_read_like_a_single_dot(patient):
    assert fhirpath(patient, "name..family") == ["Muster"]
