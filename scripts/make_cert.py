"""自己署名証明書を作って HTTPS で配信できるようにする。

なぜ必要か:
  ブラウザの音声入力（Web Speech API / getUserMedia）と PWA のインストールは
  「セキュアコンテキスト」でしか動かない。localhost は例外だが、
  スマートフォンから http://192.168.x.x:8000 で開くと音声が使えない。
  自己署名でも HTTPS にすればセキュアコンテキストになる。

実行:
  python scripts/make_cert.py

生成後、.env に次を追記して再起動する:
  HTTPS_CERT_FILE=config/cert.pem
  HTTPS_KEY_FILE=config/key.pem

初回アクセス時はスマートフォンに「この接続ではプライバシーが保護されません」と
表示されるので、「詳細設定」→「アクセスする」を選ぶ（家庭内LANのみで使う前提）。
"""
from __future__ import annotations

import datetime as dt
import ipaddress
import socket
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config  # noqa: E402

CERT_PATH = config.CONFIG_DIR / "cert.pem"
KEY_PATH = config.CONFIG_DIR / "key.pem"


def local_addresses() -> list[str]:
    """この端末が持つ IPv4 アドレスを集める。"""
    addresses = {"127.0.0.1"}
    hostname = socket.gethostname()
    try:
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            addresses.add(info[4][0])
    except socket.gaierror:
        pass
    # Linux / Raspberry Pi では hostname -I の方が確実
    try:
        output = subprocess.run(
            ["hostname", "-I"], capture_output=True, text=True, timeout=3, check=False
        ).stdout
        addresses.update(part for part in output.split() if part.count(".") == 3)
    except (OSError, subprocess.SubprocessError):
        pass
    return sorted(addresses)


def main() -> int:
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError:
        print("cryptography が入っていません。pip install -r requirements.txt を実行してください。")
        return 1

    hostname = socket.gethostname()
    names: list[x509.GeneralName] = [
        x509.DNSName("localhost"),
        x509.DNSName(hostname),
        x509.DNSName(f"{hostname}.local"),
        x509.DNSName("raspberrypi.local"),
    ]
    for address in local_addresses():
        try:
            names.append(x509.IPAddress(ipaddress.ip_address(address)))
        except ValueError:
            continue

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, hostname),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Student AI Assistant"),
    ])
    now = dt.datetime.now(dt.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName(names), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    KEY_PATH.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    CERT_PATH.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))

    print(f"証明書を作成しました: {CERT_PATH}")
    print(f"秘密鍵を作成しました: {KEY_PATH}")
    print()
    print(".env に次の2行を追記して、もう一度起動してください:")
    print("  HTTPS_CERT_FILE=config/cert.pem")
    print("  HTTPS_KEY_FILE=config/key.pem")
    print()
    print("対象ホスト名 / IP:")
    for name in names:
        print(f"  - {name.value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
