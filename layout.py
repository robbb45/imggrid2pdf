"""Rectangle layouts in print pixels; independent of image processing and Tk."""

import math


PIXELS_PER_CM = 300 / 2.54
LAYOUT_KEYS = (
    "modo_layout", "encaixe_figuras_por_pagina", "encaixe_mesma_area",
    "encaixe_area_max_cm2", "encaixe_largura_max_cm", "encaixe_altura_max_cm",
    "encaixe_permitir_giro",
)


def automatic(config):
    return config.get("modo_layout", "grid") == "encaixe"


def signature(config):
    return tuple(config.get(key) for key in LAYOUT_KEYS)


def _limit(config, key, factor=1):
    value = float(config.get(key, 0))
    if not math.isfinite(value) or value < 0:
        raise ValueError("Os limites de tamanho devem ser números positivos, ou zero para automático.")
    return value * factor if value else math.inf


def dimensions(item, scale, rotate=False):
    width, height = item["source_size"]
    if rotate:
        width, height = height, width
    ratio = width / height
    cw = max(1, math.floor(scale * math.sqrt(ratio)))
    ch = max(1, math.floor(scale / math.sqrt(ratio)))
    config = item["config"]
    margin = float(config.get("margem_interna_quadrado", 0.06))
    if not math.isfinite(margin) or not 0 <= margin < 0.5:
        raise ValueError("A margem interna deve estar entre 0 e 0,49.")
    border = max(0, int(config.get("borda_preta_espessura", 8))) + 1
    px = border + math.ceil(cw * margin / (1 - 2 * margin))
    py = border + math.ceil(ch * margin / (1 - 2 * margin))
    return {"width": cw + 2 * px, "height": ch + 2 * py,
            "content_width": cw, "content_height": ch, "rotation": 90 if rotate else 0}


def _inside(a, b):
    return (a[0] >= b[0] and a[1] >= b[1]
            and a[0] + a[2] <= b[0] + b[2]
            and a[1] + a[3] <= b[1] + b[3])


def _intersects(a, b):
    return (a[0] < b[0] + b[2] and a[0] + a[2] > b[0]
            and a[1] < b[1] + b[3] and a[1] + a[3] > b[1])


def _split(free, used):
    split = []
    x, y, w, h = used
    for f in free:
        if not _intersects(f, used):
            split.append(f)
            continue
        fx, fy, fw, fh = f
        if x > fx:
            split.append((fx, fy, x - fx, fh))
        if x + w < fx + fw:
            split.append((x + w, fy, fx + fw - x - w, fh))
        if y > fy:
            split.append((fx, fy, fw, y - fy))
        if y + h < fy + fh:
            split.append((fx, y + h, fw, fy + fh - y - h))
    return [f for i, f in enumerate(split) if f[2] > 0 and f[3] > 0
            and not any(i != j and _inside(f, g) and (f != g or j < i)
                        for j, g in enumerate(split))]


def _valid_dimensions(rect, limits):
    area, width, height = limits
    return (rect["content_width"] * rect["content_height"] <= area
            and rect["content_width"] <= width and rect["content_height"] <= height)


def _pack(items, scale, size, gaps, order, heuristic, rotate, limits):
    gx, gy = gaps
    free = [(0, 0, size[0] + gx, size[1] + gy)]
    placed = []
    for index in order:
        item = items[index]
        best = None
        for turned in ((False, True) if rotate else (False,)):
            rect = dimensions(item, scale, turned)
            if not _valid_dimensions(rect, limits):
                continue
            w, h = rect["width"] + gx, rect["height"] + gy
            for f in free:
                if w > f[2] or h > f[3]:
                    continue
                short, long = sorted((f[2] - w, f[3] - h))
                score = ((short, long, f[1], f[0]) if heuristic == 0 else
                         (f[2] * f[3] - w * h, short, f[1], f[0]) if heuristic == 1 else
                         (f[1] + h, f[0], short, long))
                if best is None or score < best[0]:
                    best = score, f, rect
        if best is None:
            return None
        _, f, rect = best
        placed.append({"img": item["img"], "x": f[0], "y": f[1], **rect})
        free = _split(free, (f[0], f[1], rect["width"] + gx, rect["height"] + gy))
    return placed


def _orders(items):
    indices = list(range(len(items)))
    ratios = [w / h for w, h in (item["source_size"] for item in items)]
    orders = [indices, indices[::-1],
              sorted(indices, key=lambda i: ratios[i]),
              sorted(indices, key=lambda i: -ratios[i]),
              sorted(indices, key=lambda i: -max(ratios[i], 1 / ratios[i]))]
    return list(dict.fromkeys(tuple(order) for order in orders))


def _best_layout(items, size, gaps, rotate, limits, fixed_scale=None, free_sizes=False):
    upper = min(math.sqrt(size[0] * size[1] / len(items)), math.sqrt(limits[0]))
    best_scale, best_rects = 0, None
    candidates = []
    for order in _orders(items):
        for heuristic in range(3):
            if fixed_scale is not None:
                rects = _pack(items, fixed_scale, size, gaps, order, heuristic, rotate, limits)
                if rects is not None:
                    return fixed_scale, rects
                continue
            low, high, rects = 0, upper, None
            for _ in range(20):
                scale = (low + high) / 2
                candidate = _pack(items, scale, size, gaps, order, heuristic, rotate, limits)
                if candidate is None:
                    high = scale
                else:
                    low, rects = scale, candidate
            if rects is not None and (best_rects is None or low > best_scale):
                best_scale, best_rects = low, rects
            if rects is not None:
                candidates.append((low, rects))
    if best_rects is None:
        raise ValueError("Não há espaço para essa quantidade de imagens com as margens e bordas escolhidas. Reduza a quantidade, as margens ou os espaçamentos.")
    if free_sizes:
        grown = [(scale, _grow_independently(rects, items, size, gaps, limits))
                 for scale, rects in candidates]
        return max(grown, key=lambda candidate: sum(r["content_width"] * r["content_height"] for r in candidate[1]))
    return best_scale, best_rects


