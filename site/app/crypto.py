"""
crypto.py — верификация ed25519-подписей channel record / манифестов видео.

Формат подписи: подписывается канонический JSON записи БЕЗ поля "signature"
(отсортированные ключи, без пробелов) — это гарантирует, что сервер и клиент
считают одну и ту же байтовую строку.
"""

from __future__ import annotations

import base64
import hashlib
import json

import nacl.exceptions
import nacl.signing


class SignatureVerificationError(Exception):
    pass


def canonical_json(data: dict) -> bytes:
    """Каноническая сериализация — отсортированные ключи, без пробелов."""
    payload = {k: v for k, v in data.items() if k != "signature"}
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def compute_channel_id(public_key_b64: str) -> str:
    """channel_id = base32(sha256(public_key)), без паддинга, нижний регистр."""
    pubkey_bytes = base64.b64decode(public_key_b64)
    digest = hashlib.sha256(pubkey_bytes).digest()
    return base64.b32encode(digest).decode("ascii").rstrip("=").lower()


def verify_signature(record: dict, public_key_b64: str) -> bool:
    """
    Проверяет, что record["signature"] — валидная ed25519-подпись
    canonical_json(record) от ключа public_key_b64.
    """
    signature_b64 = record.get("signature")
    if not signature_b64:
        return False

    try:
        pubkey_bytes = base64.b64decode(public_key_b64)
        signature_bytes = base64.b64decode(signature_b64)
        verify_key = nacl.signing.VerifyKey(pubkey_bytes)
        verify_key.verify(canonical_json(record), signature_bytes)
        return True
    except (nacl.exceptions.BadSignatureError, ValueError, Exception):
        return False


def verify_channel_record(record: dict) -> tuple[bool, str]:
    """
    Полная проверка channel record: подпись валидна И channel_id соответствует
    заявленному public_key (защита от подмены — нельзя просто взять чужой
    channel_id и подписать своим ключом).

    Возвращает (ok, error_message).
    """
    public_key = record.get("public_key")
    channel_id = record.get("channel_id")

    if not public_key or not channel_id:
        return False, "missing public_key or channel_id"

    expected_channel_id = compute_channel_id(public_key)
    if expected_channel_id != channel_id:
        return False, f"channel_id mismatch: expected {expected_channel_id}, got {channel_id}"

    if not verify_signature(record, public_key):
        return False, "invalid signature"

    return True, ""

def verify_video_manifest(manifest: dict, channel_public_key_b64: str) -> tuple[bool, str]:
    """
    Проверяет подпись манифеста видео публичным ключом ЕГО КАНАЛА (взятым из БД,
    не из самого манифеста — манифест не содержит public_key, только channel_id).
    """
    if not verify_signature(manifest, channel_public_key_b64):
        return False, "invalid signature"

    # video_id = sha256 от манифеста БЕЗ полей signature, video_id (самоссылка
    # иначе никогда не сойдётся — id не может включать сам себя в хешируемые
    # данные) И published_at (video_id должен зависеть только от контента,
    # не от момента публикации — см. bridge/policy/crypto_utils.py:canonical_json_for_id,
    # ЭТУ логику нужно менять синхронно на обеих сторонах, иначе здесь начнёт
    # падать video_id mismatch на полностью валидных публикациях).
    import hashlib
    payload = {k: v for k, v in manifest.items() if k not in ("signature", "video_id", "published_at")}
    canonical_for_id = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    computed_id = hashlib.sha256(canonical_for_id).hexdigest()

    if computed_id != manifest.get("video_id"):
        return False, f"video_id mismatch: expected {computed_id}"

    return True, ""
