"""Production refuses to start in any configuration it cannot keep its promises in."""

from __future__ import annotations

import pytest

from histor.config import load_settings

PROD = {
    "HISTOR_PROFILE": "prod",
    "HISTOR_OPERATOR_TOKEN": "x" * 32,
    "HISTOR_PUBLIC_BASE": "https://histor.example",
    "HISTOR_DATABASE_URL": "postgresql://h:p@db:5432/histor",
}


def set_env(monkeypatch, **env):
    for key in ("HISTOR_PROFILE", "HISTOR_OPERATOR_TOKEN", "HISTOR_PUBLIC_BASE", "HISTOR_DATABASE_URL",
                "HISTOR_ALLOW_PRIVATE_TARGETS"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)


def test_a_complete_production_config_loads(monkeypatch):
    set_env(monkeypatch, **PROD)
    assert load_settings().is_prod


@pytest.mark.parametrize("override,message", [
    ({"HISTOR_DATABASE_URL": ""}, "postgresql"),
    ({"HISTOR_DATABASE_URL": "sqlite:///x"}, "postgresql"),
    ({"HISTOR_OPERATOR_TOKEN": "short"}, "OPERATOR_TOKEN"),
    ({"HISTOR_PUBLIC_BASE": "http://histor.example"}, "https"),
    ({"HISTOR_ALLOW_PRIVATE_TARGETS": "1"}, "PRIVATE"),
])
def test_production_fails_closed(monkeypatch, override, message):
    set_env(monkeypatch, **{**PROD, **override})
    with pytest.raises(RuntimeError, match=message):
        load_settings()


def test_development_defaults_to_sqlite(monkeypatch):
    set_env(monkeypatch)
    settings = load_settings()
    assert settings.profile == "dev" and settings.database_url == ""


def test_sitemap_extra_takes_paths_only(monkeypatch):
    set_env(monkeypatch, HISTOR_SITEMAP_EXTRA=" /datasets/ , https://elsewhere.example/x,,/about")
    assert load_settings().sitemap_extra == ("/datasets/", "/about")
    monkeypatch.delenv("HISTOR_SITEMAP_EXTRA")
    assert load_settings().sitemap_extra == ()


def test_operator_can_pin_sandbox_generation_during_a_staged_rollout(monkeypatch):
    set_env(monkeypatch, HISTOR_SANDBOX_OBSERVER="4", HISTOR_SANDBOX_OBSERVERS_VALID="3")
    settings = load_settings()
    assert settings.sandbox_observer == "4" and settings.sandbox_observers_valid == ("3",)
