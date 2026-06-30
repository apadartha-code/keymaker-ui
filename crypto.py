import os
import sys
import termios
import ctypes
import mmap
import resource

import traceback

import abc
import json
import base64
from contextlib import contextmanager
from typing import Any, Generator

import secrets
import hashlib
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes


# Securely load the standard C library
try:
    libc = ctypes.CDLL(None)
except Exception:
    import ctypes.util
    libc = ctypes.CDLL(ctypes.util.find_library('c'))


def read_password_via_syscall(prompt: str = "Enter Secret: ") -> bytearray:
    """
    Disables TTY echo and reads input directly from the raw file descriptor 
    using the OS read system call, bypassing all Python/libc IO buffers.
    """
    sys.stderr.write(prompt)
    sys.stderr.flush()

    # File descriptor 0 is raw standard input
    fd = 0 
    
    # Save and modify terminal flags to strip ECHO
    old_settings = termios.tcgetattr(fd)
    new_settings = termios.tcgetattr(fd)
    new_settings[3] = new_settings[3] & ~termios.ECHO # index 3 is lflags

    password_buffer = bytearray()

    try:
        termios.tcsetattr(fd, termios.TCSANOW, new_settings)
        
        while True:
            # Execute a direct read(2) system call fetching exactly 1 byte
            char_byte = os.read(fd, 1)
            
            if not char_byte or char_byte == b'\n' or char_byte == b'\r':
                break
                
            password_buffer.extend(char_byte)
            
    finally:
        # Guarantee terminal recovery
        termios.tcsetattr(fd, termios.TCSANOW, old_settings)
        sys.stderr.write('\n')
        sys.stderr.flush()

    return password_buffer


class SecureKeyScope:
    def __init__(self, password: bytearray, salt: bytes, iterations: int = 100_000):
        if not isinstance(password, bytearray):
            raise TypeError("Password must be a mutable bytearray.")
            
        self.password = password
        self.salt = salt
        self.iterations = iterations
        self._kdf = None
        self._derived_key_buffer = None

    def __enter__(self) -> bytearray:
        self._kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=self.salt,
            iterations=self.iterations,
        )
        # 1. Pre-allocate a safe, mutable bytearray container for the key
        self._derived_key_buffer = bytearray(32)
        
        # 2. Derive directly INTO the mutable buffer (no read-only bytes created)
        self._kdf.derive_into(self.password, self._derived_key_buffer)
        
        # Return the mutable bytearray to the context block
        return self._derived_key_buffer

    def __exit__(self, exc_type, exc_val, exc_tb):
        # 3. Wiping the key buffer now works flawlessly with from_buffer
        if self._derived_key_buffer:
            self._secure_zero_mutable(self._derived_key_buffer)
            # Break internal indexing values 
            for i in range(len(self._derived_key_buffer)):
                self._derived_key_buffer[i] = 0x00
            self._derived_key_buffer = None
            
        # 4. Wipe the initial password bytearray buffer
        if self.password:
            self._secure_zero_mutable(self.password)
            for i in range(len(self.password)):
                self.password[i] = 0x00
            self.password = None
            
        # 5. Clear backend dictionary states 
        if self._kdf:
            for attr_name in ['_key_material', '_password', '_key']:
                if hasattr(self._kdf, attr_name):
                    hidden_bytes = getattr(self._kdf, attr_name)
                    # Use safety-checked wiping depending on type
                    if isinstance(hidden_bytes, bytearray):
                        self._secure_zero_mutable(hidden_bytes)
            self._kdf = None

        self.salt = None
        return False

    def _secure_zero_mutable(self, obj: bytearray):
        """Safely zeros out mutable buffers without any pointer offset calculations."""
        length = len(obj)
        if length == 0:
            return
        buffer_address = ctypes.c_char.from_buffer(obj)
        ctypes.memset(ctypes.byref(buffer_address), 0, length)


def xor_mask_data(data: bytearray, key_seed: int) -> bytearray:
    """Applies an in-place XOR mask to flatten the entropy signature."""
    state = key_seed
    masked = bytearray(len(data))
    for i in range(len(data)):
        state = (1103515245 * state + 12345) & 0x7fffffff
        mask_byte = (state >> 16) & 0xFF
        masked[i] = data[i] ^ mask_byte
    return masked


def mutable_urandom(bytecount: int) -> bytearray:
    """Because... os.urandom output cannot be explicitly zeroed!"""
    return xor_mask_data(bytearray(os.urandom(bytecount)), secrets.randbelow(2**64))

    
