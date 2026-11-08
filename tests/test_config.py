import os
from unittest.mock import patch

from core.config import Settings


def test_fhir_settings_defaults():
    # Pass empty _env_file or clean env to verify defaults
    with patch.dict(os.environ, {}, clear=True):
        settings = Settings(_env_file=None)
        assert settings.FHIR_BASE_URL == ""
        assert settings.FHIR_POLL_SEC == 120
        assert settings.FHIR_ACCESSION_SYSTEM == ""
        assert settings.FHIR_PATIENT_ID_SYSTEM == "urn:oid:2.16.840.1.113883.4.3.25"


def test_fhir_settings_env_override():
    custom_env = {
        "GOSMART_GS_FHIR_BASE_URL": "https://fhir.example.com/r4",
        "GOSMART_GS_FHIR_POLL_SEC": "300",
        "GOSMART_GS_FHIR_ACCESSION_SYSTEM": "http://hospital.org/accession",
        "GOSMART_GS_FHIR_PATIENT_ID_SYSTEM": "http://hospital.org/mrn",
    }
    with patch.dict(os.environ, custom_env, clear=True):
        settings = Settings(_env_file=None)
        assert settings.FHIR_BASE_URL == "https://fhir.example.com/r4"
        assert settings.FHIR_POLL_SEC == 300
        assert settings.FHIR_ACCESSION_SYSTEM == "http://hospital.org/accession"
        assert settings.FHIR_PATIENT_ID_SYSTEM == "http://hospital.org/mrn"


def test_vector_embed_settings_defaults():
    with patch.dict(os.environ, {}, clear=True):
        settings = Settings(_env_file=None)
        assert settings.VECTOR_EMBED_PROVIDER == ""
        assert settings.VECTOR_EMBED_MODEL == ""
        assert settings.VECTOR_EMBED_SERVER_URL == ""
        assert settings.VECTOR_EMBED_DIMENSIONS is None
        assert settings.VERTEX_AI_API_KEY is None
        assert settings.VERTEX_AI_LOCATION == "us-central1"


def test_vector_embed_settings_env_override():
    custom_env = {
        "GOSMART_GS_VECTOR_EMBED_PROVIDER": "ollama",
        "GOSMART_GS_VECTOR_EMBED_MODEL": "nomic-embed-text:latest",
        "GOSMART_GS_VECTOR_EMBED_SERVER_URL": "http://10.200.0.103:11434",
        "GOSMART_GS_VECTOR_EMBED_DIMENSIONS": "768",
        "GOSMART_GS_VERTEX_AI_API_KEY": "AIzaSyTestKey123",
        "GOSMART_GS_VERTEX_AI_LOCATION": "us-east4",
    }
    with patch.dict(os.environ, custom_env, clear=True):
        settings = Settings(_env_file=None)
        assert settings.VECTOR_EMBED_PROVIDER == "ollama"
        assert settings.VECTOR_EMBED_MODEL == "nomic-embed-text:latest"
        assert settings.VECTOR_EMBED_SERVER_URL == "http://10.200.0.103:11434"
        assert settings.VECTOR_EMBED_DIMENSIONS == 768
        assert settings.VERTEX_AI_API_KEY is not None
        assert settings.VERTEX_AI_API_KEY.get_secret_value() == "AIzaSyTestKey123"
        assert settings.VERTEX_AI_LOCATION == "us-east4"
