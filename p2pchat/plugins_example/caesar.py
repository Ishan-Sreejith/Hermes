class Plugin:
    name = "caesar"

    def __init__(self, shift: int = 13):
        self.shift = shift

    def encrypt(self, plaintext: bytes, key: bytes) -> bytes:
        shift = key[0] if key and len(key) > 0 else self.shift
        return bytes((b + shift) % 256 for b in plaintext)

    def decrypt(self, ciphertext: bytes, key: bytes) -> bytes:
        shift = key[0] if key and len(key) > 0 else self.shift
        return bytes((b - shift) % 256 for b in ciphertext)

