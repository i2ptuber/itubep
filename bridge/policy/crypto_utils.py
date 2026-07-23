"""
crypto_utils.py — генерация/хранение ключа канала и подпись манифестов на
стороне моста. Зеркалит алгоритм канонической сериализации из site/app/crypto.py
(должен давать идентичный результат, иначе подпись не пройдёт верификацию).
"""

from __future__ import annotations

import base64
import hashlib
import json

import nacl.exceptions
import nacl.pwhash
import nacl.secret
import nacl.signing
import nacl.utils

from .storage import PolicyStorage

# Версия формата зашифрованного блока — если когда-нибудь придётся менять
# параметры KDF или схему, старые блоки останутся читаемыми по этому полю.
ENCRYPTED_BLOB_VERSION = 1

# MODERATE — компромисс между стойкостью к перебору пароля (это ключ
# авторства канала, потеря = кто-то может издавать видео от вашего имени
# навсегда, цена компрометации высокая) и временем разблокировки (~5с на
# обычном железе). Разблокировка происходит один раз за время работы
# процесса моста (см. authz.py — результат кешируется в памяти), а не на
# каждую публикацию, поэтому даже несколько секунд не бьют по UX.
KDF_OPSLIMIT = nacl.pwhash.argon2id.OPSLIMIT_MODERATE
KDF_MEMLIMIT = nacl.pwhash.argon2id.MEMLIMIT_MODERATE


class WrongPassword(Exception):
    pass


def canonical_json(data: dict) -> bytes:
    payload = {k: v for k, v in data.items() if k not in ("signature",)}
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_json_for_id(data: dict) -> bytes:
    payload = {k: v for k, v in data.items() if k not in ("signature", "video_id")}
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def compute_channel_id(public_key_b64: str) -> str:
    pubkey_bytes = base64.b64decode(public_key_b64)
    digest = hashlib.sha256(pubkey_bytes).digest()
    return base64.b32encode(digest).decode("ascii").rstrip("=").lower()


def encrypt_seed(seed: bytes, password: str) -> str:
    """
    Шифрует 32-байтовый seed приватного ключа паролем пользователя.
    Возвращает JSON-строку — именно её и кладём в settings как значение
    "channel_private_key" (раньше там лежал голый base64 ключа открытым
    текстом).

    Схема: Argon2id (nacl.pwhash) для превращения пароля в симметричный
    ключ + XSalsa20-Poly1305 (nacl.secret.SecretBox) для собственно
    шифрования — именно то, что было заложено в исходном плане
    (crypto_pwhash + crypto_secretbox — это libsodium-имена тех же
    примитивов, PyNaCl оборачивает их как pwhash/secret.SecretBox).
    """
    salt = nacl.utils.random(nacl.pwhash.argon2id.SALTBYTES)
    symmetric_key = nacl.pwhash.argon2id.kdf(
        nacl.secret.SecretBox.KEY_SIZE, password.encode("utf-8"), salt,
        opslimit=KDF_OPSLIMIT, memlimit=KDF_MEMLIMIT,
    )
    box = nacl.secret.SecretBox(symmetric_key)
    nonce = nacl.utils.random(nacl.secret.SecretBox.NONCE_SIZE)
    ciphertext = box.encrypt(seed, nonce).ciphertext

    blob = {
        "v": ENCRYPTED_BLOB_VERSION,
        "kdf": "argon2id",
        "opslimit": KDF_OPSLIMIT,
        "memlimit": KDF_MEMLIMIT,
        "salt": base64.b64encode(salt).decode(),
        "nonce": base64.b64encode(nonce).decode(),
        "ciphertext": base64.b64encode(ciphertext).decode(),
    }
    return json.dumps(blob)


def decrypt_seed(blob_json: str, password: str) -> bytes:
    """
    Обратная операция. Бросает WrongPassword при неверном пароле (или
    повреждённых данных — MAC от Poly1305 не даст молча получить мусор
    вместо ошибки, secretbox.decrypt откажет с CryptoError).
    """
    blob = json.loads(blob_json)
    salt = base64.b64decode(blob["salt"])
    nonce = base64.b64decode(blob["nonce"])
    ciphertext = base64.b64decode(blob["ciphertext"])

    symmetric_key = nacl.pwhash.argon2id.kdf(
        nacl.secret.SecretBox.KEY_SIZE, password.encode("utf-8"), salt,
        opslimit=blob["opslimit"], memlimit=blob["memlimit"],
    )
    box = nacl.secret.SecretBox(symmetric_key)
    try:
        return box.decrypt(ciphertext, nonce)
    except nacl.exceptions.CryptoError:
        raise WrongPassword("Неверный пароль или повреждённые данные ключа")


