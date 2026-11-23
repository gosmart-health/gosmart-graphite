import os
import sys
from unittest.mock import patch

from gosmart_graphite.core.config import _ROOT_ENV_FILE, _TESTS_ENV_FILE, Settings, _resolve_env_files


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


def test_resolve_env_files_pytest_order(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    env_files = _resolve_env_files()
    assert env_files[0] == _ROOT_ENV_FILE
    assert env_files[1] == _TESTS_ENV_FILE


def test_resolve_env_files_excludes_tests_env_outside_pytest(monkeypatch, tmp_path):
    monkeypatch.chdir(_ROOT_ENV_FILE.parent)
    monkeypatch.delitem(sys.modules, "pytest", raising=False)
    env_files = _resolve_env_files()
    assert env_files == (_ROOT_ENV_FILE,)


def test_tests_env_overrides_root_env(tmp_path):
    root_env = tmp_path / ".env"
    tests_env = tmp_path / "tests.env"
    root_env.write_text("GOSMART_GS_APP_NAME=RootApp\nGOSMART_GS_PORT=9001\n", encoding="utf-8")
    tests_env.write_text("GOSMART_GS_PORT=9999\n", encoding="utf-8")

    with patch.dict(os.environ, {}, clear=True):
        settings = Settings(_env_file=(root_env, tests_env))
        assert settings.APP_NAME == "RootApp"
        assert settings.PORT == 9999


def test_dev_mode_and_db_connect_retry_defaults():
    with patch.dict(os.environ, {}, clear=True):
        settings = Settings(_env_file=None)
        assert settings.DEV_MODE is False
        assert settings.DB_CONNECT_RETRY_TIMEOUT_SEC == 600
        assert settings.DB_CONNECT_RETRY_INITIAL_DELAY_SEC == 2.0
        assert settings.DB_CONNECT_RETRY_MAX_DELAY_SEC == 30.0


def test_dev_mode_and_db_connect_retry_env_override():
    custom_env = {
        "GOSMART_GS_DEV_MODE": "true",
        "GOSMART_GS_DB_CONNECT_RETRY_TIMEOUT_SEC": "1200",
        "GOSMART_GS_DB_CONNECT_RETRY_INITIAL_DELAY_SEC": "1.5",
        "GOSMART_GS_DB_CONNECT_RETRY_MAX_DELAY_SEC": "45.0",
    }
    with patch.dict(os.environ, custom_env, clear=True):
        settings = Settings(_env_file=None)
        assert settings.DEV_MODE is True
        assert settings.DB_CONNECT_RETRY_TIMEOUT_SEC == 1200
        assert settings.DB_CONNECT_RETRY_INITIAL_DELAY_SEC == 1.5
        assert settings.DB_CONNECT_RETRY_MAX_DELAY_SEC == 45.0