# (TBD: __IMPORTANT__!! algorithms.AES can accept bytearray - no need to pass bytes!)
def aes_cbc_process(key: bytes, mode_str: str, iv: bytes, data: bytearray) -> bytearray:
    """
    Executes standard, secure AES-CBC encryption or decryption using PyCA Cryptography.
    """
    # Derive a cryptographically sound 16-byte IV for this specific operation
    # iv = hashlib.md5(iv_seed).digest() 
    
    backend = default_backend()
    cipher_algo = algorithms.AES(key)
    
    # Correctly evaluate and map the structural mode parameter
    if mode_str.lower() == 'encrypt':
        cipher_mode = modes.CBC(iv)
        cipher = Cipher(cipher_algo, cipher_mode, backend=backend)
        processor = cipher.encryptor()
    elif mode_str.lower() == 'decrypt':
        cipher_mode = modes.CBC(iv)
        cipher = Cipher(cipher_algo, cipher_mode, backend=backend)
        processor = cipher.decryptor()
    else:
        raise ValueError("Invalid mode parameter. Must be 'encrypt' or 'decrypt'.")

    # Handle standard PKCS7 padding requirements for block ciphers
    payload = bytearray(data)
    if mode_str.lower() == 'encrypt':
        pad_len = 16 - (len(payload) % 16)
        payload.extend([pad_len] * pad_len)

    # Process the bytes through the genuine AES pipeline
    result = bytearray(processor.update(bytes(payload)) + processor.finalize())

    # Strip standard PKCS7 padding on decryption
    if mode_str.lower() == 'decrypt':
        pad_len = result[-1]
        if 0 < pad_len <= 16:
            result = result[:-pad_len]

    return result

def aes_cbc_decrypt(key: bytes, iv_and_cipherbytes: bytearray) -> bytearray:
    iv = iv_and_cipherbytes[:16]
    data = iv_and_cipherbytes[16:]
    return aes_cbc_process(key, 'decrypt', iv, data)

def aes_cbc_encrypt(key: bytes, plainbytes: bytearray) -> bytearray:
    iv = os.urandom(16)
    ciphertext = aes_cbc_process(key, 'encrypt', iv, plainbytes)
    return iv + ciphertext


class BaseAppTranscoder(abc.ABC):
    """
    This class instantiates the encoding / decoding primitives
    expected by the blueprint in the backend that complement the
    corresponding front-end primitives, bot supplied by the
    main app. In our case, the main objective of the encoding /
    decoding is to securely transport the user inputs to the
    blueprint in a manner that does not get dumped at edge firewalls
    or can be scanned from the server's memory.
    """
    def __init__(self, rules: Any = None):
        self.rules = rules
        self.iv_length = 12  # Standard secure IV length for AES-GCM is 12 bytes
        self.tag_length = 16 # Standard GCM authentication tag size is 16 bytes

    @abc.abstractmethod
    def _get_key(self) -> bytearray:
        """
        Must be implemented by the derived class.
        Should return a fresh or mutable bytearray holding the AES key.
        """
        raise NotImplementedError("You must implement _get_key() in your subclass.")

    @contextmanager
    def _use_key(self) -> Generator[bytearray, None, None]:
        """
        Context manager that fetches the key bytearray, yields it for use,
        and guarantees its contents are zeroed out immediately afterward.
        """
        key_bytes = self._get_key()
        try:
            yield key_bytes
        finally:
            # Overwrite every byte in the bytearray with zero to clear it from memory
            for i in range(len(key_bytes)):
                key_bytes[i] = 0

    # ==========================================
    # 1. BASE PAIR (Bytes <-> Base64)
    # ==========================================

    def encode(self, raw_bytes: bytes) -> str:
        """
        Encrypts raw bytes using AES-GCM, prepends a unique IV,
        and outputs a unified Base64 string.
        """

        # Generate a fresh, securely random 12-byte IV
        iv = os.urandom(self.iv_length)

        # 1. Open the key context and create the encryptor passing the bytearray directly
        with self._use_key() as key:
            encryptor = Cipher(
                algorithms.AES(key),
                modes.GCM(iv),
                backend=default_backend()
            ).encryptor()

        # The 'with' block ends here, and the bytearray is IMMEDIATELY zeroed out.
        # The key material only lives on inside the C-level OpenSSL cipher structure now.

        # 2. Perform encryption and finalize to get the auth tag
        ciphertext = encryptor.update(raw_bytes) + encryptor.finalize()
        tag = encryptor.tag # AES-GCM authentication tag

        # 3. Combine: IV (12B) + Ciphertext + Tag (16B) to match standard WebCrypto structures
        combined_buffer = iv + ciphertext + tag

        # 4. Convert the unified byte array into a Base64 string output
        return base64.b64encode(combined_buffer).decode('utf-8')

    def decode(self, base64_data: str) -> bytes:
        """
        Decodes a composite Base64 string, strips the IV and auth tag,
        decrypts the payload via AES-GCM, and returns the raw plaintext bytes.
        """

        try:
            # 1. Convert the input Base64 string back into raw bytes
            combined_buffer = base64.b64decode(base64_data)

            # 2. Structural sanity check: Must at least contain IV and Tag overhead lengths
            min_length = self.iv_length + self.tag_length
            if len(combined_buffer) <= min_length:
                raise ValueError("Ciphertext data payload is structurally invalid or truncated.")

            # 3. Dissect the combined buffer payload:
            # Layout: [ IV (12 bytes) ] [ Ciphertext (Variable) ] [ Tag (16 bytes) ]
            iv = combined_buffer[:self.iv_length]
            ciphertext = combined_buffer[self.iv_length:-self.tag_length]
            tag = combined_buffer[-self.tag_length:]

            # 4. Open the key context and create the decryptor passing the bytearray directly
            with self._use_key() as key:
                decryptor = Cipher(
                    algorithms.AES(key),
                    modes.GCM(iv, tag), # Provide the tag here to GCM mode for validation
                    backend=default_backend()
                ).decryptor()

            # At this point, the context manager has exited and the bytearray is zeroed out.

            # 5. Perform decryption. If validation or the authentication tag fails,
            # this step will automatically raise an InvalidTag exception.
            return decryptor.update(ciphertext) + decryptor.finalize()

        except Exception as e:
            # Catching integrity failures, incorrect tags, or corrupt payloads safely
            print("ERROR:", str(e))
            traceback.print_exc()
            return b""

    # ==========================================
    # 2. STRING VERSION (String <-> Base64)
    # ==========================================

    def encode_str(self, text: str) -> str:
        raw_bytes = text.encode('utf-8')
        return self.encode(raw_bytes)

    def decode_str(self, base64_data: str) -> str:
        decrypted_bytes = self.decode(base64_data)
        if not decrypted_bytes:
            return ""
        return decrypted_bytes.decode('utf-8')

    # ==========================================
    # 3. OBJECT VERSION (Object <-> Base64)
    # ==========================================

    def encode_obj(self, obj: Any) -> str:
        json_string = json.dumps(obj)
        return self.encode_str(json_string)

    def decode_obj(self, base64_data: str) -> Any:
        json_string = self.decode_str(base64_data)
        if not json_string:
            return None

        try:
            return json.loads(json_string)
        except json.JSONDecodeError:
            return None


