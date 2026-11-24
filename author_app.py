# -*- coding: utf-8 -*-
"""
监考系统 —— 母体（出题人用）
功能：
  - 可视化录入/修改题目（单选/多选/判断/填空/主观）
  - 配置考试（时长、抽题数量、分值、防作弊开关、AI 判分接口）
  - 保存/加载题库（JSON）
  - 一键生成"子体"：题目+答案打包成独立的考生客户端 exe（含图标、名称、强防作弊）
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from core.models import ExamPackage, ExamConfig, Question, QTYPE_LABELS, QTYPES, SINGLE, MULTIPLE, JUDGE, FILL, SUBJECTIVE
from core import ui_theme as TH
from core import bank as BANK
import mimetypes

HERE = os.path.dirname(os.path.abspath(__file__))
ICON = os.path.join(HERE, "assets", "icon.ico")
BANK_DEFAULT = os.path.join(HERE, "bank.json")


class AuthorApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("监考系统 · 母体（出题人）")
        TH.setup(root)
        if os.path.exists(ICON):
            try:
                root.iconbitmap(ICON)
            except Exception:
                pass
        root.geometry("1120x760")
        self.pkg = ExamPackage()
        self._build_ui()
        # 若已有默认题库文件夹，自动载入第一个
        banks = BANK.list_banks()
        if banks:
            try:
                self.pkg = BANK.load_bank(os.path.join(BANK.bank_root(), banks[0]))
                self.refresh_tree()
                self._sync_config_to_ui()
                self._set_bank_label("当前题库：" + banks[0])
            except Exception:
                pass

    # ---------------- UI ----------------
    def _build_ui(self):
        TH.header(self.root, "监考系统", "母体 · 出题人工作台")
        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=10, pady=(10, 10))
        self.tab_q = ttk.Frame(nb, style="BG.TFrame", padding=8)
        self.tab_cfg = ttk.Frame(nb, style="BG.TFrame", padding=12)
        self.tab_gen = ttk.Frame(nb, style="BG.TFrame", padding=12)
        nb.add(self.tab_q, text="题库管理")
        nb.add(self.tab_cfg, text="考试配置")
        nb.add(self.tab_gen, text="生成子体")

        self._build_question_tab()
        self._build_config_tab()
        self._build_generate_tab()

    # ---- 题库管理 ----
    def _build_question_tab(self):
        top = ttk.Frame(self.tab_q, style="BG.TFrame")
        top.pack(fill="x", padx=6, pady=6)
        ttk.Button(top, text="导入题库", style="Ghost.TButton",
                   command=self.import_bank).pack(side="left")
        ttk.Button(top, text="保存题库", style="Ghost.TButton",
                   command=self.save_bank).pack(side="left", padx=8)
        ttk.Button(top, text="＋ 添加题目", style="Primary.TButton",
                   command=self.add_question).pack(side="left")
        ttk.Button(top, text="编辑", style="Ghost.TButton",
                   command=self.edit_question).pack(side="left", padx=8)
        ttk.Button(top, text="删除", style="Danger.TButton",
                   command=self.delete_question).pack(side="left")
        self.bank_lbl = ttk.Label(top, text="当前题库：（未保存）", style="Sub.TLabel")
        self.bank_lbl.pack(side="right")

        mid = ttk.Frame(self.tab_q, style="BG.TFrame")
        mid.pack(fill="both", expand=True, padx=6)
        self.qtree = ttk.Treeview(mid, columns=("type", "stem", "points", "media"),
                                  show="headings", height=20)
        for c, t, w in [("type", "题型", 90), ("stem", "题干", 700),
                        ("points", "分值", 70), ("media", "附件", 110)]:
            self.qtree.heading(c, text=t)
            self.qtree.column(c, width=w)
        self.qtree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(mid, command=self.qtree.yview)
        sb.pack(side="right", fill="y")
        self.qtree.config(yscrollcommand=sb.set)
        self.qtree.bind("<Double-1>", lambda e: self.edit_question())
        self.refresh_tree()

    def refresh_tree(self):
        for i in self.qtree.get_children():
            self.qtree.delete(i)
        for q in self.pkg.questions:
            n = len(q.images) + len(q.videos)
            self.qtree.insert("", "end", iid=q.id,
                              values=(QTYPE_LABELS.get(q.type, q.type),
                                      q.stem[:60].replace("\n", " "), q.points,
                                      ("%d 个" % n) if n else ""))

    def _set_bank_label(self, text):
        self.bank_lbl.configure(text=text)

    def add_question(self):
        dlg = QuestionDialog(self.root, None)
        if dlg.result:
            self.pkg.questions.append(dlg.result)
            self.refresh_tree()
            messagebox.showinfo("提示", "题目已添加，记得点『保存题库』")

    def edit_question(self):
        sel = self.qtree.selection()
        if not sel:
            messagebox.showwarning("提示", "请先选中一道题"); return
        q = next((x for x in self.pkg.questions if x.id == sel[0]), None)
        if not q:
            return
        dlg = QuestionDialog(self.root, q)
        if dlg.result:
            idx = next((i for i, x in enumerate(self.pkg.questions) if x.id == q.id), -1)
            if idx >= 0:
                self.pkg.questions[idx] = dlg.result
            self.refresh_tree()

    def delete_question(self):
        sel = self.qtree.selection()
        if not sel:
            messagebox.showwarning("提示", "请先选中一道题"); return
        self.pkg.questions = [x for x in self.pkg.questions if x.id != sel[0]]
        self.refresh_tree()

    def save_bank(self, path=None):
        """把当前题目+配置保存成以考试名称命名的文件夹。"""
        # 先把配置页的考试名称同步过来
        if hasattr(self, "c_title"):
            self.pkg.config.title = self.c_title.get().strip() or self.pkg.config.title
        try:
            folder, msg = BANK.save_bank(self.pkg, path)
            self._set_bank_label("当前题库：" + os.path.basename(folder))
            messagebox.showinfo("保存成功", msg)
        except Exception as e:
            import traceback
            messagebox.showerror("错误", traceback.format_exc())

    def import_bank(self):
        folder = filedialog.askdirectory(title="选择题库文件夹", initialdir=BANK.bank_root())
        if not folder:
            return
        try:
            self.pkg = BANK.load_bank(folder)
            self.refresh_tree()
            self._sync_config_to_ui()
            self._set_bank_label("当前题库：" + os.path.basename(folder))
            messagebox.showinfo("提示", "已导入题库：" + folder)
        except Exception as e:
            messagebox.showerror("错误", str(e))

    # ---- 考试配置 ----
    def _build_config_tab(self):
        f = self.tab_cfg
        cfg = self.pkg.config
        r = ttk.Frame(f); r.pack(fill="x", padx=10, pady=6)
        ttk.Label(r, text="考试名称:").pack(side="left")
        self.c_title = ttk.Entry(r, width=30); self.c_title.insert(0, cfg.title); self.c_title.pack(side="left", padx=4)
        ttk.Label(r, text="时长(分钟):").pack(side="left", padx=8)
        self.c_dur = ttk.Spinbox(r, from_=1, to=300, width=6); self.c_dur.set(str(cfg.duration_minutes)); self.c_dur.pack(side="left")
        ttk.Label(r, text="及格分:").pack(side="left", padx=8)
        self.c_pass = ttk.Spinbox(r, from_=0, to=100, width=6); self.c_pass.set(str(cfg.pass_score)); self.c_pass.pack(side="left")

        f2 = ttk.LabelFrame(f, text="各题型抽题数量（0=不抽该类；抽题数≤题库数）", style="Card.TLabelframe", padding=8)
        f2.pack(fill="x", padx=10, pady=6)
        self.c_counts = {}
        for i, t in enumerate(QTYPES):
            frame = ttk.Frame(f2); frame.grid(row=i//5, column=i%5, padx=8, pady=4)
            ttk.Label(frame, text=QTYPE_LABELS[t]).pack(side="left")
            sp = ttk.Spinbox(frame, from_=0, to=999, width=5)
            sp.set(str(cfg.counts.get(t, 0))); sp.pack(side="left")
            self.c_counts[t] = sp

        f3 = ttk.LabelFrame(f, text="防作弊开关（生成子体时固化）", style="Card.TLabelframe", padding=8)
        f3.pack(fill="x", padx=10, pady=6)
        self.ac = {}
        opts = [
            ("ac_fullscreen", "全屏置顶+隐藏任务栏"),
            ("ac_block_keys", "键盘hook屏蔽快捷键"),
            ("ac_anti_vm", "反虚拟机检测"),
            ("ac_network_lock", "锁网（普通题断网）"),
            ("ac_uac", "UAC提权"),
        ]
        for i, (k, label) in enumerate(opts):
            v = tk.BooleanVar(value=getattr(cfg, k, True))
            self.ac[k] = v
            ttk.Checkbutton(f3, text=label, variable=v).grid(row=i//3, column=i%3, sticky="w", padx=10, pady=3)

        f4 = ttk.LabelFrame(f, text="其他", style="Card.TLabelframe", padding=8)
        f4.pack(fill="x", padx=10, pady=6)
        self.c_shuffle_q = tk.BooleanVar(value=cfg.shuffle_questions)
        self.c_shuffle_o = tk.BooleanVar(value=cfg.shuffle_options)
        ttk.Checkbutton(f4, text="打乱题目顺序", variable=self.c_shuffle_q).pack(side="left", padx=10)
        ttk.Checkbutton(f4, text="打乱选项顺序", variable=self.c_shuffle_o).pack(side="left", padx=10)

        f5 = ttk.LabelFrame(f, text="AI 判分接口（OpenAI chat-completions 兼容；留空则用关键词近似评分）", style="Card.TLabelframe", padding=8)
        f5.pack(fill="x", padx=10, pady=6)
        self.ai = {}
        for i, (k, label, w) in enumerate([("ai_url", "API URL", 40), ("ai_key", "API Key", 30), ("ai_model", "模型名", 20)]):
            ttk.Label(f5, text=label+":").grid(row=i, column=0, sticky="e", padx=6, pady=3)
            e = ttk.Entry(f5, width=w, show="*" if k == "ai_key" else None)
            e.insert(0, getattr(cfg, k, "")); e.grid(row=i, column=1, sticky="w", pady=3)
            self.ai[k] = e

        ttk.Button(f, text="保存配置到当前题库", style="Primary.TButton", command=self.apply_config).pack(pady=10)

    def apply_config(self):
        c = self.pkg.config
        c.title = self.c_title.get() or "监考系统考试"
        try:
            c.duration_minutes = int(self.c_dur.get())
        except Exception:
            c.duration_minutes = 60
        try:
            c.pass_score = float(self.c_pass.get())
        except Exception:
            c.pass_score = 60.0
        for t in QTYPES:
            try:
                c.counts[t] = int(self.c_counts[t].get())
            except Exception:
                c.counts[t] = 0
        for k, v in self.ac.items():
            setattr(c, k, v.get())
        c.shuffle_questions = self.c_shuffle_q.get()
        c.shuffle_options = self.c_shuffle_o.get()
        for k, e in self.ai.items():
            setattr(c, k, e.get().strip())
        self.pkg.save(BANK_DEFAULT)
        messagebox.showinfo("提示", "配置已保存到 " + BANK_DEFAULT)

    # ---- 生成子体 ----
    def _build_generate_tab(self):
        f = self.tab_gen
        ttk.Label(f, text="生成考生客户端（子体）：把当前题库+答案+配置打包成独立 exe，考生在本机运行，含强防作弊与只读报告。", wraplength=900).pack(anchor="w", padx=12, pady=8)

        # 安全加固说明
        sec = ttk.LabelFrame(f, text="🔒 安全加固（v2）", style="Card.TLabelframe", padding=10)
        sec.pack(fill="x", padx=12, pady=6)
        sec_txt = (
            "生成子体时自动启用以下防护：\n"
            "  • 答案不明文：客观题/关键词全部转成 HMAC 摘要，考生扒包也拿不到答案\n"
            "  • 报告防伪造：Ed25519 非对称签名，考生改分后无法重新签名\n"
            "  • 状态加密：作答状态用机器特征派生的密钥加密，改不了、拷不走\n"
            "  • 单调计时：改系统时间无效\n"
            "  • 网络令牌：考生恢复网络必须输入你手里的令牌\n"
            "  • AI Key 不下发：避免考生盗刷你的 API\n\n"
            "生成后，在子体工程目录会多出几个『出题人…』文件——\n"
            "  ✓ 只把 dist\\监考系统.exe 发给考生，其余文件自己保管，千万别一起发！"
        )
        ttk.Label(sec, text=sec_txt, style="Card.TLabel", justify="left",
                  wraplength=860).pack(anchor="w")

        self.gen_status = tk.StringVar(value="就绪")
        ttk.Label(f, textvariable=self.gen_status, foreground="green").pack(anchor="w", padx=12)
        ttk.Button(f, text="生成子体 exe …", style="Primary.TButton", command=self.generate_child).pack(anchor="w", padx=12, pady=8)
        ttk.Button(f, text="仅导出子体数据包(.json)", style="Ghost.TButton", command=self.export_package).pack(anchor="w", padx=12, pady=4)

        # 统计
        self.stat = tk.StringVar()
        ttk.Label(f, textvariable=self.stat, wraplength=900).pack(anchor="w", padx=12, pady=10)
        self.update_stat()

    def update_stat(self):
        cnt = {}
        for q in self.pkg.questions:
            cnt[q.type] = cnt.get(q.type, 0) + 1
        s = "当前题库：共 %d 题 — " % len(self.pkg.questions)
        s += "，".join("%s %d" % (QTYPE_LABELS[t], cnt.get(t, 0)) for t in QTYPES)
        self.stat.set(s)

    def export_package(self):
        p = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")], initialfile="exam_child.json")
        if not p:
            return
        self.pkg.save(p)
        messagebox.showinfo("提示", "已导出：" + p)

    def generate_child(self):
        self.update_stat()
        if not self.pkg.questions:
            messagebox.showwarning("提示", "题库为空，请先出题"); return
        outdir = filedialog.askdirectory(title="选择子体输出目录")
        if not outdir:
            return
        self.gen_status.set("正在生成，请稍候…")
        self.root.update()
        try:
            from core.builder import build_child
            exe = build_child(self.pkg, outdir, ICON)
            self.gen_status.set("已生成：" + exe)
            messagebox.showinfo("完成", "子体已生成：\n" + exe)
        except Exception as e:
            import traceback
            self.gen_status.set("生成失败：" + str(e))
            messagebox.showerror("错误", traceback.format_exc())

    # ---- 题库 IO ----
    def _sync_config_to_ui(self):
        c = self.pkg.config
        self.c_title.delete(0, "end"); self.c_title.insert(0, c.title)
        self.c_dur.delete(0, "end"); self.c_dur.insert(0, str(c.duration_minutes))
        self.c_pass.delete(0, "end"); self.c_pass.insert(0, str(c.pass_score))
        for t in QTYPES:
            sp = self.c_counts[t]; sp.delete(0, "end"); sp.insert(0, str(c.counts.get(t, 0)))
        for k, v in self.ac.items():
            v.set(getattr(c, k, True))
        for k, e in self.ai.items():
            e.delete(0, "end"); e.insert(0, getattr(c, k, ""))


class QuestionDialog(tk.Toplevel):
    """新增/编辑题目弹窗：题型、题干、分值、答案、附件（图片/视频）。"""
    def __init__(self, parent, question):
        super().__init__(parent)
        self.title("添加题目" if question is None else "编辑题目")
        if os.path.exists(ICON):
            try:
                self.iconbitmap(ICON)
            except Exception:
                pass
        self.geometry("760x760")
        self.transient(parent)
        self.grab_set()
        self.result = None
        self._q = question
        self._images = list(question.images) if question else []
        self._videos = list(question.videos) if question else []
        self._answer_images = list(question.answer_images) if question else []
        self._answer_videos = list(question.answer_videos) if question else []
        self._build()
        if question:
            self._fill(question)

    def _build(self):
        f = ttk.Frame(self, style="BG.TFrame", padding=14)
        f.pack(fill="both", expand=True)

        r = ttk.Frame(f, style="BG.TFrame")
        r.pack(fill="x", pady=4)
        ttk.Label(r, text="题型：").pack(side="left")
        self.d_type = ttk.Combobox(r, values=[QTYPE_LABELS[t] for t in QTYPES],
                                   width=12, state="readonly")
        self.d_type.current(0)
        self.d_type.pack(side="left", padx=4)
        ttk.Label(r, text="分值：").pack(side="left", padx=10)
        self.d_points = ttk.Spinbox(r, from_=1, to=100, width=6)
        self.d_points.set("10")
        self.d_points.pack(side="left")

        ttk.Label(f, text="题干：").pack(anchor="w", pady=(8, 2))
        self.d_stem = tk.Text(f, height=4, wrap="word", font=(TH.FONT, 10),
                              relief="solid", bd=1, highlightbackground=TH.BORDER,
                              highlightcolor=TH.PRIMARY, highlightthickness=1, padx=6, pady=5)
        self.d_stem.pack(fill="x")

        ttk.Label(f, text="选项（单选/多选/判断，每行一个，如 A. 选项内容）：").pack(anchor="w", pady=(8, 2))
        self.d_opts = tk.Text(f, height=4, wrap="word", font=(TH.FONT, 10),
                              relief="solid", bd=1, highlightbackground=TH.BORDER,
                              highlightcolor=TH.PRIMARY, highlightthickness=1, padx=6, pady=5)
        self.d_opts.pack(fill="x")

        self.ans_lbl = ttk.Label(f, text="正确答案：")
        self.ans_lbl.pack(anchor="w", pady=(8, 2))
        self.d_answer = tk.Text(f, height=2, wrap="word", font=(TH.FONT, 10),
                                relief="solid", bd=1, highlightbackground=TH.BORDER,
                                highlightcolor=TH.PRIMARY, highlightthickness=1, padx=6, pady=5)
        self.d_answer.pack(fill="x")
        self.hint = ttk.Label(f, text="", style="Sub.TLabel")
        self.hint.pack(anchor="w")

        ttk.Label(f, text="解析（可选）：").pack(anchor="w", pady=(8, 2))
        self.d_analysis = tk.Text(f, height=2, wrap="word", font=(TH.FONT, 10),
                                  relief="solid", bd=1, highlightbackground=TH.BORDER,
                                  highlightcolor=TH.PRIMARY, highlightthickness=1, padx=6, pady=5)
        self.d_analysis.pack(fill="x")

        # 附件
        mf = ttk.LabelFrame(f, text="题目附件（图片/视频，将附在子体报告中）",
                            style="Card.TLabelframe", padding=8)
        mf.pack(fill="x", pady=8)
        bar = ttk.Frame(mf, style="Card.TFrame")
        bar.pack(fill="x")
        ttk.Button(bar, text="上传图片", style="Ghost.TButton",
                   command=lambda: self._upload("image")).pack(side="left")
        ttk.Button(bar, text="上传视频", style="Ghost.TButton",
                   command=lambda: self._upload("video")).pack(side="left", padx=6)
        self.media_lbl = ttk.Label(mf, text="", style="Sub.TLabel", wraplength=620)
        self.media_lbl.pack(anchor="w", pady=(6, 0))
        self._refresh_media_lbl()

        # 答案附件（参考答案的图片/视频，将附在报告中）
        amf = ttk.LabelFrame(f, text="答案附件（参考答案的图片/视频，将附在报告中）",
                             style="Card.TLabelframe", padding=8)
        amf.pack(fill="x", pady=(0, 8))
        abar = ttk.Frame(amf, style="Card.TFrame")
        abar.pack(fill="x")
        ttk.Button(abar, text="上传答案图片", style="Ghost.TButton",
                   command=lambda: self._upload_answer("image")).pack(side="left")
        ttk.Button(abar, text="上传答案视频", style="Ghost.TButton",
                   command=lambda: self._upload_answer("video")).pack(side="left", padx=6)
        self.answer_media_lbl = ttk.Label(amf, text="", style="Sub.TLabel", wraplength=620)
        self.answer_media_lbl.pack(anchor="w", pady=(6, 0))
        self._refresh_answer_media_lbl()

        # 题型切换时更新提示
        self.d_type.bind("<<ComboboxSelected>>", lambda e: self._update_hint())

        btns = ttk.Frame(f, style="BG.TFrame")
        btns.pack(fill="x", pady=(10, 0))
        ttk.Button(btns, text="确定", style="Primary.TButton",
                   command=self._ok).pack(side="right")
        ttk.Button(btns, text="取消", style="Ghost.TButton",
                   command=self.destroy).pack(side="right", padx=8)
        self._update_hint()

    def _update_hint(self):
        label = self.d_type.get()
        t = next((x for x in QTYPES if QTYPE_LABELS[x] == label), SINGLE)
        hints = {
            SINGLE: "单选：填选项字母，如 B",
            MULTIPLE: "多选：填多个字母，如 A,C",
            JUDGE: "判断：填 A/B 或 正确/错误",
            FILL: "填空：填关键词，用逗号分隔；近义词用 | 隔开，如  算法,步骤|流程,有限",
            SUBJECTIVE: "主观：填评分关键词，用逗号分隔；近义词用 | 隔开，按命中数量给分",
        }
        self.hint.configure(text=hints[t])

    def _upload(self, kind):
        if kind == "image":
            p = filedialog.askopenfilename(title="选择图片", filetypes=[("图片", "*.png *.jpg *.jpeg *.gif *.bmp *.webp")])
            if p:
                self._images.append(p)
        else:
            p = filedialog.askopenfilename(title="选择视频", filetypes=[("视频", "*.mp4 *.avi *.mov *.mkv *.webm")])
            if p:
                self._videos.append(p)
        self._refresh_media_lbl()

    def _upload_answer(self, kind):
        if kind == "image":
            p = filedialog.askopenfilename(title="选择答案图片", filetypes=[("图片", "*.png *.jpg *.jpeg *.gif *.bmp *.webp")])
            if p:
                self._answer_images.append(p)
        else:
            p = filedialog.askopenfilename(title="选择答案视频", filetypes=[("视频", "*.mp4 *.avi *.mov *.mkv *.webm")])
            if p:
                self._answer_videos.append(p)
        self._refresh_answer_media_lbl()

    def _refresh_media_lbl(self):
        parts = []
        if self._images:
            parts.append("图片 %d 个：%s" % (len(self._images), "、".join(os.path.basename(x) for x in self._images)))
        if self._videos:
            parts.append("视频 %d 个：%s" % (len(self._videos), "、".join(os.path.basename(x) for x in self._videos)))
        self.media_lbl.configure(text="；".join(parts) if parts else "暂无附件")

    def _refresh_answer_media_lbl(self):
        parts = []
        if self._answer_images:
            parts.append("图片 %d 个：%s" % (len(self._answer_images), "、".join(os.path.basename(x) for x in self._answer_images)))
        if self._answer_videos:
            parts.append("视频 %d 个：%s" % (len(self._answer_videos), "、".join(os.path.basename(x) for x in self._answer_videos)))
        self.answer_media_lbl.configure(text="；".join(parts) if parts else "暂无附件")

    def _fill(self, q):
        self.d_type.set(QTYPE_LABELS.get(q.type, "单选题"))
        self.d_points.delete(0, "end"); self.d_points.insert(0, str(q.points))
        self.d_stem.insert("1.0", q.stem)
        self.d_opts.insert("1.0", "\n".join(q.options))
        ans = q.answer if isinstance(q.answer, str) else ",".join(q.answer)
        self.d_answer.insert("1.0", ans)
        self.d_analysis.insert("1.0", q.analysis)
        self._update_hint()

    def _ok(self):
        label = self.d_type.get()
        t = next((x for x in QTYPES if QTYPE_LABELS[x] == label), SINGLE)
        stem = self.d_stem.get("1.0", "end").strip()
        if not stem:
            messagebox.showwarning("提示", "题干不能为空", parent=self)
            return
        try:
            pts = float(self.d_points.get())
        except Exception:
            pts = 10.0
        opts = [x.strip() for x in self.d_opts.get("1.0", "end").splitlines() if x.strip()]
        ans = self.d_answer.get("1.0", "end").strip()
        if t == MULTIPLE:
            answer = [a.strip() for a in ans.replace("，", ",").split(",") if a.strip()]
        else:
            answer = ans
        q = self._q or Question()
        q.type = t
        q.stem = stem
        q.options = opts
        q.answer = answer
        q.points = pts
        q.analysis = self.d_analysis.get("1.0", "end").strip()
        q.images = list(self._images)
        q.videos = list(self._videos)
        q.answer_images = list(self._answer_images)
        q.answer_videos = list(self._answer_videos)
        self.result = q
        self.destroy()


def main():
    root = tk.Tk()
    AuthorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
