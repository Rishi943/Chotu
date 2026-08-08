"""Generate a CA + server TLS cert/key chain for the Chotu console (:8888).

Why two certificates?
---------------------
An operating system can only *anchor* trust in a certificate that is marked
as a CA (``BasicConstraints: critical, CA:TRUE``). A lone self-signed leaf
(``CA:FALSE``) can be installed a hundred times and the OS will still refuse
to trust it, because nothing in the chain is a trust anchor. That is exactly
the bug this script fixes: the old script emitted only a leaf, so iOS showed
the toggle in Certificate Trust Settings but the connection still failed with
ERR_CERT_AUTHORITY_INVALID. This whole change exists because of that rule.

So this script now produces a two-part chain in certs/:

  * chotu-ca.pem      local CA the phone installs and trusts (5 years).
  * chotu-ca-key.pem  the CA's private key. NEVER served by the web server;
                      kept so the CA can sign again later.
  * chotu.pem         the server certificate, signed by that CA (360 days).
  * chotu-key.pem     the server's private key, served alongside chotu.pem.

Safari refuses getUserMedia on a plain http://192.168.x.x origin, and the
localhost secure-origin exemption only applies on the machine itself. The
server cert's SubjectAlternativeName covers localhost, 127.0.0.1, and the
machine's real LAN IPv4 (discovered at runtime, never hardcoded), so an iPhone
on the same wifi will accept it after installing chotu-ca.pem and enabling it
in iOS "Certificate Trust Settings".

Usage:
    python scripts/make_cert.py
    python scripts/make_cert.py --host 10.0.0.5 --force
"""

import argparse
import datetime
import ipaddress
import os
import pathlib
import socket

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

CERTS_DIR = pathlib.Path(__file__).resolve().parents[1] / "certs"
CA_CERT_PATH = CERTS_DIR / "chotu-ca.pem"
CA_KEY_PATH = CERTS_DIR / "chotu-ca-key.pem"
CERT_PATH = CERTS_DIR / "chotu.pem"
KEY_PATH = CERTS_DIR / "chotu-key.pem"

ALL_PATHS = [CA_CERT_PATH, CA_KEY_PATH, CERT_PATH, KEY_PATH]



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


def _write_pem(path, data):
    """Atomically write a PEM blob: write to a temp name in the same dir, then
    os.replace over the real name so a reader never sees a torn file. All four
    files are (re)written from the same in-memory CA/key each run, so a server
    cert can never outlive the CA that signed it on disk."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _make_ca(now):
    """The local root CA. Self-signed, BasicConstraints(ca=True,
    path_length=0) marked critical, and KeyUsage key_cert_sign/crl_sign. Its
    private key is written to disk so the CA can sign a fresh server cert
    later without regenerating the root."""
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_subject = ca_issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "chotu-console local CA"),
    ])
    # 5 years. Apple's 398-day cap applies to *server* certificates, not to a
    # user-installed root, so a long-lived CA is fine (and convenient).
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_subject)
        .issuer_name(ca_issuer)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=5 * 365))
        .add_extension(
            x509.BasicConstraints(ca=True, path_length=0), critical=True
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                key_encipherment=False,
                content_commitment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    return ca_key, ca_cert


def _make_server_cert(hosts, ca_key, ca_cert, now):
    """The leaf server cert, signed by the CA key (not self-signed). Keeps the
    SAN list, BasicConstraints(ca=False), SERVER_AUTH EKU, KeyUsage
    digital_signature/key_encipherment, RSA 2048, SHA-256, 360-day life."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "chotu-console"),
    ])
    # 360 days, inside Apple's 398-day cap for server certificates. iOS closes
    # the connection outright (no override-able warning page) above that.
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=360))
        .add_extension(build_san(hosts), critical=False)
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None), critical=True
        )
        # Apple requires the id-kp-serverAuth OID in an ExtendedKeyUsage
        # extension on any server certificate; iOS closes the connection
        # outright (no override-able warning page) without it.
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=True,
                content_commitment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    return key, cert


