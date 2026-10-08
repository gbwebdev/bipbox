"""Server settings and their guard rails.

The validators matter because these values are set by environment variable in a
compose file, where a typo is silent: `browser_voip_mode: wiregaurd` would
otherwise mean "no browser voice" with no indication why.
"""

import pytest
from app.config import Settings


def test_defaults_are_development_safe():
    s = Settings(_env_file=None)
    assert s.protocol_version == 1
    assert s.min_protocol_version == 1
    assert s.browser_voip_mode == "wireguard"
    assert s.wg_subnet == "100.64.42.0/24"


def test_wg_subnet_default_avoids_the_usual_collisions():
    """Not 10/8 (Guillaume's network), not 172.16/12 (Docker), not 192.168 (clients)."""
    subnet = Settings(_env_file=None).wg_subnet
    assert subnet.startswith("100.64."), subnet


def test_secrets_are_generated_when_unset():
    a = Settings(_env_file=None)
    b = Settings(_env_file=None)
    assert a.session_secret and b.session_secret
    assert a.session_secret != b.session_secret
    assert a.device_secret_pepper != b.device_secret_pepper


def test_unknown_browser_voip_mode_is_rejected():
    with pytest.raises(ValueError, match="browser_voip_mode"):
        Settings(_env_file=None, browser_voip_mode="wiregaurd")


@pytest.mark.parametrize("mode", ["off", "wireguard", "plain_rtp"])
def test_the_three_documented_modes_are_accepted(mode):
    assert Settings(_env_file=None, browser_voip_mode=mode).browser_voip_mode == mode


def test_unknown_environment_is_rejected():
    with pytest.raises(ValueError, match="environment"):
        Settings(_env_file=None, environment="staging")


def test_monitoring_is_disabled_without_a_token():
    assert Settings(_env_file=None).monitoring_enabled is False
    assert Settings(_env_file=None, monitor_token="t").monitoring_enabled is True


def test_development_raises_no_production_warnings():
    s = Settings(_env_file=None, environment="development")
    assert s.production_warnings() == []


def test_production_warns_about_generated_secrets(monkeypatch):
    monkeypatch.delenv("BIPBOX_SESSION_SECRET", raising=False)
    monkeypatch.delenv("BIPBOX_DEVICE_SECRET_PEPPER", raising=False)
    s = Settings(_env_file=None, environment="production")

    warnings = " ".join(s.production_warnings())

    assert "session_secret" in warnings
    assert "device_secret_pepper" in warnings
    assert "monitor_token" in warnings


def test_production_is_quiet_once_the_secrets_are_set(monkeypatch):
    monkeypatch.setenv("BIPBOX_SESSION_SECRET", "x" * 32)
    monkeypatch.setenv("BIPBOX_DEVICE_SECRET_PEPPER", "y" * 32)
    s = Settings(_env_file=None, environment="production", monitor_token="z")

    assert s.production_warnings() == []


def test_production_warnings_are_returned_not_raised(monkeypatch):
    """A server that refuses to boot over a missing monitor token is worse
    than one that says so loudly."""
    monkeypatch.delenv("BIPBOX_SESSION_SECRET", raising=False)
    s = Settings(_env_file=None, environment="production")
    assert isinstance(s.production_warnings(), list)
