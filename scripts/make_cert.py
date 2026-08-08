"""Generate a self-signed TLS cert/key for the Chotu console (:8888).

Safari refuses getUserMedia on a plain http://192.168.x.x origin, and the
localhost secure-origin exemption only applies on the machine itself. This
script writes a self-signed certificate whose SubjectAlternativeName covers
localhost, 127.0.0.1, and the machine's real LAN IPv4 (discovered at
runtime, never hardcoded), so an iPhone on the same wifi will accept it
after a manual "trust this certificate" tap.

Usage:
    python scripts/make_cert.py
    python scripts/make_cert.py --host 10.0.0.5 --force
"""

import argparse
import datetime
import ipaddress
import pathlib
import socket

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

CERTS_DIR = pathlib.Path(__file__).resolve().parents[1] / "certs"
CERT_PATH = CERTS_DIR / "chotu.pem"
KEY_PATH = CERTS_DIR / "chotu-key.pem"


def discover_lan_ip() -> str:
    """The machine's real LAN IPv4, found by opening a UDP socket to 8.8.8.8
    and reading getsockname()[0]. No packet is actually sent (UDP connect is
    local-only routing resolution), and the address is not hardcoded because
    it changes across networks."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def build_san(hosts):
    names = []
    for h in hosts:
        try:
            names.append(x509.IPAddress(ipaddress.ip_address(h)))
        except ValueError:
            names.append(x509.DNSName(h))
    return x509.SubjectAlternativeName(names)


def generate(hosts, force=False):
    if CERT_PATH.exists() or KEY_PATH.exists():
        if not force:
            raise SystemExit(
                f"{CERT_PATH} or {KEY_PATH} already exists. Pass --force to overwrite."
            )

    CERTS_DIR.mkdir(parents=True, exist_ok=True)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "chotu-console"),
    ])

    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=825))
        .add_extension(build_san(hosts), critical=False)
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None), critical=True
        )
        .sign(key, hashes.SHA256())
    )

    KEY_PATH.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    CERT_PATH.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    return cert


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--host",
        action="append",
        dest="hosts",
        default=None,
        help="Repeatable. Defaults to localhost, 127.0.0.1, and the discovered LAN IPv4.",
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    hosts = args.hosts
    if hosts is None:
        lan_ip = discover_lan_ip()
        hosts = ["localhost", "127.0.0.1", lan_ip]
    else:
        lan_ip = None
        for h in hosts:
            try:
                ip = ipaddress.ip_address(h)
                if ip.is_private:
                    lan_ip = h
            except ValueError:
                pass

    generate(hosts, force=args.force)

    print(f"Wrote {CERT_PATH} and {KEY_PATH}")
    print(f"SAN hosts: {', '.join(hosts)}")
    if lan_ip:
        print(f"Open on the phone: https://{lan_ip}:8888/")
    else:
        print("No LAN IPv4 found among --host values; phone check will not work.")


if __name__ == "__main__":
    main()