class TransCrypter(BaseAppTranscoder):
    """
    Exposed as a protocol mechanism to the blueprint to securely transfer
    the generated key to the main app.. Also, bundles the front-end
    encode / decode mechanism. So, it's both a transcoder (Trans) as well
    as an encrypted storage (Crypt).
    Since the transcoding also turns out to be encryption / decryption of
    frontend parameters, we chose to combine the two functionalities to
    reduce the context management code involved in wiping cleartext keys.
    The only functionality that the blueprint will use here is:
    save_encoding(...). The rest of the encoding / decoding functionality
    comes from the base class.
    """
    def __init__(self, master_key_store, mask_seed = 42):
        super().__init__(rules=None)
        self._master_key_store = master_key_store
        self._mask_seed = mask_seed
        self._encrypted_fe_secret = None
        self._encrypted_be_secret = None

    def set_fe_secret(self, secret: bytearray):
        self._encrypted_fe_secret = self._master_key_store.encrypt(secret)
        # As an exception, we shall wipe out the plaintext secret here:
        for i in range(len(secret)): secret[i] = 0

    def save_encoding(self, encoding: bytearray, masked = False):
        # If the caller has the encoding in a hex string
        # convert using bytearray.fromhex(data)
        if masked:
            unmasked_data = self.mask(encoding) # Reapply the mask to unmask.
        else:
            unmasked_data = encoding
        self._encrypted_be_secret = self._master_key_store.encrypt(unmasked_data)
        if masked:
            # Vaporize the unmasked plaintext
            for i in range(len(unmasked_data)): unmasked_data[i] = 0

    def get_be_secret(self):
        return self._master_key_store.decrypt(self._encrypted_be_secret)

    def mask(self, data: bytearray) -> bytearray:
        return xor_mask_data(data, self._mask_seed)

    def _get_key(self) -> bytearray:
        # Decrypt the encrypted frontend secret for the base class to use.
        # It will be wiped by the base class.
        return self._master_key_store.decrypt(self._encrypted_fe_secret)


