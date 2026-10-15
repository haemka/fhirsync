"""Sample resources for the tests, a fresh copy for every test (tests may change them)."""
import json
import os

import pytest

DATA = os.path.join(os.path.dirname(__file__), "data")


def load(name):
    with open(os.path.join(DATA, f"{name}.json"), encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def gics_consent():
    """consent-status Consent from gICS 2026.1.1, patient.display 'Pseudonym 1000000001'."""
    return load("gics_consent")


@pytest.fixture
def gics_research_study():
    """ResearchStudy of the gICS domain MII, identifier https://ths-greifswald.de/fhir/gics/|MII."""
    return load("gics_research_study")


@pytest.fixture
def gics_patient():
    """gICS Patient, only identifier: Pseudonym 1000000001."""
    return load("gics_patient")


@pytest.fixture
def patient():
    """Hospital Patient p-4711, hospital number (MRN) 0012345, female, born 1980-05-17."""
    return load("patient")


@pytest.fixture
def observation():
    """Hemoglobin Observation o-1 (LOINC 718-7) of Patient/p-4711, value 13.2 g/dL, no meta."""
    return load("observation")
