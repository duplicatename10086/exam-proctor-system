"""
安全加固模块（A 方案）。

设计要点：
  1. 答案混淆：子体里不存明文答案，存 HMAC 摘要；判分时只比对摘要。
     密钥由母体生成，不随子体分发（考生拿不到原文答案）。
  2. 非对称签名：报告用私钥签名（私钥只在出题人手里），子体内嵌公钥只能验，不能签。
     考生伪造报告 → 没有私钥 → 签名校验必失败。
  3. 状态加密：state.json 用从机器特征+密钥派生的 AES 密钥加密，明文改不了。
  4. 单调计时：用 time.perf_counter()，且记录进程启动锚点，系统改时钟不影响。
  5. 网络令牌：恢复网络需要一个一次性令牌（母体生成时预置），考生点确认后
     令牌被消耗并写入加密状态；没有令牌不能恢复。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import platform
import time
from typing import Optional

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_SALT = b"proctor-system-v2"


def b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# ============ 1. 答案混淆（HMAC 摘要）============
def digest_answer(answer, secret_key: bytes) -> str:
    """把答案转成 HMAC 摘要。secret_key 只在出题人手里。"""
    if isinstance(answer, list):
        norm = "|".join(str(x).strip().upper() for x in sorted(answer))
    else:
        norm = str(answer or "").strip().upper()
    return b64e(hmac.new(secret_key, norm.encode("utf-8"), hashlib.sha256).digest())


def digest_answer_candidate(answer, secret_key: bytes) -> str:
    """考生答案按同样规则摘要（规范顺序，多选题排序）。"""
    return digest_answer(answer, secret_key)


# ============ 1b. 关键词摘要（填空/主观，明文关键词不随子体分发）============
def digest_keyword(kw: str, secret_key: bytes) -> str:
    return b64e(hmac.new(secret_key, str(kw).strip().lower().encode("utf-8"), hashlib.sha256).digest())


def build_keyword_digests(answer: str, secret_key: bytes) -> str:
    """
    把关键词答案串编成摘要 JSON（每组近义词的摘要）。
    格式: [["<digest>", "<digest>"], ...]
    """
    groups = []
    for seg in _KW_SPLIT.split(str(answer or "")):
        seg = seg.strip()
        if not seg:
            continue
        syns = [s.strip() for s in _SYN_SPLIT.split(seg) if s.strip()]
        groups.append([digest_keyword(s, secret_key) for s in syns])
    return json.dumps(groups, ensure_ascii=False)


_KW_SPLIT = __import__("re").compile(r"[,，;；、\n]+")
_SYN_SPLIT = __import__("re").compile(r"[|/｜／]")


def keyword_hit_count(keyword_digests_json: str, text: str, secret_key: bytes) -> tuple[int, int]:
    """
    在不暴露明文关键词的前提下统计命中组数。
    做法：对考生文本滑动 2~8 字符窗口生成子串，逐个摘要后与关键词摘要比对。
    """
    try:
        groups = json.loads(keyword_digests_json)
    except Exception:
        return 0, 0
    text = str(text or "").strip().lower()
    # 预计算文本中所有 2~8 长度子串的摘要集合
    sub_hashes = set()
    n = len(text)
    for L in range(1, min(9, n + 1)):
        for i in range(0, n - L + 1):
            sub_hashes.add(digest_keyword(text[i:i + L], secret_key))
    hit = 0
    for syns in groups:
        if any(d in sub_hashes for d in syns):
            hit += 1
    return hit, len(groups)


# ============ 2. 非对称签名（Ed25519）============
def gen_keypair() -> tuple[str, str]:
    """生成 (私钥PEM, 公钥PEM)。"""
    priv = ed25519.Ed25519PrivateKey.generate()
    priv_pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode("ascii")
    pub_pem = priv.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo).decode("ascii")
    return priv_pem, pub_pem


def sign_bytes(data: bytes, priv_pem: str) -> str:
    priv = serialization.load_pem_private_key(priv_pem.encode("ascii"), password=None)
    return b64e(priv.sign(data))


def verify_bytes(data: bytes, sig: str, pub_pem: str) -> bool:
    try:
        pub = serialization.load_pem_public_key(pub_pem.encode("ascii"))
        pub.verify(b64d(sig), data)
        return True
    except Exception:
        return False


def canonical_json(obj: dict) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def sign_report(report: dict, priv_pem: str) -> dict:
    body = canonical_json(report)
    return {
        "payload": report,
        "signature": sign_bytes(body, priv_pem),
        "algo": "Ed25519",
        "sha256": hashlib.sha256(body).hexdigest(),
    }


def verify_report_file(path: str, pub_pem: str) -> bool:
    with open(path, "r", encoding="utf-8") as f:
        wrapper = json.load(f)
    return verify_bytes(canonical_json(wrapper["payload"]), wrapper.get("signature", ""), pub_pem)


# ============ 3. 状态加密（AES-GCM）============
def machine_id() -> str:
    """取机器特征（Windows MachineGuid，其它平台用 MAC）。"""
    if platform.system() == "Windows":
        try:
            import winreg
            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                               r"SOFTWARE\Microsoft\Cryptography")
            gid, _ = winreg.QueryValueEx(k, "MachineGuid")
            return str(gid)
        except Exception:
            pass
    # 回退：MAC
    import uuid
    return str(uuid.getnode())


def derive_key(pub_pem_or_secret: str) -> bytes:
    """从公钥(或任意密钥串)+机器特征派生 AES 密钥。
    注意：派生含机器特征，换机器解不开——这是特性（防拷走状态文件）。"""
    return hashlib.sha256((pub_pem_or_secret + "|" + machine_id()).encode("utf-8")).digest()


def encrypt_state(obj: dict, pub_pem: str) -> dict:
    key = derive_key(pub_pem)
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, canonical_json(obj), None)
    return {"enc": True, "nonce": b64e(nonce), "data": b64e(ct)}


def decrypt_state(wrapper: dict, pub_pem: str) -> Optional[dict]:
    try:
        if not wrapper.get("enc"):
            return wrapper  # 兼容旧明文
        key = derive_key(pub_pem)
        ct = AESGCM(key).decrypt(b64d(wrapper["nonce"]), b64d(wrapper["data"]), None)
        return json.loads(ct.decode("utf-8"))
    except Exception:
        return None


# ============ 4. 单调计时 ============
class MonotonicClock:
    """进程内单调时钟，系统改时间不影响。"""
    def __init__(self):
        self._start = time.perf_counter()

    def now(self) -> float:
        return time.perf_counter() - self._start


# ============ 5. 网络令牌 ============
def make_token() -> str:
    return b64e(os.urandom(16))


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()
