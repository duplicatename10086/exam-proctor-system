"""数据模型：题目 / 考试配置 / 考试状态。母体与子体共用。"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

SINGLE = "single"
MULTIPLE = "multiple"
JUDGE = "judge"
FILL = "fill"
SUBJECTIVE = "subjective"

QTYPES = [SINGLE, MULTIPLE, JUDGE, FILL, SUBJECTIVE]
QTYPE_LABELS = {
    SINGLE: "单选题",
    MULTIPLE: "多选题",
    JUDGE: "判断题",
    FILL: "填空题",
    SUBJECTIVE: "主观题",
}


def new_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class Question:
    id: str = field(default_factory=new_id)
    type: str = SINGLE
    stem: str = ""
    options: List[str] = field(default_factory=list)
    answer: Any = ""          # single/judge: str; multiple: list[str]; fill/subjective: 关键词串
    points: float = 10.0
    analysis: str = ""
    images: List[str] = field(default_factory=list)         # 题干附件：图片
    videos: List[str] = field(default_factory=list)         # 题干附件：视频
    answer_images: List[str] = field(default_factory=list)  # 参考答案附件：图片
    answer_videos: List[str] = field(default_factory=list)  # 参考答案附件：视频
    answer_digest: str = ""      # 加固后：客观题答案的 HMAC 摘要
    keyword_digest: str = ""     # 加固后：填空/主观关键词的摘要 JSON
    answer_hashed: bool = False  # 是否用摘要模式

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Question":
        return Question(
            id=d.get("id") or new_id(),
            type=d.get("type", SINGLE),
            stem=d.get("stem", ""),
            options=list(d.get("options") or []),
            answer=d.get("answer", ""),
            points=float(d.get("points", 10.0)),
            analysis=d.get("analysis", ""),
            images=list(d.get("images") or []),
            videos=list(d.get("videos") or []),
            answer_images=list(d.get("answer_images") or []),
            answer_videos=list(d.get("answer_videos") or []),
            answer_digest=d.get("answer_digest", ""),
            keyword_digest=d.get("keyword_digest", ""),
            answer_hashed=bool(d.get("answer_hashed", False)),
        )

    def standard_answer_text(self) -> str:
        if self.type == MULTIPLE:
            return "".join(sorted(str(x) for x in (self.answer or [])))
        if self.type == JUDGE:
            return "正确" if str(self.answer).strip().upper() in ("A", "正确", "TRUE", "T", "1", "对") else "错误"
        return str(self.answer or "")


@dataclass
class ExamConfig:
    """母体中可配置，生成子体时固化进去。"""
    title: str = "监考系统考试"
    duration_minutes: int = 60
    counts: Dict[str, int] = field(default_factory=lambda: {t: 0 for t in QTYPES})
    points: Dict[str, float] = field(default_factory=lambda: {t: 10.0 for t in QTYPES})
    shuffle_questions: bool = True
    shuffle_options: bool = True
    pass_score: float = 60.0
    author: str = ""
    # 反作弊开关（子体启用，母体可配）
    ac_fullscreen: bool = True
    ac_block_keys: bool = True
    ac_anti_vm: bool = True
    ac_network_lock: bool = True
    ac_uac: bool = True
    # AI 判分（可插拔，兼容 OpenAI chat-completions 格式）
    ai_url: str = ""
    ai_key: str = ""
    ai_model: str = ""
    network_token_hash: str = ""   # 恢复网络令牌的 SHA256（母体生成时预置）

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "ExamConfig":
        c = ExamConfig()
        for k, v in d.items():
            if hasattr(c, k):
                setattr(c, k, v)
        # 兼容缺省
        for t in QTYPES:
            c.counts.setdefault(t, 0)
            c.points.setdefault(t, 10.0)
        return c


@dataclass
class AnswerRecord:
    question_id: str = ""
    answer: Any = ""
    correct: Optional[bool] = None
    score: float = 0.0
    full_score: float = 0.0
    ai_feedback: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ExamState:
    """子体运行时状态，增量写盘。"""
    candidate: Dict[str, str] = field(default_factory=dict)
    question_ids: List[str] = field(default_factory=list)
    answers: Dict[str, Any] = field(default_factory=dict)
    start_ts: float = 0.0          # 单调时钟（计时用，改系统时间无效）
    submit_ts: float = 0.0
    start_wall: float = 0.0        # 墙上时钟（仅显示用）
    submit_wall: float = 0.0
    events: List[Dict[str, Any]] = field(default_factory=list)
    status: str = "idle"          # idle / running / submitted
    result: List[Dict[str, Any]] = field(default_factory=list)
    total_score: float = 0.0
    full_score: float = 0.0
    report_path: str = ""

    def event(self, kind: str, detail: str = ""):
        self.events.append({
            "ts": time.time(),
            "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "kind": kind,
            "detail": detail,
        })

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "ExamState":
        s = ExamState()
        for k, v in d.items():
            if hasattr(s, k):
                setattr(s, k, v)
        return s


@dataclass
class ExamPackage:
    """子体包：题目 + 答案 + 配置 + 密钥。"""
    version: int = 2
    config: ExamConfig = field(default_factory=ExamConfig)
    questions: List[Question] = field(default_factory=list)
    secret: str = field(default_factory=new_id)   # 旧 HMAC 密钥（兼容）
    private_key: str = ""   # Ed25519 私钥 PEM —— 只放母体，绝不放子体
    public_key: str = ""    # Ed25519 公钥 PEM —— 子体里只放这个（验签用）
    answer_key: str = ""    # 答案摘要密钥 —— 子体里用于计算考生答案摘要

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "config": self.config.to_dict(),
            "questions": [q.to_dict() for q in self.questions],
            "secret": self.secret,
            "private_key": self.private_key,
            "public_key": self.public_key,
            "answer_key": self.answer_key,
        }

    @staticmethod
    def from_dict(d: dict) -> "ExamPackage":
        return ExamPackage(
            version=int(d.get("version", 2)),
            config=ExamConfig.from_dict(d.get("config", {})),
            questions=[Question.from_dict(x) for x in d.get("questions", [])],
            secret=d.get("secret") or new_id(),
            private_key=d.get("private_key", ""),
            public_key=d.get("public_key", ""),
            answer_key=d.get("answer_key", ""),
        )

    def save(self, path: str):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @staticmethod
    def load(path: str) -> "ExamPackage":
        with open(path, "r", encoding="utf-8") as f:
            return ExamPackage.from_dict(json.load(f))
