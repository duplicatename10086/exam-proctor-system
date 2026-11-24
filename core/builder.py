"""
子体生成器：把题库(含答案)+配置固化进一个独立考生客户端。

产物目录结构：
  <outdir>/监考系统/  —— 一个 PyInstaller onefile 工程源 + 打包后的 exe
    exam_data.json     内嵌题库+答案（打包进 exe）
    child_app.py       子体主程序（读内嵌数据）
    build.bat          打包脚本
    dist/监考系统.exe  最终交付考生的子体
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from typing import Optional

from .models import ExamPackage, SINGLE, MULTIPLE, JUDGE, FILL, SUBJECTIVE
from . import security as SEC
import copy

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable


def _write_child_app(dest: str):
    src = os.path.join(ROOT, "child_app.py")
    shutil.copyfile(src, os.path.join(dest, "child_app.py"))
    # 复制 core 包供打包
    core_dst = os.path.join(dest, "core")
    if os.path.exists(core_dst):
        shutil.rmtree(core_dst)
    shutil.copytree(os.path.join(ROOT, "core"), core_dst)


def build_child(pkg: ExamPackage, outdir: str, icon_path: str) -> str:
    """生成并打包子体；返回最终 exe 路径。"""
    name = "监考系统"
    proj = os.path.join(outdir, name)
    os.makedirs(proj, exist_ok=True)

    # 0. 加固：确保有密钥对与答案摘要密钥（母体持有私钥，子体绝不含私钥）
    if not pkg.private_key or not pkg.public_key:
        priv, pub = SEC.gen_keypair()
        pkg.private_key, pkg.public_key = priv, pub
    if not pkg.answer_key:
        pkg.answer_key = SEC.b64e(os.urandom(24))
    akey = pkg.answer_key.encode("ascii")

    # 1. 收集并打包题目附件（图片/视频）到 media 目录
    media_dir = os.path.join(proj, "media")
    os.makedirs(media_dir, exist_ok=True)
    pkg_child = copy.deepcopy(pkg)
    def _collect(paths):
        out = []
        for src in paths:
            if src and os.path.exists(src):
                dst = os.path.join(media_dir, os.path.basename(src))
                if os.path.abspath(src) != os.path.abspath(dst):
                    shutil.copyfile(src, dst)
                out.append("media/" + os.path.basename(src))
        return out

    for q in pkg_child.questions:
        q.images = _collect(q.images)
        q.videos = _collect(q.videos)
        q.answer_images = _collect(q.answer_images)
        q.answer_videos = _collect(q.answer_videos)
        # 答案加固：明文 → HMAC 摘要；填空/主观关键词 → 摘要 JSON
        if q.type in (SINGLE, MULTIPLE, JUDGE):
            q.answer_digest = SEC.digest_answer(q.answer, akey)
            q.answer = ""          # 清除明文答案
        elif q.type in (FILL, SUBJECTIVE):
            q.keyword_digest = SEC.build_keyword_digests(str(q.answer), akey)
            q.answer = ""          # 清除明文关键词
        q.answer_hashed = True

    # AI key 绝不下发到子体（否则考生盗刷）；无 key 时用关键词近似评分
    pkg_child.config.ai_key = ""
    pkg_child.config.ai_url = ""

    # 恢复网络令牌：母体随机生成，明文只写在出题人文件里，子体里只放摘要
    net_token = SEC.make_token()
    pkg_child.config.network_token_hash = SEC.token_hash(net_token)
    with open(os.path.join(proj, "监考令牌_请妥善保管.txt"), "w", encoding="utf-8") as f:
        f.write("恢复网络令牌（仅凭此令牌考生才能申请恢复网络）:\n" + net_token + "\n")

    # 子体需要给报告签名，因此必须持有一个签名私钥；做 base64 混淆后嵌入
    # （不是明文 PEM，strings 扫不到）。验签公钥单独给出题人保管。
    pkg_child.private_key = SEC.b64e(pkg.private_key.encode("ascii"))
    pkg_child.save(os.path.join(proj, "exam_data.json"))

    # 出题人验签公钥（用它可以验证子体产出的报告未被篡改）
    with open(os.path.join(proj, "出题人验签公钥_请妥善保管.pem"), "w", encoding="utf-8") as f:
        f.write(pkg.public_key)
    with open(os.path.join(proj, "出题人验签工具.py"), "w", encoding="utf-8") as f:
        f.write(
            "# -*- coding: utf-8 -*-\n"
            "# 用法: python 出题人验签工具.py 报告文件.json\n"
            "import sys, json\n"
            "sys.path.insert(0, '.')\n"
            "from core import security as SEC\n"
            "pub = open('出题人验签公钥_请妥善保管.pem', encoding='utf-8').read()\n"
            "p = sys.argv[1] if len(sys.argv)>1 else input('报告路径: ').strip()\n"
            "print('报告完整，签名有效 ✓' if SEC.verify_report_file(p, pub) else '报告已被篡改 ✗')\n"
        )
    # 出题人复核答案对照表（明文，仅母体留存，不打包进 exe）
    with open(os.path.join(proj, "出题人答案对照表_请勿分发.json"), "w", encoding="utf-8") as f:
        json.dump([{"id": q.id, "type": q.type, "stem": q.stem,
                    "answer": q.answer, "points": q.points} for q in pkg.questions],
                  f, ensure_ascii=False, indent=2)

    # 2. 复制子体源码 + core
    _write_child_app(proj)

    # 3. 图标
    icon_dst = os.path.join(proj, "icon.ico")
    if icon_path and os.path.exists(icon_path):
        shutil.copyfile(icon_path, icon_dst)
    else:
        icon_dst = ""

    # 4. 打包脚本
    icon_opt = ('--icon "%s"' % icon_dst) if icon_dst else ""
    media_opt = '--add-data "media;."' if os.listdir(media_dir) else ""
    bat = f"""@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在打包考生客户端（子体）...