class SecureMemoryPassword:
    def __init__(self, raw_input: bytearray, decoy_count: int = 32):
        """
        Hardens the process, generates Page Noise Flooding (decoy allocations),
        allocates a structural-blind real block, and locks them all in RAM.
        """
        if not isinstance(raw_input, bytearray):
            raise TypeError("Input must be a mutable bytearray.")

        self._length = len(raw_input)
        self._decoy_count = decoy_count
        self._decoys = []  # Tracks tuples of (mmap_obj, raw_address)

        # 1. Enforce core process protections
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0)) # Block core dumps
        if hasattr(libc, 'prctl'):
            libc.prctl(4, 0, 0, 0, 0) # Clear PR_SET_DUMPABLE (Anti-Ptrace)

        self._mask_seed = secrets.randbits(31)

        # 2. IMPLEMENT PAGE NOISE FLOODING
        # Create identical-looking, locked decoy pages to flood the kernel's PTE map
        # We align decoy allocation sizes to system page size (typically 4096 bytes) for realism
        page_size = mmap.PAGESIZE if hasattr(mmap, 'PAGESIZE') else 4096
        allocation_size = max(self._length, page_size)

        for _ in range(self._decoy_count):
            decoy_map = mmap.mmap(
                -1, 
                allocation_size,
                flags=mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS,
                prot=mmap.PROT_READ | mmap.PROT_WRITE
            )
            decoy_address = ctypes.addressof(ctypes.c_char.from_buffer(decoy_map))
            
            # Fill decoys with flat, realistic-looking mask noise to blend entropy profiles
            decoy_noise = secrets.token_bytes(allocation_size)
            decoy_map.write(decoy_noise)
            
            # Pinned via mlock so it gets the exact same kernel PTE flags as the real secret
            if hasattr(libc, 'mlock'):
                libc.mlock(decoy_address, allocation_size)
                
            self._decoys.append((decoy_map, decoy_address, allocation_size))

        # 3. Allocate the REAL structural-blind Ghost Memory block
        self._ghost_map = mmap.mmap(
            -1, 
            self._length,
            flags=mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS,
            prot=mmap.PROT_READ | mmap.PROT_WRITE
        )
        self._address = ctypes.addressof(ctypes.c_char.from_buffer(self._ghost_map))

        # Lock the real page
        if hasattr(libc, 'mlock'):
            libc.mlock(self._address, self._length)

        # 4. Mask the real secret and copy it into the ghost allocation
        masked_bytes = xor_mask_data(raw_input, self._mask_seed)
        ctypes.memmove(self._address, ctypes.c_char_p(bytes(masked_bytes)), self._length)

        # 5. Instantly scrub temporary variables
        for i in range(len(raw_input)):
            raw_input[i] = 0
            masked_bytes[i] = 0

    def _get_unmasked_secret(self) -> bytearray:
        """Temporarily extracts and unmasks the data for crypto actions."""
        if self._ghost_map is None:
            raise ValueError("Buffer has been securely destroyed.")
        raw_masked = bytearray(self._ghost_map[:])
        return xor_mask_data(raw_masked, self._mask_seed)

    def encrypt_collection(self, collection):
        encrypted_collection = []
        kek = self._get_unmasked_secret()
        try:
            bkek = bytes(kek)
            for item in collection:
                encrypted_collection.append(aes_cbc_encrypt(bkek, item))
        finally:
            # Vaporize the plaintext KEK reference
            for i in range(len(kek)): kek[i] = 0
        return encrypted_collection

    def encrypt(self, data: bytearray) -> bytearray:
        response = self.encrypt_collection([ data ])
        return response[0]

    def decrypt_collection(self, collection) -> bytearray:
        decrypted_collection = []
        kek = self._get_unmasked_secret()
        try:
            bkek = bytes(kek)
            for item in collection:
                decrypted_collection.append(aes_cbc_decrypt(bkek, item))
        finally:
            # Vaporize the plaintext KEK reference
            for i in range(len(kek)): kek[i] = 0
        return decrypted_collection

    def decrypt(self, cipherbytes: bytearray) -> bytearray:
        response = self.decrypt_collection([ cipherbytes ])
        return response[0]

    def clear(self):
        """Tears down the entire infrastructure: wipes real data and all decoy pages."""
        # A. Clear the real secret ghost block
        if self._ghost_map is not None:
            try:
                if hasattr(libc, 'memset'):
                    ctypes.memset(self._address, 0, self._length)
                self._ghost_map.write(secrets.token_bytes(self._length))
            finally:
                if hasattr(libc, 'munlock'):
                    libc.munlock(self._address, self._length)
                self._ghost_map.close()
                self._ghost_map = None
                self._address = None

        # B. Clear the entire page flood array (Decoys)
        for decoy_map, decoy_address, alloc_size in self._decoys:
            try:
                if hasattr(libc, 'memset'):
                    ctypes.memset(decoy_address, 0, alloc_size)
            finally:
                if hasattr(libc, 'munlock'):
                    libc.munlock(decoy_address, alloc_size)
                decoy_map.close()
        
        self._decoys.clear()
        self._mask_seed = 0

    def __enter__(self): return self
    def __exit__(self, exc_type, exc_val, exc_tb): self.clear()
    def __del__(self): self.clear()