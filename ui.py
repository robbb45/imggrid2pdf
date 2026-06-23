from __future__ import annotations

import json
import os
import threading
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk

from PIL import Image, ImageDraw, ImageTk

import script

IMAGE_OVERRIDE_KEYS = (
    "remover_fundo_modo",
    "backend_remocao_fundo",
    "modelo_remocao_fundo",
    "modo_inspyrenet",
    "inspyrenet_device",
    "rembg_alpha_matting",
    "rembg_post_process_mask",
    "rembg_foreground_threshold",
    "rembg_background_threshold",
    "rembg_erode_size",
    "limiar_alpha",
    "tolerancia_fundo",
    "margem_interna_quadrado",
    "borda_preta_espessura",
    "estilo_borda",
    "raio_borda",
    "tamanho_numero_relativo",
    "padding_numero",
    "caixa_numero_padding_x",
    "caixa_numero_padding_y",
    "numero_glow_blur",
    "numero_glow_opacidade",
    "cor_borda",
    "cor_numero",
    "posicao_padrao_numero",
)

CACHE_SCHEMA_VERSION = 3


class ToolTip:
    def __init__(self, widget, text: str):
        self.widget = widget
        self.text = text
        self.tip = None
        self.widget.bind("<Enter>", self._show, add="+")
        self.widget.bind("<Leave>", self._hide, add="+")

    def _show(self, _event=None):
        if self.tip is not None or not self.text:
            return
        x = self.widget.winfo_rootx() + 14
        y = self.widget.winfo_rooty() + 22
        self.tip = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        label = tk.Label(
            tw,
            text=self.text,
            justify="left",
            background="#ffffe0",
            relief="solid",
            borderwidth=1,
            padx=6,
            pady=4,
        )
        label.pack()

    def _hide(self, _event=None):
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None


class PDFSheetUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Gerador A4 - Pré-visualização")
        self.root.geometry("1500x940+80+40")
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(400, lambda: self.root.attributes("-topmost", False))
        self.root.focus_force()

        self.script_dir = Path(__file__).resolve().parent
        self.config_path = self.script_dir / "config.json"
        self.config = script.carregar_config()
        self.config.setdefault("cor_fundo_janela", "#f0f0f0")
        self.config.setdefault("ultima_pasta_recorte", "")
        self.config.setdefault("recorte_grade_colunas", 3)
        self.config.setdefault("recorte_grade_linhas", 2)
        self.config.setdefault("recorte_grade_inset", 6)
        self.config.setdefault("recorte_grade_remover_borda", True)
        self.config.setdefault("recorte_grade_prefixo", "")

        self.imagens = []
        self.imagem_atual = None
        self.paginas_cache = []
        self.indice_pagina_preview = 0
        self.render_lock = threading.Lock()
        self.preview_lock = threading.Lock()
        self.preview_req_id = 0
        self.preview_after_id = None
        self.page_auto_after_id = None
        self.preview_resize_after_id = None
        self.global_sidebar_after_id = None
        self.preview_backend_warning = None
        self.apply_all_hint_after_id = None
        self.preview_cache = {}
        self.page_cache = {}
        self.rembg_cache = {}
        self.figure_cache = {}
        self.raw_cache = {}
        self.preview_raw_cache = {}
        self.image_content_cache = {}
        self.tooltips = []
        self.image_overrides = {}
        self.page_layout_cache = []
        self.page_layout_signature = None
        self.dirty_page_images = set()
        self.page_preview_meta = None
        self.page_zoom = 1.0
        self.suspend_trace = False
        self.backend_guard_active = False
        self.list_drag_index = None
        self.rename_panel_visible = False
        self.rename_inline_dirty = False
        self.global_cfg = dict(self.config)
        self.cache_root = script.CACHE_ROOT
        self.rembg_cache_dir = self.cache_root / "rembg"
        self.pages_cache_dir = self.cache_root / "pages"
        self.figures_cache_dir = self.cache_root / "figures"
        self.raw_cache_dir = self.cache_root / "raw"
        self.rembg_cache_dir.mkdir(parents=True, exist_ok=True)
        self.pages_cache_dir.mkdir(parents=True, exist_ok=True)
        self.figures_cache_dir.mkdir(parents=True, exist_ok=True)
        self.raw_cache_dir.mkdir(parents=True, exist_ok=True)
        self.overrides_file = self.script_dir / "image_overrides.json"
        self.layout_file = self.script_dir / "ui_layout.json"
        self.model_markers_file = script.MODELS_ROOT / "prepared_backends.json"
        self.model_markers = self._load_model_markers()

        self.preview_original_ref = None
        self.preview_crop_ref = None
        self.preview_final_ref = None
        self.preview_pagina_ref = None
        self.preview_display_size = (430, 300)

        self._build_ui()
        self._setup_menu()
        self._apply_window_bg()
        self.status_var.set("Carregando imagens e preparando prévias...")
        self.progress.configure(mode="indeterminate")
        self.progress.start(8)
        self.root.update_idletasks()
        self._load_overrides()
        self._load_images()
        self._refresh_all_previews()
        if not self.imagens:
            self._stop_progress()

    @staticmethod
    def _image_key(imagem: Path):
        return script.normalizar_chave_imagem(imagem)

    def _setup_menu(self):
        menubar = tk.Menu(self.root)
        menu_arquivo = tk.Menu(menubar, tearoff=0)
        menu_arquivo.add_command(label="Configurações Globais...", command=self._open_global_settings_dialog)
        menu_arquivo.add_command(label="Escolher Pasta de Imagens...", command=self._pick_folder)
        menu_arquivo.add_command(label="Abrir Pasta de Imagens", command=self._open_images_folder)
        menu_arquivo.add_separator()
        menu_arquivo.add_command(label="Sair", command=self.root.destroy)
        menubar.add_cascade(label="Arquivo", menu=menu_arquivo)

        menu_ferr = tk.Menu(menubar, tearoff=0)
        menu_ferr.add_command(label="Recarregar Imagens", command=self._reload_everything)
        menu_ferr.add_command(label="Renderizar Prévia de Página", command=self._render_page_preview_thread)
        menu_ferr.add_command(label="Recortar Grade...", command=self._open_sheet_cropper)
        menu_ferr.add_command(label="Resetar Todas as Imagens para Padrão", command=self._reset_all_image_overrides)
        menu_ferr.add_command(label="Limpar Cache", command=self._clear_all_cache)
        menubar.add_cascade(label="Ferramentas", menu=menu_ferr)

        menu_ajuda = tk.Menu(menubar, tearoff=0)
        menu_ajuda.add_command(
            label="Sobre Cache",
            command=lambda: messagebox.showinfo(
                "Sobre Cache",
                "O app usa a pasta cache ao lado do programa para remoção de fundo e páginas.\n"
                "Modelos baixados ficam na pasta models e dependências opcionais ficam em deps.\n"
                "Limpe pelo menu Ferramentas se necessário.",
            ),
        )
        menubar.add_cascade(label="Ajuda", menu=menu_ajuda)
        self.root.config(menu=menubar)

    def _apply_window_bg(self):
        cor = str(self.global_cfg.get("cor_fundo_janela", "#f0f0f0"))
        try:
            self.root.configure(bg=cor)
        except Exception:
            pass
        try:
            if hasattr(self, "status_label"):
                self.status_label.configure(bg=cor)
        except Exception:
            pass

    def _build_ui(self):
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        self.main_pane = tk.PanedWindow(
            self.root,
            orient=tk.HORIZONTAL,
            sashrelief=tk.RAISED,
            sashwidth=8,
            showhandle=True,
            opaqueresize=True,
        )
        self.main_pane.grid(row=0, column=0, sticky="nsew")

        sidebar_outer = ttk.Frame(self.main_pane)
        sidebar_outer.rowconfigure(0, weight=1)
        sidebar_outer.columnconfigure(0, weight=1)

        sidebar_canvas = tk.Canvas(sidebar_outer, highlightthickness=0)
        sidebar_scrollbar = ttk.Scrollbar(sidebar_outer, orient="vertical", command=sidebar_canvas.yview)
        sidebar_canvas.configure(yscrollcommand=sidebar_scrollbar.set)
        sidebar_canvas.grid(row=0, column=0, sticky="nsew")
        sidebar_scrollbar.grid(row=0, column=1, sticky="ns")

        painel_cfg = ttk.Frame(sidebar_canvas, padding=10)
        sidebar_frame_id = sidebar_canvas.create_window((0, 0), window=painel_cfg, anchor="nw")
        painel_cfg.columnconfigure(1, weight=1)

        def sync_sidebar_scrollregion(_event=None):
            sidebar_canvas.configure(scrollregion=sidebar_canvas.bbox("all"))

        def sync_sidebar_width(event):
            sidebar_canvas.itemconfigure(sidebar_frame_id, width=event.width)

        def scroll_sidebar(event):
            widget = getattr(event, "widget", None)
            try:
                widget_class = str(widget.winfo_class()) if widget is not None else ""
            except Exception:
                widget_class = ""
            if widget_class in {"TSpinbox", "Spinbox", "TCombobox", "Entry", "TEntry"}:
                return "break"
            delta = 0
            if getattr(event, "delta", 0):
                delta = -int(event.delta / 120)
            elif getattr(event, "num", None) == 4:
                delta = -1
            elif getattr(event, "num", None) == 5:
                delta = 1
            if delta:
                sidebar_canvas.yview_scroll(delta, "units")

        def bind_sidebar_scroll(_event=None):
            sidebar_canvas.bind_all("<MouseWheel>", scroll_sidebar)
            sidebar_canvas.bind_all("<Button-4>", scroll_sidebar)
            sidebar_canvas.bind_all("<Button-5>", scroll_sidebar)

        def unbind_sidebar_scroll(_event=None):
            sidebar_canvas.unbind_all("<MouseWheel>")
            sidebar_canvas.unbind_all("<Button-4>")
            sidebar_canvas.unbind_all("<Button-5>")

        painel_cfg.bind("<Configure>", sync_sidebar_scrollregion)
        sidebar_canvas.bind("<Configure>", sync_sidebar_width)
        sidebar_canvas.bind("<Enter>", bind_sidebar_scroll)
        sidebar_canvas.bind("<Leave>", unbind_sidebar_scroll)

        visual = ttk.Frame(self.main_pane, padding=10)
        visual.columnconfigure(0, weight=1)
        visual.rowconfigure(0, weight=1)

        self.main_pane.add(sidebar_outer, minsize=280)
        self.main_pane.add(visual)

        self.vars = {}
        self.global_sidebar_vars = {}
        self.param_help = {
            "pasta_imagens": "Pasta com as imagens de entrada. Pode ser caminho relativo ou absoluto.",
            "arquivo_saida_pdf": "Nome/caminho do PDF final gerado.",
            "figuras_por_pagina": "Quantidade de figuras por página A4: 12, 9, 6 ou 4.",
            "orientacao": "Orientação da página A4: horizontal ou vertical.",
            "margem_externa": "Margem entre a borda da página e a grade de figuras (em pixels).",
            "espaco_horizontal": "Espaço horizontal entre células da grade (em pixels).",
            "espaco_vertical": "Espaço vertical entre células da grade (em pixels).",
            "borda_preta_espessura": "Espessura da borda de recorte em cada célula (em pixels).",
            "estilo_borda": "Estilo da borda de recorte da imagem: sólida ou tracejada.",
            "raio_borda": "Arredondamento dos cantos da borda de recorte (em pixels).",
            "margem_interna_quadrado": "Margem interna da imagem dentro do quadrado (0.00 a 0.25).",
            "tamanho_numero_relativo": "Tamanho do número relativo ao tamanho da célula.",
            "padding_numero": "Distância do número em relação à borda interna da célula.",
            "numero_glow_blur": "Desfoque do brilho branco atrás do número (halo).",
            "numero_glow_opacidade": "Opacidade do brilho branco do número (0 a 255).",
            "cor_borda": "Cor da borda de recorte da imagem.",
            "cor_numero": "Cor do texto do número.",
            "cor_fundo_janela": "Cor de fundo da janela principal.",
            "limiar_alpha": "Controle prático do recorte em imagens com transparência. Aumente quando sobra uma borda/halo transparente ao redor da figura. Diminua se partes suaves, cabelo, sombras ou detalhes finos estão sendo cortados.",
            "tolerancia_fundo": "Controle prático para cortar fundo branco/quase branco conectado às bordas. Aumente quando sobra fundo claro ao redor da figura. Diminua se o recorte começa a comer partes claras da ilustração.",
            "limite_lado_processamento": "Reduz imagens grandes antes de remover fundo, recortar e gerar previews. Use 2000 para boa qualidade e velocidade em A4; aumente para máxima qualidade; use 0 para nunca reduzir.",
            "remover_fundo_modo": "Modo do rembg: todos, apenas nomes com RBG, ou desligado.",
            "backend_remocao_fundo": "Backend de remoção de fundo: rembg, withoutbg ou inspyrenet.",
            "modelo_remocao_fundo": "Modelo do backend rembg. Exemplos: birefnet-general-lite, birefnet-general, bria-rmbg, u2net.",
            "modo_inspyrenet": "Modo do backend InSPyReNet/transparent-background.",
            "inspyrenet_device": "Dispositivo do backend InSPyReNet: auto, cuda ou cpu.",
            "rembg_alpha_matting": "Refina as bordas da máscara no rembg, tentando preservar transições suaves como cabelo, tecido fino, sombras e anti-aliasing. Use quando a borda fica dura/serrilhada. Pode ficar mais lento e às vezes criar halos.",
            "rembg_post_process_mask": "Limpa a máscara depois da remoção do fundo. Use quando aparecem pequenos pontos soltos, buracos ou sujeira na transparência. Desligue se ele estiver apagando detalhes finos.",
            "rembg_foreground_threshold": "Threshold de primeiro plano do alpha matting do rembg.",
            "rembg_background_threshold": "Threshold de fundo do alpha matting do rembg.",
            "rembg_erode_size": "Tamanho de erosão usado no alpha matting do rembg.",
            "remover_fundo_local": "Liga/desliga remoção de fundo para a imagem selecionada.",
            "evitar_sobrescrever_pdf": "Quando ativo, cria arquivo com sufixo _01, _02... se já existir.",
            "salvar_paginas_png": "Também salva cada página em PNG além do PDF.",
            "auto_preview_pagina": "Quando ativo, re-renderiza a prévia de página automaticamente após mudanças.",
            "override_limiar_alpha": "Override por imagem do limiar alpha para recorte.",
            "override_tolerancia_fundo": "Override por imagem da tolerância de cor do fundo.",
            "override_remover_fundo_modo": "Override por imagem do modo de remoção de fundo.",
        }

        self.apply_all_label_keys = {
            "borda_preta_espessura",
            "estilo_borda",
            "raio_borda",
            "margem_interna_quadrado",
            "tamanho_numero_relativo",
            "padding_numero",
            "numero_glow_blur",
            "numero_glow_opacidade",
            "limiar_alpha",
            "tolerancia_fundo",
            "remover_fundo_modo",
            "backend_remocao_fundo",
            "modelo_remocao_fundo",
            "modo_inspyrenet",
            "inspyrenet_device",
            "rembg_alpha_matting",
            "rembg_post_process_mask",
            "rembg_foreground_threshold",
            "rembg_background_threshold",
            "rembg_erode_size",
        }
        self.apply_all_group_keys = {
            "borda_preta_espessura": ("borda_preta_espessura", "cor_borda"),
            "estilo_borda": ("estilo_borda", "raio_borda"),
            "tamanho_numero_relativo": ("tamanho_numero_relativo", "cor_numero"),
        }

        status_box = ttk.Frame(painel_cfg)
        status_box.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        status_box.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(status_box, orient="horizontal", mode="determinate", maximum=100)
        self.progress.grid(row=0, column=0, sticky="ew")
        self.status_var = tk.StringVar(value="Pronto")
        self.status_label = tk.Label(
            status_box,
            textvariable=self.status_var,
            wraplength=300,
            justify="left",
            anchor="nw",
            height=2,
            bg=self.global_cfg.get("cor_fundo_janela", "#f0f0f0"),
        )
        self.status_label.grid(row=1, column=0, sticky="ew", pady=(3, 0))

        global_box = ttk.LabelFrame(painel_cfg, text="Layout da Página")
        global_box.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        global_box.columnconfigure(1, weight=1)

        def add_global_combo(label, key, row, values):
            ttk.Label(global_box, text=label).grid(row=row, column=0, sticky="w", pady=3)
            var = tk.StringVar(value=str(self.global_cfg.get(key, script.CONFIG_PADRAO.get(key, ""))))
            self.global_sidebar_vars[key] = var
            cb = ttk.Combobox(global_box, textvariable=var, values=values, state="readonly", width=12)
            cb.grid(row=row, column=1, sticky="ew", pady=3)
            self._bind_tooltip(cb, key)

        def add_global_slider(label, key, row, frm, to):
            ttk.Label(global_box, text=label).grid(row=row, column=0, sticky="w", pady=3)
            var = tk.IntVar(value=int(self.global_cfg.get(key, script.CONFIG_PADRAO.get(key, 0))))
            self.global_sidebar_vars[key] = var
            frame = ttk.Frame(global_box)
            frame.grid(row=row, column=1, sticky="ew", pady=3)
            frame.columnconfigure(0, weight=1)
            scl = ttk.Scale(frame, from_=frm, to=to, variable=var, orient="horizontal")
            scl.grid(row=0, column=0, sticky="ew")
            self._bind_scale_wheel(scl, var, frm, to, 1)
            ttk.Label(frame, textvariable=var, width=4).grid(row=0, column=1, padx=(6, 0))
            self._bind_tooltip(frame, key)

        add_global_combo("Figuras/página", "figuras_por_pagina", 0, ["12", "9", "6", "4"])
        add_global_combo("Orientação", "orientacao", 1, ["horizontal", "vertical"])
        add_global_slider("Margem externa", "margem_externa", 2, 0, 250)
        add_global_slider("Espaço horizontal", "espaco_horizontal", 3, 0, 160)
        add_global_slider("Espaço vertical", "espaco_vertical", 4, 0, 160)

        for var in self.global_sidebar_vars.values():
            try:
                var.trace_add("write", self._on_global_sidebar_change)
            except Exception:
                pass

        def make_apply_all_label(parent, text, key, row, column=0, sticky="w", pady=3):
            lbl = ttk.Label(parent, text=text, cursor="hand2", foreground="#1f5aa6")
            lbl.grid(row=row, column=column, sticky=sticky, pady=pady)
            if key in self.apply_all_label_keys:
                lbl.bind("<Double-Button-1>", lambda _e, k=key: self._apply_param_to_other_images(k))
                lbl.bind("<Enter>", lambda _e, k=key: self._show_apply_all_hint(k))
                self._bind_tooltip(lbl, key, apply_all=True)
            return lbl

        def bind_apply_all_widget(widget, key):
            if key not in self.apply_all_label_keys:
                self._bind_tooltip(widget, key)
                return
            widget.bind("<Double-Button-1>", lambda _e, k=key: self._apply_param_to_other_images(k), add="+")
            widget.bind("<Enter>", lambda _e, k=key: self._show_apply_all_hint(k), add="+")
            self._bind_tooltip(widget, key, apply_all=True)

        def add_entry(label, key, row):
            lbl = make_apply_all_label(painel_cfg, label, key, row)
            var = tk.StringVar(value=str(self.config.get(key, "")))
            self.vars[key] = var
            ent = ttk.Entry(painel_cfg, textvariable=var, width=36)
            ent.grid(row=row, column=1, sticky="ew", pady=3)
            self._bind_tooltip(lbl, key)
            self._bind_tooltip(ent, key)
            return ent

        def add_spin(label, key, row, frm, to):
            lbl = make_apply_all_label(painel_cfg, label, key, row)
            var = tk.IntVar(value=int(self.config.get(key, 0)))
            self.vars[key] = var
            sp = ttk.Spinbox(painel_cfg, from_=frm, to=to, textvariable=var, width=10)
            sp.grid(row=row, column=1, sticky="w", pady=3)
            self._bind_tooltip(lbl, key)
            self._bind_tooltip(sp, key)

        def add_slider_int(label, key, row, frm, to, apply_all=False):
            lbl = make_apply_all_label(painel_cfg, label, key, row)
            var = tk.IntVar(value=int(self.config.get(key, 0)))
            self.vars[key] = var
            frame = ttk.Frame(painel_cfg)
            frame.grid(row=row, column=1, sticky="ew", pady=3)
            frame.columnconfigure(0, weight=1)
            scl = ttk.Scale(frame, from_=frm, to=to, variable=var, orient="horizontal")
            scl.grid(row=0, column=0, sticky="ew")
            self._bind_scale_wheel(scl, var, frm, to, 1)
            val = ttk.Label(frame, textvariable=var, width=4)
            val.grid(row=0, column=1, padx=(6, 0))
            self._bind_tooltip(lbl, key)
            self._bind_tooltip(scl, key)
            self._bind_tooltip(val, key)
            return frame

        def add_slider_float(label, key, row, frm, to, apply_all=False):
            lbl = make_apply_all_label(painel_cfg, label, key, row)
            var = tk.DoubleVar(value=float(self.config.get(key, 0.0)))
            self.vars[key] = var
            frame = ttk.Frame(painel_cfg)
            frame.grid(row=row, column=1, sticky="ew", pady=3)
            frame.columnconfigure(0, weight=1)
            scl = ttk.Scale(frame, from_=frm, to=to, variable=var, orient="horizontal")
            scl.grid(row=0, column=0, sticky="ew")
            self._bind_scale_wheel(scl, var, frm, to, 0.01)
            val = ttk.Label(frame, textvariable=var, width=6)
            val.grid(row=0, column=1, padx=(6, 0))
            self._bind_tooltip(lbl, key)
            self._bind_tooltip(scl, key)
            self._bind_tooltip(val, key)

        def add_color_picker(label, key, row):
            lbl = make_apply_all_label(painel_cfg, label, key, row)
            var = tk.StringVar(value=str(self.config.get(key, "#000000")))
            self.vars[key] = var
            frame = ttk.Frame(painel_cfg)
            frame.grid(row=row, column=1, sticky="w", pady=3)
            swatch = tk.Label(frame, width=3, relief="solid", bd=1, bg=var.get())
            swatch.pack(side="left")
            ent = ttk.Entry(frame, textvariable=var, width=10)
            ent.pack(side="left", padx=(6, 0))
            def pick():
                c = colorchooser.askcolor(color=var.get(), title=label)[1]
                if c:
                    var.set(c)
                    swatch.configure(bg=c)
            ttk.Button(frame, text="...", width=3, command=pick).pack(side="left", padx=(4, 0))
            def sync(*_):
                try:
                    swatch.configure(bg=var.get())
                except Exception:
                    pass
            var.trace_add("write", sync)
            self._bind_tooltip(lbl, key)
            self._bind_tooltip(swatch, key)
            self._bind_tooltip(ent, key)

        def add_inline_color(frame, key):
            var = self.vars.get(key)
            if var is None:
                var = tk.StringVar(value=str(self.config.get(key, "#000000")))
                self.vars[key] = var
            swatch = tk.Label(frame, width=2, relief="solid", bd=1, bg=var.get(), cursor="hand2")
            swatch.grid(row=0, column=2, padx=(8, 0))
            def pick():
                c = colorchooser.askcolor(color=var.get(), title=key)[1]
                if c:
                    var.set(c)
                    swatch.configure(bg=c)
            swatch.bind("<Button-1>", lambda _e: pick())
            def sync(*_):
                try:
                    swatch.configure(bg=var.get())
                except Exception:
                    pass
            var.trace_add("write", sync)
            self._bind_tooltip(swatch, key)

        def add_inline_combo(frame, key, values, width=10, row=0, column=None):
            var = self.vars.get(key)
            if var is None:
                var = tk.StringVar(value=str(self.config.get(key, script.CONFIG_PADRAO.get(key, ""))))
                self.vars[key] = var
            cb = ttk.Combobox(frame, textvariable=var, values=values, state="readonly", width=width)
            next_col = frame.grid_size()[0] if column is None else column
            cb.grid(row=row, column=next_col, padx=(8, 0), sticky="w")
            bind_apply_all_widget(cb, key)
            return cb

        def add_inline_spin(frame, key, frm, to, width=4, row=0, column=None):
            var = self.vars.get(key)
            if var is None:
                var = tk.IntVar(value=int(self.config.get(key, script.CONFIG_PADRAO.get(key, 0))))
                self.vars[key] = var
            sp = ttk.Spinbox(frame, from_=frm, to=to, textvariable=var, width=width)
            next_col = frame.grid_size()[0] if column is None else column
            sp.grid(row=row, column=next_col, padx=(8, 0), sticky="w")
            bind_apply_all_widget(sp, key)
            return sp

        def add_float(label, key, row):
            make_apply_all_label(painel_cfg, label, key, row)
            var = tk.DoubleVar(value=float(self.config.get(key, 0.0)))
            self.vars[key] = var
            sp = ttk.Spinbox(
                painel_cfg,
                from_=0.0,
                to=1.0,
                increment=0.01,
                textvariable=var,
                width=10,
            )
            sp.grid(row=row, column=1, sticky="w", pady=3)

        row = 2

        frame_borda = add_slider_int("Espessura borda", "borda_preta_espessura", row, 1, 30, apply_all=True)
        add_inline_color(frame_borda, "cor_borda")
        row += 1
        lbl_estilo_borda = make_apply_all_label(painel_cfg, "Estilo borda", "estilo_borda", row)
        frame_estilo_borda = ttk.Frame(painel_cfg)
        frame_estilo_borda.grid(row=row, column=1, sticky="w", pady=3)
        add_inline_combo(frame_estilo_borda, "estilo_borda", script.listar_estilos_borda(), width=12, row=0, column=0)
        lbl_raio = ttk.Label(frame_estilo_borda, text="Raio")
        lbl_raio.grid(row=0, column=1, padx=(10, 2), sticky="e")
        self._bind_tooltip(lbl_estilo_borda, "estilo_borda")
        self._bind_tooltip(lbl_raio, "raio_borda")
        add_inline_spin(frame_estilo_borda, "raio_borda", 0, 120, width=5, row=0, column=2)
        row += 1
        add_slider_float("Margem interna", "margem_interna_quadrado", row, 0.0, 0.25, apply_all=True)
        row += 1

        lbl_tnr = make_apply_all_label(painel_cfg, "Tamanho número (%)", "tamanho_numero_relativo", row)
        self.vars["tamanho_numero_relativo"] = tk.DoubleVar(value=float(self.config.get("tamanho_numero_relativo", 0.085)))
        frame_num = ttk.Frame(painel_cfg)
        frame_num.grid(row=row, column=1, sticky="ew", pady=3)
        frame_num.columnconfigure(0, weight=1)
        scl_num = ttk.Scale(frame_num, from_=0.03, to=0.25, variable=self.vars["tamanho_numero_relativo"], orient="horizontal")
        scl_num.grid(
            row=0, column=0, sticky="ew"
        )
        self._bind_scale_wheel(scl_num, self.vars["tamanho_numero_relativo"], 0.03, 0.25, 0.005)
        val_num = ttk.Label(frame_num, textvariable=self.vars["tamanho_numero_relativo"], width=6)
        val_num.grid(row=0, column=1, padx=(6, 0))
        self._bind_tooltip(lbl_tnr, "tamanho_numero_relativo")
        self._bind_tooltip(scl_num, "tamanho_numero_relativo")
        self._bind_tooltip(val_num, "tamanho_numero_relativo")
        add_inline_color(frame_num, "cor_numero")
        row += 1
        add_slider_int("Padding número", "padding_numero", row, 0, 80, apply_all=True)
        row += 1
        add_slider_int("Glow blur", "numero_glow_blur", row, 0, 20, apply_all=True)
        row += 1
        add_slider_int("Glow opacidade", "numero_glow_opacidade", row, 0, 255, apply_all=True)
        row += 1
        add_slider_int("Limiar alpha", "limiar_alpha", row, 0, 255, apply_all=True)
        row += 1
        add_slider_int("Tolerância fundo", "tolerancia_fundo", row, 0, 80, apply_all=True)
        row += 1
        self.vars["remover_fundo_local"] = tk.BooleanVar(
            value=str(self.config.get("remover_fundo_modo", "todos")) != "desligado"
        )
        chk_rf = ttk.Checkbutton(
            painel_cfg,
            text="Remover fundo (imagem selecionada)",
            variable=self.vars["remover_fundo_local"],
        )
        chk_rf.grid(row=row, column=0, columnspan=2, sticky="w", pady=3)
        self._bind_tooltip(chk_rf, "remover_fundo_local")
        row += 1

        make_apply_all_label(painel_cfg, "Backend fundo", "backend_remocao_fundo", row)
        self.vars["backend_remocao_fundo"] = tk.StringVar(
            value=str(self.config.get("backend_remocao_fundo", "rembg"))
        )
        cb_backend = ttk.Combobox(
            painel_cfg,
            textvariable=self.vars["backend_remocao_fundo"],
            values=script.listar_backends_remocao_fundo(),
            state="readonly",
        )
        cb_backend.grid(row=row, column=1, sticky="ew", pady=3)
        self._bind_tooltip(cb_backend, "backend_remocao_fundo")
        row += 1

        self.backend_rembg_frame = ttk.Frame(painel_cfg)
        self.backend_rembg_frame.grid(row=row, column=0, columnspan=2, sticky="ew", pady=0)
        self.backend_rembg_frame.columnconfigure(1, weight=1)
        make_apply_all_label(self.backend_rembg_frame, "Modelo rembg", "modelo_remocao_fundo", 0)
        self.vars["modelo_remocao_fundo"] = tk.StringVar(
            value=str(self.config.get("modelo_remocao_fundo", "birefnet-general-lite"))
        )
        cb_modelo = ttk.Combobox(
            self.backend_rembg_frame,
            textvariable=self.vars["modelo_remocao_fundo"],
            values=script.listar_modelos_rembg_disponiveis() or [
                "birefnet-general-lite",
                "birefnet-general",
                "bria-rmbg",
                "u2net",
            ],
            state="readonly",
        )
        cb_modelo.grid(row=0, column=1, sticky="ew", pady=3)
        self._bind_tooltip(cb_modelo, "modelo_remocao_fundo")
        self.vars["rembg_alpha_matting"] = tk.BooleanVar(value=bool(self.config.get("rembg_alpha_matting", False)))
        chk_alpha = ttk.Checkbutton(
            self.backend_rembg_frame,
            text="Alpha matting",
            variable=self.vars["rembg_alpha_matting"],
        )
        chk_alpha.grid(row=1, column=0, columnspan=2, sticky="w", pady=3)
        self._bind_tooltip(chk_alpha, "rembg_alpha_matting", apply_all=True)
        self.vars["rembg_post_process_mask"] = tk.BooleanVar(value=bool(self.config.get("rembg_post_process_mask", False)))
        chk_post = ttk.Checkbutton(
            self.backend_rembg_frame,
            text="Post-process mask",
            variable=self.vars["rembg_post_process_mask"],
        )
        chk_post.grid(row=2, column=0, columnspan=2, sticky="w", pady=3)
        self._bind_tooltip(chk_post, "rembg_post_process_mask", apply_all=True)
        make_apply_all_label(self.backend_rembg_frame, "FG threshold", "rembg_foreground_threshold", 3)
        self.vars["rembg_foreground_threshold"] = tk.IntVar(value=int(self.config.get("rembg_foreground_threshold", 240)))
        fg_frame = ttk.Frame(self.backend_rembg_frame)
        fg_frame.grid(row=3, column=1, sticky="ew", pady=3)
        fg_frame.columnconfigure(0, weight=1)
        scl_fg = ttk.Scale(fg_frame, from_=0, to=255, variable=self.vars["rembg_foreground_threshold"], orient="horizontal")
        scl_fg.grid(row=0, column=0, sticky="ew")
        self._bind_scale_wheel(scl_fg, self.vars["rembg_foreground_threshold"], 0, 255, 1)
        ttk.Label(fg_frame, textvariable=self.vars["rembg_foreground_threshold"], width=4).grid(row=0, column=1, padx=(6,0))
        make_apply_all_label(self.backend_rembg_frame, "BG threshold", "rembg_background_threshold", 4)
        self.vars["rembg_background_threshold"] = tk.IntVar(value=int(self.config.get("rembg_background_threshold", 10)))
        bg_frame = ttk.Frame(self.backend_rembg_frame)
        bg_frame.grid(row=4, column=1, sticky="ew", pady=3)
        bg_frame.columnconfigure(0, weight=1)
        scl_bg = ttk.Scale(bg_frame, from_=0, to=255, variable=self.vars["rembg_background_threshold"], orient="horizontal")
        scl_bg.grid(row=0, column=0, sticky="ew")
        self._bind_scale_wheel(scl_bg, self.vars["rembg_background_threshold"], 0, 255, 1)
        ttk.Label(bg_frame, textvariable=self.vars["rembg_background_threshold"], width=4).grid(row=0, column=1, padx=(6,0))
        make_apply_all_label(self.backend_rembg_frame, "Erode size", "rembg_erode_size", 5)
        self.vars["rembg_erode_size"] = tk.IntVar(value=int(self.config.get("rembg_erode_size", 10)))
        erode_frame = ttk.Frame(self.backend_rembg_frame)
        erode_frame.grid(row=5, column=1, sticky="ew", pady=3)
        erode_frame.columnconfigure(0, weight=1)
        scl_erode = ttk.Scale(erode_frame, from_=0, to=100, variable=self.vars["rembg_erode_size"], orient="horizontal")
        scl_erode.grid(row=0, column=0, sticky="ew")
        self._bind_scale_wheel(scl_erode, self.vars["rembg_erode_size"], 0, 100, 1)
        ttk.Label(erode_frame, textvariable=self.vars["rembg_erode_size"], width=4).grid(row=0, column=1, padx=(6,0))
        row += 1

        self.backend_inspy_frame = ttk.Frame(painel_cfg)
        self.backend_inspy_frame.grid(row=row, column=0, columnspan=2, sticky="ew", pady=0)
        self.backend_inspy_frame.columnconfigure(1, weight=1)
        make_apply_all_label(self.backend_inspy_frame, "Modo InSPyReNet", "modo_inspyrenet", 0)
        self.vars["modo_inspyrenet"] = tk.StringVar(
            value=str(self.config.get("modo_inspyrenet", "base"))
        )
        cb_inspy = ttk.Combobox(
            self.backend_inspy_frame,
            textvariable=self.vars["modo_inspyrenet"],
            values=script.listar_modos_inspyrenet(),
            state="readonly",
        )
        cb_inspy.grid(row=0, column=1, sticky="ew", pady=3)
        self._bind_tooltip(cb_inspy, "modo_inspyrenet")
        make_apply_all_label(self.backend_inspy_frame, "Dispositivo", "inspyrenet_device", 1)
        self.vars["inspyrenet_device"] = tk.StringVar(
            value=str(self.config.get("inspyrenet_device", "auto"))
        )
        cb_inspy_dev = ttk.Combobox(
            self.backend_inspy_frame,
            textvariable=self.vars["inspyrenet_device"],
            values=script.listar_dispositivos_inspyrenet(),
            state="readonly",
        )
        cb_inspy_dev.grid(row=1, column=1, sticky="ew", pady=3)
        self._bind_tooltip(cb_inspy_dev, "inspyrenet_device")
        row += 1

        botoes = ttk.Frame(painel_cfg)
        botoes.grid(row=row, column=0, columnspan=3, sticky="ew", pady=8)
        botoes.columnconfigure(0, weight=1)
        botoes.columnconfigure(1, weight=1)
        ttk.Button(botoes, text="Salvar config global", command=self._save_config).grid(row=0, column=0, sticky="ew", padx=3, pady=2)
        ttk.Button(botoes, text="Resetar para Padrão", command=self._reset_left_defaults).grid(row=0, column=1, sticky="ew", padx=3, pady=2)
        ttk.Button(botoes, text="Atualizar preview", command=self._refresh_all_previews).grid(row=1, column=0, sticky="ew", padx=3, pady=2)
        ttk.Button(botoes, text="Gerar PDF", command=self._gerar_pdf_thread).grid(row=1, column=1, sticky="ew", padx=3, pady=2)

        self.visual_pane = tk.PanedWindow(
            visual,
            orient=tk.VERTICAL,
            sashrelief=tk.RAISED,
            sashwidth=8,
            showhandle=True,
            opaqueresize=True,
        )
        self.visual_pane.grid(row=0, column=0, sticky="nsew")

        topo = ttk.Frame(self.visual_pane)
        topo.columnconfigure(1, weight=1)
        topo.columnconfigure(0, weight=1)
        topo.columnconfigure(2, weight=1)
        topo.rowconfigure(2, weight=1)

        header = ttk.Frame(topo)
        header.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 4))
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="Imagens:").grid(row=0, column=0, sticky="w")

        tools = ttk.Frame(header)
        tools.grid(row=0, column=1, sticky="e")
        ttk.Label(tools, text="Ordem").pack(side="left", padx=(0, 4))
        self.sort_mode_var = tk.StringVar(value="Manual")
        sort_combo = ttk.Combobox(
            tools,
            textvariable=self.sort_mode_var,
            values=["Manual", "Nome A-Z", "Nome Z-A", "Número"],
            state="readonly",
            width=10,
        )
        sort_combo.pack(side="left", padx=(0, 4))
        sort_combo.bind("<<ComboboxSelected>>", lambda _e: self._apply_image_sort())
        ttk.Button(tools, text="Renomear", command=self._toggle_rename_panel).pack(side="left", padx=(0, 4))
        for label, code in (("SE", "SE"), ("SD", "SD"), ("IE", "IE"), ("ID", "ID")):
            btn = ttk.Button(tools, text=label, width=3, command=lambda c=code: self._apply_position_code_to_selected(c))
            btn.pack(side="left", padx=1)
            self._bind_static_tooltip(btn, {
                "SE": "Superior esquerdo",
                "SD": "Superior direito",
                "IE": "Inferior esquerdo",
                "ID": "Inferior direito",
            }[code])

        self.rename_panel = ttk.Frame(topo)
        self.rename_panel.columnconfigure(5, weight=1)
        ttk.Label(self.rename_panel, text="Nome").grid(row=0, column=0, sticky="w", padx=(0, 4))
        self.rename_single_var = tk.StringVar(value="")
        self.rename_single_entry = ttk.Entry(self.rename_panel, textvariable=self.rename_single_var, width=24)
        self.rename_single_entry.grid(row=0, column=1, columnspan=4, sticky="ew", padx=(0, 6))
        self.rename_single_entry.bind("<Return>", lambda _e: self._rename_selected_image_direct())
        self.rename_single_entry.bind("<FocusIn>", lambda _e: setattr(self, "rename_inline_dirty", False))
        self.rename_single_entry.bind("<KeyRelease>", self._on_single_rename_key)
        self.rename_extension_var = tk.StringVar(value="")
        ttk.Label(self.rename_panel, textvariable=self.rename_extension_var, width=7).grid(row=0, column=5, sticky="w", padx=(0, 8))
        ttk.Button(self.rename_panel, text="Aplicar nome", command=self._rename_selected_image_direct).grid(row=0, column=6, sticky="e", padx=2)

        self.rename_padding_var = tk.IntVar(value=2)
        ttk.Spinbox(self.rename_panel, from_=1, to=6, textvariable=self.rename_padding_var, width=4).grid(row=1, column=1, sticky="w", padx=(0, 8))
        ttk.Label(self.rename_panel, text="Dígitos").grid(row=1, column=0, sticky="w", padx=(0, 4))
        ttk.Label(self.rename_panel, text="Início").grid(row=1, column=2, sticky="w", padx=(0, 4))
        self.rename_start_var = tk.IntVar(value=1)
        ttk.Spinbox(self.rename_panel, from_=0, to=9999, textvariable=self.rename_start_var, width=6).grid(row=1, column=3, sticky="w", padx=(0, 8))
        ttk.Label(self.rename_panel, text="Prefixo").grid(row=1, column=4, sticky="w", padx=(0, 4))
        self.rename_prefix_var = tk.StringVar(value="")
        ttk.Entry(self.rename_panel, textvariable=self.rename_prefix_var, width=8).grid(row=1, column=5, sticky="ew", padx=(0, 8))
        ttk.Label(self.rename_panel, text="Sufixo").grid(row=1, column=6, sticky="w", padx=(0, 4))
        self.rename_suffix_var = tk.StringVar(value="")
        ttk.Entry(self.rename_panel, textvariable=self.rename_suffix_var, width=8).grid(row=1, column=7, sticky="ew", padx=(0, 8))
        ttk.Button(self.rename_panel, text="Selecionadas", command=lambda: self._renumber_images(False)).grid(row=1, column=8, sticky="e", padx=2)
        ttk.Button(self.rename_panel, text="Todas", command=lambda: self._renumber_images(True)).grid(row=1, column=9, sticky="e", padx=2)

        self.listbox = tk.Listbox(topo, height=6, exportselection=False, selectmode=tk.EXTENDED)
        self.listbox.grid(row=2, column=0, columnspan=3, sticky="nsew")
        self.listbox.bind("<<ListboxSelect>>", lambda e: self._on_select_image())
        self.listbox.bind("<Double-Button-1>", self._start_inline_rename)
        self.listbox.bind("<ButtonPress-1>", self._on_listbox_drag_start)
        self.listbox.bind("<B1-Motion>", self._on_listbox_drag_motion)
        self.listbox.bind("<ButtonRelease-1>", self._on_listbox_drag_end)
        self.info_img_var = tk.StringVar(value="")
        ttk.Label(topo, textvariable=self.info_img_var).grid(row=3, column=0, columnspan=3, sticky="w", pady=(6, 0))

        b_ov = ttk.Frame(topo)
        b_ov.grid(row=4, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Button(b_ov, text="Salvar Ajustes da Imagem em Global", command=self._apply_current_image_to_global).pack(side="left", padx=2)
        ttk.Button(b_ov, text="Limpar Override da Imagem", command=self._clear_image_override).pack(side="left", padx=2)
        ttk.Button(b_ov, text="Resetar Todas", command=self._reset_all_image_overrides).pack(side="left", padx=2)

        prev_frame = ttk.Frame(self.visual_pane)
        prev_frame.columnconfigure(0, weight=1)
        prev_frame.rowconfigure(1, weight=1)
        previews_grid = ttk.Frame(prev_frame)
        previews_grid.grid(row=1, column=0, sticky="nsew")
        previews_grid.columnconfigure(0, weight=1, uniform="preview")
        previews_grid.columnconfigure(1, weight=1, uniform="preview")
        previews_grid.columnconfigure(2, weight=1, uniform="preview")
        previews_grid.rowconfigure(0, weight=1)

        titles = ttk.Frame(prev_frame)
        titles.grid(row=0, column=0, sticky="ew")
        titles.columnconfigure(0, weight=1, uniform="preview")
        titles.columnconfigure(1, weight=1, uniform="preview")
        titles.columnconfigure(2, weight=1, uniform="preview")
        ttk.Label(titles, text="Original").grid(row=0, column=0, sticky="w")
        ttk.Label(titles, text="Recorte (fundo removido)").grid(row=0, column=1, sticky="w")
        ttk.Label(titles, text="Resultado final da célula").grid(row=0, column=2, sticky="w")

        pane_o = ttk.Frame(previews_grid)
        pane_o.columnconfigure(0, weight=1)
        pane_o.rowconfigure(0, weight=1)
        self.lbl_original = ttk.Label(pane_o)
        self.lbl_original.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)

        pane_c = ttk.Frame(previews_grid)
        pane_c.columnconfigure(0, weight=1)
        pane_c.rowconfigure(0, weight=1)
        self.lbl_crop = ttk.Label(pane_c)
        self.lbl_crop.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)

        pane_f = ttk.Frame(previews_grid)
        pane_f.columnconfigure(0, weight=1)
        pane_f.rowconfigure(0, weight=1)
        self.lbl_final = ttk.Label(pane_f)
        self.lbl_final.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
        pane_o.grid(row=0, column=0, sticky="nsew")
        pane_c.grid(row=0, column=1, sticky="nsew")
        pane_f.grid(row=0, column=2, sticky="nsew")

        page_frame = ttk.Frame(self.visual_pane)
        page_frame.columnconfigure(0, weight=1)
        page_frame.rowconfigure(1, weight=1)

        ctrls = ttk.Frame(page_frame)
        ctrls.grid(row=0, column=0, sticky="ew")
        self.page_info_var = tk.StringVar(value="Página 0/0")
        self.page_zoom_var = tk.StringVar(value="100%")
        ttk.Button(ctrls, text="<<", command=lambda: self._mudar_pagina(-1)).pack(side="left")
        ttk.Button(ctrls, text=">>", command=lambda: self._mudar_pagina(1)).pack(side="left")
        ttk.Button(ctrls, text="Renderizar prévia de página", command=self._render_page_preview_thread).pack(side="left", padx=8)
        ttk.Label(ctrls, textvariable=self.page_info_var).pack(side="left", padx=10)
        ttk.Button(ctrls, text="-", width=3, command=lambda: self._ajustar_zoom_pagina(-0.1)).pack(side="left", padx=(10, 2))
        ttk.Label(ctrls, textvariable=self.page_zoom_var, width=5).pack(side="left")
        ttk.Button(ctrls, text="+", width=3, command=lambda: self._ajustar_zoom_pagina(0.1)).pack(side="left", padx=2)
        ttk.Button(ctrls, text="100%", width=5, command=self._resetar_zoom_pagina).pack(side="left", padx=(2, 0))

        self.lbl_page = ttk.Label(page_frame)
        self.lbl_page.grid(row=1, column=0, sticky="nsew", pady=4)
        self.lbl_page.bind("<Button-1>", self._on_click_page_preview)

        self.visual_pane.add(topo, minsize=120)
        self.visual_pane.add(prev_frame, minsize=180)
        self.visual_pane.add(page_frame, minsize=180)

        previews_grid.bind("<Configure>", self._on_preview_area_resize)

        for v in self.vars.values():
            try:
                v.trace_add("write", self._on_config_change)
            except Exception:
                pass
        try:
            self.vars["backend_remocao_fundo"].trace_add("write", self._on_backend_ui_changed)
        except Exception:
            pass
        for key in ("modelo_remocao_fundo", "modo_inspyrenet", "inspyrenet_device"):
            try:
                self.vars[key].trace_add("write", self._on_backend_ui_changed)
            except Exception:
                pass
        self._update_backend_specific_controls()
        self._load_layout_state()
        self.root.bind("<ButtonRelease-1>", self._on_layout_changed)
        self.root.bind("<F2>", self._start_inline_rename)

    def _pick_folder(self):
        folder = filedialog.askdirectory(initialdir=str(self.script_dir))
        if folder:
            folder_path = Path(folder)
            try:
                rel = str(folder_path.relative_to(self.script_dir))
            except ValueError:
                rel = str(folder_path)
            self.global_cfg["pasta_imagens"] = rel
            self._save_config()
            self._sync_global_sidebar_vars()
            self._reload_everything()

    def _current_images_folder(self):
        cfg = self._get_config_ui()
        pasta = Path(cfg["pasta_imagens"])
        if not pasta.is_absolute():
            pasta = self.script_dir / pasta
        return pasta

    def _last_cropper_folder(self):
        pasta = str(self.global_cfg.get("ultima_pasta_recorte", "") or "").strip()
        if pasta:
            path = Path(pasta)
            if path.exists():
                return path
        return self._current_images_folder()

    def _set_last_cropper_folder(self, pasta: Path):
        try:
            self.global_cfg["ultima_pasta_recorte"] = str(pasta)
            self._save_config()
        except Exception:
            pass

    def _save_cropper_prefs(self, cols, rows, inset, trim, prefix):
        try:
            self.global_cfg["recorte_grade_colunas"] = int(cols)
            self.global_cfg["recorte_grade_linhas"] = int(rows)
            self.global_cfg["recorte_grade_inset"] = int(inset)
            self.global_cfg["recorte_grade_remover_borda"] = bool(trim)
            self.global_cfg["recorte_grade_prefixo"] = str(prefix)
            self._save_config()
        except Exception:
            pass

    def _toggle_rename_panel(self):
        self.rename_panel_visible = not self.rename_panel_visible
        if self.rename_panel_visible:
            self.rename_panel.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, 4))
            self._sync_single_rename_field()
            self.info_img_var.set("Use a ordem visível para renumerar. Arraste itens em modo Manual.")
        else:
            self.rename_panel.grid_remove()

    def _on_single_rename_key(self, _event=None):
        self.rename_inline_dirty = True

    def _sync_single_rename_field(self):
        if not hasattr(self, "rename_single_var"):
            return
        if self.imagem_atual is None:
            self.rename_inline_dirty = False
            self.rename_single_var.set("")
            self.rename_extension_var.set("")
            self.rename_single_entry.state(["disabled"])
            return
        self.rename_single_entry.state(["!disabled"])
        self.rename_inline_dirty = False
        self.rename_single_var.set(self.imagem_atual.stem)
        self.rename_extension_var.set(self.imagem_atual.suffix)

    def _start_inline_rename(self, _event=None):
        if self.imagem_atual is None:
            return "break"
        if not self.rename_panel_visible:
            self.rename_panel_visible = True
            self.rename_panel.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, 4))
        self._sync_single_rename_field()
        self.rename_single_entry.focus_set()
        self.rename_single_entry.selection_range(0, tk.END)
        return "break"

    def _selected_image_indices(self):
        if not hasattr(self, "listbox"):
            return []
        return [i for i in self.listbox.curselection() if 0 <= i < len(self.imagens)]

    def _selected_images(self):
        return [self.imagens[i] for i in self._selected_image_indices()]

    def _refresh_image_listbox(self, selected_paths=None):
        selected_keys = set()
        if selected_paths:
            selected_keys = {self._image_key(p) for p in selected_paths}
        elif self.imagem_atual is not None:
            selected_keys = {self._image_key(self.imagem_atual)}

        self.listbox.delete(0, tk.END)
        for p in self.imagens:
            self.listbox.insert(tk.END, p.name)

        selected_any = False
        for idx, path in enumerate(self.imagens):
            if self._image_key(path) in selected_keys:
                self.listbox.selection_set(idx)
                self.listbox.see(idx)
                selected_any = True

        if not selected_any and self.imagens:
            self.listbox.selection_set(0)
            self.listbox.see(0)

    def _invalidate_page_order(self):
        self.page_cache.clear()
        self.page_layout_cache = []
        self.page_layout_signature = None
        self.paginas_cache = []
        self.dirty_page_images = {self._image_key(p) for p in self.imagens}
        if bool(self.global_cfg.get("auto_preview_pagina", False)):
            self._render_page_preview_thread()
        else:
            self.page_info_var.set("Página 0/0")
            self.lbl_page.configure(image="")

    def _apply_image_sort(self):
        if not self.imagens:
            return
        mode = self.sort_mode_var.get()
        selected = self._selected_images()
        if mode == "Manual":
            self.status_var.set("Ordem manual ativa. Arraste as imagens para reorganizar.")
            return
        if mode == "Nome Z-A":
            self.imagens.sort(key=lambda p: script.natural_key(p.name), reverse=True)
        elif mode == "Número":
            cfg = self._get_config_ui()
            self.imagens.sort(key=lambda p: script.natural_key(script.interpretar_nome_arquivo(p, cfg)[0]))
        else:
            self.imagens.sort(key=lambda p: script.natural_key(p.name))
        self._refresh_image_listbox(selected)
        self._on_select_image()
        self._invalidate_page_order()
        self.status_var.set(f"Imagens ordenadas por: {mode}.")

    def _on_listbox_drag_start(self, event):
        if not self.imagens:
            return
        idx = self.listbox.nearest(event.y)
        if 0 <= idx < len(self.imagens):
            self.list_drag_index = idx
            self.sort_mode_var.set("Manual")

    def _on_listbox_drag_motion(self, event):
        if self.list_drag_index is None or not self.imagens:
            return
        new_idx = self.listbox.nearest(event.y)
        new_idx = max(0, min(len(self.imagens) - 1, new_idx))
        old_idx = self.list_drag_index
        if new_idx == old_idx:
            return
        moved = self.imagens.pop(old_idx)
        self.imagens.insert(new_idx, moved)
        self.list_drag_index = new_idx
        selected = self._selected_images()
        self._refresh_image_listbox(selected_paths=selected or [moved])
        self.listbox.selection_clear(0, tk.END)
        self.listbox.selection_set(new_idx)
        self.listbox.see(new_idx)
        self.imagem_atual = moved
        self._invalidate_page_order()

    def _on_listbox_drag_end(self, _event):
        if self.list_drag_index is not None:
            self.list_drag_index = None
            self._on_select_image()
            self.status_var.set("Ordem manual atualizada.")

    @staticmethod
    def _split_stem_position_code(stem: str):
        upper = stem.upper()
        for code in ("SE", "SD", "IE", "ID"):
            if upper.endswith(f"_{code}") or upper.endswith(f"-{code}"):
                return stem[:-3].strip("_- "), code
        for code in ("SE", "SD", "IE", "ID"):
            if upper.endswith(code) and len(stem) > len(code):
                return stem[:-len(code)].strip("_- "), code
        if upper.endswith("_B") or upper.endswith("-B"):
            return stem[:-2].strip("_- "), "SD"
        if upper.endswith("B") and len(stem) > 1:
            return stem[:-1].strip("_- "), "SD"
        return stem.strip(), ""

    @staticmethod
    def _filename_part_is_valid(text: str):
        return "/" not in text and "\\" not in text and "\0" not in text

    def _apply_position_code_to_selected(self, code: str):
        targets = self._selected_images()
        if not targets:
            messagebox.showwarning("Aviso", "Selecione uma ou mais imagens.")
            return

        rename_map = {}
        for image in targets:
            base, _old_code = self._split_stem_position_code(image.stem)
            new_name = f"{base}_{code}{image.suffix}"
            rename_map[image] = image.with_name(new_name)
        self._apply_image_renames(rename_map, "Aplicar posição", confirm=False, refresh_page_preview=True)

    def _renumber_images(self, all_images: bool):
        targets = list(self.imagens) if all_images else self._selected_images()
        if not targets:
            messagebox.showwarning("Aviso", "Selecione uma ou mais imagens.")
            return

        try:
            width = max(1, int(self.rename_padding_var.get()))
            start = int(self.rename_start_var.get())
        except Exception:
            messagebox.showerror("Erro", "Padding e início precisam ser números válidos.")
            return

        prefix = self.rename_prefix_var.get()
        suffix = self.rename_suffix_var.get()
        if not self._filename_part_is_valid(prefix) or not self._filename_part_is_valid(suffix):
            messagebox.showerror("Erro", "Prefixo e sufixo não podem conter barras.")
            return

        rename_map = {}
        for offset, image in enumerate(targets):
            _base, code = self._split_stem_position_code(image.stem)
            number = str(start + offset).zfill(width)
            new_stem = f"{prefix}{number}{suffix}"
            if code:
                new_stem = f"{new_stem}_{code}"
            rename_map[image] = image.with_name(f"{new_stem}{image.suffix}")
        self._apply_image_renames(rename_map, "Renumerar imagens")

    def _rename_selected_image_direct(self):
        if self.imagem_atual is None:
            messagebox.showwarning("Aviso", "Selecione uma imagem.")
            return
        new_stem = self.rename_single_var.get().strip()
        if not new_stem:
            messagebox.showerror("Erro", "O nome da imagem não pode ficar vazio.")
            return
        if not self._filename_part_is_valid(new_stem):
            messagebox.showerror("Erro", "O nome da imagem não pode conter barras.")
            return
        rename_map = {
            self.imagem_atual: self.imagem_atual.with_name(f"{new_stem}{self.imagem_atual.suffix}")
        }
        self._apply_image_renames(rename_map, "Renomear imagem")

    def _apply_image_renames(self, rename_map, title: str, confirm: bool = True, refresh_page_preview: bool = False):
        changes = [
            (old, new)
            for old, new in rename_map.items()
            if self._image_key(old) != self._image_key(new)
        ]
        if not changes:
            self.status_var.set("Nenhum arquivo precisava ser renomeado.")
            return

        target_keys = [self._image_key(new) for _old, new in changes]
        if len(target_keys) != len(set(target_keys)):
            messagebox.showerror("Conflito", "Duas ou mais imagens receberiam o mesmo nome.")
            return

        source_keys = {self._image_key(old) for old, _new in changes}
        existing_conflicts = [
            new for _old, new in changes
            if new.exists() and self._image_key(new) not in source_keys
        ]
        if existing_conflicts:
            preview = "\n".join(p.name for p in existing_conflicts[:8])
            messagebox.showerror("Conflito", f"Já existem arquivos com estes nomes:\n\n{preview}")
            return

        if confirm:
            preview_lines = [f"{old.name} -> {new.name}" for old, new in changes[:10]]
            extra = "" if len(changes) <= 10 else f"\n... e mais {len(changes) - 10}"
            if not messagebox.askyesno(title, "Confirmar renomeação?\n\n" + "\n".join(preview_lines) + extra):
                return

        temp_steps = []
        completed_temp = []
        try:
            for idx, (old, new) in enumerate(changes):
                temp = old.with_name(f".rename_tmp_{os.getpid()}_{idx}{old.suffix}")
                counter = 1
                while temp.exists():
                    temp = old.with_name(f".rename_tmp_{os.getpid()}_{idx}_{counter}{old.suffix}")
                    counter += 1
                old.rename(temp)
                temp_steps.append((old, temp, new))
                completed_temp.append((old, temp))

            for old, temp, new in temp_steps:
                temp.rename(new)
                old_key = self._image_key(old)
                new_key = self._image_key(new)
                if old_key in self.image_overrides:
                    self.image_overrides[new_key] = self.image_overrides.pop(old_key)
        except Exception as exc:
            for old, temp in reversed(completed_temp):
                try:
                    if temp.exists() and not old.exists():
                        temp.rename(old)
                except Exception:
                    pass
            messagebox.showerror("Erro", f"Falha ao renomear arquivos.\n\n{exc}")
            return

        final_map = {old: new for old, new in changes}
        selected_after = [final_map.get(p, p) for p in self._selected_images()]
        self.imagens = [final_map.get(p, p) for p in self.imagens]
        self._save_overrides()
        self.preview_cache.clear()
        self.page_cache.clear()
        self.figure_cache.clear()
        self.page_layout_cache = []
        self.paginas_cache = []
        self.page_preview_meta = None
        self.preview_pagina_ref = None
        self.lbl_page.configure(image="")
        self.dirty_page_images = {self._image_key(p) for p in self.imagens}
        self._refresh_image_listbox(selected_after)
        self._on_select_image()
        self._sync_single_rename_field()
        self.status_var.set(f"{len(changes)} arquivo(s) renomeado(s).")
        if refresh_page_preview:
            self._render_page_preview_thread()

    def _apply_param_to_other_images(self, key, value=None):
        if self.imagem_atual is None or not self.imagens:
            return
        selected_key = self._image_key(self.imagem_atual)
        cfg_atual = self._collect_vars_as_cfg()
        group_keys = tuple(self.apply_all_group_keys.get(key, (key,)))
        values = {}
        for group_key in group_keys:
            if value is not None and group_key == key:
                values[group_key] = value
            elif group_key == "remover_fundo_modo":
                values[group_key] = cfg_atual.get("remover_fundo_modo", "todos")
            else:
                values[group_key] = cfg_atual.get(group_key)
        if any(v is None for v in values.values()):
            return

        changed = 0
        for imagem in self.imagens:
            image_key = self._image_key(imagem)
            if image_key == selected_key:
                continue
            override = dict(self.image_overrides.get(image_key, {}))
            for group_key, group_value in values.items():
                override[group_key] = group_value
            self.image_overrides[image_key] = override
            self.dirty_page_images.add(image_key)
            changed += 1

        if changed:
            self._save_overrides()
            self.preview_cache.clear()
            self.page_cache.clear()
            self.rembg_cache.clear()
            self.raw_cache.clear()
            self.figure_cache.clear()
            self.preview_raw_cache.clear()
            if len(group_keys) > 1:
                applied = ", ".join(group_keys)
                self.status_var.set(f"Valores de '{applied}' aplicados a {changed} outras imagens.")
            else:
                self.status_var.set(f"Valor de '{key}' aplicado a {changed} outras imagens.")
            self._refresh_all_previews()

    def _on_preview_area_resize(self, event):
        width = max(120, int(event.width // 3) - 16)
        height = max(120, int(event.height) - 12)
        new_size = (width, height)
        if new_size == self.preview_display_size:
            return
        self.preview_display_size = new_size
        if self.preview_resize_after_id is not None:
            self.root.after_cancel(self.preview_resize_after_id)
        self.preview_resize_after_id = self.root.after(120, self._refresh_image_preview_async)

    def _on_backend_ui_changed(self, *_):
        self._update_backend_specific_controls()
        if self.suspend_trace or self.backend_guard_active:
            return
        self.root.after(80, self._ensure_selected_backend_ready)

    def _update_backend_specific_controls(self):
        backend = str(self.vars.get("backend_remocao_fundo").get() if self.vars.get("backend_remocao_fundo") else "rembg")
        if hasattr(self, "backend_rembg_frame"):
            if backend == "rembg":
                self.backend_rembg_frame.grid()
            else:
                self.backend_rembg_frame.grid_remove()
        if hasattr(self, "backend_inspy_frame"):
            if backend == "inspyrenet":
                self.backend_inspy_frame.grid()
            else:
                self.backend_inspy_frame.grid_remove()

    def _load_model_markers(self):
        try:
            if self.model_markers_file.exists():
                with open(self.model_markers_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception:
            pass
        return {}

    def _save_model_markers(self):
        try:
            self.model_markers_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.model_markers_file, "w", encoding="utf-8") as f:
                json.dump(self.model_markers, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def _backend_signature(self, cfg):
        backend = script.obter_backend_remocao_fundo(cfg)
        if backend == "rembg":
            return f"rembg:{script.obter_modelo_remocao_fundo(cfg)}"
        if backend == "inspyrenet":
            return f"inspyrenet:{script.obter_modo_inspyrenet(cfg)}:{script.obter_dispositivo_inspyrenet(cfg)}"
        return backend

    @staticmethod
    def _backend_package_available(backend):
        if backend == "rembg":
            return script.garantir_rembg_importado()
        if backend == "withoutbg":
            return script.garantir_withoutbg_importado()
        if backend == "inspyrenet":
            return script.garantir_inspyrenet_importado()
        return False

    def _set_selected_backend_default(self):
        self.backend_guard_active = True
        self.suspend_trace = True
        try:
            if "backend_remocao_fundo" in self.vars:
                self.vars["backend_remocao_fundo"].set("rembg")
            if "modelo_remocao_fundo" in self.vars:
                self.vars["modelo_remocao_fundo"].set("birefnet-general-lite")
        finally:
            self.suspend_trace = False
            self.backend_guard_active = False
            self._update_backend_specific_controls()
        self._on_config_change()

    def _ensure_selected_backend_ready(self):
        if self.backend_guard_active:
            return
        cfg = self._collect_vars_as_cfg()
        backend = script.obter_backend_remocao_fundo(cfg)
        assinatura = self._backend_signature(cfg)

        if not self._backend_package_available(backend):
            if not messagebox.askyesno(
                "Backend não instalado",
                f"O backend '{backend}' ainda não está instalado.\n\nInstalar agora em:\n{script.BACKEND_DEPS.get(backend)}?",
            ):
                self._set_selected_backend_default()
                return
            if not self._instalar_backend_windows(backend):
                self._set_selected_backend_default()
                return
            self._set_selected_backend_default()
            messagebox.showinfo(
                "Reinicie o app",
                "O backend foi instalado. Reinicie o app antes de selecioná-lo novamente.",
            )
            return

        if self.model_markers.get(assinatura):
            return

        if not messagebox.askyesno(
            "Modelo não preparado",
            f"O backend/modelo selecionado ainda não foi preparado:\n{assinatura}\n\nBaixar/preparar agora?",
        ):
            self._set_selected_backend_default()
            return

        self.status_var.set(f"Preparando {assinatura}...")
        try:
            ok = script.baixar_backend_remocao_fundo(cfg)
        except Exception as exc:
            messagebox.showerror("Erro", f"Falha ao preparar backend/modelo.\n\n{exc}")
            ok = False

        if ok:
            self.model_markers[assinatura] = True
            self._save_model_markers()
            self.status_var.set(f"{assinatura} pronto para uso.")
            return

        messagebox.showwarning("Aviso", f"Não foi possível preparar {assinatura}. Voltando ao padrão.")
        self._set_selected_backend_default()

    def _show_apply_all_hint(self, key):
        if self.apply_all_hint_after_id is not None:
            self.root.after_cancel(self.apply_all_hint_after_id)
        self.status_var.set(f"Dica: clique duplo no nome do parâmetro para aplicar '{key}' a todas as outras imagens.")
        self.apply_all_hint_after_id = self.root.after(2200, lambda: self.status_var.set("Pronto"))

    def _reset_left_defaults(self):
        if self.imagem_atual is None:
            return
        self.image_overrides.pop(self._image_key(self.imagem_atual), None)
        self._save_overrides()
        self._refresh_controls_for_mode()
        self.preview_cache.clear()
        self.page_cache.clear()
        self.rembg_cache.clear()
        self.raw_cache.clear()
        self.figure_cache.clear()
        self.preview_raw_cache.clear()
        self._refresh_all_previews()

    def _reset_all_image_overrides(self):
        if not self.image_overrides:
            self.status_var.set("Nenhum override de imagem para resetar.")
            return
        if not messagebox.askyesno(
            "Resetar Todas",
            "Resetar todas as imagens para os padrões globais?",
        ):
            return
        self.image_overrides = {}
        self._save_overrides()
        self._refresh_controls_for_mode()
        self.preview_cache.clear()
        self.page_cache.clear()
        self.rembg_cache.clear()
        self.raw_cache.clear()
        self.figure_cache.clear()
        self.preview_raw_cache.clear()
        self.dirty_page_images = {self._image_key(p) for p in self.imagens}
        self._refresh_all_previews()
        self.status_var.set("Todas as imagens foram resetadas para os padrões globais.")

    def _load_layout_state(self):
        if not self.layout_file.exists():
            return
        try:
            with open(self.layout_file, "r", encoding="utf-8") as f:
                d = json.load(f)
            self.root.after(
                100,
                lambda: self._apply_layout_positions(
                    d.get("main_sash"),
                    d.get("visual_sash0"),
                    d.get("visual_sash1"),
                ),
            )
            zoom = d.get("page_zoom")
            if zoom is not None:
                self.page_zoom = max(0.3, min(2.5, float(zoom)))
                self.page_zoom_var.set(f"{int(round(self.page_zoom * 100))}%")
        except Exception:
            pass

    def _apply_layout_positions(self, main_sash, visual_sash0, visual_sash1):
        try:
            if main_sash is not None:
                self._set_sash_pos(self.main_pane, 0, int(main_sash))
            if visual_sash0 is not None:
                self._set_sash_pos(self.visual_pane, 0, int(visual_sash0))
            if visual_sash1 is not None:
                self._set_sash_pos(self.visual_pane, 1, int(visual_sash1))
        except Exception:
            pass

    def _on_layout_changed(self, _event=None):
        try:
            data = {
                "main_sash": self._get_sash_pos(self.main_pane, 0),
                "visual_sash0": self._get_sash_pos(self.visual_pane, 0),
                "visual_sash1": self._get_sash_pos(self.visual_pane, 1),
                "page_zoom": self.page_zoom,
            }
            with open(self.layout_file, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception:
            pass

    @staticmethod
    def _get_sash_pos(pane, index):
        if hasattr(pane, "sashpos"):
            return int(pane.sashpos(index))
        x, y = pane.sash_coord(index)
        orient = str(pane.cget("orient")).lower()
        return int(x if orient == "horizontal" else y)

    @staticmethod
    def _set_sash_pos(pane, index, pos):
        if hasattr(pane, "sashpos"):
            pane.sashpos(index, pos)
            return
        x, y = pane.sash_coord(index)
        orient = str(pane.cget("orient")).lower()
        if orient == "horizontal":
            pane.sash_place(index, pos, y)
        else:
            pane.sash_place(index, x, pos)

    def _open_images_folder(self):
        cfg = self._get_config_ui()
        pasta = Path(cfg["pasta_imagens"])
        if not pasta.is_absolute():
            pasta = self.script_dir / pasta
        try:
            os.startfile(str(pasta))
        except Exception as exc:
            messagebox.showerror("Erro", f"Não foi possível abrir a pasta:\n{pasta}\n\n{exc}")

    def _clear_all_cache(self):
        try:
            if self.cache_root.exists():
                shutil.rmtree(self.cache_root)
            self.rembg_cache_dir.mkdir(parents=True, exist_ok=True)
            self.pages_cache_dir.mkdir(parents=True, exist_ok=True)
            self.figures_cache_dir.mkdir(parents=True, exist_ok=True)
            self.raw_cache_dir.mkdir(parents=True, exist_ok=True)
            self.preview_cache.clear()
            self.page_cache.clear()
            self.rembg_cache.clear()
            self.raw_cache.clear()
            self.figure_cache.clear()
            self.preview_raw_cache.clear()
            self.page_layout_cache = []
            self.status_var.set("Cache limpo com sucesso.")
        except Exception as exc:
            messagebox.showerror("Erro", f"Falha ao limpar cache.\n\n{exc}")

    def _instalar_backend_windows(self, backend):
        backend = str(backend).strip().lower()
        pacotes = {
            "rembg": [
                "rembg",
                "onnxruntime-gpu",
                "nvidia-cudnn-cu12",
                "nvidia-cublas-cu12",
                "nvidia-cuda-nvrtc-cu12",
            ],
            "withoutbg": ["withoutbg"],
            "inspyrenet": ["transparent-background"],
        }.get(backend)
        destino = script.BACKEND_DEPS.get(backend)
        if not pacotes or destino is None:
            messagebox.showerror("Erro", f"Backend desconhecido: {backend}")
            return False

        destino.mkdir(parents=True, exist_ok=True)
        self.status_var.set(f"Instalando backend {backend} em {destino}...")
        try:
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--upgrade",
                    "--target",
                    str(destino),
                    *pacotes,
                ],
                check=True,
            )
            if backend == "inspyrenet":
                self.status_var.set("Instalando PyTorch CUDA para InSPyReNet...")
                subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "pip",
                        "install",
                        "--upgrade",
                        "--target",
                        str(destino),
                        "--index-url",
                        "https://download.pytorch.org/whl/cu128",
                        "torch==2.11.0+cu128",
                        "torchvision==0.26.0+cu128",
                        "torchaudio==2.11.0+cu128",
                    ],
                    check=True,
                )
        except Exception as exc:
            messagebox.showerror("Erro", f"Falha ao instalar {backend}.\n\n{exc}")
            self.status_var.set(f"Falha ao instalar backend {backend}.")
            return False

        self.status_var.set(f"Backend {backend} instalado.")
        messagebox.showinfo(
            "Concluído",
            f"Backend {backend} instalado em:\n{destino}\n\nReinicie o app para carregar novas dependências.",
        )
        return True

    def _baixar_modelo_backend(self, cfg):
        backend = script.obter_backend_remocao_fundo(cfg)
        self.status_var.set(f"Baixando modelo do backend {backend}...")
        try:
            ok = script.baixar_backend_remocao_fundo(cfg)
        except Exception as exc:
            messagebox.showerror("Erro", f"Falha ao baixar modelo.\n\n{exc}")
            self.status_var.set("Falha ao baixar modelo.")
            return
        if ok:
            self.model_markers[self._backend_signature(cfg)] = True
            self._save_model_markers()
            self.status_var.set(f"Modelo do backend {backend} pronto para uso.")
            messagebox.showinfo("Concluído", f"Modelo do backend {backend} baixado/preparado.")
        else:
            self.status_var.set(f"Backend {backend} indisponível.")
            messagebox.showwarning("Aviso", f"Não foi possível preparar o backend {backend}.")

    def _open_global_settings_dialog(self):
        win = tk.Toplevel(self.root)
        win.title("Configurações Globais")
        win.geometry("620x760+140+80")
        win.transient(self.root)
        win.grab_set()

        outer = ttk.Frame(win)
        outer.pack(fill="both", expand=True)

        canvas = tk.Canvas(outer, highlightthickness=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        frame = ttk.Frame(canvas, padding=10)
        frame_id = canvas.create_window((0, 0), window=frame, anchor="nw")

        def sync_scrollregion(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def sync_width(event):
            canvas.itemconfigure(frame_id, width=event.width)

        def on_mousewheel(event):
            delta = 0
            if getattr(event, "delta", 0):
                delta = -int(event.delta / 120)
            elif getattr(event, "num", None) == 4:
                delta = -1
            elif getattr(event, "num", None) == 5:
                delta = 1
            if delta:
                canvas.yview_scroll(delta, "units")

        frame.bind("<Configure>", sync_scrollregion)
        canvas.bind("<Configure>", sync_width)
        canvas.bind_all("<MouseWheel>", on_mousewheel)
        canvas.bind_all("<Button-4>", on_mousewheel)
        canvas.bind_all("<Button-5>", on_mousewheel)
        win.bind(
            "<Destroy>",
            lambda _event: (
                canvas.unbind_all("<MouseWheel>"),
                canvas.unbind_all("<Button-4>"),
                canvas.unbind_all("<Button-5>"),
            ),
            add="+",
        )

        frame.columnconfigure(1, weight=1)

        keys = [
            ("pasta_imagens", "Pasta imagens"),
            ("arquivo_saida_pdf", "PDF saída"),
            ("figuras_por_pagina", "Figuras por página"),
            ("orientacao", "Orientação"),
            ("margem_externa", "Margem externa"),
            ("espaco_horizontal", "Espaço horizontal"),
            ("espaco_vertical", "Espaço vertical"),
            ("borda_preta_espessura", "Espessura borda"),
            ("estilo_borda", "Estilo borda"),
            ("raio_borda", "Raio borda"),
            ("margem_interna_quadrado", "Margem interna"),
            ("tamanho_numero_relativo", "Tamanho número"),
            ("padding_numero", "Padding número"),
            ("caixa_numero_padding_x", "Padding caixa X"),
            ("caixa_numero_padding_y", "Padding caixa Y"),
            ("numero_glow_blur", "Glow blur"),
            ("numero_glow_opacidade", "Glow opacidade"),
            ("cor_borda", "Cor borda"),
            ("cor_numero", "Cor número"),
            ("cor_fundo_janela", "Cor fundo janela"),
            ("limiar_alpha", "Limiar alpha"),
            ("tolerancia_fundo", "Tolerância fundo"),
            ("limite_lado_processamento", "Máx lado processamento"),
            ("remover_fundo_modo", "Remover fundo"),
            ("backend_remocao_fundo", "Backend fundo"),
        ]
        local_vars = {}

        row = 0
        for key, label in keys:
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=3)
            if key in ("orientacao", "remover_fundo_modo", "figuras_por_pagina", "backend_remocao_fundo", "modelo_remocao_fundo", "modo_inspyrenet", "estilo_borda"):
                v = tk.StringVar(value=str(self.global_cfg.get(key, script.CONFIG_PADRAO.get(key, ""))))
                values = {
                    "orientacao": ["horizontal", "vertical"],
                    "remover_fundo_modo": ["todos", "tag_rbg", "desligado"],
                    "figuras_por_pagina": ["12", "9", "6", "4"],
                    "estilo_borda": script.listar_estilos_borda(),
                    "backend_remocao_fundo": script.listar_backends_remocao_fundo(),
                    "modelo_remocao_fundo": script.listar_modelos_rembg_disponiveis() or ["birefnet-general-lite", "birefnet-general", "bria-rmbg", "u2net"],
                    "modo_inspyrenet": script.listar_modos_inspyrenet(),
                }[key]
                cb = ttk.Combobox(frame, textvariable=v, values=values, state="readonly")
                cb.grid(row=row, column=1, sticky="ew", pady=3)
            else:
                v = tk.StringVar(value=str(self.global_cfg.get(key, script.CONFIG_PADRAO.get(key, ""))))
                ttk.Entry(frame, textvariable=v).grid(row=row, column=1, sticky="ew", pady=3)
            local_vars[key] = v
            row += 1

        backend_btns = ttk.Frame(frame)
        backend_btns.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(8, 6))

        def cfg_local_atual():
            cfg_tmp = dict(self.global_cfg)
            for k, v in local_vars.items():
                cfg_tmp[k] = v.get()
            return cfg_tmp

        ttk.Button(
            backend_btns,
            text="Instalar Backend",
            command=lambda: self._instalar_backend_windows(local_vars["backend_remocao_fundo"].get()),
        ).pack(side="left", padx=4)
        ttk.Button(
            backend_btns,
            text="Baixar Modelo",
            command=lambda: self._baixar_modelo_backend(cfg_local_atual()),
        ).pack(side="left", padx=4)
        row += 1

        rembg_adv = ttk.LabelFrame(frame, text="Opções rembg")
        rembg_adv.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(4, 6))
        rembg_adv.columnconfigure(1, weight=1)
        local_vars["modelo_remocao_fundo"] = tk.StringVar(value=str(self.global_cfg.get("modelo_remocao_fundo", script.CONFIG_PADRAO.get("modelo_remocao_fundo", ""))))
        ttk.Label(rembg_adv, text="Modelo").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Combobox(rembg_adv, textvariable=local_vars["modelo_remocao_fundo"], values=script.listar_modelos_rembg_disponiveis() or ["birefnet-general-lite", "birefnet-general", "bria-rmbg", "u2net"], state="readonly").grid(row=0, column=1, sticky="ew", pady=3)
        local_vars["rembg_alpha_matting"] = tk.BooleanVar(value=bool(self.global_cfg.get("rembg_alpha_matting", False)))
        ttk.Checkbutton(rembg_adv, text="Alpha matting", variable=local_vars["rembg_alpha_matting"]).grid(row=1, column=0, columnspan=2, sticky="w", pady=3)
        local_vars["rembg_post_process_mask"] = tk.BooleanVar(value=bool(self.global_cfg.get("rembg_post_process_mask", False)))
        ttk.Checkbutton(rembg_adv, text="Post-process mask", variable=local_vars["rembg_post_process_mask"]).grid(row=2, column=0, columnspan=2, sticky="w", pady=3)
        local_vars["rembg_foreground_threshold"] = tk.StringVar(value=str(self.global_cfg.get("rembg_foreground_threshold", 240)))
        ttk.Label(rembg_adv, text="FG threshold").grid(row=3, column=0, sticky="w", pady=3)
        ttk.Entry(rembg_adv, textvariable=local_vars["rembg_foreground_threshold"]).grid(row=3, column=1, sticky="ew", pady=3)
        local_vars["rembg_background_threshold"] = tk.StringVar(value=str(self.global_cfg.get("rembg_background_threshold", 10)))
        ttk.Label(rembg_adv, text="BG threshold").grid(row=4, column=0, sticky="w", pady=3)
        ttk.Entry(rembg_adv, textvariable=local_vars["rembg_background_threshold"]).grid(row=4, column=1, sticky="ew", pady=3)
        local_vars["rembg_erode_size"] = tk.StringVar(value=str(self.global_cfg.get("rembg_erode_size", 10)))
        ttk.Label(rembg_adv, text="Erode size").grid(row=5, column=0, sticky="w", pady=3)
        ttk.Entry(rembg_adv, textvariable=local_vars["rembg_erode_size"]).grid(row=5, column=1, sticky="ew", pady=3)

        inspy_adv = ttk.LabelFrame(frame, text="Opções InSPyReNet")
        inspy_adv.grid(row=row + 1, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        inspy_adv.columnconfigure(1, weight=1)
        local_vars["modo_inspyrenet"] = tk.StringVar(value=str(self.global_cfg.get("modo_inspyrenet", script.CONFIG_PADRAO.get("modo_inspyrenet", ""))))
        ttk.Label(inspy_adv, text="Modo").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Combobox(inspy_adv, textvariable=local_vars["modo_inspyrenet"], values=script.listar_modos_inspyrenet(), state="readonly").grid(row=0, column=1, sticky="ew", pady=3)
        local_vars["inspyrenet_device"] = tk.StringVar(value=str(self.global_cfg.get("inspyrenet_device", script.CONFIG_PADRAO.get("inspyrenet_device", "auto"))))
        ttk.Label(inspy_adv, text="Dispositivo").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Combobox(inspy_adv, textvariable=local_vars["inspyrenet_device"], values=script.listar_dispositivos_inspyrenet(), state="readonly").grid(row=1, column=1, sticky="ew", pady=3)
        row += 2

        def update_backend_groups(*_args):
            backend = str(local_vars["backend_remocao_fundo"].get())
            if backend == "rembg":
                rembg_adv.grid()
            else:
                rembg_adv.grid_remove()
            if backend == "inspyrenet":
                inspy_adv.grid()
            else:
                inspy_adv.grid_remove()

        local_vars["backend_remocao_fundo"].trace_add("write", update_backend_groups)
        update_backend_groups()

        bool_vars = {
            "evitar_sobrescrever_pdf": tk.BooleanVar(value=bool(self.global_cfg.get("evitar_sobrescrever_pdf", True))),
            "salvar_paginas_png": tk.BooleanVar(value=bool(self.global_cfg.get("salvar_paginas_png", False))),
            "auto_preview_pagina": tk.BooleanVar(value=bool(self.global_cfg.get("auto_preview_pagina", False))),
        }
        for key, text in [
            ("evitar_sobrescrever_pdf", "Não sobrescrever PDF"),
            ("salvar_paginas_png", "Salvar páginas PNG"),
            ("auto_preview_pagina", "Auto prévia de página"),
        ]:
            ttk.Checkbutton(frame, text=text, variable=bool_vars[key]).grid(row=row, column=0, columnspan=2, sticky="w", pady=3)
            row += 1

        btns = ttk.Frame(frame)
        btns.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(10, 0))

        def salvar():
            try:
                novo = dict(self.global_cfg)
                for k, v in local_vars.items():
                    val = v.get()
                    if k in ("margem_interna_quadrado", "tamanho_numero_relativo"):
                        novo[k] = float(val)
                    elif k in ("pasta_imagens", "arquivo_saida_pdf", "orientacao", "remover_fundo_modo", "cor_borda", "cor_numero", "cor_fundo_janela", "backend_remocao_fundo", "modelo_remocao_fundo", "modo_inspyrenet", "inspyrenet_device", "estilo_borda"):
                        novo[k] = str(val)
                    elif k in ("rembg_alpha_matting", "rembg_post_process_mask"):
                        novo[k] = bool(val)
                    else:
                        novo[k] = int(val)
                for k, v in bool_vars.items():
                    novo[k] = bool(v.get())
                self.global_cfg.update(novo)
                self._save_config()
                self._sync_global_sidebar_vars()
                self._apply_window_bg()
                self.preview_cache.clear()
                self.page_cache.clear()
                self.rembg_cache.clear()
                self.raw_cache.clear()
                self.figure_cache.clear()
                self.preview_raw_cache.clear()
                self._refresh_controls_for_mode()
                self._reload_everything()
                win.destroy()
            except Exception as exc:
                messagebox.showerror("Erro", f"Valores inválidos.\n\n{exc}")

        ttk.Button(btns, text="Salvar", command=salvar).pack(side="left", padx=4)
        def resetar_padrao():
            padrao = script.CONFIG_PADRAO
            for k, v in local_vars.items():
                if k in padrao:
                    try:
                        v.set(padrao[k])
                    except Exception:
                        v.set(str(padrao[k]))
            for k, v in bool_vars.items():
                if k in padrao:
                    v.set(bool(padrao[k]))
            update_backend_groups()

        ttk.Button(btns, text="Resetar padrões", command=resetar_padrao).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancelar", command=win.destroy).pack(side="left", padx=4)

    def _open_sheet_cropper(self):
        win = tk.Toplevel(self.root)
        win.title("Recortar Grade")
        win.geometry("1320x900+120+70")
        win.transient(self.root)
        win.grab_set()
        win.columnconfigure(0, weight=1)
        win.rowconfigure(1, weight=1)

        state = {
            "source_path": None,
            "image": None,
            "photo": None,
            "image_box": None,
            "crop_box": None,
            "v_lines": [],
            "h_lines": [],
            "drag": None,
            "cell_offsets": {},
            "selected_cells": set(),
            "click_cell": None,
            "drag_moved": False,
            "drag_after_id": None,
            "pending_cell_drag": None,
        }

        rows_var = tk.IntVar(value=int(self.global_cfg.get("recorte_grade_linhas", 2)))
        cols_var = tk.IntVar(value=int(self.global_cfg.get("recorte_grade_colunas", 3)))
        inset_var = tk.IntVar(value=int(self.global_cfg.get("recorte_grade_inset", 6)))
        trim_var = tk.BooleanVar(value=bool(self.global_cfg.get("recorte_grade_remover_borda", True)))
        prefix_var = tk.StringVar(value=str(self.global_cfg.get("recorte_grade_prefixo", "")))
        info_var = tk.StringVar(value="Abra uma imagem e ajuste as linhas de corte.")
        source_var = tk.StringVar(value="Nenhuma imagem aberta.")
        export_var = tk.StringVar(value=f"Salvar em: {self._current_images_folder()}")

        top = ttk.Frame(win, padding=10)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(9, weight=1)

        ttk.Button(top, text="Abrir imagem...", command=lambda: choose_source()).grid(row=0, column=0, padx=(0, 8), pady=2, sticky="w")
        ttk.Label(top, text="Colunas").grid(row=0, column=1, sticky="w")
        cols_spin = ttk.Spinbox(top, from_=1, to=12, textvariable=cols_var, width=4)
        cols_spin.grid(row=0, column=2, padx=(4, 10), pady=2, sticky="w")
        ttk.Label(top, text="Linhas").grid(row=0, column=3, sticky="w")
        rows_spin = ttk.Spinbox(top, from_=1, to=12, textvariable=rows_var, width=4)
        rows_spin.grid(row=0, column=4, padx=(4, 10), pady=2, sticky="w")
        inset_label = ttk.Label(top, text="Inset célula")
        inset_label.grid(row=0, column=5, sticky="w")
        inset_spin = ttk.Spinbox(top, from_=0, to=60, textvariable=inset_var, width=5)
        inset_spin.grid(row=0, column=6, padx=(4, 10), pady=2, sticky="w")
        trim_check = ttk.Checkbutton(top, text="Remover borda branca da célula", variable=trim_var)
        trim_check.grid(row=0, column=7, padx=(0, 10), sticky="w")
        ttk.Button(top, text="Auto detectar", command=lambda: auto_detect_lines()).grid(row=0, column=8, padx=(0, 6), pady=2, sticky="w")
        ttk.Button(top, text="Distribuir igual", command=lambda: distribute_lines()).grid(row=0, column=9, padx=(0, 6), pady=2, sticky="w")
        ttk.Button(top, text="Exportar", command=lambda: export_cells()).grid(row=0, column=10, pady=2, sticky="e")
        ToolTip(inset_label, "Inset corta alguns pixels para dentro de cada célula. A área vermelha mostra o que será descartado antes de exportar.")
        ToolTip(inset_spin, "Inset corta alguns pixels para dentro de cada célula. A área vermelha mostra o que será descartado antes de exportar.")
        ToolTip(trim_check, "Depois do corte da grade, tenta remover a borda branca conectada às bordas de cada célula.")

        ttk.Label(top, text="Prefixo").grid(row=1, column=0, sticky="w", pady=(8, 2))
        ttk.Entry(top, textvariable=prefix_var, width=28).grid(row=1, column=1, columnspan=3, sticky="ew", padx=(4, 10), pady=(8, 2))
        ttk.Label(top, textvariable=source_var).grid(row=1, column=4, columnspan=4, sticky="w", pady=(8, 2))
        ttk.Label(top, textvariable=export_var).grid(row=1, column=8, columnspan=3, sticky="e", pady=(8, 2))

        canvas = tk.Canvas(win, bg="#202020", highlightthickness=0, cursor="crosshair")
        canvas.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 8))

        bottom = ttk.Frame(win, padding=(10, 0, 10, 10))
        bottom.grid(row=2, column=0, sticky="ew")
        bottom.columnconfigure(0, weight=1)
        ttk.Label(bottom, textvariable=info_var).grid(row=0, column=0, sticky="w")

        def clamp_crop_and_lines():
            if state["image"] is None or state["crop_box"] is None:
                return
            largura, altura = state["image"].size
            left, top_y, right, bottom_y = state["crop_box"]
            left = max(0, min(left, largura - 2))
            top_y = max(0, min(top_y, altura - 2))
            right = max(left + 2, min(right, largura))
            bottom_y = max(top_y + 2, min(bottom_y, altura))
            state["crop_box"] = [left, top_y, right, bottom_y]

            min_gap = 4
            v_sorted = sorted(int(x) for x in state["v_lines"])
            h_sorted = sorted(int(y) for y in state["h_lines"])

            fixed_v = []
            prev = left
            for x in v_sorted:
                x = max(prev + min_gap, min(x, right - min_gap))
                fixed_v.append(x)
                prev = x
            for idx in range(len(fixed_v) - 1, -1, -1):
                max_here = right - min_gap * (len(fixed_v) - idx)
                fixed_v[idx] = min(fixed_v[idx], max_here)
            state["v_lines"] = fixed_v

            fixed_h = []
            prev = top_y
            for y in h_sorted:
                y = max(prev + min_gap, min(y, bottom_y - min_gap))
                fixed_h.append(y)
                prev = y
            for idx in range(len(fixed_h) - 1, -1, -1):
                max_here = bottom_y - min_gap * (len(fixed_h) - idx)
                fixed_h[idx] = min(fixed_h[idx], max_here)
            state["h_lines"] = fixed_h

        def distribute_lines():
            if state["image"] is None:
                return
            try:
                cols = max(1, int(cols_var.get()))
                rows = max(1, int(rows_var.get()))
            except Exception:
                return
            if state["crop_box"] is None:
                largura, altura = state["image"].size
                state["crop_box"] = [0, 0, largura, altura]
            left, top_y, right, bottom_y = state["crop_box"]
            width = right - left
            height = bottom_y - top_y
            state["v_lines"] = [int(round(left + width * idx / cols)) for idx in range(1, cols)]
            state["h_lines"] = [int(round(top_y + height * idx / rows)) for idx in range(1, rows)]
            state["cell_offsets"] = {}
            state["selected_cells"] = {(row, col) for row in range(rows) for col in range(cols)}
            clamp_crop_and_lines()
            redraw_preview()

        def current_cell_boxes():
            if state["crop_box"] is None:
                return []
            try:
                cols = max(1, int(cols_var.get()))
                rows = max(1, int(rows_var.get()))
                inset = max(0, int(inset_var.get()))
            except Exception:
                return []
            x_positions = [state["crop_box"][0], *state["v_lines"], state["crop_box"][2]]
            y_positions = [state["crop_box"][1], *state["h_lines"], state["crop_box"][3]]
            cells = []
            for row in range(rows):
                for col in range(cols):
                    outer = (
                        x_positions[col],
                        y_positions[row],
                        x_positions[col + 1],
                        y_positions[row + 1],
                    )
                    base_inner = (
                        outer[0] + inset,
                        outer[1] + inset,
                        outer[2] - inset,
                        outer[3] - inset,
                    )
                    dx, dy = state["cell_offsets"].get((row, col), (0, 0))
                    if base_inner[2] <= base_inner[0] or base_inner[3] <= base_inner[1]:
                        inner = base_inner
                        dx = 0
                        dy = 0
                    else:
                        min_dx = outer[0] - base_inner[0]
                        max_dx = outer[2] - base_inner[2]
                        min_dy = outer[1] - base_inner[1]
                        max_dy = outer[3] - base_inner[3]
                        dx = max(min_dx, min(int(dx), max_dx))
                        dy = max(min_dy, min(int(dy), max_dy))
                        state["cell_offsets"][(row, col)] = (dx, dy)
                        inner = (
                            base_inner[0] + dx,
                            base_inner[1] + dy,
                            base_inner[2] + dx,
                            base_inner[3] + dy,
                        )
                    cells.append((row, col, outer, inner))
            return cells

        def projection_density(gray_img, axis):
            largura, altura = gray_img.size
            px = gray_img.load()
            if axis == "x":
                dens = []
                for x in range(largura):
                    score = 0
                    for y in range(altura):
                        if px[x, y] < 245:
                            score += 1
                    dens.append(score)
                return dens
            dens = []
            for y in range(altura):
                score = 0
                for x in range(largura):
                    if px[x, y] < 245:
                        score += 1
                dens.append(score)
            return dens

        def detect_bounds(dens, total_other_axis):
            threshold = max(2, int(total_other_axis * 0.01))
            start = 0
            end = len(dens) - 1
            while start < len(dens) and dens[start] <= threshold:
                start += 1
            while end >= 0 and dens[end] <= threshold:
                end -= 1
            if end <= start:
                return 0, len(dens)
            return start, end + 1

        def detect_internal_lines(dens, start, end, count):
            if count <= 1:
                return []
            region = max(2, end - start)
            segment = region / count
            out = []
            for idx in range(1, count):
                target = int(round(start + segment * idx))
                radius = max(8, int(segment * 0.2))
                lo = max(start + 2, target - radius)
                hi = min(end - 2, target + radius)
                if hi <= lo:
                    out.append(target)
                    continue
                best = min(range(lo, hi + 1), key=lambda pos: (dens[pos], abs(pos - target)))
                out.append(best)
            return out

        def auto_detect_lines():
            if state["image"] is None:
                return
            try:
                cols = max(1, int(cols_var.get()))
                rows = max(1, int(rows_var.get()))
            except Exception:
                messagebox.showerror("Erro", "Linhas e colunas precisam ser números válidos.")
                return

            gray = state["image"].convert("L")
            dens_x = projection_density(gray, "x")
            dens_y = projection_density(gray, "y")
            left, right = detect_bounds(dens_x, gray.height)
            top_y, bottom_y = detect_bounds(dens_y, gray.width)
            state["crop_box"] = [left, top_y, right, bottom_y]
            state["v_lines"] = detect_internal_lines(dens_x, left, right, cols)
            state["h_lines"] = detect_internal_lines(dens_y, top_y, bottom_y, rows)
            state["cell_offsets"] = {}
            state["selected_cells"] = {(row, col) for row in range(rows) for col in range(cols)}
            clamp_crop_and_lines()
            redraw_preview()
            info_var.set("Linhas detectadas automaticamente. Arraste para ajustar se necessário.")

        def choose_source():
            initial_dir = self._last_cropper_folder()
            if (
                not initial_dir.exists()
                and state["source_path"] is not None
                and state["source_path"].parent.exists()
            ):
                initial_dir = state["source_path"].parent
            file_path = filedialog.askopenfilename(
                title="Escolher imagem da grade",
                initialdir=str(initial_dir),
                filetypes=[
                    ("Imagens", "*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff"),
                    ("Todos", "*.*"),
                ],
            )
            if file_path:
                load_source(Path(file_path))

        def load_source(path: Path, update_last_folder: bool = True):
            try:
                with Image.open(path) as im:
                    state["image"] = im.convert("RGB")
            except Exception as exc:
                messagebox.showerror("Erro", f"Não foi possível abrir a imagem.\n\n{exc}")
                return
            state["source_path"] = path
            if update_last_folder:
                self._set_last_cropper_folder(path.parent)
            self._save_cropper_prefs(cols_var.get(), rows_var.get(), inset_var.get(), trim_var.get(), prefix_var.get().strip())
            source_var.set(f"Origem: {path.name}")
            if not prefix_var.get().strip():
                prefix_var.set(path.stem)
            self._save_cropper_prefs(cols_var.get(), rows_var.get(), inset_var.get(), trim_var.get(), prefix_var.get().strip())
            export_var.set(f"Salvar em: {self._current_images_folder()}")
            auto_detect_lines()

        def image_to_canvas(x, y):
            box = state["image_box"]
            if box is None or state["image"] is None:
                return 0, 0
            left, top_y, right, bottom_y = box
            largura, altura = state["image"].size
            scale_x = (right - left) / max(1, largura)
            scale_y = (bottom_y - top_y) / max(1, altura)
            return left + x * scale_x, top_y + y * scale_y

        def canvas_to_image(x, y):
            box = state["image_box"]
            if box is None or state["image"] is None:
                return 0, 0
            left, top_y, right, bottom_y = box
            largura, altura = state["image"].size
            scale_x = largura / max(1, right - left)
            scale_y = altura / max(1, bottom_y - top_y)
            img_x = int(round((x - left) * scale_x))
            img_y = int(round((y - top_y) * scale_y))
            return img_x, img_y

        def redraw_preview(_event=None):
            canvas.delete("all")
            if state["image"] is None:
                canvas.create_text(
                    max(40, canvas.winfo_width() // 2),
                    max(30, canvas.winfo_height() // 2),
                    text="Abra uma imagem para recortar a grade.",
                    fill="#f0f0f0",
                    font=("TkDefaultFont", 14),
                )
                return

            canvas_w = max(200, canvas.winfo_width())
            canvas_h = max(200, canvas.winfo_height())
            display = state["image"].copy()
            display.thumbnail((canvas_w - 20, canvas_h - 20), Image.LANCZOS)
            preview_rgba = display.convert("RGBA")
            left = (canvas_w - display.width) // 2
            top_y = (canvas_h - display.height) // 2
            right = left + display.width
            bottom_y = top_y + display.height
            state["image_box"] = (left, top_y, right, bottom_y)

            if state["crop_box"] is None:
                largura, altura = state["image"].size
                state["crop_box"] = [0, 0, largura, altura]
                distribute_lines()

            clamp_crop_and_lines()
            crop_left, crop_top, crop_right, crop_bottom = state["crop_box"]
            overlay = Image.new("RGBA", preview_rgba.size, (0, 0, 0, 0))
            overlay_draw = ImageDraw.Draw(overlay, "RGBA")

            def image_to_preview_coords(x, y):
                px = int(round((x / max(1, state["image"].width)) * preview_rgba.width))
                py = int(round((y / max(1, state["image"].height)) * preview_rgba.height))
                return px, py

            for row, col, outer, inner in current_cell_boxes():
                ox0, oy0 = image_to_preview_coords(outer[0], outer[1])
                ox1, oy1 = image_to_preview_coords(outer[2], outer[3])
                ix0, iy0 = image_to_preview_coords(inner[0], inner[1])
                ix1, iy1 = image_to_preview_coords(inner[2], inner[3])
                selected = (row, col) in state["selected_cells"]

                if iy0 > oy0:
                    overlay_draw.rectangle((ox0, oy0, ox1, iy0), fill=(216, 76, 76, 95))
                if oy1 > iy1:
                    overlay_draw.rectangle((ox0, iy1, ox1, oy1), fill=(216, 76, 76, 95))
                if ix0 > ox0 and iy1 > iy0:
                    overlay_draw.rectangle((ox0, iy0, ix0, iy1), fill=(216, 76, 76, 95))
                if ox1 > ix1 and iy1 > iy0:
                    overlay_draw.rectangle((ix1, iy0, ox1, iy1), fill=(216, 76, 76, 95))
                if not selected:
                    overlay_draw.rectangle((ox0, oy0, ox1, oy1), fill=(255, 59, 48, 110))

            preview_rgba.alpha_composite(overlay)
            state["photo"] = ImageTk.PhotoImage(preview_rgba)
            canvas.create_image(left, top_y, image=state["photo"], anchor="nw")

            x0, y0 = image_to_canvas(crop_left, crop_top)
            x1, y1 = image_to_canvas(crop_right, crop_bottom)
            canvas.create_rectangle(x0, y0, x1, y1, outline="#ff5252", width=2)

            for row, col, outer, inner in current_cell_boxes():
                ox0, oy0 = image_to_canvas(outer[0], outer[1])
                ox1, oy1 = image_to_canvas(outer[2], outer[3])
                ix0, iy0 = image_to_canvas(inner[0], inner[1])
                ix1, iy1 = image_to_canvas(inner[2], inner[3])
                selected = (row, col) in state["selected_cells"]
                if ix1 > ix0 and iy1 > iy0:
                    canvas.create_rectangle(ix0, iy0, ix1, iy1, outline="#6cf28a" if selected else "#ff7272", width=1)
                canvas.create_text(
                    ox0 + 8,
                    oy0 + 8,
                    anchor="nw",
                    text=f"{row + 1},{col + 1}",
                    fill="#ffffff" if selected else "#ffd0d0",
                    font=("TkDefaultFont", 9, "bold"),
                )

            for pos in state["v_lines"]:
                cx0, cy0 = image_to_canvas(pos, crop_top)
                cx1, cy1 = image_to_canvas(pos, crop_bottom)
                canvas.create_line(cx0, cy0, cx1, cy1, fill="#25b7ff", width=2)
            for pos in state["h_lines"]:
                cx0, cy0 = image_to_canvas(crop_left, pos)
                cx1, cy1 = image_to_canvas(crop_right, pos)
                canvas.create_line(cx0, cy0, cx1, cy1, fill="#25b7ff", width=2)

            canvas.create_text(
                12,
                12,
                anchor="nw",
                text="Vermelho: área descartada | Azul: cortes internos | Clique numa célula para incluir/excluir",
                fill="#ffffff",
                font=("TkDefaultFont", 10),
            )

        def on_grid_change(*_args):
            if state["image"] is not None:
                distribute_lines()

        def begin_drag(event):
            if state["image"] is None or state["crop_box"] is None or state["image_box"] is None:
                return
            state["drag_moved"] = False
            state["click_cell"] = None
            if state["drag_after_id"] is not None:
                win.after_cancel(state["drag_after_id"])
                state["drag_after_id"] = None
            state["pending_cell_drag"] = None
            img_x, img_y = canvas_to_image(event.x, event.y)
            crop_left, crop_top, crop_right, crop_bottom = state["crop_box"]
            threshold = 10
            candidates = [
                (abs(img_x - crop_left), ("left", 0)),
                (abs(img_x - crop_right), ("right", 0)),
                (abs(img_y - crop_top), ("top", 0)),
                (abs(img_y - crop_bottom), ("bottom", 0)),
            ]
            for idx, pos in enumerate(state["v_lines"]):
                candidates.append((abs(img_x - pos), ("v", idx)))
            for idx, pos in enumerate(state["h_lines"]):
                candidates.append((abs(img_y - pos), ("h", idx)))
            dist, drag = min(candidates, key=lambda item: item[0])
            if dist <= threshold:
                state["drag"] = drag
                return
            for row, col, outer, inner in current_cell_boxes():
                if (row, col) in state["selected_cells"] and inner[0] <= img_x <= inner[2] and inner[1] <= img_y <= inner[3]:
                    state["click_cell"] = (row, col)
                    state["pending_cell_drag"] = (row, col, img_x, img_y)
                    def activate_cell_drag():
                        if state["pending_cell_drag"] == (row, col, img_x, img_y):
                            state["drag"] = ("cell", row, col, img_x, img_y)
                            state["drag_after_id"] = None
                    state["drag_after_id"] = win.after(180, activate_cell_drag)
                    return
                if outer[0] <= img_x <= outer[2] and outer[1] <= img_y <= outer[3]:
                    state["click_cell"] = (row, col)
                    break

        def drag_motion(event):
            if state["image"] is None or state["crop_box"] is None:
                return
            img_x, img_y = canvas_to_image(event.x, event.y)

            if state["drag"] is None:
                if state["pending_cell_drag"] is not None:
                    row, col, start_x, start_y = state["pending_cell_drag"]
                    if abs(img_x - start_x) > 3 or abs(img_y - start_y) > 3:
                        state["pending_cell_drag"] = None
                        if state["drag_after_id"] is not None:
                            win.after_cancel(state["drag_after_id"])
                            state["drag_after_id"] = None
                return

            state["drag_moved"] = True
            crop_left, crop_top, crop_right, crop_bottom = state["crop_box"]
            largura, altura = state["image"].size
            min_gap = 4
            kind = state["drag"][0]

            if kind == "cell":
                _kind, row, col, last_x, last_y = state["drag"]
                dx = img_x - last_x
                dy = img_y - last_y
                cur_dx, cur_dy = state["cell_offsets"].get((row, col), (0, 0))
                state["cell_offsets"][(row, col)] = (cur_dx + dx, cur_dy + dy)
                state["drag"] = ("cell", row, col, img_x, img_y)
                redraw_preview()
                return

            _kind, index = state["drag"]

            if kind in ("left", "right", "v"):
                prev = 0 if kind == "left" else (state["v_lines"][index - 1] if kind == "v" and index > 0 else crop_left)
                nxt = largura if kind == "right" else (state["v_lines"][index + 1] if kind == "v" and index < len(state["v_lines"]) - 1 else crop_right)
                new_x = max(prev + min_gap, min(img_x, nxt - min_gap))
                if kind == "left":
                    state["crop_box"][0] = new_x
                elif kind == "right":
                    state["crop_box"][2] = new_x
                else:
                    state["v_lines"][index] = new_x
            else:
                prev = 0 if kind == "top" else (state["h_lines"][index - 1] if kind == "h" and index > 0 else crop_top)
                nxt = altura if kind == "bottom" else (state["h_lines"][index + 1] if kind == "h" and index < len(state["h_lines"]) - 1 else crop_bottom)
                new_y = max(prev + min_gap, min(img_y, nxt - min_gap))
                if kind == "top":
                    state["crop_box"][1] = new_y
                elif kind == "bottom":
                    state["crop_box"][3] = new_y
                else:
                    state["h_lines"][index] = new_y

            clamp_crop_and_lines()
            redraw_preview()

        def end_drag(_event=None):
            if state["drag_after_id"] is not None:
                win.after_cancel(state["drag_after_id"])
                state["drag_after_id"] = None
            if state["drag"] is None and state["pending_cell_drag"] is not None and not state["drag_moved"]:
                state["click_cell"] = (state["pending_cell_drag"][0], state["pending_cell_drag"][1])
            if state["drag"] is None and state["click_cell"] is not None and not state["drag_moved"]:
                cell = state["click_cell"]
                if cell in state["selected_cells"]:
                    state["selected_cells"].remove(cell)
                else:
                    state["selected_cells"].add(cell)
                redraw_preview()
            state["drag"] = None
            state["pending_cell_drag"] = None
            state["click_cell"] = None
            state["drag_moved"] = False

        def export_cells():
            if state["image"] is None or state["crop_box"] is None:
                messagebox.showwarning("Aviso", "Abra uma imagem antes de exportar.")
                return

            try:
                cols = max(1, int(cols_var.get()))
                rows = max(1, int(rows_var.get()))
                inset = max(0, int(inset_var.get()))
            except Exception:
                messagebox.showerror("Erro", "Linhas, colunas e inset precisam ser números válidos.")
                return

            output_dir = self._current_images_folder()
            output_dir.mkdir(parents=True, exist_ok=True)
            export_var.set(f"Salvar em: {output_dir}")
            self._save_cropper_prefs(cols, rows, inset, bool(trim_var.get()), prefix_var.get().strip())

            base_name = prefix_var.get().strip() or (state["source_path"].stem if state["source_path"] else "recorte")
            suffix = state["source_path"].suffix.lower() if state["source_path"] else ".png"
            if suffix not in script.EXTENSOES_ACEITAS:
                suffix = ".png"

            cfg_trim = self._get_config_ui()
            selected_cells = sorted(state["selected_cells"])
            if not selected_cells:
                messagebox.showwarning("Aviso", "Selecione pelo menos uma célula para exportar.")
                return
            digits = max(2, len(str(len(selected_cells))))
            saved = []
            export_index = 1

            for row, col, _outer, inner in current_cell_boxes():
                if (row, col) not in state["selected_cells"]:
                    continue
                x0, y0, x1, y1 = inner
                if x1 <= x0 or y1 <= y0:
                    continue
                cell = state["image"].crop((x0, y0, x1, y1)).convert("RGBA")
                if bool(trim_var.get()):
                    cell = script.cortar_espacos_brancos(cell, cfg_trim)
                target = output_dir / f"{base_name}_{str(export_index).zfill(digits)}{suffix}"
                target = script.obter_caminho_saida_disponivel(target)
                if suffix in (".jpg", ".jpeg"):
                    cell.convert("RGB").save(target, quality=95)
                else:
                    cell.save(target)
                saved.append(target)
                export_index += 1

            if not saved:
                messagebox.showwarning("Aviso", "Nenhuma célula válida foi exportada.")
                return

            self.status_var.set(f"{len(saved)} imagem(ns) exportada(s) para {output_dir}.")
            self._reload_everything()
            messagebox.showinfo("Concluído", f"{len(saved)} imagem(ns) exportada(s) em:\n{output_dir}")

        cols_var.trace_add("write", on_grid_change)
        rows_var.trace_add("write", on_grid_change)
        inset_var.trace_add("write", lambda *_args: redraw_preview())
        canvas.bind("<Configure>", redraw_preview)
        canvas.bind("<ButtonPress-1>", begin_drag)
        canvas.bind("<B1-Motion>", drag_motion)
        canvas.bind("<ButtonRelease-1>", end_drag)

        if self.imagem_atual is not None and self.imagem_atual.exists():
            load_source(self.imagem_atual, update_last_folder=False)
        else:
            redraw_preview()

    def _get_config_ui(self):
        cfg = script.CONFIG_PADRAO.copy()
        cfg.update(self.global_cfg)
        cfg["figuras_por_pagina"] = int(cfg["figuras_por_pagina"])
        cfg["margem_interna_quadrado"] = float(cfg["margem_interna_quadrado"])
        cfg["tamanho_numero_relativo"] = float(cfg["tamanho_numero_relativo"])
        return cfg

    def _collect_vars_as_cfg(self):
        cfg = self._get_config_ui()
        for key, var in self.vars.items():
            cfg[key] = var.get()
        if "remover_fundo_local" in self.vars:
            cfg["remover_fundo_modo"] = "todos" if bool(self.vars["remover_fundo_local"].get()) else "desligado"
            cfg.pop("remover_fundo_local", None)
        return cfg

    def _collect_image_override_cfg(self):
        cfg = self._collect_vars_as_cfg()
        return {k: cfg[k] for k in IMAGE_OVERRIDE_KEYS if k in cfg}

    def _set_vars_from_cfg(self, cfg):
        self.suspend_trace = True
        try:
            for key, var in self.vars.items():
                if key == "remover_fundo_local":
                    try:
                        var.set(str(cfg.get("remover_fundo_modo", "todos")) != "desligado")
                    except Exception:
                        pass
                    continue
                if key not in cfg:
                    continue
                try:
                    var.set(cfg[key])
                except Exception:
                    pass
        finally:
            self.suspend_trace = False
            self._update_backend_specific_controls()

    def _bind_tooltip(self, widget, key, apply_all=False):
        text = self.param_help.get(key, "")
        try:
            is_apply_all_label = key in self.apply_all_label_keys and str(widget.cget("cursor")) == "hand2"
        except Exception:
            is_apply_all_label = False
        if apply_all or is_apply_all_label:
            extra = "Duplo clique no nome do parâmetro para aplicar este valor a todas as outras imagens."
            text = f"{text}\n\n{extra}" if text else extra
        if text:
            existing = getattr(widget, "_app_tooltip", None)
            if existing is not None:
                existing.text = text
                return
            tip = ToolTip(widget, text)
            setattr(widget, "_app_tooltip", tip)
            self.tooltips.append(tip)

    def _bind_static_tooltip(self, widget, text: str):
        if not text:
            return
        existing = getattr(widget, "_app_tooltip", None)
        if existing is not None:
            existing.text = text
            return
        tip = ToolTip(widget, text)
        setattr(widget, "_app_tooltip", tip)
        self.tooltips.append(tip)

    def _bind_scale_wheel(self, widget, var, minimum, maximum, step):
        minimum = float(minimum)
        maximum = float(maximum)
        step = float(step)

        def on_wheel(event):
            direction = 0
            if getattr(event, "delta", 0):
                direction = 1 if event.delta > 0 else -1
            elif getattr(event, "num", None) == 4:
                direction = 1
            elif getattr(event, "num", None) == 5:
                direction = -1
            if not direction:
                return "break"

            try:
                current = float(var.get())
            except Exception:
                current = minimum

            new_value = max(minimum, min(maximum, current + direction * step))
            try:
                if isinstance(var, tk.IntVar):
                    var.set(int(round(new_value)))
                else:
                    var.set(round(new_value, 4))
            except Exception:
                pass
            return "break"

        widget.bind("<MouseWheel>", on_wheel)
        widget.bind("<Button-4>", on_wheel)
        widget.bind("<Button-5>", on_wheel)

    def _save_config(self):
        cfg = script.CONFIG_PADRAO.copy()
        cfg.update(self.global_cfg)
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        self.status_var.set("Configuração salva.")

    def _sync_global_sidebar_vars(self):
        if not hasattr(self, "global_sidebar_vars"):
            return
        self.suspend_trace = True
        try:
            for key, var in self.global_sidebar_vars.items():
                if key in self.global_cfg:
                    var.set(self.global_cfg[key])
        finally:
            self.suspend_trace = False

    def _on_global_sidebar_change(self, *_):
        if self.suspend_trace:
            return
        if self.global_sidebar_after_id is not None:
            self.root.after_cancel(self.global_sidebar_after_id)
        self.global_sidebar_after_id = self.root.after(300, self._apply_global_sidebar_change)

    def _apply_global_sidebar_change(self):
        try:
            for key, var in self.global_sidebar_vars.items():
                val = var.get()
                if key in ("figuras_por_pagina", "margem_externa", "espaco_horizontal", "espaco_vertical", "limite_lado_processamento", "raio_borda"):
                    self.global_cfg[key] = int(float(val))
                else:
                    self.global_cfg[key] = str(val)
            self._save_config()
            self.page_cache.clear()
            self.raw_cache.clear()
            self.figure_cache.clear()
            self.preview_raw_cache.clear()
            self.page_layout_cache = []
            self.dirty_page_images = {self._image_key(p) for p in self.imagens}
            if bool(self.global_cfg.get("auto_preview_pagina", False)):
                self._render_page_preview_thread()
            else:
                self._try_show_cached_page_preview()
        except Exception as exc:
            self.status_var.set(f"Erro ao aplicar layout global: {exc}")

    def _load_images(self):
        cfg = self._get_config_ui()
        pasta = Path(cfg["pasta_imagens"])
        if not pasta.is_absolute():
            pasta = self.script_dir / pasta
        if not pasta.exists():
            self.imagens = []
            self.listbox.delete(0, tk.END)
            self.info_img_var.set(f"Pasta não encontrada: {pasta}")
            self.imagem_atual = None
            self._sync_single_rename_field()
            return
        self.imagens = script.listar_imagens(pasta)
        if hasattr(self, "sort_mode_var"):
            self.sort_mode_var.set("Nome A-Z")
        self._refresh_image_listbox()
        if self.imagens:
            self._on_select_image()
        else:
            self.info_img_var.set("Nenhuma imagem encontrada.")

    def _reload_everything(self):
        self.preview_cache.clear()
        self.page_cache.clear()
        self.rembg_cache.clear()
        self.raw_cache.clear()
        self.figure_cache.clear()
        self.preview_raw_cache.clear()
        self.page_layout_cache = []
        self._load_images()
        self._refresh_all_previews()

    def _on_select_image(self):
        sel = self.listbox.curselection()
        if not sel or not self.imagens:
            self.imagem_atual = None
            self._sync_single_rename_field()
            return
        self.imagem_atual = self.imagens[sel[0]]
        self._sync_single_rename_field()
        self._refresh_controls_for_mode()
        self._refresh_image_preview_async()

    def _clear_image_override(self):
        if self.imagem_atual is None:
            return
        self.image_overrides.pop(self._image_key(self.imagem_atual), None)
        self._save_overrides()
        self._refresh_controls_for_mode()
        self.preview_cache.clear()
        self.page_cache.clear()
        self.rembg_cache.clear()
        self.raw_cache.clear()
        self.figure_cache.clear()
        self.preview_raw_cache.clear()
        self._refresh_all_previews()

    def _apply_current_image_to_global(self):
        if self.imagem_atual is None:
            return
        cfg_vars = self._collect_vars_as_cfg()
        self.global_cfg.update(cfg_vars)
        self._save_config()
        self._sync_global_sidebar_vars()
        self._refresh_controls_for_mode()
        self.preview_cache.clear()
        self.page_cache.clear()
        self.rembg_cache.clear()
        self.raw_cache.clear()
        self.figure_cache.clear()
        self.preview_raw_cache.clear()
        self._refresh_all_previews()

    def _refresh_controls_for_mode(self):
        if self.imagem_atual is not None:
            cfg = self._effective_config_for_image(self.imagem_atual, self._get_config_ui())
            self._set_vars_from_cfg(cfg)
        else:
            self._set_vars_from_cfg(self.global_cfg)

    def _on_config_change(self, *_):
        if self.suspend_trace:
            return

        cfg_vars = self._collect_vars_as_cfg()
        if self.imagem_atual is not None:
            key = self._image_key(self.imagem_atual)
            self.image_overrides[key] = self._collect_image_override_cfg()
            self.dirty_page_images.add(key)
            self._save_overrides()
        else:
            self.global_cfg.update(cfg_vars)

        if self.preview_after_id is not None:
            self.root.after_cancel(self.preview_after_id)
        self.preview_after_id = self.root.after(350, self._refresh_image_preview_async)

        if bool(self.vars.get("auto_preview_pagina", tk.BooleanVar(value=False)).get()):
            if self.page_auto_after_id is not None:
                self.root.after_cancel(self.page_auto_after_id)
            self.page_auto_after_id = self.root.after(1200, self._render_page_preview_thread)

    def _refresh_all_previews(self):
        self._refresh_image_preview_async()
        self._try_show_cached_page_preview()

    def _try_show_cached_page_preview(self):
        if not self.imagens:
            return
        try:
            cfg = self._get_config_ui()
            page_key = self._page_cache_key(cfg)
            paginas, layout = self._load_pages_cache_disk(page_key)
            if paginas is None:
                return
            self.paginas_cache = paginas
            self.page_layout_cache = layout
            self.page_layout_signature = self._page_layout_signature(cfg)
            self.indice_pagina_preview = 0
            self._update_page_preview_ui()
            self.status_var.set("Prévia de página carregada do cache.")
        except Exception:
            pass

    @staticmethod
    def _fit_image(img, max_w, max_h):
        copia = img.copy()
        copia.thumbnail((max_w, max_h), Image.LANCZOS)
        return copia

    @staticmethod
    def _composite_for_preview(img, bg_rgb=None):
        if img.mode != "RGBA":
            return img.convert("RGB")
        if bg_rgb is None:
            return img.convert("RGB")
        fundo = Image.new("RGBA", img.size, (bg_rgb[0], bg_rgb[1], bg_rgb[2], 255))
        fundo.alpha_composite(img)
        return fundo.convert("RGB")

    def _refresh_image_preview_async(self):
        if self.imagem_atual is None:
            return
        if not self.preview_lock.acquire(blocking=False):
            return

        self.preview_req_id += 1
        req_id = self.preview_req_id
        cfg = self._get_config_ui()
        imagem = self.imagem_atual
        self.status_var.set("Atualizando preview da imagem...")
        self.progress.configure(mode="indeterminate")
        self.progress.start(8)
        threading.Thread(
            target=self._refresh_image_preview_worker,
            args=(req_id, imagem, cfg),
            daemon=True,
        ).start()

    def _refresh_image_preview_worker(self, req_id, imagem, cfg):
        try:
            cfg_img = self._effective_config_for_image(imagem, cfg)
            backend_warning = None
            cache_key = self._preview_cache_key(imagem, cfg_img)
            cache_hit = self.preview_cache.get(cache_key)

            if cache_hit is None:
                raw_key = self._preview_raw_cache_key(imagem, cfg_img)
                raw = self.preview_raw_cache.get(raw_key)
                if raw is None:
                    raw_disk = self._load_raw_cache_disk(raw_key)
                    if raw_disk is None:
                        with Image.open(imagem) as im:
                            original = script.reduzir_para_processamento(im, cfg_img)
                            rembg_key = self._rembg_cache_key(imagem, cfg_img)
                            rembg_cached = self.rembg_cache.get(rembg_key)
                            if rembg_cached is None:
                                bg_removed = self._load_rembg_cache_disk(rembg_key)
                                if bg_removed is None:
                                    bg_removed = script.aplicar_remocao_fundo(original, imagem, cfg_img)
                                    backend_warning = script.obter_ultimo_erro_remocao_fundo()
                                    self._save_rembg_cache_disk(rembg_key, bg_removed)
                                self.rembg_cache[rembg_key] = bg_removed.copy()
                            else:
                                bg_removed = rembg_cached.copy()
                                backend_warning = None
                            cropped = script.cortar_espacos_brancos(bg_removed, cfg_img)
                        numero, posicao = script.interpretar_nome_arquivo(imagem, cfg_img)
                        self._save_raw_cache_disk(raw_key, original, cropped, numero, posicao)
                    else:
                        original, cropped, numero, posicao = raw_disk
                    self.preview_raw_cache[raw_key] = (original.copy(), cropped.copy(), numero, posicao)
                else:
                    original, cropped, numero, posicao = raw[0].copy(), raw[1].copy(), raw[2], raw[3]
                numero, posicao = script.interpretar_nome_arquivo(imagem, cfg_img)

                fig_key_data = self._figure_key_data(imagem, cfg_img, 720, numero, posicao)
                final = self._load_figure_cache_disk(imagem, 720, fig_key_data)
                if final is None:
                    final = script.transformar_em_quadrado_com_margem(cropped, 720, cfg_img)
                    script.desenhar_borda_preta(final, cfg_img)
                    script.desenhar_numero_com_glow(final, numero, posicao, cfg_img)
                    self._save_figure_cache_disk(imagem, 720, fig_key_data, final)
                self.preview_cache[cache_key] = (
                    original.copy(),
                    cropped.copy(),
                    final.copy(),
                    numero,
                    posicao,
                    backend_warning,
                )
            else:
                original, cropped, final, numero, posicao, backend_warning = (
                    cache_hit[0].copy(),
                    cache_hit[1].copy(),
                    cache_hit[2].copy(),
                    cache_hit[3],
                    cache_hit[4],
                    cache_hit[5] if len(cache_hit) > 5 else None,
                )

            o = self._fit_image(self._composite_for_preview(original), *self.preview_display_size)
            c = self._fit_image(self._composite_for_preview(cropped, (0, 0, 0)), *self.preview_display_size)
            f = self._fit_image(self._composite_for_preview(final), *self.preview_display_size)

            area_o = original.width * original.height
            area_c = cropped.width * cropped.height
            reducao = 100.0 * (1.0 - (area_c / area_o)) if area_o > 0 else 0.0
            info = (
                f"{imagem.name} | número: {numero} | posição: {posicao} | "
                f"recorte: {original.width}x{original.height} -> {cropped.width}x{cropped.height} "
                f"({reducao:.1f}% área removida)"
            )
            self.root.after(0, lambda: self._apply_preview_result(req_id, o, c, f, info, backend_warning))
        except Exception as exc:
            self.root.after(0, lambda: self.status_var.set(f"Erro no preview da imagem: {exc}"))
            self.root.after(0, self._release_preview_lock)

    def _apply_preview_result(self, req_id, o, c, f, info, backend_warning=None):
        try:
            if req_id != self.preview_req_id:
                return
            self.preview_original_ref = ImageTk.PhotoImage(o)
            self.preview_crop_ref = ImageTk.PhotoImage(c)
            self.preview_final_ref = ImageTk.PhotoImage(f)
            self.lbl_original.configure(image=self.preview_original_ref)
            self.lbl_crop.configure(image=self.preview_crop_ref)
            self.lbl_final.configure(image=self.preview_final_ref)
            self.info_img_var.set(info)
            self.preview_backend_warning = backend_warning
            if backend_warning:
                self.status_var.set(
                    f"Aviso: backend {backend_warning['backend']} falhou para a imagem atual; usando fallback. {backend_warning['detalhe']}"
                )
            else:
                self.status_var.set("Preview da imagem atualizado.")
        finally:
            self._release_preview_lock()

    def _release_preview_lock(self):
        if self.preview_lock.locked():
            self.preview_lock.release()
        if not self.render_lock.locked():
            self._stop_progress()

    def _refresh_image_preview(self):
        # Compatibilidade com chamadas antigas.
        self._refresh_image_preview_async()

    def _render_page_preview_thread(self):
        if not self.imagens:
            return
        if not self.render_lock.acquire(blocking=False):
            return
        self.status_var.set("Renderizando prévia de página...")
        self.progress.configure(mode="indeterminate")
        self.progress.start(8)
        threading.Thread(target=self._render_page_preview_worker, daemon=True).start()

    def _render_page_preview_worker(self):
        try:
            cfg = self._get_config_ui()
            page_key = self._page_cache_key(cfg)
            cached = self.page_cache.get(page_key)
            if cached is None:
                patched = self._try_patch_dirty_page_cells(cfg)
                if patched is not None:
                    paginas, layout = patched
                    self._save_pages_cache_disk(page_key, paginas, layout)
                else:
                    paginas, layout = self._load_pages_cache_disk(page_key)
                if paginas is None:
                    paginas, layout = self._criar_paginas_ui(self.imagens, cfg)
                    self._save_pages_cache_disk(page_key, paginas, layout)
                self.page_cache[page_key] = ([p.copy() for p in paginas], layout)
            else:
                paginas = [p.copy() for p in cached[0]]
                layout = cached[1]
            self.paginas_cache = paginas
            self.page_layout_cache = layout
            self.page_layout_signature = self._page_layout_signature(cfg)
            self.dirty_page_images.clear()
            if self.indice_pagina_preview >= len(paginas):
                self.indice_pagina_preview = max(0, len(paginas) - 1)
            self.root.after(0, self._update_page_preview_ui)
        except Exception as exc:
            self.root.after(0, lambda: self.status_var.set(f"Erro na prévia de página: {exc}"))
        finally:
            self.root.after(0, self._stop_progress)
            self.render_lock.release()

    def _update_page_preview_ui(self):
        if not self.paginas_cache:
            self.page_info_var.set("Página 0/0")
            self.lbl_page.configure(image="")
            return
        total = len(self.paginas_cache)
        idx = self.indice_pagina_preview + 1
        self.page_info_var.set(f"Página {idx}/{total}")
        pagina = self.paginas_cache[self.indice_pagina_preview]
        base = self._fit_image(pagina, 900, 420)
        if abs(self.page_zoom - 1.0) > 0.001:
            img = base.resize(
                (
                    max(1, int(round(base.width * self.page_zoom))),
                    max(1, int(round(base.height * self.page_zoom))),
                ),
                Image.LANCZOS,
            )
        else:
            img = base
        self.page_zoom_var.set(f"{int(round(self.page_zoom * 100))}%")
        self.page_preview_meta = {
            "orig_w": pagina.width,
            "orig_h": pagina.height,
            "disp_w": img.width,
            "disp_h": img.height,
        }
        self.preview_pagina_ref = ImageTk.PhotoImage(img)
        self.lbl_page.configure(image=self.preview_pagina_ref)
        self.status_var.set("Prévia de página atualizada.")

    def _ajustar_zoom_pagina(self, delta):
        self.page_zoom = max(0.3, min(2.5, self.page_zoom + float(delta)))
        self._update_page_preview_ui()
        self._on_layout_changed()

    def _resetar_zoom_pagina(self):
        self.page_zoom = 1.0
        self._update_page_preview_ui()
        self._on_layout_changed()

    def _mudar_pagina(self, delta):
        if not self.paginas_cache:
            return
        novo = self.indice_pagina_preview + delta
        novo = max(0, min(len(self.paginas_cache) - 1, novo))
        if novo != self.indice_pagina_preview:
            self.indice_pagina_preview = novo
            self._update_page_preview_ui()

    def _gerar_pdf_thread(self):
        if not self.imagens:
            messagebox.showwarning("Aviso", "Nenhuma imagem encontrada.")
            return
        if not self.render_lock.acquire(blocking=False):
            messagebox.showinfo("Aguarde", "Uma renderização já está em andamento.")
            return
        self.status_var.set("Gerando PDF...")
        self.progress.configure(mode="indeterminate")
        self.progress.start(8)
        threading.Thread(target=self._gerar_pdf_worker, daemon=True).start()

    def _gerar_pdf_worker(self):
        try:
            cfg = self._get_config_ui()
            arquivo_saida = self.script_dir / cfg["arquivo_saida_pdf"]
            if bool(cfg.get("evitar_sobrescrever_pdf", True)):
                arquivo_saida = script.obter_caminho_saida_disponivel(arquivo_saida)

            paginas, _layout = self._criar_paginas_ui(self.imagens, cfg)
            arquivo_final = script.salvar_pdf(paginas, arquivo_saida)

            if cfg.get("salvar_paginas_png", False):
                script.salvar_paginas_png(paginas, self.script_dir)

            self.root.after(0, lambda: self.status_var.set(f"PDF gerado: {arquivo_final}"))
            self.root.after(0, lambda: messagebox.showinfo("Concluído", f"PDF gerado em:\n{arquivo_final}"))
        except Exception as exc:
            self.root.after(0, lambda: messagebox.showerror("Erro", str(exc)))
            self.root.after(0, lambda: self.status_var.set(f"Erro ao gerar PDF: {exc}"))
        finally:
            self.root.after(0, self._stop_progress)
            self.render_lock.release()

    def _stop_progress(self):
        self.progress.stop()
        self.progress.configure(mode="determinate")

    def _effective_config_for_image(self, imagem: Path, cfg: dict):
        out = dict(cfg)
        ov = self.image_overrides.get(self._image_key(imagem))
        if ov:
            out.update(ov)
        return out

    def _page_layout_signature(self, cfg: dict):
        return (
            tuple(self._image_key(p) for p in self.imagens),
            tuple((p.stat().st_mtime_ns, p.stat().st_size) for p in self.imagens),
            int(cfg.get("figuras_por_pagina", 12)),
            str(cfg.get("orientacao", "horizontal")),
            int(cfg.get("margem_externa", 80)),
            int(cfg.get("espaco_horizontal", 30)),
            int(cfg.get("espaco_vertical", 30)),
        )

    def _try_patch_dirty_page_cells(self, cfg: dict):
        if not self.paginas_cache or not self.page_layout_cache or not self.dirty_page_images:
            return None
        if self.page_layout_signature != self._page_layout_signature(cfg):
            return None

        paginas = [p.copy() for p in self.paginas_cache]
        layout = self.page_layout_cache
        patched_any = False

        for page_index, page_layout in enumerate(layout):
            for item in page_layout:
                caminho = item["img"]
                if self._image_key(caminho) not in self.dirty_page_images:
                    continue
                cfg_img = self._effective_config_for_image(caminho, cfg)
                figura = self._render_single_cell(caminho, int(item["size"]), cfg_img)
                x = int(item["x"])
                y = int(item["y"])
                paginas[page_index].paste(figura, (x, y))
                patched_any = True

        if not patched_any:
            return None
        return paginas, layout

    def _render_single_cell(self, caminho: Path, tamanho_quadrado: int, cfg_img: dict):
        raw_key = self._raw_cache_key(caminho, cfg_img)
        raw = self.raw_cache.get(raw_key)
        if raw is None:
            raw_disk = self._load_raw_cache_disk(raw_key)
            if raw_disk is None:
                with Image.open(caminho) as im:
                    original = script.reduzir_para_processamento(im, cfg_img)
                    rembg_key = self._rembg_cache_key(caminho, cfg_img)
                    rembg_cached = self.rembg_cache.get(rembg_key)
                    if rembg_cached is None:
                        bg_removed = self._load_rembg_cache_disk(rembg_key)
                        if bg_removed is None:
                            bg_removed = script.aplicar_remocao_fundo(original, caminho, cfg_img)
                            self._save_rembg_cache_disk(rembg_key, bg_removed)
                        self.rembg_cache[rembg_key] = bg_removed.copy()
                    else:
                        bg_removed = rembg_cached.copy()
                    cropped = script.cortar_espacos_brancos(bg_removed, cfg_img)
                numero, posicao = script.interpretar_nome_arquivo(caminho, cfg_img)
                self._save_raw_cache_disk(raw_key, original, cropped, numero, posicao)
            else:
                original, cropped, numero, posicao = raw_disk
            self.raw_cache[raw_key] = (original.copy(), cropped.copy(), numero, posicao)
        else:
            _original, cropped, numero, posicao = raw[0].copy(), raw[1].copy(), raw[2], raw[3]
        numero, posicao = script.interpretar_nome_arquivo(caminho, cfg_img)

        fig_key = (
            raw_key,
            tamanho_quadrado,
            str(numero),
            str(posicao),
            int(cfg_img.get("borda_preta_espessura", 8)),
            str(cfg_img.get("estilo_borda", "solida")),
            int(cfg_img.get("raio_borda", 0)),
            float(cfg_img.get("margem_interna_quadrado", 0.06)),
            float(cfg_img.get("tamanho_numero_relativo", 0.085)),
            int(cfg_img.get("padding_numero", 10)),
            int(cfg_img.get("caixa_numero_padding_x", 10)),
            int(cfg_img.get("caixa_numero_padding_y", 6)),
            int(cfg_img.get("numero_glow_blur", 4)),
            int(cfg_img.get("numero_glow_opacidade", 220)),
            str(cfg_img.get("cor_borda", "#000000")),
            str(cfg_img.get("cor_numero", "#000000")),
            str(cfg_img.get("posicao_padrao_numero", "superior_esquerdo")),
        )
        fig_cached = self.figure_cache.get(fig_key)
        if fig_cached is not None:
            return fig_cached.copy()

        fig_key_data = self._figure_key_data(caminho, cfg_img, tamanho_quadrado, numero, posicao)
        figura_rgba = self._load_figure_cache_disk(caminho, tamanho_quadrado, fig_key_data)
        if figura_rgba is None:
            figura_rgba = script.transformar_em_quadrado_com_margem(cropped.copy(), tamanho_quadrado, cfg_img)
            script.desenhar_borda_preta(figura_rgba, cfg_img)
            script.desenhar_numero_com_glow(figura_rgba, numero, posicao, cfg_img)
            self._save_figure_cache_disk(caminho, tamanho_quadrado, fig_key_data, figura_rgba)
        figura = figura_rgba.convert("RGB")
        self.figure_cache[fig_key] = figura.copy()
        return figura

    def _criar_paginas_ui(self, figuras, cfg):
        paginas = []
        layout_paginas = []

        pagina_largura, pagina_altura = script.obter_tamanho_pagina(cfg)
        colunas, linhas = script.obter_grade(cfg["figuras_por_pagina"], cfg.get("orientacao"))
        figuras_por_pagina = colunas * linhas
        margem_externa = int(cfg["margem_externa"])
        espaco_horizontal = int(cfg["espaco_horizontal"])
        espaco_vertical = int(cfg["espaco_vertical"])

        largura_celula = (
            pagina_largura - 2 * margem_externa - (colunas - 1) * espaco_horizontal
        ) // colunas
        altura_celula = (
            pagina_altura - 2 * margem_externa - (linhas - 1) * espaco_vertical
        ) // linhas
        tamanho_quadrado = min(largura_celula, altura_celula)

        for inicio in range(0, len(figuras), figuras_por_pagina):
            lote = figuras[inicio:inicio + figuras_por_pagina]
            pagina = Image.new("RGB", (pagina_largura, pagina_altura), (255, 255, 255))
            layout = []
            for indice, caminho in enumerate(lote):
                cfg_img = self._effective_config_for_image(caminho, cfg)
                figura = self._render_single_cell(caminho, tamanho_quadrado, cfg_img)

                linha = indice // colunas
                coluna = indice % colunas
                x_celula = margem_externa + coluna * (largura_celula + espaco_horizontal)
                y_celula = margem_externa + linha * (altura_celula + espaco_vertical)
                x = x_celula + (largura_celula - tamanho_quadrado) // 2
                y = y_celula + (altura_celula - tamanho_quadrado) // 2

                pagina.paste(figura, (x, y))
                layout.append({"img": caminho, "x": x, "y": y, "size": tamanho_quadrado})

            paginas.append(pagina)
            layout_paginas.append(layout)

        return paginas, layout_paginas

    def _on_click_page_preview(self, event):
        if not self.paginas_cache or not self.page_preview_meta:
            return
        if self.indice_pagina_preview >= len(self.page_layout_cache):
            return
        meta = self.page_preview_meta
        if meta["disp_w"] <= 0 or meta["disp_h"] <= 0:
            return

        px = int(event.x * (meta["orig_w"] / meta["disp_w"]))
        py = int(event.y * (meta["orig_h"] / meta["disp_h"]))
        for item in self.page_layout_cache[self.indice_pagina_preview]:
            x = item["x"]
            y = item["y"]
            s = item["size"]
            if x <= px <= x + s and y <= py <= y + s:
                self._select_image_in_list(item["img"])
                return

    def _select_image_in_list(self, caminho: Path):
        try:
            idx = self.imagens.index(caminho)
        except ValueError:
            return
        self.listbox.selection_clear(0, tk.END)
        self.listbox.selection_set(idx)
        self.listbox.see(idx)
        self._on_select_image()

    def _image_content_fingerprint(self, imagem: Path):
        st = imagem.stat()
        stat_key = (str(imagem), st.st_mtime_ns, st.st_size)
        cached = self.image_content_cache.get(stat_key)
        if cached is not None:
            return cached
        h = hashlib.sha1()
        with open(imagem, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        fingerprint = h.hexdigest()
        self.image_content_cache[stat_key] = fingerprint
        return fingerprint

    @staticmethod
    def _preview_cache_key(imagem: Path, cfg: dict):
        st = imagem.stat()
        return (
            CACHE_SCHEMA_VERSION,
            script.normalizar_chave_imagem(imagem),
            st.st_mtime_ns,
            st.st_size,
            str(cfg.get("backend_remocao_fundo", "rembg")),
            str(cfg.get("remover_fundo_modo", "todos")),
            bool(script.deve_remover_fundo(imagem, cfg)),
            str(cfg.get("modelo_remocao_fundo", "birefnet-general-lite")),
            str(cfg.get("modo_inspyrenet", "base")),
            bool(cfg.get("rembg_alpha_matting", False)),
            bool(cfg.get("rembg_post_process_mask", False)),
            int(cfg.get("rembg_foreground_threshold", 240)),
            int(cfg.get("rembg_background_threshold", 10)),
            int(cfg.get("rembg_erode_size", 10)),
            int(cfg.get("limiar_alpha", 10)),
            int(cfg.get("tolerancia_fundo", 18)),
            float(cfg.get("margem_interna_quadrado", 0.06)),
            int(cfg.get("borda_preta_espessura", 8)),
            str(cfg.get("estilo_borda", "solida")),
            int(cfg.get("raio_borda", 0)),
            float(cfg.get("tamanho_numero_relativo", 0.085)),
            int(cfg.get("padding_numero", 10)),
            int(cfg.get("caixa_numero_padding_x", 10)),
            int(cfg.get("caixa_numero_padding_y", 6)),
            int(cfg.get("numero_glow_blur", 4)),
            int(cfg.get("numero_glow_opacidade", 220)),
            str(cfg.get("cor_borda", "#000000")),
            str(cfg.get("cor_numero", "#000000")),
            str(cfg.get("posicao_padrao_numero", "superior_esquerdo")),
        )

    def _raw_cache_key(self, imagem: Path, cfg: dict):
        st = imagem.stat()
        return (
            CACHE_SCHEMA_VERSION,
            self._image_content_fingerprint(imagem),
            st.st_mtime_ns,
            st.st_size,
            str(cfg.get("backend_remocao_fundo", "rembg")),
            str(cfg.get("remover_fundo_modo", "todos")),
            bool(script.deve_remover_fundo(imagem, cfg)),
            str(cfg.get("modelo_remocao_fundo", "birefnet-general-lite")),
            str(cfg.get("modo_inspyrenet", "base")),
            bool(cfg.get("rembg_alpha_matting", False)),
            bool(cfg.get("rembg_post_process_mask", False)),
            int(cfg.get("rembg_foreground_threshold", 240)),
            int(cfg.get("rembg_background_threshold", 10)),
            int(cfg.get("rembg_erode_size", 10)),
            int(cfg.get("limiar_alpha", 10)),
            int(cfg.get("tolerancia_fundo", 18)),
            int(cfg.get("limite_lado_processamento", 2000)),
        )

    def _preview_raw_cache_key(self, imagem: Path, cfg: dict):
        st = imagem.stat()
        return (
            CACHE_SCHEMA_VERSION,
            self._image_content_fingerprint(imagem),
            st.st_mtime_ns,
            st.st_size,
            str(cfg.get("backend_remocao_fundo", "rembg")),
            str(cfg.get("remover_fundo_modo", "todos")),
            bool(script.deve_remover_fundo(imagem, cfg)),
            str(cfg.get("modelo_remocao_fundo", "birefnet-general-lite")),
            str(cfg.get("modo_inspyrenet", "base")),
            bool(cfg.get("rembg_alpha_matting", False)),
            bool(cfg.get("rembg_post_process_mask", False)),
            int(cfg.get("rembg_foreground_threshold", 240)),
            int(cfg.get("rembg_background_threshold", 10)),
            int(cfg.get("rembg_erode_size", 10)),
            int(cfg.get("limiar_alpha", 10)),
            int(cfg.get("tolerancia_fundo", 18)),
            int(cfg.get("limite_lado_processamento", 2000)),
        )

    def _page_cache_key(self, cfg: dict):
        img_sig = tuple(
            (self._image_key(p), p.stat().st_mtime_ns, p.stat().st_size)
            for p in self.imagens
        )
        ov_sig = tuple(
            (
                self._image_key(p),
                self._render_signature_for_image(self._effective_config_for_image(p, cfg)),
            )
            for p in self.imagens
        )
        return (
            CACHE_SCHEMA_VERSION,
            img_sig,
            ov_sig,
            int(cfg.get("figuras_por_pagina", 12)),
            str(cfg.get("orientacao", "horizontal")),
            int(cfg.get("margem_externa", 80)),
            int(cfg.get("espaco_horizontal", 30)),
            int(cfg.get("espaco_vertical", 30)),
            int(cfg.get("borda_preta_espessura", 8)),
            float(cfg.get("margem_interna_quadrado", 0.06)),
            str(cfg.get("posicao_padrao_numero", "superior_esquerdo")),
            float(cfg.get("tamanho_numero_relativo", 0.085)),
            int(cfg.get("padding_numero", 10)),
            int(cfg.get("caixa_numero_padding_x", 10)),
            int(cfg.get("caixa_numero_padding_y", 6)),
            int(cfg.get("numero_glow_blur", 4)),
            int(cfg.get("numero_glow_opacidade", 220)),
            int(cfg.get("limiar_alpha", 10)),
            int(cfg.get("tolerancia_fundo", 18)),
            int(cfg.get("limite_lado_processamento", 2000)),
            str(cfg.get("backend_remocao_fundo", "rembg")),
            str(cfg.get("remover_fundo_modo", "todos")),
            str(cfg.get("modelo_remocao_fundo", "birefnet-general-lite")),
            str(cfg.get("modo_inspyrenet", "base")),
            bool(cfg.get("rembg_alpha_matting", False)),
            bool(cfg.get("rembg_post_process_mask", False)),
            int(cfg.get("rembg_foreground_threshold", 240)),
            int(cfg.get("rembg_background_threshold", 10)),
            int(cfg.get("rembg_erode_size", 10)),
        )

    @staticmethod
    def _render_signature_for_image(cfg: dict):
        return (
            str(cfg.get("backend_remocao_fundo", "rembg")),
            str(cfg.get("remover_fundo_modo", "todos")),
            str(cfg.get("modelo_remocao_fundo", "birefnet-general-lite")),
            str(cfg.get("modo_inspyrenet", "base")),
            bool(cfg.get("rembg_alpha_matting", False)),
            bool(cfg.get("rembg_post_process_mask", False)),
            int(cfg.get("rembg_foreground_threshold", 240)),
            int(cfg.get("rembg_background_threshold", 10)),
            int(cfg.get("rembg_erode_size", 10)),
            int(cfg.get("limiar_alpha", 10)),
            int(cfg.get("tolerancia_fundo", 18)),
            int(cfg.get("limite_lado_processamento", 2000)),
            float(cfg.get("margem_interna_quadrado", 0.06)),
            int(cfg.get("borda_preta_espessura", 8)),
            str(cfg.get("estilo_borda", "solida")),
            int(cfg.get("raio_borda", 0)),
            float(cfg.get("tamanho_numero_relativo", 0.085)),
            int(cfg.get("padding_numero", 10)),
            int(cfg.get("caixa_numero_padding_x", 10)),
            int(cfg.get("caixa_numero_padding_y", 6)),
            int(cfg.get("numero_glow_blur", 4)),
            int(cfg.get("numero_glow_opacidade", 220)),
            str(cfg.get("cor_borda", "#000000")),
            str(cfg.get("cor_numero", "#000000")),
            str(cfg.get("posicao_padrao_numero", "superior_esquerdo")),
        )

    def _rembg_cache_key(self, imagem: Path, cfg: dict):
        st = imagem.stat()
        return (
            CACHE_SCHEMA_VERSION,
            self._image_content_fingerprint(imagem),
            st.st_mtime_ns,
            st.st_size,
            str(cfg.get("backend_remocao_fundo", "rembg")),
            str(cfg.get("remover_fundo_modo", "todos")),
            bool(script.deve_remover_fundo(imagem, cfg)),
            str(cfg.get("modelo_remocao_fundo", "birefnet-general-lite")),
            str(cfg.get("modo_inspyrenet", "base")),
            bool(cfg.get("rembg_alpha_matting", False)),
            bool(cfg.get("rembg_post_process_mask", False)),
            int(cfg.get("rembg_foreground_threshold", 240)),
            int(cfg.get("rembg_background_threshold", 10)),
            int(cfg.get("rembg_erode_size", 10)),
            int(cfg.get("limite_lado_processamento", 2000)),
        )

    def _rembg_cache_file(self, key):
        raw = "|".join(str(x) for x in key)
        h = hashlib.sha1(raw.encode("utf-8")).hexdigest()
        return self.rembg_cache_dir / f"{h}.png"

    def _load_rembg_cache_disk(self, key):
        caminho = self._rembg_cache_file(key)
        if not caminho.exists():
            return None
        try:
            with Image.open(caminho) as im:
                return im.convert("RGBA")
        except Exception:
            return None

    def _save_rembg_cache_disk(self, key, img_rgba):
        caminho = self._rembg_cache_file(key)
        try:
            img_rgba.save(caminho, format="PNG")
        except Exception:
            pass

    @staticmethod
    def _image_hash(imagem: Path):
        return hashlib.sha1(script.normalizar_chave_imagem(imagem).encode("utf-8")).hexdigest()

    def _figure_cache_paths(self, imagem: Path, tamanho: int):
        h = self._image_hash(imagem)
        img_file = self.figures_cache_dir / f"{h}_{tamanho}.png"
        meta_file = self.figures_cache_dir / f"{h}_{tamanho}.json"
        return img_file, meta_file

    def _figure_key_data(self, imagem: Path, cfg: dict, tamanho: int, numero: str, posicao: str):
        st = imagem.stat()
        return {
            "cache_schema_version": CACHE_SCHEMA_VERSION,
            "image": self._image_key(imagem),
            "mtime": int(st.st_mtime_ns),
            "size": int(st.st_size),
            "tamanho": int(tamanho),
            "numero": str(numero),
            "posicao": str(posicao),
            "margem_interna_quadrado": float(cfg.get("margem_interna_quadrado", 0.06)),
            "borda_preta_espessura": int(cfg.get("borda_preta_espessura", 8)),
            "estilo_borda": str(cfg.get("estilo_borda", "solida")),
            "raio_borda": int(cfg.get("raio_borda", 0)),
            "tamanho_numero_relativo": float(cfg.get("tamanho_numero_relativo", 0.085)),
            "padding_numero": int(cfg.get("padding_numero", 10)),
            "caixa_numero_padding_x": int(cfg.get("caixa_numero_padding_x", 10)),
            "caixa_numero_padding_y": int(cfg.get("caixa_numero_padding_y", 6)),
            "numero_glow_blur": int(cfg.get("numero_glow_blur", 4)),
            "numero_glow_opacidade": int(cfg.get("numero_glow_opacidade", 220)),
            "cor_borda": str(cfg.get("cor_borda", "#000000")),
            "cor_numero": str(cfg.get("cor_numero", "#000000")),
            "backend_remocao_fundo": str(cfg.get("backend_remocao_fundo", "rembg")),
            "rembg_alpha_matting": bool(cfg.get("rembg_alpha_matting", False)),
            "rembg_post_process_mask": bool(cfg.get("rembg_post_process_mask", False)),
            "rembg_foreground_threshold": int(cfg.get("rembg_foreground_threshold", 240)),
            "rembg_background_threshold": int(cfg.get("rembg_background_threshold", 10)),
            "rembg_erode_size": int(cfg.get("rembg_erode_size", 10)),
            "limiar_alpha": int(cfg.get("limiar_alpha", 10)),
            "tolerancia_fundo": int(cfg.get("tolerancia_fundo", 18)),
            "limite_lado_processamento": int(cfg.get("limite_lado_processamento", 2000)),
            "remover_fundo_modo": str(cfg.get("remover_fundo_modo", "todos")),
            "modelo_remocao_fundo": str(cfg.get("modelo_remocao_fundo", "birefnet-general-lite")),
            "modo_inspyrenet": str(cfg.get("modo_inspyrenet", "base")),
        }

    def _load_figure_cache_disk(self, imagem: Path, tamanho: int, key_data: dict):
        img_file, meta_file = self._figure_cache_paths(imagem, tamanho)
        if not img_file.exists() or not meta_file.exists():
            return None
        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                meta = json.load(f)
            if meta != key_data:
                return None
            with Image.open(img_file) as im:
                return im.convert("RGBA")
        except Exception:
            return None

    def _save_figure_cache_disk(self, imagem: Path, tamanho: int, key_data: dict, figura_rgba):
        img_file, meta_file = self._figure_cache_paths(imagem, tamanho)
        try:
            figura_rgba.save(img_file, format="PNG")
            with open(meta_file, "w", encoding="utf-8") as f:
                json.dump(key_data, f)
        except Exception:
            pass

    def _raw_cache_paths(self, key):
        h = self._hash_any(key)
        base = self.raw_cache_dir / h
        return base.with_suffix(".json"), base.with_name(base.name + "_orig.png"), base.with_name(base.name + "_crop.png")

    def _save_raw_cache_disk(self, key, original_rgba, cropped_rgba, numero, posicao):
        meta, orig_file, crop_file = self._raw_cache_paths(key)
        try:
            original_rgba.save(orig_file, format="PNG")
            cropped_rgba.save(crop_file, format="PNG")
            with open(meta, "w", encoding="utf-8") as f:
                json.dump({"numero": str(numero), "posicao": str(posicao)}, f)
        except Exception:
            pass

    def _load_raw_cache_disk(self, key):
        meta, orig_file, crop_file = self._raw_cache_paths(key)
        if not meta.exists() or not orig_file.exists() or not crop_file.exists():
            return None
        try:
            with open(meta, "r", encoding="utf-8") as f:
                info = json.load(f)
            with Image.open(orig_file) as im_o, Image.open(crop_file) as im_c:
                return (
                    im_o.convert("RGBA"),
                    im_c.convert("RGBA"),
                    str(info.get("numero", "")),
                    str(info.get("posicao", "superior_esquerdo")),
                )
        except Exception:
            return None

    @staticmethod
    def _hash_any(value):
        raw = repr(value).encode("utf-8")
        return hashlib.sha1(raw).hexdigest()

    def _pages_cache_paths(self, page_key):
        h = self._hash_any(page_key)
        meta = self.pages_cache_dir / f"{h}.json"
        return h, meta

    def _save_pages_cache_disk(self, page_key, paginas, layout):
        h, meta = self._pages_cache_paths(page_key)
        try:
            serial_layout = []
            for page_layout in layout:
                serial_layout.append(
                    [
                        {
                            "img": str(item["img"]),
                            "x": int(item["x"]),
                            "y": int(item["y"]),
                            "size": int(item["size"]),
                        }
                        for item in page_layout
                    ]
                )
            for i, page in enumerate(paginas):
                page.save(self.pages_cache_dir / f"{h}_{i:03d}.jpg", format="JPEG", quality=90)
            with open(meta, "w", encoding="utf-8") as f:
                json.dump({"count": len(paginas), "layout": serial_layout}, f)
        except Exception:
            pass

    def _load_overrides(self):
        if not self.overrides_file.exists():
            return
        try:
            with open(self.overrides_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                cleaned = {}
                for path_key, payload in data.items():
                    if not isinstance(payload, dict):
                        continue
                    cleaned[script.normalizar_chave_imagem(path_key)] = {
                        k: payload[k] for k in IMAGE_OVERRIDE_KEYS if k in payload
                    }
                self.image_overrides = cleaned
        except Exception:
            self.image_overrides = {}

    def _save_overrides(self):
        try:
            self.cache_root.mkdir(parents=True, exist_ok=True)
            with open(self.overrides_file, "w", encoding="utf-8") as f:
                json.dump(self.image_overrides, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _load_pages_cache_disk(self, page_key):
        h, meta = self._pages_cache_paths(page_key)
        if not meta.exists():
            return None, None
        try:
            with open(meta, "r", encoding="utf-8") as f:
                data = json.load(f)
            count = int(data.get("count", 0))
            if count <= 0:
                return None, None
            paginas = []
            for i in range(count):
                p = self.pages_cache_dir / f"{h}_{i:03d}.jpg"
                if not p.exists():
                    return None, None
                with Image.open(p) as im:
                    paginas.append(im.convert("RGB"))
            layout = []
            for page_layout in data.get("layout", []):
                layout.append(
                    [
                        {
                            "img": Path(item["img"]),
                            "x": int(item["x"]),
                            "y": int(item["y"]),
                            "size": int(item["size"]),
                        }
                        for item in page_layout
                    ]
                )
            return paginas, layout
        except Exception:
            return None, None


def main():
    root = tk.Tk()
    app = PDFSheetUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