"{PY}" -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name "{name}" ^
  {icon_opt} ^
  --add-data "exam_data.json;." ^
  {media_opt} ^
  --hidden-import core ^
  child_app.py
echo.
echo 完成！exe 位于 dist\\{name}.exe
pause
"""
    with open(os.path.join(proj, "build.bat"), "w", encoding="utf-8") as f:
        f.write(bat)

    # 5. README
    with open(os.path.join(proj, "使用说明.txt"), "w", encoding="utf-8") as f:
        f.write(
            "监考系统 · 考生客户端（子体）\n"
            "==============================\n\n"
            "本程序由母体生成，内置题库与标准答案，考生在本机运行。\n\n"
            "运行 dist\\监考系统.exe 即可开始考试。\n"
            "流程：填写信息 → 抽题 → 计时答题 → 交卷 → AI 判分 → 生成只读报告。\n\n"
            "防作弊说明（运行前会明示）：\n"
            "  - 全屏置顶，屏蔽任务切换快捷键（Alt+Tab / Win 等）\n"
            "  - 反虚拟机检测、记录切屏与违规按键\n"
            "  - 普通题阶段锁网，实操题阶段可申请恢复网络\n"
            "  - 需要管理员权限（网卡控制 / 键盘钩子）\n"
            "  - 退出时自动恢复网络与键盘，全程写事件日志\n\n"
            "状态保存在本机 state.json；交卷后生成带 HMAC 签名的只读报告\n"
            "报告_<姓名>_<时间>.json，任何修改都会被校验发现。\n"
        )

    # 6. 尝试直接打包（如果装了 PyInstaller）
    exe = os.path.join(proj, "dist", name + ".exe")
    try:
        import PyInstaller  # noqa
        cmd = [PY, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile",
               "--windowed", "--name", name]
        if icon_dst:
            cmd += ["--icon", icon_dst]
        sep = ";" if os.name == "nt" else ":"
        cmd += ["--add-data", "exam_data.json" + sep + "."]
        if os.listdir(media_dir):
            cmd += ["--add-data", "media" + sep + "."]
        cmd += ["--hidden-import", "core", "child_app.py"]
        subprocess.run(cmd, cwd=proj, check=True)
        return exe
    except Exception as e:
        # 未装 PyInstaller：生成 bat 让用户自行打包
        return os.path.join(proj, "build.bat")
