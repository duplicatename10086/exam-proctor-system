"""判分：客观题按内置答案比对；填空/主观题按关键词（含近义词）命中数量给分。"""
from __future__ import annotations

import json
import re
from typing import List

from .models import (
    AnswerRecord, ExamConfig, Question, SINGLE, MULTIPLE, JUDGE, FILL, SUBJECTIVE,
)
from . import security as SEC

# 关键词分隔符：逗号/分号/顿号/竖线（竖线用于近义词，见下）
_KW_SPLIT = re.compile(r"[,，;；、\n]+")
_SYN_SPLIT = re.compile(r"[|/｜／]")


def grade_objective(q: Question, ans, answer_key: str = "") -> tuple[bool, float]:
    """客观题判分。摘要模式下只比对 HMAC 摘要，明文答案不随子体分发。"""
    if q.answer_hashed and answer_key:
        cand = SEC.digest_answer_candidate(ans, answer_key.encode())
        correct = SEC.hmac.compare_digest(cand, q.answer_digest)
    else:
        if q.type == SINGLE:
            correct = str(ans or "").strip().upper() == str(q.answer or "").strip().upper()
        elif q.type == MULTIPLE:
            a = sorted(str(x).strip().upper() for x in (ans or []))
            b = sorted(str(x).strip().upper() for x in (q.answer or []))
            correct = a == b
        elif q.type == JUDGE:
            correct = str(ans or "").strip().upper()[:1] == str(q.answer or "").strip().upper()[:1]
        else:
            correct = False
    return correct, (q.points if correct else 0.0)


def parse_keywords(answer: str) -> list[list[str]]:
    """
    把答案串解析成关键词组。
      输入: "算法,步骤|流程,有限"
      输出: [["算法"], ["步骤","流程"], ["有限"]]   （每项为一组近义词）
    """
    groups = []
    for seg in _KW_SPLIT.split(str(answer or "")):
        seg = seg.strip()
        if not seg:
            continue
        synonyms = [s.strip() for s in _SYN_SPLIT.split(seg) if s.strip()]
        if synonyms:
            groups.append(synonyms)
    return groups


def keyword_grade(q: Question, ans, cfg: ExamConfig = None,
                  answer_key: str = "") -> tuple[float, str]:
    """
    填空/主观题：按关键词命中数量给分。
    摘要模式：明文关键词不随子体分发，用 HMAC 摘要滑动匹配。
    """
    text = str(ans or "").strip()
    if q.answer_hashed and q.keyword_digest and answer_key:
        hit, total = SEC.keyword_hit_count(q.keyword_digest, text, answer_key.encode())
        if total == 0:
            return 0.0, "未设置评分关键词"
        score = round(float(q.points) * hit / total, 1)
        fb = "命中 %d/%d 个关键词要点" % (hit, total)
    else:
        groups = parse_keywords(q.answer)
        if not groups:
            return 0.0, "未设置评分关键词"
        hit, miss = [], []
        for syns in groups:
            if any(s.lower() in text.lower() for s in syns):
                hit.append("/".join(syns))
            else:
                miss.append("/".join(syns))
        score = round(float(q.points) * len(hit) / len(groups), 1)
        fb = "命中 %d/%d 个关键词：%s" % (len(hit), len(groups), "、".join(hit))
        if miss:
            fb += "；缺少：" + "、".join(miss)

    # AI 可选增强（评语）；摘要模式下不把明文关键词发给 AI
    if cfg and cfg.ai_url and cfg.ai_key:
        ai_fb = _ai_feedback(q, text, fb, cfg, expose_keywords=not (q.answer_hashed and answer_key))
        if ai_fb:
            fb += "。" + ai_fb
    return score, fb


def _ai_feedback(q: Question, text: str, kw_fb: str, cfg: ExamConfig,
                 expose_keywords: bool = True) -> str:
    kw = q.answer if expose_keywords else "（已混淆，按关键词评分结果）"
    try:
        import requests
        r = requests.post(
            cfg.ai_url,
            headers={"Authorization": "Bearer " + cfg.ai_key,
                     "Content-Type": "application/json"},
            json={"model": cfg.ai_model or "gpt-4o-mini",
                  "messages": [{"role": "user", "content":
                      "你是阅卷老师。题目：%s\n参考答案关键词：%s\n考生答案：%s\n关键词评分：%s\n请用一句中文简评考生答案质量。" %
                      (q.stem, kw, text, kw_fb)}],
                  "temperature": 0},
            timeout=30,
        )
        return r.json()["choices"][0]["message"]["content"].strip()[:120]
    except Exception:
        return ""


def grade_all(questions: List[Question], answers: dict, cfg: ExamConfig,
              answer_key: str = "") -> tuple[List[dict], float, float]:
    """返回 (结果列表, 总分, 满分)。answer_key: 答案摘要密钥。"""
    results: List[dict] = []
    full = sum(q.points for q in questions)
    total = 0.0

    for q in questions:
        ans = answers.get(q.id, "")
        rec = AnswerRecord(question_id=q.id, answer=ans, full_score=q.points)
        if q.type in (SINGLE, MULTIPLE, JUDGE):
            ok, sc = grade_objective(q, ans, answer_key)
            rec.correct = ok
            rec.score = sc
            rec.ai_feedback = "正确" if ok else "答案错误"
        else:
            sc, fb = keyword_grade(q, ans, cfg, answer_key)
            rec.score = sc
            rec.ai_feedback = fb
            rec.correct = sc >= q.points * 0.6
        total += rec.score
        results.append(rec.to_dict())

    return (results, round(total, 1), round(full, 1))
