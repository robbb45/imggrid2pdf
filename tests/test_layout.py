import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image, ImageChops, ImageDraw

import layout
import script
import ui


def config(**values):
    return {**script.CONFIG_PADRAO, "modo_layout": "encaixe",
            "remover_fundo_modo": "desligado", "limite_lado_processamento": 0,
            "encaixe_figuras_por_pagina": 6, **values}


def items_for(ratios, cfg):
    return [{"img": Path(f"{i + 1:02}.png"), "source_size": (int(r * 1000), 1000), "config": cfg}
            for i, r in enumerate(ratios)]


class LayoutTests(unittest.TestCase):
    ratios = [3.1, 0.52, 2.6, 0.62, 1.1, 1.9, 0.45, 1.3, 3.6, 0.72, 2.2, 1]

    def assert_geometry(self, pages, source_items, cfg, size):
        originals = {item["img"]: item for item in source_items}
        flattened = [r for page in pages for r in page]
        self.assertCountEqual([r["img"] for r in flattened], list(originals))
        for page in pages:
            for i, a in enumerate(page):
                self.assertGreaterEqual(a["x"], cfg["margem_externa"])
                self.assertGreaterEqual(a["y"], cfg["margem_externa"])
                self.assertLessEqual(a["x"] + a["width"], size[0] - cfg["margem_externa"])
                self.assertLessEqual(a["y"] + a["height"], size[1] - cfg["margem_externa"])
                w, h = originals[a["img"]]["source_size"]
                if a["rotation"]:
                    w, h = h, w
                # Integer print pixels may differ from the exact ratio by <1 px per side.
                ratio = w / h
                self.assertLessEqual(abs(a["content_width"] - a["content_height"] * ratio), 1 + ratio)
                for b in page[i + 1:]:
                    self.assertTrue(
                        a["x"] + a["width"] + cfg["espaco_horizontal"] <= b["x"]
                        or b["x"] + b["width"] + cfg["espaco_horizontal"] <= a["x"]
                        or a["y"] + a["height"] + cfg["espaco_vertical"] <= b["y"]
                        or b["y"] + b["height"] + cfg["espaco_vertical"] <= a["y"],
                        f"Overlapping frames: {a} and {b}")

    def test_counts_proportions_margins_and_gaps(self):
        for count in (1, 4, 6, 9, 12):
            for orientation in ("horizontal", "vertical"):
                for same_area in (False, True):
                    with self.subTest(count=count, orientation=orientation, same_area=same_area):
                        cfg = config(encaixe_figuras_por_pagina=count, orientacao=orientation,
                                     encaixe_mesma_area=same_area, espaco_horizontal=23, espaco_vertical=41)
                        items = items_for(self.ratios[:count] + [1.4, 0.7], cfg)
                        size = script.obter_tamanho_pagina(cfg)
                        pages = layout.arrange(items, size, cfg)
                        self.assertEqual([len(p) for p in pages], [count] * (len(items) // count) + ([len(items) % count] if len(items) % count else []))
                        self.assert_geometry(pages, items, cfg, size)

    def test_equal_area_is_optional_and_includes_last_page(self):
        cfg = config(encaixe_mesma_area=True)
        items = items_for(self.ratios[:6] + [1.4], cfg)
        size = script.obter_tamanho_pagina(cfg)
        equal = layout.arrange(items, size, cfg)
        areas = [r["content_width"] * r["content_height"] for p in equal for r in p]
        self.assertLess((max(areas) - min(areas)) / max(areas), 0.005)
        free = layout.arrange(items, size, {**cfg, "encaixe_mesma_area": False})
        free_areas = [r["content_width"] * r["content_height"] for p in free for r in p]
        self.assertGreater(max(free_areas), max(areas) * 2)
        self.assertGreater(sum(free_areas), sum(areas))

    def test_resolution_does_not_change_printed_size(self):
        cfg = config(encaixe_mesma_area=True)
        a = items_for([2, 0.5, 1], cfg)
        b = [{**item, "source_size": (item["source_size"][0] * (i + 3), item["source_size"][1] * (i + 3))} for i, item in enumerate(a)]
        self.assertEqual(layout.arrange(a, (3508, 2480), cfg), layout.arrange(b, (3508, 2480), cfg))

    def test_optional_physical_limits(self):
        for equal in (False, True):
            cfg = config(encaixe_mesma_area=equal, encaixe_area_max_cm2=40,
                         encaixe_largura_max_cm=10, encaixe_altura_max_cm=8)
            items = items_for([4, 0.25, 1.5, 0.8], cfg)
            pages = layout.arrange(items, (3508, 2480), cfg)
            for page in pages:
                for r in page:
                    self.assertLessEqual(r["content_width"] / layout.PIXELS_PER_CM, 10)
                    self.assertLessEqual(r["content_height"] / layout.PIXELS_PER_CM, 8)
                    self.assertLessEqual(r["content_width"] * r["content_height"] / layout.PIXELS_PER_CM ** 2, 40)

    def test_rotation_is_optional_and_improves_tall_page(self):
        cfg = config(encaixe_figuras_por_pagina=1, margem_externa=20)
        items = items_for([5], cfg)
        plain = layout.arrange(items, (600, 1000), cfg)[0][0]
        turned = layout.arrange(items, (600, 1000), {**cfg, "encaixe_permitir_giro": True})[0][0]
        self.assertEqual(plain["rotation"], 0)
        self.assertEqual(turned["rotation"], 90)
        self.assertGreater(turned["content_width"] * turned["content_height"], plain["content_width"] * plain["content_height"])

    def test_extreme_shapes_and_per_image_borders(self):
        cfg = config(encaixe_mesma_area=True)
        items = items_for([20, 0.05, 3, 0.3, 1, 1.5], cfg)
        items[1]["config"] = {**cfg, "borda_preta_espessura": 25, "margem_interna_quadrado": 0.2}
        pages = layout.arrange(items, (3508, 2480), cfg)
        self.assert_geometry(pages, items, cfg, (3508, 2480))

    def test_invalid_settings_and_impossible_layout(self):
        cfg = config()
        items = items_for([1, 2], cfg)
        for values in ({"encaixe_figuras_por_pagina": 0}, {"encaixe_figuras_por_pagina": 2.5},
                       {"encaixe_figuras_por_pagina": 101}, {"encaixe_area_max_cm2": -1},
                       {"encaixe_altura_max_cm": math.nan}, {"espaco_horizontal": -1},
                       {"margem_externa": 2000}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                layout.arrange(items, (3508, 2480), {**cfg, **values})
        with self.assertRaises(ValueError):
            layout.arrange(items_for([1] * 100, cfg), (200, 200), {**cfg, "encaixe_figuras_por_pagina": 100})


class RenderingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.cfg = config(encaixe_figuras_por_pagina=4, encaixe_mesma_area=True)
        self.paths = []
        for i, (w, h) in enumerate(((260, 100), (100, 220), (190, 110), (110, 200), (170, 90))):
            image = Image.new("RGB", (w + 16, h + 16), "white")
            ImageDraw.Draw(image).rectangle((8, 8, w + 7, h + 7), fill=(35, 110 + i * 10, 180))
            path = self.folder / f"{i + 1:02}_SD.png"
            image.save(path)
            self.paths.append(path)
        self.app = ui.PDFSheetUI.__new__(ui.PDFSheetUI)
        self.app.global_cfg = dict(self.cfg)
        self.app.imagens = self.paths
        self.app.image_overrides = {}
        for name in ("raw_cache", "rembg_cache", "figure_cache", "image_content_cache"):
            setattr(self.app, name, {})
        for name in ("raw_cache_dir", "rembg_cache_dir", "figures_cache_dir", "pages_cache_dir"):
            directory = self.folder / name
            directory.mkdir()
            setattr(self.app, name, directory)

    def test_cli_and_editor_have_identical_pages(self):
        for mode in ("grid", "encaixe"):
            with self.subTest(mode=mode):
                cfg = {**self.cfg, "modo_layout": mode, "figuras_por_pagina": 4}
                self.app.global_cfg = cfg
                cli = script.criar_paginas(self.paths, cfg)
                editor, rects = self.app._criar_paginas_ui(self.paths, cfg)
                self.assertEqual(len(cli), len(editor))
                for a, b in zip(cli, editor):
                    self.assertIsNone(ImageChops.difference(a, b).getbbox())
                if mode == "encaixe":
                    self.assertEqual([len(p) for p in rects], [4, 1])

    def test_rectangle_cache_round_trip_and_invalidation(self):
        pages, rects = self.app._criar_paginas_ui(self.paths, self.cfg)
        key = self.app._page_cache_key(self.cfg)
        self.app._save_pages_cache_disk(key, pages, rects)
        loaded, restored = self.app._load_pages_cache_disk(key)
        self.assertEqual(restored, rects)
        self.assertEqual([p.size for p in pages], [p.size for p in loaded])
        for name in layout.LAYOUT_KEYS:
            cfg = dict(self.cfg)
            value = cfg[name]
            cfg[name] = not value if isinstance(value, bool) else value + 1 if isinstance(value, (int, float)) else "grid"
            self.assertNotEqual(self.app._page_cache_key(cfg), key)
        self.assertNotEqual(self.app._preview_cache_key(self.paths[0], self.cfg),
                            self.app._preview_cache_key(self.paths[0], {**self.cfg, "modo_layout": "grid"}))

    def test_styles_numbers_and_shifts_do_not_crop_content(self):
        cropped = Image.new("RGBA", (200, 80), (255, 0, 0, 255))
        cfg = {**self.cfg, "borda_preta_espessura": 0, "margem_interna_quadrado": 0.06,
               "deslocamento_x": 100, "deslocamento_y": -100}
        rect = layout.dimensions({"source_size": cropped.size, "config": cfg}, 200)
        image = script.renderizar_figura_retangular(cropped, rect, cfg, "", "superior_esquerdo")
        red = image.getchannel("G").point(lambda p: 255 if p == 0 else 0)
        self.assertEqual(sum(red.histogram()[1:]), rect["content_width"] * rect["content_height"])
        for style in script.listar_estilos_borda():
            cfg_style = {**self.cfg, "estilo_borda": style, "raio_borda": 12}
            result = script.renderizar_figura_retangular(cropped, rect, cfg_style, "123456", "inferior_direito")
            self.assertEqual(result.size, (rect["width"], rect["height"]))

    def test_rectangle_click_selection(self):
        self.app.paginas_cache = [Image.new("RGB", (300, 200))]
        self.app.indice_pagina_preview = 0
        self.app.page_preview_meta = {"disp_w": 300, "disp_h": 200, "orig_w": 300, "orig_h": 200}
        self.app.page_layout_cache = [[{"img": self.paths[0], "x": 10, "y": 20, "width": 200, "height": 50}]]
        self.app.page_canvas = SimpleNamespace(canvasx=lambda x: x, canvasy=lambda y: y)
        self.app._select_image_in_list = Mock()
        self.app._on_click_page_preview(SimpleNamespace(x=190, y=60))
        self.app._select_image_in_list.assert_called_once_with(self.paths[0])
        self.app._select_image_in_list.reset_mock()
        self.app._on_click_page_preview(SimpleNamespace(x=190, y=100))
        self.app._select_image_in_list.assert_not_called()

    def test_busy_image_preview_keeps_latest_update(self):
        import threading
        self.app.imagem_atual = self.paths[0]
        self.app.preview_lock = threading.Lock()
        self.app.preview_lock.acquire()
        self.app.render_lock = threading.Lock()
        self.app.preview_req_id = 1
        self.app.preview_refresh_pending = False
        self.app.root = Mock()
        self.app._stop_progress = Mock()
        self.app._refresh_image_preview_async()
        self.assertEqual(self.app.preview_req_id, 2)
        self.assertTrue(self.app.preview_refresh_pending)
        self.app._release_preview_lock()
        self.assertFalse(self.app.preview_lock.locked())
        self.assertFalse(self.app.preview_refresh_pending)
        self.app.root.after.assert_called_once_with(0, self.app._refresh_image_preview_async)

    def test_stale_page_preview_is_discarded(self):
        import threading
        self.app.render_lock = threading.Lock()
        self.app.render_lock.acquire()
        self.app.root = Mock()
        self.app._get_config_ui = Mock(side_effect=[self.cfg, {**self.cfg, "encaixe_mesma_area": False}])
        self.app._try_patch_dirty_page_cells = Mock(return_value=None)
        self.app._load_pages_cache_disk = Mock(return_value=([Image.new("RGB", (20, 20))], []))
        self.app.page_cache = {}
        self.app.paginas_cache = []
        self.app._render_page_preview_worker()
        self.assertEqual(self.app.paginas_cache, [])
        self.assertEqual(self.app.page_cache, {})
        self.assertFalse(self.app.render_lock.locked())
        self.app.root.after.assert_any_call(0, self.app._render_page_preview_thread)

    def test_export_uses_captured_image_settings(self):
        captured = {path: self.app._effective_config_for_image(path, self.cfg) for path in self.paths}
        expected, _ = self.app._criar_paginas_ui(self.paths, self.cfg, captured)
        self.app.global_cfg = {**self.cfg, "borda_preta_espessura": 28, "cor_borda": "#ff0000"}
        actual, _ = self.app._criar_paginas_ui(self.paths, self.cfg, captured)
        for a, b in zip(expected, actual):
            self.assertIsNone(ImageChops.difference(a, b).getbbox())

    def test_pdf_generation(self):
        pages, _ = self.app._criar_paginas_ui(self.paths, self.cfg)
        destination = self.folder / "result.pdf"
        script.salvar_pdf(pages, destination)
        data = destination.read_bytes()
        self.assertTrue(data.startswith(b"%PDF"))
        self.assertIn(b"/Count 2", data)

    def test_composition_tabs_preserve_grid_and_optional_area(self):
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        cfg = {**self.cfg, "modo_layout": "grid", "encaixe_mesma_area": False, "pasta_imagens": str(self.folder)}
        with patch.object(ui, "__file__", str(self.folder / "ui.py")), \
             patch.object(script, "CACHE_ROOT", self.folder / "cache"), \
             patch.object(script, "carregar_config", return_value=cfg), \
             patch.object(root, "deiconify"), patch.object(root, "lift"), patch.object(root, "focus_force"), \
             patch.object(ui.PDFSheetUI, "_load_images"), patch.object(ui.PDFSheetUI, "_refresh_all_previews"), \
             patch.object(ui.PDFSheetUI, "_save_config"), patch.object(ui.PDFSheetUI, "_refresh_image_preview_async"), \
             patch.object(ui.PDFSheetUI, "_render_page_preview_thread"):
            app = ui.PDFSheetUI(root)
            self.assertEqual(app.composition_tabs.index("current"), 0)
            self.assertFalse(app.global_sidebar_vars["encaixe_mesma_area"].get())
            app.composition_tabs.select(1)
            root.update()
            self.assertEqual(app.global_cfg["modo_layout"], "encaixe")
            app.global_sidebar_vars["encaixe_mesma_area"].set(True)
            app._apply_global_sidebar_change()
            self.assertIs(app.global_cfg["encaixe_mesma_area"], True)
            app.global_sidebar_vars["encaixe_figuras_por_pagina"].set("2.5")
            self.assertFalse(app._apply_global_sidebar_change())
            self.assertEqual(app.global_cfg["encaixe_figuras_por_pagina"], cfg["encaixe_figuras_por_pagina"])
            app.global_sidebar_vars["encaixe_figuras_por_pagina"].set(str(cfg["encaixe_figuras_por_pagina"]))
            app.composition_tabs.select(0)
            root.update()
            self.assertEqual(app.global_cfg["modo_layout"], "grid")
            self.assertEqual(app.global_cfg["figuras_por_pagina"], cfg["figuras_por_pagina"])
            app._cancel_ui_callbacks()


if __name__ == "__main__":
    unittest.main()
