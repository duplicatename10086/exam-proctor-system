# -*- coding: utf-8 -*-
"""
监考系统 —— 子体（考生用）
母体生成的内置题库客户端。流程：信息→抽题→计时→交卷→AI判分→只读报告。
"""
from __future__ import annotations

import json
import os
import random
import sys
import time
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

HERE = os.path.dirname(os.path.abspath(__file__))
# PyInstaller onefile 解压目录
BUNDLE = getattr(sys, "_MEIPASS", HERE)
sys.path.insert(0, BUNDLE)
sys.path.insert(0, HERE)

from core.models import (ExamPackage, ExamConfig, ExamState, Question, QTYPES,
                         QTYPE_LABELS, SINGLE, MULTIPLE, JUDGE, FILL, SUBJECTIVE)
from core.storage import StateStore, build_report, write_report, verify_report
from core.grader import grade_all
from core import anticheat
from core import ui_theme as TH
from core import security as SEC

DATA_FILE = os.path.join(BUNDLE, "exam_data.json")
if not os.path.exists(DATA_FILE):
    DATA_FILE = os.path.join(HERE, "exam_data.json")

STATE_FILE = os.path.join(os.path.expanduser("~"), "监考系统_state.json")
ICON = os.path.join(BUNDLE, "icon.ico")
WHITE_C = "#FFFFFF"


class ExamApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("监考系统")
        self.root.geometry("1024x740")
        TH.setup(self.root)
        if os.path.exists(ICON):
            try:
                self.root.iconbitmap(ICON)
            except Exception:
                pass
        self.pkg = ExamPackage.load(DATA_FILE)
        # 题目附件相对路径 → 打包目录绝对路径
        for q in self.pkg.questions:
            q.images = [self._media_abs(p) for p in q.images]
            q.videos = [self._media_abs(p) for p in q.videos]
            q.answer_images = [self._media_abs(p) for p in q.answer_images]
            q.answer_videos = [self._media_abs(p) for p in q.answer_videos]
        self.cfg: ExamConfig = self.pkg.config
        # 加固：报告签名私钥（base64 混淆）、答案摘要密钥、验签公钥
        self.priv_pem = ""
        if self.pkg.private_key:
            try:
                self.priv_pem = SEC.b64d(self.pkg.private_key).decode("ascii")
            except Exception:
                self.priv_pem = self.pkg.private_key
        self.answer_key = self.pkg.answer_key
        self.verify_pub = self.pkg.public_key
        self.clock = SEC.MonotonicClock()
        self.store = StateStore(STATE_FILE, pub_pem=self.answer_key or "proctor")
        self.state = self.store.load() or ExamState()
        self.state.candidate["_exam_title"] = self.cfg.title
        self.state.candidate["_pass_score"] = self.cfg.pass_score
        anticheat.set_log_callback(self._ac_event)

        self.net = anticheat.NetworkLock(enabled=self.cfg.ac_network_lock)
        self.kbd = anticheat.KeyboardGuard(enabled=self.cfg.ac_block_keys)
        self.kbd.set_violation_callback(lambda v: self._ac_event("key_violation", v))
        # 本地 AI 服务监测（防用惊云/Ollama 等本地模型查答案）
        self.ai_watcher = anticheat.LocalAIWatcher(on_detect=self._on_local_ai)
        self.local_ai_signs = []   # 本次考试检测到的 AI 服务迹象
        self.vm = {"suspected": False, "signs": [], "hardware": ""}
        self._tick_id = None
        self._answers_vars = {}
        self._qwidget = None
        self._restore_state()

        self._build_intro()

    def _media_abs(self, p):
        if not p:
            return p
        if os.path.isabs(p):
            return p
        cand = os.path.join(BUNDLE, p.replace("/", os.sep))
        if os.path.exists(cand):
            return cand
        cand2 = os.path.join(HERE, p.replace("/", os.sep))
        return cand2 if os.path.exists(cand2) else cand

    # ---------- 反作弊事件回调 ----------
    def _ac_event(self, kind, detail=""):
        self.state.event(kind, detail)

    def _on_local_ai(self, info: dict):
        """检测到本地 AI 服务：记录 + 醒目告警（不打断答题）。"""
        signs = info.get("signs", [])
        self.local_ai_signs.extend(signs)
        self.state.event("local_ai_detected", "; ".join(signs))
        self._save()
        # 弹一次醒目告警（每个新迹象都提示，但 watcher 已去重）
        try:
            messagebox.showwarning(
                "反作弊警告",
                "检测到本机正在运行本地 AI 模型服务（如惊云/Ollama/LM Studio）。\n"
                "考试期间使用 AI 辅助作答属于违规行为，已记入报告：\n\n"
                + "\n".join("• " + s for s in signs))
        except Exception:
            pass

    def _upload_answer_media(self, qid=None):
        """考生为当前题上传作答附件（图片/视频），按题存储，附进报告。"""
        from tkinter import filedialog
        if qid is None:
            qid = self._questions[self._cur_idx].id
        paths = filedialog.askopenfilenames(
            title="选择本题作答附件（可多选）",
            filetypes=[("图片/视频", "*.png *.jpg *.jpeg *.gif *.bmp *.webp *.mp4 *.avi *.mov *.mkv *.webm")])
        if not paths:
            return
        mapping = dict(self.state.candidate.get("_answer_media", {}))
        cur = list(mapping.get(qid, []))
        cur.extend(paths)
        mapping[qid] = cur
        self.state.candidate["_answer_media"] = mapping
        self._save()
        self._refresh_upload_lbl()
        messagebox.showinfo("提示", "已为本题上传 %d 个作答附件。" % len(paths))

    def _refresh_upload_lbl(self):
        if not hasattr(self, "upload_lbl"):
            return
        qid = self._questions[self._cur_idx].id
        n = len(dict(self.state.candidate.get("_answer_media", {})).get(qid, []))
        self.upload_lbl.configure(text=("本题已上传 %d 个附件" % n) if n else "本题暂无作答附件")

    # ---------- 断点续考 ----------
    def _restore_state(self):
        if self.state.status == "running" and self.state.question_ids:
            self.resume = True
        else:
            self.resume = False

    # ---------- 界面：欢迎/信息采集 ----------
    def _build_intro(self):
        self._clear()
        # 顶部蓝色标题条
        TH.header(self.root, "监考系统", "考生客户端 · 请如实填写信息后开始")
        # 页面容器
        page = ttk.Frame(self.root, style="BG.TFrame", padding=20)
        page.pack(fill="both", expand=True)

        hero = TH.card(page, padding=18)
        hero.pack(fill="x")
        ttk.Label(hero, text=self.cfg.title, style="Stem.TLabel",
                  font=(TH.FONT, 18, "bold")).pack(anchor="w")
        ttk.Label(hero, text="时长 %d 分钟   •   及格 %g 分   •   共 %d 题" % (
            self.cfg.duration_minutes, self.cfg.pass_score,
            sum(self.cfg.counts.values())), style="Sub.TLabel").pack(anchor="w", pady=(4, 0))

        info = ttk.LabelFrame(page, text="考生信息", style="Card.TLabelframe", padding=14)
        info.pack(fill="x", pady=(14, 8))
        self.fields = {}
        for i, (k, label) in enumerate([("name", "姓名"), ("id", "学号 / 工号"), ("class", "班级 / 部门")]):
            ttk.Label(info, text=label, style="Card.TLabel").grid(row=i, column=0, sticky="e", padx=10, pady=7)
            e = ttk.Entry(info, width=34, style="Card.TEntry")
            e.grid(row=i, column=1, sticky="w", pady=7)
            if self.state.candidate.get(k):
                e.insert(0, self.state.candidate[k])
            self.fields[k] = e

        guards = [
            ("全屏锁定 + 隐藏任务栏", self.cfg.ac_fullscreen),
            ("键盘钩子屏蔽切换快捷键", self.cfg.ac_block_keys),
            ("反虚拟机检测", self.cfg.ac_anti_vm),
            ("普通题阶段锁定网络", self.cfg.ac_network_lock),
            ("申请管理员权限", self.cfg.ac_uac),
        ]
        gf = ttk.LabelFrame(page, text="防护声明", style="Card.TLabelframe", padding=12)
        gf.pack(fill="x", pady=6)
        ttk.Label(gf, text="本场考试将启用以下防护，退出时自动恢复；考试全程记录。",
                  style="Sub.TLabel").pack(anchor="w", pady=(0, 6))
        for name, on in guards:
            ttk.Label(gf, text=("  ✓  " if on else "  ×  ") + name,
                      style="OK.TLabel" if on else "Sub.TLabel").pack(anchor="w", pady=1)

        btns = ttk.Frame(page, style="BG.TFrame")
        btns.pack(fill="x", pady=(16, 0))
        ttk.Button(btns, text="同意并开始考试", style="Primary.TButton",
                   command=self._start_exam).pack(side="left")
        if self.resume:
            ttk.Button(btns, text="恢复上次未交卷的考试", style="Ghost.TButton",
                       command=self._resume_exam).pack(side="left", padx=10)

    # ---------- 开始考试 ----------
    def _start_exam(self):
        for k, e in self.fields.items():
            self.state.candidate[k] = e.get().strip()
        if not self.state.candidate.get("name"):
            messagebox.showwarning("提示", "请填写姓名"); return

        # UAC 提权
        if self.cfg.ac_uac and not anticheat.is_admin():
            if not anticheat.ensure_admin():
                messagebox.showwarning("提示", "未获得管理员权限，部分防护将不可用。")

        # 明示防护已启用
        anticheat.protect_process()
        self.vm = anticheat.detect_vm() if self.cfg.ac_anti_vm else {"suspected": False, "signs": [], "hardware": ""}
        self.state.event("anti_vm", "虚拟机嫌疑=%s 迹象=%s" % (self.vm.get("suspected"), self.vm.get("signs")))
        if self.vm.get("suspected"):
            messagebox.showwarning("反作弊提醒", "检测到虚拟机环境，本次考试将被标记。")

        # 全屏 + 键盘钩子 + 断网
        if self.cfg.ac_fullscreen:
            anticheat.make_fullscreen(self.root)
        if self.cfg.ac_block_keys:
            if not self.kbd.hook():
                messagebox.showwarning("提示", "键盘钩子安装失败（可能权限不足）。")
        if self.cfg.ac_network_lock:
            self.net.disable()
            self.state.event("network_lock", "普通题阶段网络已锁定")
        # 启动本地 AI 服务监测 + 立即检测一次
        self.ai_watcher.start()
        try:
            initial_ai = anticheat.detect_local_ai()
            if initial_ai["suspected"]:
                for s in initial_ai["signs"]:
                    if s not in self.local_ai_signs:
                        self.local_ai_signs.append(s)
                        self.state.event("local_ai_detected", s)
                messagebox.showwarning("反作弊提醒",
                    "开局检测到本机正在运行本地 AI 模型服务，本次考试将被标记：\n\n"
                    + "\n".join("• " + s for s in initial_ai["signs"]))
        except Exception:
            pass

        # 抽题
        self._draw_questions()
        self.state.status = "running"
        self.state.start_ts = self.clock.now()
        self.state.start_wall = time.time()
        self.state.answers = {}
        self._save()
        self._build_exam_ui()

    def _resume_exam(self):
        anticheat.protect_process()
        if self.cfg.ac_fullscreen:
            anticheat.make_fullscreen(self.root)
        if self.cfg.ac_block_keys:
            self.kbd.hook()
        self.ai_watcher.start()
        self._build_exam_ui()

    # ---------- 抽题 ----------
    def _draw_questions(self):
        picked = []
        rng = random.Random()
        for t in QTYPES:
            pool = [q for q in self.pkg.questions if q.type == t]
            n = min(self.cfg.counts.get(t, 0), len(pool))
            if n > 0:
                picked += rng.sample(pool, n)
        if self.cfg.shuffle_questions:
            rng.shuffle(picked)
        self._questions = picked
        self.state.question_ids = [q.id for q in picked]
        for q in picked:
            q.points = self.cfg.points.get(q.type, q.points)
        self._qmap = {q.id: q for q in picked}

    # ---------- 考试界面 ----------
    def _build_exam_ui(self):
        self._clear()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # 顶部蓝色条：标题 + 计时 + 进度
        top = ttk.Frame(self.root, style="Header.TFrame", height=64)
        top.pack(fill="x", side="top")
        top.pack_propagate(False)
        ttk.Label(top, text=self.cfg.title, style="Title.TLabel").pack(side="left", padx=20)
        self.time_var = tk.StringVar()
        self.time_lbl = tk.Label(top, textvariable=self.time_var, font=("Consolas", 18, "bold"),
                                 fg=WHITE_C, bg=TH.PRIMARY_DK, padx=12, pady=4, relief="flat")
        self.time_lbl.pack(side="right", padx=16, pady=10)
        self.progress_var = tk.StringVar()
        tk.Label(top, textvariable=self.progress_var, font=(TH.FONT, 11),
                 fg="#DBEAFE", bg=TH.PRIMARY).pack(side="right", padx=8)

        body = ttk.Frame(self.root, style="BG.TFrame")
        body.pack(fill="both", expand=True, padx=12, pady=10)

        # 左侧答题卡（白卡片）
        left = TH.card(body, padding=10)
        left.pack(side="left", fill="y")
        ttk.Label(left, text="答题卡", style="Card.TLabel",
                  font=(TH.FONT, 12, "bold")).pack(anchor="w", pady=(0, 8))
        self.nav = ttk.Frame(left, style="Card.TFrame")
        self.nav.pack(fill="y")
        self.nav_btns = []
        for i, q in enumerate(self._questions):
            b = tk.Button(self.nav, text=str(i+1), width=3,
                          font=(TH.FONT, 10, "bold"), relief="flat",
                          bg=TH.NAV_BG, fg=TH.TEXT,
                          activebackground=TH.NAV_DONE,
                          command=lambda qq=q: self._show_question(qq))
            b.grid(row=i//5, column=i%5, padx=3, pady=3)
            self.nav_btns.append(b)
        # 图例
        legend = ttk.Frame(left, style="Card.TFrame")
        legend.pack(side="bottom", pady=(10, 0))
        tk.Label(legend, text="■ 当前", fg=TH.NAV_CUR, bg=TH.WHITE, font=(TH.FONT, 8)).pack(anchor="w")
        tk.Label(legend, text="■ 已答", fg=TH.PRIMARY, bg=TH.WHITE, font=(TH.FONT, 8)).pack(anchor="w")
        tk.Label(legend, text="■ 未答", fg=TH.TEXT_SUB, bg=TH.WHITE, font=(TH.FONT, 8)).pack(anchor="w")

        # 右侧题目区（白卡片）
        right = TH.card(body, padding=0)
        right.pack(side="left", fill="both", expand=True, padx=(12, 0))
        self.q_frame = ttk.Frame(right, style="Card.TFrame")
        self.q_frame.pack(fill="both", expand=True, padx=18, pady=16)

        # 底部按钮栏
        btnbar = ttk.Frame(right, style="Card.TFrame")
        btnbar.pack(fill="x", padx=18, pady=(0, 14))
        ttk.Button(btnbar, text="◀ 上一题", style="Ghost.TButton",
                   command=self._prev_q).pack(side="left")
        ttk.Button(btnbar, text="下一题 ▶", style="Primary.TButton",
                   command=self._next_q).pack(side="left", padx=8)
        ttk.Button(btnbar, text="上传本题作答附件", style="Ghost.TButton",
                   command=self._upload_answer_media).pack(side="left", padx=8)
        self.upload_lbl = ttk.Label(btnbar, text="", style="Sub.TLabel")
        self.upload_lbl.pack(side="left", padx=4)
        if self.cfg.ac_network_lock:
            self.net_btn = ttk.Button(btnbar, text="申请恢复网络（实操题）",
                                      style="Ghost.TButton", command=self._request_network)
            self.net_btn.pack(side="left", padx=8)
        ttk.Button(btnbar, text="交卷", style="Danger.TButton",
                   command=self._submit).pack(side="right")

        self._cur_idx = 0
        if self._questions:
            self._show_question(self._questions[0])
        self._tick()

    def _show_question(self, q):
        self._cur_idx = self._questions.index(q)
        for w in self.q_frame.winfo_children():
            w.destroy()
        self._answers_vars = {}

        # 题型徽章 + 题号
        badge = tk.Frame(self.q_frame, bg=TH.PRIMARY_LT)
        badge.pack(anchor="w", pady=(0, 8))
        tk.Label(badge, text="  " + QTYPE_LABELS[q.type] + "  ",
                 font=(TH.FONT, 10, "bold"), fg=TH.PRIMARY_DK, bg=TH.PRIMARY_LT,
                 padx=4, pady=3).pack(side="left")
        tk.Label(self.q_frame, text="第 %d / %d 题" % (self._cur_idx+1, len(self._questions)),
                 font=(TH.FONT, 10), fg=TH.TEXT_SUB, bg=TH.WHITE).pack(side="top", anchor="w")
        tk.Label(self.q_frame, text="（%g 分）" % q.points,
                 font=(TH.FONT, 10), fg=TH.PRIMARY, bg=TH.WHITE).pack(anchor="w", pady=(2, 4))

        # 题干卡片
        stem_card = tk.Frame(self.q_frame, bg=TH.BG_SOFT, padx=12, pady=10)
        stem_card.pack(fill="x", pady=(0, 12))
        tk.Label(stem_card, text=q.stem, wraplength=640, justify="left",
                 font=(TH.FONT, 12), fg=TH.TEXT, bg=TH.BG_SOFT).pack(anchor="w")

        # 题目附件（图片/视频）
        media = [p for p in (list(q.images) + list(q.videos)) if p and os.path.exists(p)]
        if media:
            mcard = tk.Frame(self.q_frame, bg=TH.WHITE, padx=10, pady=6)
            mcard.pack(fill="x", pady=(0, 10))
            ttk.Label(mcard, text="题目附件：", style="Sub.TLabel").pack(anchor="w")
            for p in media:
                is_img = p.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"))
                if is_img:
                    try:
                        from PIL import Image, ImageTk
                        im = Image.open(p); im.thumbnail((320, 240))
                        photo = ImageTk.PhotoImage(im)
                        lbl = tk.Label(mcard, image=photo, bg=TH.WHITE, cursor="hand2")
                        lbl.image = photo  # 保持引用
                        lbl.pack(anchor="w", pady=3)
                    except Exception:
                        tk.Label(mcard, text="[图片] " + os.path.basename(p),
                                 fg=TH.PRIMARY, bg=TH.WHITE).pack(anchor="w")
                else:
                    def _openvideo(path=p):
                        try:
                            os.startfile(path)
                        except Exception:
                            pass
                    tk.Button(mcard, text="▶ 播放 " + os.path.basename(p),
                              fg=TH.PRIMARY, bg=TH.BG_SOFT, relief="flat",
                              font=(TH.FONT, 9), command=_openvideo).pack(anchor="w", pady=3)

        opts = list(q.options)
        if self.cfg.shuffle_options and q.type in (SINGLE, MULTIPLE):
            # 保留 A/B/C/D 标签，只打乱内容
            import string
            labels = [string.ascii_uppercase[i] for i in range(len(opts))]
            pairs = list(zip(labels, opts))
            random.shuffle(pairs)
            opts = ["%s. %s" % (l, o.split(".", 1)[-1].strip() if "." in o else o) for l, o in pairs]

        cur = self.state.answers.get(q.id)
        if q.type in (SINGLE, JUDGE):
            v = tk.StringVar(value=str(cur) if cur else "")
            self._answers_vars[q.id] = v
            for o in opts:
                key = o.split(".", 1)[0].strip().upper()
                tk.Radiobutton(self.q_frame, text=o, variable=v, value=key,
                               font=(TH.FONT, 11), fg=TH.TEXT, bg=TH.WHITE,
                               selectcolor=TH.PRIMARY_LT, activebackground=TH.WHITE,
                               anchor="w", command=lambda: (self._save_answer(q), self._refresh_nav())).pack(
                                   fill="x", anchor="w", padx=16, pady=3)
        elif q.type == MULTIPLE:
            cur_list = cur if isinstance(cur, list) else []
            vars_ = {}
            for o in opts:
                key = o.split(".", 1)[0].strip().upper()
                vv = tk.BooleanVar(value=key in cur_list)
                vars_[key] = vv
                tk.Checkbutton(self.q_frame, text=o, variable=vv,
                               font=(TH.FONT, 11), fg=TH.TEXT, bg=TH.WHITE,
                               selectcolor=TH.PRIMARY_LT, activebackground=TH.WHITE,
                               anchor="w", command=lambda: (self._save_answer(q), self._refresh_nav())).pack(
                                   fill="x", anchor="w", padx=16, pady=3)
            self._answers_vars[q.id] = vars_
        elif q.type == FILL:
            t = tk.Text(self.q_frame, height=3, wrap="word", font=(TH.FONT, 11),
                        fg=TH.TEXT, bg=TH.WHITE, relief="solid", bd=1,
                        highlightbackground=TH.BORDER, highlightcolor=TH.PRIMARY,
                        highlightthickness=1, padx=8, pady=6)
            t.pack(fill="x", padx=16, pady=6)
            if cur:
                t.insert("1.0", str(cur))
            t.bind("<KeyRelease>", lambda e: (self._save_answer(q), self._refresh_nav()))
            self._answers_vars[q.id] = t
        elif q.type == SUBJECTIVE:
            t = tk.Text(self.q_frame, height=9, wrap="word", font=(TH.FONT, 11),
                        fg=TH.TEXT, bg=TH.WHITE, relief="solid", bd=1,
                        highlightbackground=TH.BORDER, highlightcolor=TH.PRIMARY,
                        highlightthickness=1, padx=10, pady=8)
            t.pack(fill="both", expand=True, padx=16, pady=6)
            if cur:
                t.insert("1.0", str(cur))
            t.bind("<KeyRelease>", lambda e: (self._save_answer(q), self._refresh_nav()))
            self._answers_vars[q.id] = t

        self.progress_var.set("第 %d/%d 题" % (self._cur_idx+1, len(self._questions)))
        self._refresh_nav()
        self._refresh_upload_lbl()

    def _refresh_nav(self):
        """答题卡颜色：当前 / 已答 / 未答。"""
        if not hasattr(self, "nav_btns"):
            return
        for i, b in enumerate(self.nav_btns):
            q = self._questions[i]
            if i == self._cur_idx:
                b.configure(bg=TH.NAV_CUR, fg=TH.WHITE)
            elif self.state.answers.get(q.id):
                b.configure(bg=TH.NAV_DONE, fg=TH.PRIMARY_DK)
            else:
                b.configure(bg=TH.NAV_BG, fg=TH.TEXT_SUB)

    def _save_answer(self, q=None):
        if q is None:
            q = self._questions[self._cur_idx]
        v = self._answers_vars.get(q.id)
        if v is None:
            return
        if q.type in (SINGLE, JUDGE):
            ans = v.get()
        elif q.type == MULTIPLE:
            ans = [k for k, b in v.items() if b.get()]
        else:
            ans = v.get("1.0", "end").strip()
        self.state.answers[q.id] = ans
        self._save()

    def _prev_q(self):
        if self._cur_idx > 0:
            self._save_answer()
            self._show_question(self._questions[self._cur_idx-1])

    def _next_q(self):
        if self._cur_idx < len(self._questions)-1:
            self._save_answer()
            self._show_question(self._questions[self._cur_idx+1])

    def _request_network(self):
        # 令牌校验：母体预置的一次性令牌（摘要），考生端只能核对不能凭空生成
        expect_hash = getattr(self.cfg, "network_token_hash", "") if hasattr(self.cfg, "network_token_hash") else ""
        if expect_hash:
            used = self.state.candidate.get("_net_token_used", False)
            if used:
                messagebox.showwarning("提示", "恢复网络令牌已使用，本次考试不可再次恢复。")
                return
            token = simpledialog.askstring("恢复网络", "请输入监考人提供的恢复网络令牌：", show="*")
            if not token or SEC.token_hash(token) != expect_hash:
                messagebox.showerror("错误", "令牌无效，请联系监考人。")
                self.state.event("network_token_fail", "考生输入了错误的恢复网络令牌")
                self._save()
                return
            self.state.candidate["_net_token_used"] = True
        if not messagebox.askyesno("申请恢复网络", "恢复网络后，之前的题目将被锁定，仅当前及之后的实操题可作答，考试继续计时。是否继续？"):
            return
        ok = self.net.restore()
        self.state.event("network_restore", "考生凭令牌申请并获准恢复网络")
        messagebox.showinfo("提示", "网络已恢复。" if ok else "恢复网络失败，请确认以管理员身份运行，或手动执行 ncpa.cpl 启用网卡。")

    def _tick(self):
        remain = int(self.cfg.duration_minutes*60 - (self.clock.now()-self.state.start_ts))
        if remain <= 0:
            self.time_var.set("00:00")
            messagebox.showwarning("时间到", "考试时间结束，系统将自动交卷。")
            self._submit(auto=True)
            return
        self.time_var.set("%02d:%02d" % (remain//60, remain%60))
        self._tick_id = self.root.after(1000, self._tick)

    # ---------- 交卷 & 判分 ----------
    def _submit(self, auto=False):
        if not messagebox.askyesno("交卷确认", "确定交卷吗？交卷后将自动判分且不可再修改。" if not auto else "考试时间到，系统自动交卷。"):
            if auto:
                self.root.after(1000, lambda: self._submit(auto=True))
            return
        self._save_answer()
        if self._tick_id:
            self.root.after_cancel(self._tick_id)
        self.state.submit_ts = self.clock.now()
        self.state.submit_wall = time.time()
        self.state.status = "submitted"
        self.state.event("submit", "自动交卷" if auto else "考生主动交卷")
        self.ai_watcher.stop()
        # 交卷瞬间再做一次 AI 服务检测
        try:
            final_ai = anticheat.detect_local_ai()
            if final_ai["suspected"]:
                for s in final_ai["signs"]:
                    if s not in self.local_ai_signs:
                        self.local_ai_signs.append(s)
                        self.state.event("local_ai_detected", s)
        except Exception:
            pass

        # 恢复网络（交卷后释放防护）
        if self.cfg.ac_network_lock:
            self.net.restore()
            self.state.event("network_restore", "交卷后恢复网络")

        results, total, full = grade_all(self._questions, self.state.answers, self.cfg,
                                         answer_key=self.answer_key)
        self.state.result = results
        self.state.total_score = total
        self.state.full_score = full
        self._save()

        # 生成只读报告（含题干附件 + 参考答案附件 + 每题考生作答附件）
        cand_map = dict(self.state.candidate.get("_answer_media", {}))
        report = build_report(self.state, self.pkg.secret, self.vm,
                              qmap=self._qmap, candidate_media_map=cand_map,
                              local_ai_signs=self.local_ai_signs)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        rpath = os.path.join(os.path.expanduser("~"),
                             "报告_%s_%s.json" % (self.state.candidate.get("name", "考生"), stamp))
        write_report(rpath, report, self.pkg.secret, priv_pem=self.priv_pem)
        self.state.report_path = rpath
        self._save()

        # 卸载防护
        self.kbd.unhook()
        anticheat.restore_taskbar()

        self._show_result(rpath)

    def _show_result(self, rpath):
        self._clear()
        TH.header(self.root, "监考系统", "考试结束 · 成绩报告")
        page = ttk.Frame(self.root, style="BG.TFrame", padding=18)
        page.pack(fill="both", expand=True)

        passed = self.state.total_score >= self.cfg.pass_score
        hero = TH.card(page, padding=16)
        hero.pack(fill="x")
        tk.Label(hero, text="考试结束", font=(TH.FONT, 20, "bold"),
                 fg=TH.PRIMARY_DK, bg=TH.WHITE).pack(anchor="w")
        score_card = tk.Frame(hero, bg=TH.PRIMARY_LT if passed else "#FEE2E2", padx=16, pady=12)
        score_card.pack(fill="x", pady=(10, 6))
        scolor = TH.OK if passed else TH.WARN
        tk.Label(score_card, text="%g / %g" % (self.state.total_score, self.state.full_score),
                 font=(TH.FONT, 30, "bold"), fg=scolor, bg=score_card["bg"]).pack(side="left")
        tk.Label(score_card, text="  " + ("通过 ✓" if passed else "未通过 ✗"),
                 font=(TH.FONT, 15, "bold"), fg=scolor, bg=score_card["bg"]).pack(side="left", pady=10)

        tbl = ttk.LabelFrame(page, text="逐题详情", style="Card.TLabelframe", padding=8)
        tbl.pack(fill="both", expand=True, pady=(10, 6))
        cols = ("题号", "题型", "得分", "评语")
        tree = ttk.Treeview(tbl, columns=cols, show="headings", height=13)
        for c in cols:
            tree.heading(c, text=c)
            tree.column(c, width=100 if c != "评语" else 460, anchor="w")
        for i, r in enumerate(self.state.result):
            q = self._qmap.get(r["question_id"])
            tree.insert("", "end", values=(i+1, QTYPE_LABELS.get(q.type, "") if q else "",
                                           "%g/%g" % (r["score"], r["full_score"]),
                                           r.get("ai_feedback", "")))
        tree.pack(fill="both", expand=True)

        tk.Label(page, text="只读报告：" + rpath, font=(TH.FONT, 9),
                 fg=TH.TEXT_SUB, bg=TH.BG).pack(anchor="w", pady=(6, 0))
        tk.Label(page, text="报告已用 HMAC-SHA256 签名，任何修改都会被校验发现。",
                 font=(TH.FONT, 9), fg=TH.TEXT_SUB, bg=TH.BG).pack(anchor="w")
        btns = ttk.Frame(page, style="BG.TFrame")
        btns.pack(fill="x", pady=8)
        ttk.Button(btns, text="校验报告完整性", style="Ghost.TButton", command=lambda: messagebox.showinfo(
            "校验结果", "报告完整，签名有效 ✓" if verify_report(rpath, self.verify_pub or self.pkg.secret) else "报告已被篡改 ✗")).pack(side="left")
        ttk.Button(btns, text="退出", style="Primary.TButton", command=self.root.destroy).pack(side="right")

    def _on_close(self):
        if messagebox.askokcancel("提示", "考试进行中，退出前请先交卷。确定退出吗？（未交卷状态会保存，下次可恢复）"):
            self.kbd.unhook()
            # 不自动恢复网络：防止考生通过关软件恢复网络作弊；交卷时才恢复
            self.root.destroy()

    def _clear(self):
        for w in self.root.winfo_children():
            w.destroy()

    def _save(self):
        self.store.save(self.state)


def main():
    root = tk.Tk()
    root.geometry("900x680")
    ExamApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
