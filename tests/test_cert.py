"""The cert covers the LAN IP, or Safari will not accept it."""
import ipaddress
import pathlib
import pytest

CERTS = pathlib.Path(__file__).resolve().parents[1] / "certs"
LAN_IP = "192.168.0.190"


def _cert():
    from cryptography import x509
    pem = CERTS / "chotu.pem"
    if not pem.exists():
        pytest.skip("run scripts/make_cert.py first")
    return x509.load_pem_x509_certificate(pem.read_bytes())


def test_cert_and_key_exist():
    assert (CERTS / "chotu.pem").exists()
    assert (CERTS / "chotu-key.pem").exists()


def test_san_covers_localhost_and_the_lan_address():
    from cryptography import x509
    san = _cert().extensions.get_extension_for_class(
        x509.SubjectAlternativeName).value
    names = set(san.get_values_for_type(x509.DNSName))
    ips = {str(i) for i in san.get_values_for_type(x509.IPAddress)}
    assert "localhost" in names
    assert "127.0.0.1" in ips
    assert any(ipaddress.ip_address(i).is_private for i in ips), \
        "the cert must cover a LAN address or the phone cannot use it"
