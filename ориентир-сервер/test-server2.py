import socket
import socketserver
import ssl
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.backends import default_backend


#=========генерация ключей и самоподписаных сертификатов=======================
def generate_keys():
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048, # сложно взломать,ниже не рекомендуется
        backend=default_backend()
    )
    public_key=private_key.public_key()

    with open('server.key','wd') as f:
        f.write(private_key.private_bytes(
            encoding=serialization.Encoding.PEM, #PEM стандарт серилизации
            format=serialization.PrivateFormat.PKCS8, #RKCS8- формат для приватных ключей 
            encryption_algorithm=serialization.NoEncryption() # NoEncryption - без парольной защиты
        ))
    with open('server.pub','wd') as f:
        f.write(public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        ))
    