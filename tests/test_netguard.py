"""The crawler never dials a private address, and connects to the address it checked."""

from __future__ import annotations

import socket

import pytest

from histor.netguard import BlockedAddress, resolve_public


def resolver(*addresses):
    def fake(host, port, type=0):  # noqa: A002 - mirrors socket.getaddrinfo
        return [(socket.AF_INET6 if ":" in a else socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port)) for a in addresses]
    return fake


@pytest.mark.parametrize("address", ["127.0.0.1", "10.1.2.3", "172.18.0.1", "192.168.1.1", "169.254.169.254",
                                     "::1", "fe80::1", "::ffff:127.0.0.1", "100.64.0.1", "0.0.0.0"])
def test_private_and_special_addresses_are_refused(address):
    with pytest.raises(BlockedAddress):
        resolve_public("https://evil.example/mcp", resolver=resolver(address))


def test_one_private_answer_in_a_round_robin_is_enough_to_refuse():
    with pytest.raises(BlockedAddress):
        resolve_public("https://rr.example/mcp", resolver=resolver("93.184.216.34", "10.0.0.5"))


def test_a_public_name_is_pinned_with_host_and_sni():
    pinned = resolve_public("https://api.example.com:8443/v1/mcp?x=1", resolver=resolver("93.184.216.34"))
    assert pinned.url == "https://93.184.216.34:8443/v1/mcp?x=1"
    assert pinned.host_header == "api.example.com:8443"
    assert pinned.sni_hostname == "api.example.com"


def test_ipv4_is_preferred_and_ipv6_is_bracketed():
    assert resolve_public("https://x.example/", resolver=resolver("2606:4700::1", "93.184.216.34")).address == "93.184.216.34"
    pinned = resolve_public("https://x.example/", resolver=resolver("2606:4700::1"))
    assert pinned.url == "https://[2606:4700::1]/"


@pytest.mark.parametrize("url", ["http://x.example/mcp", "ftp://x.example/", "https://user:pw@x.example/", "https:///nohost"])
def test_unsafe_urls_are_refused(url):
    with pytest.raises(BlockedAddress):
        resolve_public(url, resolver=resolver("93.184.216.34"))


def test_private_targets_only_when_explicitly_allowed():
    assert resolve_public("http://127.0.0.1:9/x", allow_private=True).address == "127.0.0.1"