def is_encrypted_blob(raw: str) -> bool:
    """
    Отличает новый (зашифрованный, JSON) формат от старого (голый base64
    signing-ключа, как хранилось до этого патча) — нужно для миграции
    существующих БД без ручного вмешательства.
    """
    try:
        blob = json.loads(raw)
        return isinstance(blob, dict) and "ciphertext" in blob and "salt" in blob
    except (json.JSONDecodeError, TypeError):
        return False


class ChannelIdentity:
    """Держит приватный ключ канала в памяти на время работы процесса."""

    def __init__(self, signing_key: nacl.signing.SigningKey, display_name: str):
        self.signing_key = signing_key
        self.public_key_b64 = base64.b64encode(bytes(signing_key.verify_key)).decode()
        self.channel_id = compute_channel_id(self.public_key_b64)
        self.display_name = display_name

    def sign(self, record: dict) -> str:
        signature = self.signing_key.sign(canonical_json(record)).signature
        return base64.b64encode(signature).decode()


def get_or_create_channel(storage: PolicyStorage, dialog) -> ChannelIdentity:
    """
    dialog — объект с методами:
      show_key_warning() -> bool
      prompt_channel_name() -> str | None
      prompt_new_password() -> str | None   (с подтверждением, для нового ключа
                                              и для миграции старого plaintext)
      prompt_unlock_password(attempt: int) -> str | None  (для разблокировки
                                              существующего зашифрованного ключа;
                                              attempt — номер попытки, с 1)

    Приватный ключ теперь всегда хранится в БД в зашифрованном виде
    (Argon2id + secretbox, см. encrypt_seed/decrypt_seed выше). Расшифрованный
    seed существует только в памяти процесса на время его работы — вызывающий
    код (authz.py) кеширует результат в памяти BridgePolicy, чтобы не просить
    пароль на каждую публикацию, а один раз за сессию работы моста.
    """
    existing_raw = storage.get_setting("channel_private_key")
    existing_name = storage.get_setting("channel_display_name")

    if existing_raw is not None:
        if is_encrypted_blob(existing_raw):
            return _unlock_existing_channel(existing_raw, existing_name, dialog)
        else:
            # Старый формат (plaintext base64) — до этого патча ключ хранился
            # без шифрования. Мигрируем на месте: расшифровывать нечего (ключ
            # и так открыт), но сразу просим пароль и перезаписываем БД
            # зашифрованным блоком, чтобы plaintext не остался лежать дальше.
            return _migrate_plaintext_channel(existing_raw, existing_name, storage, dialog)

    # Канала ещё нет — создаём, с обязательным предупреждением
    approved = dialog.show_key_warning()
    if not approved:
        raise RuntimeError("Пользователь отклонил создание канала")

    display_name = dialog.prompt_channel_name()
    if not display_name:
        raise RuntimeError("Название канала не указано")

    password = dialog.prompt_new_password()
    if not password:
        raise RuntimeError("Пароль для защиты ключа канала не задан")

    signing_key = nacl.signing.SigningKey.generate()
    seed = bytes(signing_key)

    storage.set_setting("channel_private_key", encrypt_seed(seed, password))
    storage.set_setting("channel_display_name", display_name)

    return ChannelIdentity(signing_key, display_name)


def _unlock_existing_channel(encrypted_raw: str, display_name: str | None, dialog) -> ChannelIdentity:
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        password = dialog.prompt_unlock_password(attempt)
        if not password:
            raise RuntimeError("Разблокировка ключа канала отменена")
        try:
            seed = decrypt_seed(encrypted_raw, password)
            signing_key = nacl.signing.SigningKey(seed)
            return ChannelIdentity(signing_key, display_name or "My Channel")
        except WrongPassword:
            if attempt == max_attempts:
                raise RuntimeError(
                    f"Неверный пароль ({max_attempts} попытки исчерпаны) — публикация отменена"
                )
            # иначе просто идём на следующую попытку цикла
    raise RuntimeError("Не удалось разблокировать ключ канала")  # недостижимо, для полноты


def _migrate_plaintext_channel(
    plaintext_key_b64: str, display_name: str | None, storage: PolicyStorage, dialog,
) -> ChannelIdentity:
    signing_key = nacl.signing.SigningKey(base64.b64decode(plaintext_key_b64))

    password = dialog.prompt_new_password(migration=True)
    if not password:
        raise RuntimeError(
            "Ключ канала всё ещё хранится незашифрованным — публикация отменена, "
            "пароль обязателен для продолжения"
        )

    storage.set_setting("channel_private_key", encrypt_seed(bytes(signing_key), password))
    return ChannelIdentity(signing_key, display_name or "My Channel")