def _grow_independently(rects, items, size, gaps, limits):
    """Grow individual figures into available space when equal area is disabled."""
    by_path = {item["img"]: item for item in items}
    gx, gy = gaps
    for _ in range(2):
        for index in sorted(range(len(rects)), key=lambda i: rects[i]["width"] * rects[i]["height"]):
            old = rects[index]
            item = by_path[old["img"]]
            start = math.sqrt(old["content_width"] * old["content_height"])
            upper = min(math.sqrt(size[0] * size[1]), math.sqrt(limits[0]))
            best = old
            for right, bottom in ((False, False), (True, False), (False, True), (True, True)):
                low, high = start, upper
                for _ in range(20):
                    scale = (low + high) / 2
                    dims = dimensions(item, scale, old["rotation"] == 90)
                    x = old["x"] + old["width"] - dims["width"] if right else old["x"]
                    y = old["y"] + old["height"] - dims["height"] if bottom else old["y"]
                    candidate = {"img": old["img"], "x": x, "y": y, **dims}
                    valid = (_valid_dimensions(dims, limits) and x >= 0 and y >= 0
                             and x + dims["width"] <= size[0] and y + dims["height"] <= size[1])
                    if valid:
                        footprint = (x, y, dims["width"] + gx, dims["height"] + gy)
                        valid = not any(j != index and _intersects(footprint, (r["x"], r["y"], r["width"] + gx, r["height"] + gy))
                                        for j, r in enumerate(rects))
                    if valid:
                        low = scale
                        if dims["content_width"] * dims["content_height"] > best["content_width"] * best["content_height"]:
                            best = candidate
                    else:
                        high = scale
            rects[index] = best
    return rects


def validate(config, page_size):
    """Validate settings without processing images or doing a layout search."""
    raw_count = float(config.get("encaixe_figuras_por_pagina", 12))
    if not math.isfinite(raw_count) or not raw_count.is_integer() or not 1 <= raw_count <= 100:
        raise ValueError("No encaixe automático, escolha de 1 a 100 imagens por página.")
    count = int(raw_count)
    margin = int(config.get("margem_externa", 80))
    gaps = (int(config.get("espaco_horizontal", 30)), int(config.get("espaco_vertical", 30)))
    size = (page_size[0] - 2 * margin, page_size[1] - 2 * margin)
    if margin < 0 or min(gaps) < 0 or min(size) <= 0:
        raise ValueError("As margens e os espaçamentos devem deixar uma área útil positiva na folha.")
    limits = (_limit(config, "encaixe_area_max_cm2", PIXELS_PER_CM ** 2),
              _limit(config, "encaixe_largura_max_cm", PIXELS_PER_CM),
              _limit(config, "encaixe_altura_max_cm", PIXELS_PER_CM))
    return count, margin, gaps, size, limits


def arrange(items, page_size, config):
    """Return rectangles in page coordinates, preserving image IDs and batches."""
    count, margin, gaps, size, limits = validate(config, page_size)
    if not items:
        return []
    if any(min(item["source_size"]) <= 0 for item in items):
        raise ValueError("As imagens devem ter largura e altura maiores que zero.")
    rotate = bool(config.get("encaixe_permitir_giro", False))
    same_area = bool(config.get("encaixe_mesma_area", False))
    batches = [items[i:i + count] for i in range(0, len(items), count)]
    solutions = [_best_layout(batch, size, gaps, rotate, limits, free_sizes=not same_area) for batch in batches]
    if same_area:
        # Keep one content area across the whole document, including its last page.
        common_scale = min(scale for scale, _ in solutions)
        layouts = []
        for batch, (scale, rects) in zip(batches, solutions):
            if scale != common_scale:
                try:
                    _, rects = _best_layout(batch, size, gaps, rotate, limits, common_scale)
                except ValueError:
                    # Heuristic placement is not monotonic. Shrinking a known
                    # feasible arrangement is always safe even if a new search fails.
                    by_path = {item["img"]: item for item in batch}
                    rects = [{**rect, **dimensions(by_path[rect["img"]], common_scale, rect["rotation"] == 90)}
                             for rect in rects]
            layouts.append(rects)
    else:
        layouts = [rects for _, rects in solutions]
    return [[{**rect, "x": rect["x"] + margin, "y": rect["y"] + margin}
             for rect in page] for page in layouts]


def summary(pages):
    areas = [item["content_width"] * item["content_height"] / PIXELS_PER_CM ** 2
             for page in pages for item in page if "content_width" in item]
    if not areas:
        return ""
    low, high = min(areas), max(areas)
    if high - low < max(0.1, high * 0.01):
        return f"Área por figura: {sum(areas) / len(areas):.1f} cm²"
    return f"Área das figuras: {low:.1f}–{high:.1f} cm²"