def generate(hosts, force=False):
    if any(p.exists() for p in ALL_PATHS):
        if not force:
            raise SystemExit(
                f"{ALL_PATHS[0]} .. {ALL_PATHS[-1]} already exist. "
                "Pass --force to overwrite."
            )

    CERTS_DIR.mkdir(parents=True, exist_ok=True)

    now = datetime.datetime.now(datetime.timezone.utc)

    ca_key, ca_cert = _make_ca(now)
    server_key, server_cert = _make_server_cert(hosts, ca_key, ca_cert, now)

    _write_pem(CA_KEY_PATH, ca_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    _write_pem(CA_CERT_PATH, ca_cert.public_bytes(serialization.Encoding.PEM))
    _write_pem(KEY_PATH, server_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    _write_pem(CERT_PATH, server_cert.public_bytes(serialization.Encoding.PEM))

    return server_cert


def _load_cert(path):
    """Load an existing PEM X.509 certificate."""
    return x509.load_pem_x509_certificate(path.read_bytes())


def _load_key(path):
    """Load an existing PEM private key (CA or server)."""
    return serialization.load_pem_private_key(path.read_bytes(), password=None)


def _san_hosts(cert):
    """The hostnames / IPs currently listed in a certificate's SANs."""
    try:
        san = cert.extensions.get_extension_for_class(
            x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return []
    hosts = []
    for name in san:
        if isinstance(name, x509.DNSName):
            hosts.append(name.value)
        elif isinstance(name, x509.IPAddress):
            hosts.append(str(name.value))
    return hosts


def reissue_leaf(target=None):
    """Reissue ONLY the server leaf, reusing the existing CA. The CA is never
    regenerated here — it is the trust anchor installed on the phone, and
    regenerating it would silently break that trust. Returns the new SAN host
    list if a new leaf was written, or None if nothing needed to change.

    ``target`` is an optional list of host/IP strings to ensure are in the SANs
    (defaults to the currently discovered LAN IPv4). localhost and 127.0.0.1 are
    always ensured as well.
    """
    if not (CA_CERT_PATH.exists() and CA_KEY_PATH.exists()):
        raise SystemExit(
            f"REFUSING to reissue the leaf: CA files are missing\n"
            f"  {CA_CERT_PATH}\n  {CA_KEY_PATH}\n"
            "The CA is the trust anchor installed on the iPhone and can never be "
            "regenerated in place without breaking the phone's trust. Regenerate "
            "the whole chain with:\n"
            "    python scripts/make_cert.py --force\n"
            "then reinstall chotu-ca.pem on the phone and re-enable it in iOS "
            "Settings > General > About > Certificate Trust Settings."
        )

    target_hosts = target if target else [discover_lan_ip()]
    if CERT_PATH.exists():
        hosts = _san_hosts(_load_cert(CERT_PATH))
    else:
        hosts = []

    added = [h for h in target_hosts if h not in hosts]
    for h in ("localhost", "127.0.0.1"):
        if h not in hosts:
            added.append(h)
    if not added:
        return None  # every requested SAN is already covered; nothing to do

    hosts = hosts + added

    ca_key = _load_key(CA_KEY_PATH)
    ca_cert = _load_cert(CA_CERT_PATH)
    now = datetime.datetime.now(datetime.timezone.utc)
    server_key, server_cert = _make_server_cert(hosts, ca_key, ca_cert, now)
    _write_pem(KEY_PATH, server_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    _write_pem(CERT_PATH, server_cert.public_bytes(serialization.Encoding.PEM))
    return hosts


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
    parser.add_argument(
        "--reissue-leaf",
        action="store_true",
        help=("Reissue ONLY the server leaf, reusing the existing CA. The CA "
              "is never regenerated. Does nothing if the current LAN IP is "
              "already in the certificate's SANs."),
    )
    args = parser.parse_args()

    if args.reissue_leaf:
        hosts = reissue_leaf(args.hosts)
        if hosts is None:
            current = args.hosts[0] if args.hosts else discover_lan_ip()
            print(f"cert: current LAN IP {current} already covered by {CERT_PATH.name}; nothing to do")
        else:
            print(f"cert: REISSUED leaf; SANs now: {', '.join(hosts)}")
            print("cert: the console must be RESTARTED to pick up the new certificate")
            print("cert: a running uvicorn holds its certificate in memory and keeps serving the old one")
        return

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

    print(f"Wrote {CA_CERT_PATH}, {CA_KEY_PATH}, {CERT_PATH} and {KEY_PATH}")
    print(f"SAN hosts: {', '.join(hosts)}")
    print(
        f"INSTALL ON THE PHONE: {CA_CERT_PATH}  "
        "(the CA to trust in iOS Settings > General > About > "
        "Certificate Trust Settings)"
    )
    if lan_ip:
        print(f"Open on the phone: https://{lan_ip}:8888/")
    else:
        print("No LAN IPv4 found among --host values; phone check will not work.")


if __name__ == "__main__":
    main()
