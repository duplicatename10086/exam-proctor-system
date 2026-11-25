"""状态磁盘持久化（加密） + 报告（Ed25519 非对称签名，私钥在出题人手里）。"""
from __future__ import annotations

import json
import os
import time
from typing import Optional

from .models import ExamState
from . import security as SEC


class StateStore:
    """增量写状态到磁盘（AES-GCM 加密，密钥从公钥+机器特征派生）。"""
    def __init__(self, path: str, pub_pem: str = ""):
        self.path = path
        self.pub_pem = pub_pem
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)

    def save(self, state: ExamState):
        tmp = self.path + ".tmp"
        payload = state.to_dict()
        if self.pub_pem:
            wrapper = SEC.encrypt_state(payload, self.pub_pem)
        else:
            wrapper = payload  # 兼容：无公钥时明文
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(wrapper, f, ensure_ascii=False)
        os.replace(tmp, self.path)

    def load(self) -> Optional[ExamState]:
        if not os.path.exists(self.path):
            return None
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                wrapper = json.load(f)
            if self.pub_pem and isinstance(wrapper, dict) and wrapper.get("enc"):
                data = SEC.decrypt_state(wrapper, self.pub_pem)
                if data is None:
                    return None  # 篡改或换机器 → 不加载
            else:
                data = wrapper
            return ExamState.from_dict(data)
        except Exception:
            return None

    def clear(self):
        if os.path.exists(self.path):
            os.remove(self.path)


# ---- 兼容旧接口（HMAC 模式，母体本地生成器仍可用） ----
import base64, hashlib, hmac
def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def sign_report_hmac(report: dict, secret: str) -> dict:
    body = SEC.canonical_json(report)
    return {"payload": report, "signature": _b64e(hmac.new(secret.encode(), body, hashlib.sha256).digest()),
            "algo": "HMAC-SHA256", "sha256": hashlib.sha256(body).hexdigest()}


def verify_report(path: str, secret_or_pub: str) -> bool:
    """自动识别签名算法：Ed25519(公钥) 或 HMAC(旧密钥)。"""
    with open(path, "r", encoding="utf-8") as f:
        wrapper = json.load(f)
    if wrapper.get("algo") == "Ed25519":
        return SEC.verify_report_file(path, secret_or_pub)
    body = SEC.canonical_json(wrapper["payload"])
    return hmac.compare_digest(_b64e(hmac.new(secret_or_pub.encode(), body, hashlib.sha256).digest()),
                               wrapper.get("signature", ""))


def _embed_media(paths: list[str]) -> list[dict]:
    """把图片/视频文件读成 base64 内嵌进报告。"""
    out = []
    import mimetypes
    for p in paths or []:
        if not p or not os.path.exists(p):
            continue
        try:
            with open(p, "rb") as f:
                data = base64.b64encode(f.read()).decode("ascii")
            mime = mimetypes.guess_type(p)[0] or "application/octet-stream"
            out.append({"name": os.path.basename(p), "mime": mime, "data": data})
        except Exception:
            pass
    return out


def build_report(state: ExamState, pkg_secret: str, vm_info: dict,
                 qmap: dict = None, candidate_media: list[str] = None,
                 candidate_media_map: dict = None,
                 local_ai_signs: list[str] = None) -> dict:
    """组装完整报告；每题含题干附件、参考答案附件、考生本题作答附件。"""
    results = [dict(r) for r in state.result]
    cmap = candidate_media_map or {}
    if qmap:
        for r in results:
            qid = r.get("question_id")
            q = qmap.get(qid)
            if q:
                r["question_images"] = _embed_media(getattr(q, "images", []))
                r["question_videos"] = _embed_media(getattr(q, "videos", []))
                r["reference_answer_images"] = _embed_media(getattr(q, "answer_images", []))
                r["reference_answer_videos"] = _embed_media(getattr(q, "answer_videos", []))
            r["candidate_answer_media"] = _embed_media(cmap.get(qid, []))
    general_media = _embed_media(candidate_media or [])
    return {
        "report_version": 2,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        "candidate": state.candidate,
        "exam_title": state.candidate.get("_exam_title", "监考系统考试"),
        "start_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(state.start_wall or state.start_ts)) if (state.start_wall or state.start_ts) else "",
        "submit_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(state.submit_wall or state.submit_ts)) if (state.submit_wall or state.submit_ts) else "",
        "duration_seconds": int(state.submit_ts - state.start_ts) if state.submit_ts and state.start_ts else 0,
        "total_score": state.total_score,
        "full_score": state.full_score,
        "pass": state.total_score >= state.candidate.get("_pass_score", 60),
        "results": results,
        "candidate_media": general_media,
        "events": state.events,
        "anti_cheat": {
            "vm_detected": vm_info.get("suspected", False),
            "vm_signs": vm_info.get("signs", []),
            "hardware": vm_info.get("hardware", ""),
            "key_violations": len([e for e in state.events if e["kind"] == "key_violation"]),
            "focus_loss": len([e for e in state.events if e["kind"] == "focus_loss"]),
            "local_ai_detected": bool(local_ai_signs),
            "local_ai_signs": list(local_ai_signs or []),
        },
    }


def write_report(path: str, report: dict, secret: str,
                 priv_pem: str = "", pub_pem: str = "") -> str:
    """写报告。若提供私钥→Ed25519签名（推荐）；否则HMAC；均设只读。"""
    if priv_pem:
        wrapper = SEC.sign_report(report, priv_pem)
    else:
        wrapper = sign_report_hmac(report, secret)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(wrapper, f, ensure_ascii=False, indent=2)
    try:
        os.chmod(path, 0o444)
    except Exception:
        pass
    return path
