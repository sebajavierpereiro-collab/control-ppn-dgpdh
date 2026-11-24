"""Ola 5 · Reforma 92: conexión cifrada (HTTPS) dentro de la red del organismo.

Arma una autoridad certificante INTERNA (válida solo para las PCs del organismo que la instalen) y el certificado del
servidor de la app. Así el navegador muestra el candado sin pagar un certificado y sin salir a internet.

Uso (en la PC o servidor donde corre la app, desde la carpeta dgpdh_web):

    python https_interno.py --nombre SERVIDOR-DGPDH --ip 10.1.2.3
    python https_interno.py --renovar          (renueva solo el certificado del servidor, con la misma autoridad)
    python https_interno.py --estado

Después:
  1. En config.ini, [seguridad] https = true
  2. Arrancar con iniciar_https.bat (puerto 8443).
  3. Instalar certs\\ca.crt en cada PC como «Entidad de certificación raíz de confianza» (doble clic → Instalar
     certificado → Equipo local), o pedirle a Sistemas que lo distribuya por directiva de grupo.

certs\\ca.key es la llave de la autoridad: no se comparte ni se copia a las PCs (se guarda aparte, por ejemplo en un
pendrive bajo llave). Si se pierde, se arma una autoridad nueva y se vuelve a instalar ca.crt en las PCs.
"""
import argparse
import datetime as dt
import ipaddress
import socket
import sys
from pathlib import Path

CARPETA = Path(__file__).resolve().parent / "certs"
CA_CRT, CA_KEY = CARPETA / "ca.crt", CARPETA / "ca.key"
SRV_CRT, SRV_KEY = CARPETA / "servidor.crt", CARPETA / "servidor.key"
DIAS_CA, DIAS_SERVIDOR = 3650, 825


def _cripto():
    try:
        from cryptography import x509  # noqa: F401
        return True
    except ImportError:
        return False


def _ips_locales():
    ips = {"127.0.0.1"}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            if info[0] == socket.AF_INET:
                ips.add(info[4][0])
    except OSError:
        pass
    return sorted(ips)


def _guardar(ruta, datos, privado=False):
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes(datos)
    if privado:
        try:
            ruta.chmod(0o600)
        except OSError:
            pass


def crear_autoridad(nombre_org="PPN · DGPDH"):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    clave = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    nombre = x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, nombre_org),
                        x509.NameAttribute(NameOID.COMMON_NAME, "Autoridad interna DGPDH")])
    ahora = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(nombre).issuer_name(nombre).public_key(clave.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(ahora - dt.timedelta(minutes=5))
            .not_valid_after(ahora + dt.timedelta(days=DIAS_CA))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=False, content_commitment=False, key_encipherment=False,
                                         data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
                                         encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(clave.public_key()), critical=False)
            .sign(clave, hashes.SHA256()))
    _guardar(CA_KEY, clave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                         serialization.NoEncryption()), privado=True)
    _guardar(CA_CRT, cert.public_bytes(serialization.Encoding.PEM))
    return cert


def crear_servidor(nombres, ips):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    ca_cert = x509.load_pem_x509_certificate(CA_CRT.read_bytes())
    ca_clave = serialization.load_pem_private_key(CA_KEY.read_bytes(), password=None)
    clave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    alt = [x509.DNSName(n) for n in dict.fromkeys(nombres) if n]
    alt += [x509.IPAddress(ipaddress.ip_address(i)) for i in dict.fromkeys(ips) if i]
    ahora = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, nombres[0])]))
            .issuer_name(ca_cert.subject).public_key(clave.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(ahora - dt.timedelta(minutes=5)).not_valid_after(ahora + dt.timedelta(days=DIAS_SERVIDOR))
            .add_extension(x509.SubjectAlternativeName(alt), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_clave.public_key()), critical=False)
            .sign(ca_clave, hashes.SHA256()))
    _guardar(SRV_KEY, clave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                          serialization.NoEncryption()), privado=True)
    _guardar(SRV_CRT, cert.public_bytes(serialization.Encoding.PEM))
    return cert


def _vence(c):
    fin = getattr(c, "not_valid_after_utc", None)
    return fin if fin is not None else c.not_valid_after.replace(tzinfo=dt.timezone.utc)


def estado():
    """Para Administración y el monitoreo: si hay certificado, para qué nombres e IPs y cuándo vence."""
    d = {"hay_ca": CA_CRT.is_file(), "hay_servidor": SRV_CRT.is_file() and SRV_KEY.is_file(), "vence": None,
         "dias": None, "nombres": [], "ca_vence": None, "cripto": _cripto()}
    if not d["cripto"]:
        return d
    from cryptography import x509
    try:
        if d["hay_servidor"]:
            c = x509.load_pem_x509_certificate(SRV_CRT.read_bytes())
            fin = _vence(c)
            d["vence"], d["dias"] = fin.date().isoformat(), (fin - dt.datetime.now(dt.timezone.utc)).days
            try:
                san = c.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
                d["nombres"] = [str(v) for v in san.get_values_for_type(x509.DNSName)] + \
                               [str(v) for v in san.get_values_for_type(x509.IPAddress)]
            except x509.ExtensionNotFound:
                pass
        if d["hay_ca"]:
            d["ca_vence"] = _vence(x509.load_pem_x509_certificate(CA_CRT.read_bytes())).date().isoformat()
    except Exception as e:
        d["error"] = str(e)
    return d


def main(argv=None):
    p = argparse.ArgumentParser(description="Certificados para HTTPS interno de la app DGPDH")
    p.add_argument("--nombre", action="append", default=[], help="Nombre del servidor en la red (se puede repetir)")
    p.add_argument("--ip", action="append", default=[], help="IP fija del servidor (se puede repetir)")
    p.add_argument("--renovar", action="store_true", help="Renueva solo el certificado del servidor")
    p.add_argument("--estado", action="store_true", help="Muestra el certificado actual")
    a = p.parse_args(argv)
    if not _cripto():
        print("Falta la librería «cryptography». Instalala con:  python -m pip install cryptography")
        return 2
    if a.estado:
        e = estado()
        print(e)
        return 0
    if not CA_CRT.is_file() or not CA_KEY.is_file():
        if a.renovar:
            print("No hay autoridad interna (certs\\ca.crt). Corré primero sin --renovar.")
            return 2
        crear_autoridad()
        print(f"Autoridad interna creada: {CA_CRT}  (instalala en cada PC)  ·  {CA_KEY}  (guardala aparte, no se comparte)")
    viejo = estado()
    nombres = a.nombre or ([n for n in viejo["nombres"] if not n[0].isdigit()] if a.renovar else []) or [socket.gethostname()]
    if "localhost" not in nombres:
        nombres.append("localhost")
    try:
        fqdn = socket.getfqdn()
        if fqdn and fqdn not in nombres and "." in fqdn:
            nombres.insert(1, fqdn)
    except OSError:
        pass
    ips = a.ip or ([n for n in viejo["nombres"] if n[0].isdigit()] if a.renovar else []) or _ips_locales()
    if "127.0.0.1" not in ips:
        ips.append("127.0.0.1")
    crear_servidor(nombres, ips)
    print(f"Certificado del servidor: {SRV_CRT} (vale {DIAS_SERVIDOR} días) para: {', '.join(nombres + ips)}")
    print("Ahora: config.ini → [seguridad] https = true, y arrancá con iniciar_https.bat")
    return 0


if __name__ == "__main__":
    sys.exit(main())
