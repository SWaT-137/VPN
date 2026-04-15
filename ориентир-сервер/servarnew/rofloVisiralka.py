import os
from cryptography.hazmat.primitives.asymmetric import x25519

# Генерация
priv_key_obj = x25519.X25519PrivateKey.generate()
priv_bytes = priv_key_obj.private_bytes_raw() # 32 байта
pub_bytes = priv_key_obj.public_key().public_bytes_raw() # 32 байта, можно не сохранять
short_id = os.urandom(8) # 8 байт

print("Private Key (hex):", priv_bytes.hex())
print("Public Key (hex): ", pub_bytes.hex())
print("Short ID (hex):   ", short_id.hex())