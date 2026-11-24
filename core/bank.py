"""
文件夹化题库存储。

题库 = 以考试名称命名的文件夹，结构：
  <题库根目录>/<考试名称>/
      bank.json         题库数据（题目+答案+配置）
      media/            题目附件（图片/视频）
          xxx.png
          yyy.mp4
"""
from __future__ import annotations

import os
import re
import shutil
from typing import Optional, Tuple

from .models import ExamPackage, Question

# 题库根目录：优先 E:\题库（和用户约定：题库一律存 E 盘，防丢防改）；
# E 盘不可用（如换机器）时回退到工程目录下的 题库。
_LOCAL_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "题库")
DEFAULT_ROOT = r"E:\题库" if os.path.isdir("E:\\") else _LOCAL_ROOT


def safe_name(name: str) -> str:
    """把考试名称转成合法文件夹名。"""
    name = (name or "未命名考试").strip()
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    return name[:80] or "未命名考试"


def bank_root() -> str:
    os.makedirs(DEFAULT_ROOT, exist_ok=True)
    return DEFAULT_ROOT


def list_banks() -> list[str]:
    """列出所有题库文件夹名。"""
    root = bank_root()
    return sorted(d for d in os.listdir(root)
                  if os.path.isdir(os.path.join(root, d)))


def save_bank(pkg: ExamPackage, root: Optional[str] = None) -> Tuple[str, str]:
    """
    把当前题目+配置保存成题库文件夹（以考试名称命名）。
    返回 (文件夹路径, 提示信息)。
    """
    import copy
    pkg = copy.deepcopy(pkg)  # 不修改内存中的原题库（保留绝对路径，供后续打包子体用）
    root = root or bank_root()
    folder = os.path.join(root, safe_name(pkg.config.title))
    os.makedirs(folder, exist_ok=True)
    media = os.path.join(folder, "media")
    os.makedirs(media, exist_ok=True)

    # 处理附件：把源路径的文件复制进 media，并改写为相对路径（只改副本）
    for q in pkg.questions:
        new_images = []
        for src in q.images:
            if not src or not os.path.exists(src):
                continue
            dst = os.path.join(media, os.path.basename(src))
            if os.path.abspath(src) != os.path.abspath(dst):
                shutil.copyfile(src, dst)
            new_images.append("media/" + os.path.basename(src))
        q.images = new_images
        new_videos = []
        for src in q.videos:
            if not src or not os.path.exists(src):
                continue
            dst = os.path.join(media, os.path.basename(src))
            if os.path.abspath(src) != os.path.abspath(dst):
                shutil.copyfile(src, dst)
            new_videos.append("media/" + os.path.basename(src))
        q.videos = new_videos
        new_ai = []
        for src in q.answer_images:
            if not src or not os.path.exists(src):
                continue
            dst = os.path.join(media, os.path.basename(src))
            if os.path.abspath(src) != os.path.abspath(dst):
                shutil.copyfile(src, dst)
            new_ai.append("media/" + os.path.basename(src))
        q.answer_images = new_ai
        new_av = []
        for src in q.answer_videos:
            if not src or not os.path.exists(src):
                continue
            dst = os.path.join(media, os.path.basename(src))
            if os.path.abspath(src) != os.path.abspath(dst):
                shutil.copyfile(src, dst)
            new_av.append("media/" + os.path.basename(src))
        q.answer_videos = new_av

    data_path = os.path.join(folder, "bank.json")
    pkg.save(data_path)
    return folder, "题库已保存：" + folder


def load_bank(folder: str) -> ExamPackage:
    """导入题库文件夹。"""
    data_path = os.path.join(folder, "bank.json")
    if not os.path.exists(data_path):
        raise FileNotFoundError("找不到 bank.json：" + data_path)
    pkg = ExamPackage.load(data_path)
    # 把相对路径还原为绝对路径（基于题库文件夹）
    for q in pkg.questions:
        q.images = [_abs(folder, p) for p in q.images]
        q.videos = [_abs(folder, p) for p in q.videos]
        q.answer_images = [_abs(folder, p) for p in q.answer_images]
        q.answer_videos = [_abs(folder, p) for p in q.answer_videos]
    return pkg


def _abs(folder: str, p: str) -> str:
    if os.path.isabs(p):
        return p
    return os.path.normpath(os.path.join(folder, p))


def bank_has_media(folder: str, rel: str) -> bool:
    return os.path.exists(_abs(folder, rel))
