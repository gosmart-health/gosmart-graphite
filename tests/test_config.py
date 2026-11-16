import os
from unittest.mock import patch

from gosmart_graphite.core.config import Settings


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


def test_llm_settings_legacy_vars_removed_and_encryption_key_default():
    with patch.dict(os.environ, {}, clear=True):
        settings = Settings(_env_file=None)
        assert not hasattr(settings, "VECTOR_EMBED_PROVIDER")
        assert not hasattr(settings, "VECTOR_EMBED_MODEL")
        assert not hasattr(settings, "VECTOR_EMBED_SERVER_URL")
        assert not hasattr(settings, "VECTOR_EMBED_DIMENSIONS")
        assert not hasattr(settings, "VERTEX_AI_API_KEY")
        assert not hasattr(settings, "VERTEX_AI_LOCATION")
        assert settings.LLM_CONFIG_ENCRYPTION_KEY is None


def test_llm_config_encryption_key_env_override():
    custom_env = {
        "GOSMART_GS_LLM_CONFIG_ENCRYPTION_KEY": "test-passphrase-for-llm-keys",
    }
    with patch.dict(os.environ, custom_env, clear=True):
        settings = Settings(_env_file=None)
        assert settings.LLM_CONFIG_ENCRYPTION_KEY is not None
        assert settings.LLM_CONFIG_ENCRYPTION_KEY.get_secret_value() == "test-passphrase-for-llm-keys"
