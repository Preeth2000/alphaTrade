# tests/unit/test_model_sync.py
from alphaTrade.config import Settings


def test_settings_has_minio_defaults():
    s = Settings()
    assert s.minio.endpoint == "localhost:9000"
    assert s.minio.access_key == "minioadmin"
    assert s.minio.secret_key == "minioadmin"
    assert s.minio.bucket == "models"
    assert s.minio.secure is False


def test_settings_has_sync_defaults():
    s = Settings()
    assert s.model_sync.user == "default"
    assert s.model_sync.account == "default"
    assert s.model_sync.poll_interval == 60
    assert s.model_sync.max_versions == 5
    assert s.model_sync.enabled is True


def test_settings_has_validation_defaults():
    s = Settings()
    assert s.model_sync.validation.min_sharpe == 0.5
    assert s.model_sync.validation.max_drawdown == 0.20
    assert s.model_sync.validation.min_hit_rate == 0.45
