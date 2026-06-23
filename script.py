from pathlib import Path
import json
import ntpath
import os
import re
import site
import sys
import tempfile
import threading
from collections import deque
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

APP_ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
MODELS_ROOT = APP_ROOT / "models"
CACHE_ROOT = APP_ROOT / "cache"
DEPS_ROOT = APP_ROOT / "deps"
BACKEND_DEPS = {
    "rembg": DEPS_ROOT / "rembg",
    "withoutbg": DEPS_ROOT / "withoutbg",
    "inspyrenet": DEPS_ROOT / "inspyrenet",
}


def preparar_ambiente_portatil():
    MODELS_ROOT.mkdir(parents=True, exist_ok=True)
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    DEPS_ROOT.mkdir(parents=True, exist_ok=True)

    for pasta in BACKEND_DEPS.values():
        if pasta.exists():
            site.addsitedir(str(pasta))

    os.environ.setdefault("U2NET_HOME", str(MODELS_ROOT / "rembg"))
    os.environ.setdefault("TORCH_HOME", str(MODELS_ROOT / "torch"))
    os.environ.setdefault("HF_HOME", str(MODELS_ROOT / "huggingface"))
    os.environ.setdefault("XDG_CACHE_HOME", str(CACHE_ROOT / "xdg"))
    os.environ.setdefault("ONNX_HOME", str(MODELS_ROOT / "onnx"))


preparar_ambiente_portatil()

rembg_remove = None
rembg_new_session = None
rembg_sessions_class = []
WithoutBG = None
InSPyReNetRemover = None
torch = None

_NVIDIA_DLL_PATHS_PREPARADOS = False
_REMBG_IMPORT_ATTEMPTED = False
_WITHOUTBG_IMPORT_ATTEMPTED = False
_INSPYRENET_IMPORT_ATTEMPTED = False
_TORCH_IMPORT_ATTEMPTED = False
_REMBG_SESSION_CACHE = {}
_WITHOUTBG_MODEL_CACHE = None
_INSPYRENET_REMOVER_CACHE = {}
_THREAD_STATE = threading.local()


def garantir_rembg_importado():
    global rembg_remove, rembg_new_session, rembg_sessions_class, _REMBG_IMPORT_ATTEMPTED
    if _REMBG_IMPORT_ATTEMPTED:
        return rembg_remove is not None and rembg_new_session is not None
    _REMBG_IMPORT_ATTEMPTED = True
    try:
        from rembg import remove as imported_remove
        from rembg import new_session as imported_new_session
        from rembg.sessions import sessions_class as imported_sessions_class
    except ImportError:
        rembg_remove = None
        rembg_new_session = None
        rembg_sessions_class = []
        return False
    rembg_remove = imported_remove
    rembg_new_session = imported_new_session
    rembg_sessions_class = imported_sessions_class
    return True


def garantir_withoutbg_importado():
    global WithoutBG, _WITHOUTBG_IMPORT_ATTEMPTED
    if _WITHOUTBG_IMPORT_ATTEMPTED:
        return WithoutBG is not None
    _WITHOUTBG_IMPORT_ATTEMPTED = True
    try:
        from withoutbg import WithoutBG as imported_withoutbg
    except ImportError:
        WithoutBG = None
        return False
    WithoutBG = imported_withoutbg
    return True


def garantir_inspyrenet_importado():
    global InSPyReNetRemover, _INSPYRENET_IMPORT_ATTEMPTED
    if _INSPYRENET_IMPORT_ATTEMPTED:
        return InSPyReNetRemover is not None
    _INSPYRENET_IMPORT_ATTEMPTED = True
    try:
        from transparent_background import Remover as imported_remover
    except ImportError:
        InSPyReNetRemover = None
        return False
    InSPyReNetRemover = imported_remover
    return True


def garantir_torch_importado():
    global torch, _TORCH_IMPORT_ATTEMPTED
    if _TORCH_IMPORT_ATTEMPTED:
        return torch is not None
    _TORCH_IMPORT_ATTEMPTED = True
    try:
        import torch as imported_torch
    except ImportError:
        torch = None
        return False
    torch = imported_torch
    return True


# =========================================================
# CONFIGURAÇÕES PADRÃO
# Se existir um arquivo config.json, ele sobrescreve essas opções.
# =========================================================

CONFIG_PADRAO = {
    "pasta_imagens": "figuras",
    "arquivo_saida_pdf": "material_figuras_A4_horizontal.pdf",

    # Opções aceitas: 12, 9, 6 ou 4
    "figuras_por_pagina": 12,

    # Opções aceitas: "horizontal" ou "vertical"
    "orientacao": "horizontal",

    "margem_externa": 80,
    "espaco_horizontal": 30,
    "espaco_vertical": 30,

    "borda_preta_espessura": 8,
    "estilo_borda": "solida",
    "raio_borda": 0,
    "cor_borda": "#000000",
    "cor_numero": "#000000",
    "margem_interna_quadrado": 0.06,

    "posicao_padrao_numero": "superior_esquerdo",
    "tamanho_numero_relativo": 0.085,
    "padding_numero": 10,
    "caixa_numero_padding_x": 10,
    "caixa_numero_padding_y": 6,
    "espessura_borda_caixa_numero": 2,
    "numero_glow_blur": 4,
    "numero_glow_opacidade": 220,

    "limiar_branco": 245,
    "limiar_alpha": 10,
    "tolerancia_fundo": 18,
    "limite_lado_processamento": 2000,

    # "todos" (padrão): aplica remoção de fundo em todas as imagens
    # "tag_rbg": aplica só quando o nome tiver "RBG"
    # "desligado": não usa rembg
    "remover_fundo_modo": "todos",
    "backend_remocao_fundo": "rembg",
    "modelo_remocao_fundo": "birefnet-general-lite",
    "modo_inspyrenet": "base",
    "inspyrenet_device": "auto",
    "rembg_alpha_matting": False,
    "rembg_post_process_mask": False,
    "rembg_foreground_threshold": 240,
    "rembg_background_threshold": 10,
    "rembg_erode_size": 10,

    "evitar_sobrescrever_pdf": True,
    "salvar_paginas_png": False
}

EXTENSOES_ACEITAS = {
    ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"
}


# =========================================================
# LEITURA DO CONFIG.JSON
# =========================================================

def carregar_config():
    pasta_script = Path(__file__).resolve().parent
    caminho_config = pasta_script / "config.json"

    config = CONFIG_PADRAO.copy()

    if caminho_config.exists():
        try:
            with open(caminho_config, "r", encoding="utf-8") as arquivo:
                config_usuario = json.load(arquivo)

            config.update(config_usuario)
            print("Arquivo config.json carregado com sucesso.")

        except Exception as erro:
            print("Erro ao ler o config.json.")
            print("O script vai usar as configurações padrão.")
            print(f"Detalhe do erro: {erro}")
    else:
        print("Arquivo config.json não encontrado.")
        print("O script vai usar as configurações padrão.")

    return config


def obter_tamanho_pagina(config):
    # A4 em 300 dpi
    largura_a4_vertical = 2480
    altura_a4_vertical = 3508

    orientacao = str(config["orientacao"]).lower().strip()

    if orientacao == "vertical":
        return largura_a4_vertical, altura_a4_vertical

    # Padrão: horizontal
    return altura_a4_vertical, largura_a4_vertical


def obter_grade(figuras_por_pagina, orientacao=None):
    figuras_por_pagina = int(figuras_por_pagina)
    orientacao = str(orientacao or "").lower().strip()

    grades = {
        12: (4, 3),
        9: (3, 3),
        6: (3, 2),
        4: (2, 2)
    }
    grades_verticais = {
        12: (3, 4),
        9: (3, 3),
        6: (2, 3),
        4: (2, 2)
    }

    if figuras_por_pagina not in grades:
        print()
        print("Valor inválido em 'figuras_por_pagina'.")
        print("Use apenas: 12, 9, 6 ou 4.")
        print("O script vai usar 12 por página.")
        return grades_verticais[12] if orientacao == "vertical" else grades[12]

    if orientacao == "vertical":
        return grades_verticais[figuras_por_pagina]

    return grades[figuras_por_pagina]


# =========================================================
# FUNÇÕES AUXILIARES
# =========================================================

def natural_key(texto):
    partes = re.findall(r"\d+|\D+", texto.upper())
    chave = []

    for parte in partes:
        if parte.isdigit():
            chave.append((0, int(parte)))
        else:
            chave.append((1, parte))

    return chave


def listar_imagens(pasta):
    arquivos = []

    for item in pasta.iterdir():
        if item.is_file() and item.suffix.lower() in EXTENSOES_ACEITAS:
            arquivos.append(item)

    arquivos.sort(key=lambda p: natural_key(p.stem))
    return arquivos


def reduzir_para_processamento(img, config):
    limite = int(config.get("limite_lado_processamento", 2000))
    if limite <= 0:
        return img.convert("RGBA")

    img_rgba = img.convert("RGBA")
    maior_lado = max(img_rgba.size)
    if maior_lado <= limite:
        return img_rgba

    escala = limite / maior_lado
    novo_tamanho = (
        max(1, int(round(img_rgba.width * escala))),
        max(1, int(round(img_rgba.height * escala))),
    )
    return img_rgba.resize(novo_tamanho, Image.LANCZOS)


def listar_modelos_rembg_disponiveis():
    nomes = []
    if not _REMBG_IMPORT_ATTEMPTED:
        return [
            "birefnet-general-lite",
            "birefnet-general",
            "bria-rmbg",
            "u2net",
            "u2netp",
            "isnet-general-use",
        ]
    garantir_rembg_importado()
    for sessao in rembg_sessions_class or []:
        try:
            nome = str(sessao.name())
        except Exception:
            continue
        if nome and nome not in nomes:
            nomes.append(nome)

    preferidos = [
        "birefnet-general-lite",
        "birefnet-general",
        "bria-rmbg",
        "u2net",
        "u2netp",
        "isnet-general-use",
    ]
    ordenados = [nome for nome in preferidos if nome in nomes]
    ordenados.extend(nome for nome in nomes if nome not in ordenados)
    return ordenados


def limpar_ultimo_erro_remocao_fundo():
    _THREAD_STATE.ultimo_erro_remocao_fundo = None


def registrar_erro_remocao_fundo(backend, detalhe):
    _THREAD_STATE.ultimo_erro_remocao_fundo = {
        "backend": str(backend),
        "detalhe": str(detalhe),
    }


def obter_ultimo_erro_remocao_fundo():
    return getattr(_THREAD_STATE, "ultimo_erro_remocao_fundo", None)


def listar_backends_remocao_fundo():
    return ["rembg", "withoutbg", "inspyrenet"]


def listar_modos_inspyrenet():
    return ["base", "fast", "base-nightly"]


def listar_dispositivos_inspyrenet():
    return ["auto", "cuda", "cpu"]


def listar_estilos_borda():
    return ["solida", "tracejada", "pontilhada", "traco_ponto"]


def obter_backend_remocao_fundo(config):
    backend = str(config.get("backend_remocao_fundo", "rembg")).strip().lower()
    if backend not in listar_backends_remocao_fundo():
        return "rembg"
    return backend


def obter_modelo_remocao_fundo(config):
    modelo = str(config.get("modelo_remocao_fundo", "birefnet-general-lite")).strip().lower()
    if not modelo:
        return "birefnet-general-lite"
    return modelo


def obter_modo_inspyrenet(config):
    modo = str(config.get("modo_inspyrenet", "base")).strip().lower()
    if modo not in listar_modos_inspyrenet():
        return "base"
    return modo


def obter_dispositivo_inspyrenet(config):
    dispositivo = str(config.get("inspyrenet_device", "auto")).strip().lower()
    if dispositivo not in listar_dispositivos_inspyrenet():
        return "auto"
    garantir_torch_importado()
    if dispositivo == "cuda":
        if torch is not None and torch.cuda.is_available():
            return "cuda"
        return "cpu"
    if dispositivo == "auto":
        if torch is not None and torch.cuda.is_available():
            return "cuda"
        return "cpu"
    return "cpu"


def obter_sessao_rembg(modelo):
    garantir_rembg_importado()
    if rembg_new_session is None:
        return None

    if modelo in _REMBG_SESSION_CACHE:
        return _REMBG_SESSION_CACHE[modelo]

    sessao = rembg_new_session(modelo)
    _REMBG_SESSION_CACHE[modelo] = sessao
    return sessao


def baixar_modelo_rembg(modelo=None):
    modelo_escolhido = str(modelo or "birefnet-general-lite").strip().lower()
    sessao = obter_sessao_rembg(modelo_escolhido)
    return sessao is not None


def obter_modelo_withoutbg():
    global _WITHOUTBG_MODEL_CACHE
    garantir_withoutbg_importado()
    if WithoutBG is None:
        return None
    if _WITHOUTBG_MODEL_CACHE is None:
        _WITHOUTBG_MODEL_CACHE = WithoutBG.opensource()
    return _WITHOUTBG_MODEL_CACHE


def baixar_modelo_withoutbg():
    return obter_modelo_withoutbg() is not None


def obter_remover_inspyrenet(modo="base", dispositivo="auto"):
    garantir_inspyrenet_importado()
    if InSPyReNetRemover is None:
        return None
    chave = (modo, dispositivo)
    if chave in _INSPYRENET_REMOVER_CACHE:
        return _INSPYRENET_REMOVER_CACHE[chave]
    remover = InSPyReNetRemover(mode=modo, device=dispositivo)
    _INSPYRENET_REMOVER_CACHE[chave] = remover
    return remover


def baixar_modelo_inspyrenet(modo="base", dispositivo="auto"):
    return obter_remover_inspyrenet(modo, dispositivo) is not None


def baixar_backend_remocao_fundo(config):
    backend = obter_backend_remocao_fundo(config)
    if backend == "withoutbg":
        return baixar_modelo_withoutbg()
    if backend == "inspyrenet":
        return baixar_modelo_inspyrenet(
            obter_modo_inspyrenet(config),
            obter_dispositivo_inspyrenet(config),
        )
    return baixar_modelo_rembg(obter_modelo_remocao_fundo(config))


def normalizar_chave_imagem(caminho):
    texto = str(caminho)
    if re.match(r"^[A-Za-z]:[\\/]", texto) or texto.startswith("\\\\"):
        return ntpath.normcase(ntpath.normpath(texto))
    try:
        return os.path.normcase(str(Path(texto).resolve()))
    except Exception:
        return os.path.normcase(str(Path(texto)))


def carregar_overrides_imagem():
    pasta_script = Path(__file__).resolve().parent
    caminho_overrides = pasta_script / "image_overrides.json"
    if not caminho_overrides.exists():
        return {}

    try:
        with open(caminho_overrides, "r", encoding="utf-8") as arquivo:
            data = json.load(arquivo)
    except Exception:
        return {}

    if not isinstance(data, dict):
        return {}

    normalizado = {}
    for chave, payload in data.items():
        if not isinstance(payload, dict):
            continue
        normalizado[normalizar_chave_imagem(chave)] = dict(payload)
    return normalizado


def obter_config_efetiva_imagem(caminho_imagem, config, overrides=None):
    cfg = dict(config)
    if overrides:
        ov = overrides.get(normalizar_chave_imagem(caminho_imagem))
        if isinstance(ov, dict):
            cfg.update(ov)
    return cfg


def exibir_progresso(atual, total, prefixo="Processando"):
    if total <= 0:
        return

    largura_barra = 30
    proporcao = atual / total
    preenchido = int(largura_barra * proporcao)
    barra = "#" * preenchido + "-" * (largura_barra - preenchido)
    percentual = int(proporcao * 100)

    print(
        f"\r{prefixo}: [{barra}] {atual}/{total} ({percentual}%)",
        end="",
        flush=True
    )

    if atual >= total:
        print()


def interpretar_nome_arquivo(caminho, config):
    """
    Regras principais:

    30.png   -> mostra número 30, posição padrão
    30B.png  -> mostra número 30, canto superior direito

    Também aceita:

    30SE.png ou 30_SE.png -> superior esquerdo
    30SD.png ou 30_SD.png -> superior direito
    30IE.png ou 30_IE.png -> inferior esquerdo
    30ID.png ou 30_ID.png -> inferior direito
    """
    nome = caminho.stem
    nome_maiusculo = nome.upper()

    posicao = config["posicao_padrao_numero"]
    numero_exibido = nome

    tags_posicao = {
        "SE": "superior_esquerdo",
        "SD": "superior_direito",
        "IE": "inferior_esquerdo",
        "ID": "inferior_direito",
    }

    for tag, pos in tags_posicao.items():
        if nome_maiusculo.endswith(f"_{tag}") or nome_maiusculo.endswith(f"-{tag}"):
            numero_exibido = nome[:-3]
            posicao = pos
            return numero_exibido.strip("_- "), posicao

        if nome_maiusculo.endswith(tag) and len(nome) > len(tag):
            numero_exibido = nome[:-len(tag)]
            posicao = pos
            return numero_exibido.strip("_- "), posicao

    # Regra simples pedida:
    # arquivo terminado em B joga o número para o canto superior direito
    if nome_maiusculo.endswith("_B") or nome_maiusculo.endswith("-B"):
        numero_exibido = nome[:-2]
        posicao = "superior_direito"
        return numero_exibido.strip("_- "), posicao

    if nome_maiusculo.endswith("B") and len(nome) > 1:
        numero_exibido = nome[:-1]
        posicao = "superior_direito"
        return numero_exibido.strip("_- "), posicao

    return numero_exibido.strip(), posicao


def deve_remover_fundo(caminho_imagem, config):
    modo = str(config.get("remover_fundo_modo", "todos")).strip().lower()

    if modo == "desligado":
        return False

    if modo == "tag_rbg":
        return "RBG" in caminho_imagem.stem.upper()

    return True


def aplicar_remocao_fundo(img, caminho_imagem, config):
    limpar_ultimo_erro_remocao_fundo()
    if not deve_remover_fundo(caminho_imagem, config):
        return img.convert("RGBA")

    try:
        preparar_paths_nvidia_dll()
        img_rgba = img.convert("RGBA")
        backend = obter_backend_remocao_fundo(config)

        if backend == "withoutbg":
            modelo = obter_modelo_withoutbg()
            if modelo is None:
                registrar_erro_remocao_fundo("withoutbg", "Backend withoutbg não está instalado ou não pôde ser inicializado.")
                return img_rgba
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp_path = tmp.name
            try:
                img_rgba.save(tmp_path, format="PNG")
                img_sem_fundo = modelo.remove_background(tmp_path)
            finally:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
            if isinstance(img_sem_fundo, Image.Image):
                return img_sem_fundo.convert("RGBA")
            return img_rgba

        if backend == "inspyrenet":
            modo = obter_modo_inspyrenet(config)
            dispositivo = obter_dispositivo_inspyrenet(config)
            remover = obter_remover_inspyrenet(modo, dispositivo)
            if remover is None:
                registrar_erro_remocao_fundo("inspyrenet", "Backend InSPyReNet não está instalado ou não pôde ser inicializado.")
                return img_rgba
            img_sem_fundo = remover.process(img_rgba.convert("RGB"), type="rgba")
            if isinstance(img_sem_fundo, Image.Image):
                return img_sem_fundo.convert("RGBA")
            return img_rgba

        if not garantir_rembg_importado() or rembg_remove is None:
            registrar_erro_remocao_fundo("rembg", "Pacote rembg não está instalado.")
            return img_rgba
        modelo = obter_modelo_remocao_fundo(config)
        sessao = obter_sessao_rembg(modelo)
        if sessao is None:
            registrar_erro_remocao_fundo("rembg", f"Modelo rembg '{modelo}' não pôde ser carregado.")
            return img_rgba
        img_sem_fundo = rembg_remove(
            img_rgba,
            session=sessao,
            alpha_matting=bool(config.get("rembg_alpha_matting", False)),
            post_process_mask=bool(config.get("rembg_post_process_mask", False)),
            alpha_matting_foreground_threshold=int(config.get("rembg_foreground_threshold", 240)),
            alpha_matting_background_threshold=int(config.get("rembg_background_threshold", 10)),
            alpha_matting_erode_size=int(config.get("rembg_erode_size", 10)),
        )
        if isinstance(img_sem_fundo, Image.Image):
            return img_sem_fundo.convert("RGBA")
    except Exception as erro:
        backend = obter_backend_remocao_fundo(config)
        registrar_erro_remocao_fundo(backend, erro)
        pass

    return img.convert("RGBA")


def preparar_paths_nvidia_dll():
    global _NVIDIA_DLL_PATHS_PREPARADOS

    if _NVIDIA_DLL_PATHS_PREPARADOS:
        return

    _NVIDIA_DLL_PATHS_PREPARADOS = True

    raizes = [Path(site.getusersitepackages())]
    raizes.extend(p for p in BACKEND_DEPS.values() if p.exists())

    pastas = []
    for raiz in raizes:
        pastas.extend([
            raiz / "nvidia" / "cudnn" / "bin",
            raiz / "nvidia" / "cublas" / "bin",
            raiz / "nvidia" / "cuda_nvrtc" / "bin",
        ])

    path_atual = os.environ.get("PATH", "")
    itens_path = [p for p in path_atual.split(";") if p]

    for pasta in pastas:
        if not pasta.exists():
            continue

        pasta_str = str(pasta)
        if pasta_str not in itens_path:
            itens_path.insert(0, pasta_str)

        try:
            os.add_dll_directory(pasta_str)
        except Exception:
            pass

    os.environ["PATH"] = ";".join(itens_path)


def carregar_fonte(tamanho):
    fontes_teste = [
        "arialbd.ttf",
        "Arial Bold.ttf",
        "DejaVuSans-Bold.ttf",
        "arial.ttf",
        "DejaVuSans.ttf",
    ]

    for fonte in fontes_teste:
        try:
            return ImageFont.truetype(fonte, tamanho)
        except:
            pass

    return ImageFont.load_default()


def encontrar_bbox_conteudo(img_rgba, config):
    """
    Detecta conteúdo removendo fundo conectado às bordas:
    - estima a cor de fundo pelos 4 cantos
    - considera transparente como fundo
    - usa flood-fill pelas bordas para marcar fundo conectado
    - retorna bbox do conteúdo remanescente
    """
    limiar_alpha = int(config["limiar_alpha"])
    tolerancia_fundo = int(config.get("tolerancia_fundo", 18))

    largura, altura = img_rgba.size
    if largura == 0 or altura == 0:
        return None

    pixels = img_rgba.load()

    cantos = [
        pixels[0, 0],
        pixels[largura - 1, 0],
        pixels[0, altura - 1],
        pixels[largura - 1, altura - 1],
    ]

    media_r = sum(px[0] for px in cantos) // 4
    media_g = sum(px[1] for px in cantos) // 4
    media_b = sum(px[2] for px in cantos) // 4

    def parecido_com_fundo(x, y):
        r, g, b, a = pixels[x, y]
        if a <= limiar_alpha:
            return True
        return (
            abs(r - media_r) <= tolerancia_fundo
            and abs(g - media_g) <= tolerancia_fundo
            and abs(b - media_b) <= tolerancia_fundo
        )

    visitado = bytearray(largura * altura)
    fila = deque()

    def marcar_se_fundo(x, y):
        idx = y * largura + x
        if visitado[idx]:
            return
        if not parecido_com_fundo(x, y):
            return
        visitado[idx] = 1
        fila.append((x, y))

    for x in range(largura):
        marcar_se_fundo(x, 0)
        marcar_se_fundo(x, altura - 1)

    for y in range(altura):
        marcar_se_fundo(0, y)
        marcar_se_fundo(largura - 1, y)

    vizinhos = ((1, 0), (-1, 0), (0, 1), (0, -1))

    while fila:
        x, y = fila.popleft()
        for dx, dy in vizinhos:
            nx = x + dx
            ny = y + dy
            if nx < 0 or ny < 0 or nx >= largura or ny >= altura:
                continue
            idx = ny * largura + nx
            if visitado[idx]:
                continue
            if not parecido_com_fundo(nx, ny):
                continue
            visitado[idx] = 1
            fila.append((nx, ny))

    min_x = largura
    min_y = altura
    max_x = -1
    max_y = -1

    for y in range(altura):
        base = y * largura
        for x in range(largura):
            idx = base + x
            if visitado[idx]:
                continue
            r, g, b, a = pixels[x, y]
            if a <= limiar_alpha:
                continue
            if x < min_x:
                min_x = x
            if y < min_y:
                min_y = y
            if x > max_x:
                max_x = x
            if y > max_y:
                max_y = y

    if max_x < min_x or max_y < min_y:
        return None

    return (min_x, min_y, max_x + 1, max_y + 1)


def cortar_espacos_brancos(img, config):
    img_rgba = img.convert("RGBA")
    bbox = encontrar_bbox_conteudo(img_rgba, config)

    if bbox is None:
        return img_rgba

    return img_rgba.crop(bbox)


def transformar_em_quadrado_com_margem(img_rgba, tamanho_saida, config):
    margem_interna = float(config["margem_interna_quadrado"])

    area_util = int(tamanho_saida * (1 - 2 * margem_interna))

    if area_util < 1:
        area_util = tamanho_saida

    largura, altura = img_rgba.size
    if largura <= 0 or altura <= 0:
        img_copia = img_rgba.copy()
    else:
        escala = min(area_util / largura, area_util / altura)
        nova_largura = max(1, int(round(largura * escala)))
        nova_altura = max(1, int(round(altura * escala)))
        img_copia = img_rgba.resize((nova_largura, nova_altura), Image.LANCZOS)

    quadrado = Image.new(
        "RGBA",
        (tamanho_saida, tamanho_saida),
        (255, 255, 255, 255)
    )

    x = (tamanho_saida - img_copia.width) // 2
    y = (tamanho_saida - img_copia.height) // 2

    quadrado.paste(img_copia, (x, y), img_copia)

    return quadrado


def desenhar_borda_preta(img_rgba, config):
    espessura = int(config["borda_preta_espessura"])
    estilo = str(config.get("estilo_borda", "solida")).strip().lower()
    if estilo not in listar_estilos_borda():
        estilo = "solida"
    raio = max(0, int(config.get("raio_borda", 0)))
    cor_borda = str(config.get("cor_borda", "#000000"))

    draw = ImageDraw.Draw(img_rgba)
    w, h = img_rgba.size
    raio = min(raio, max(0, (min(w, h) // 2) - 1))

    if espessura <= 0:
        return

    if estilo in ("tracejada", "pontilhada", "traco_ponto"):
        if estilo == "pontilhada":
            dash_pattern = [max(2, espessura), max(3, espessura * 2)]
        elif estilo == "traco_ponto":
            dash_pattern = [
                max(8, espessura * 3),
                max(4, espessura * 2),
                max(2, espessura),
                max(4, espessura * 2),
            ]
        else:
            dash_pattern = [max(8, espessura * 3), max(4, espessura * 2)]

        def draw_patterned_line(x1, y1, x2, y2):
            horizontal = y1 == y2
            if horizontal:
                start = min(x1, x2)
                end = max(x1, x2)
                pos = start
                pattern_idx = 0
                while pos < end:
                    length = dash_pattern[pattern_idx % len(dash_pattern)]
                    seg_end = min(pos + length, end)
                    if pattern_idx % 2 == 0:
                        draw.line((pos, y1, seg_end, y2), fill=cor_borda, width=espessura)
                    pos += length
                    pattern_idx += 1
            else:
                start = min(y1, y2)
                end = max(y1, y2)
                pos = start
                pattern_idx = 0
                while pos < end:
                    length = dash_pattern[pattern_idx % len(dash_pattern)]
                    seg_end = min(pos + length, end)
                    if pattern_idx % 2 == 0:
                        draw.line((x1, pos, x2, seg_end), fill=cor_borda, width=espessura)
                    pos += length
                    pattern_idx += 1

        def draw_patterned_arc(bbox, start_angle, end_angle):
            circumference = max(1, 2 * 3.14159 * max(raio, 1))
            angle = start_angle
            pattern_idx = 0
            while angle < end_angle:
                length = dash_pattern[pattern_idx % len(dash_pattern)]
                angle_step = max(3, int(360 * length / circumference))
                seg_end = min(angle + angle_step, end_angle)
                if pattern_idx % 2 == 0:
                    draw.arc(bbox, start=angle, end=seg_end, fill=cor_borda, width=espessura)
                angle += angle_step
                pattern_idx += 1

        inset = espessura // 2
        left = inset
        top = inset
        right = w - 1 - inset
        bottom = h - 1 - inset
        if raio > 0:
            draw_patterned_line(left + raio, top, right - raio, top)
            draw_patterned_line(left + raio, bottom, right - raio, bottom)
            draw_patterned_line(left, top + raio, left, bottom - raio)
            draw_patterned_line(right, top + raio, right, bottom - raio)
            draw_patterned_arc((left, top, left + 2 * raio, top + 2 * raio), 180, 270)
            draw_patterned_arc((right - 2 * raio, top, right, top + 2 * raio), 270, 360)
            draw_patterned_arc((right - 2 * raio, bottom - 2 * raio, right, bottom), 0, 90)
            draw_patterned_arc((left, bottom - 2 * raio, left + 2 * raio, bottom), 90, 180)
        else:
            draw_patterned_line(left, top, right, top)
            draw_patterned_line(left, bottom, right, bottom)
            draw_patterned_line(left, top, left, bottom)
            draw_patterned_line(right, top, right, bottom)
        return

    draw.rounded_rectangle(
        [espessura // 2, espessura // 2, w - 1 - (espessura // 2), h - 1 - (espessura // 2)],
        radius=raio,
        outline=cor_borda,
        width=espessura,
    )


def desenhar_numero_com_glow(img_rgba, texto, posicao, config):
    draw = ImageDraw.Draw(img_rgba)
    w, h = img_rgba.size

    borda_espessura = int(config["borda_preta_espessura"])
    tamanho_relativo = float(config["tamanho_numero_relativo"])
    padding_numero = int(config["padding_numero"])
    padding_x = int(config["caixa_numero_padding_x"])
    padding_y = int(config["caixa_numero_padding_y"])
    glow_blur = max(0, int(config.get("numero_glow_blur", 4)))
    glow_opacidade = int(config.get("numero_glow_opacidade", 220))
    glow_opacidade = max(0, min(255, glow_opacidade))

    tamanho_fonte = max(18, int(w * tamanho_relativo))
    fonte = carregar_fonte(tamanho_fonte)

    bbox_texto = draw.textbbox((0, 0), texto, font=fonte)
    largura_texto = bbox_texto[2] - bbox_texto[0]
    altura_texto = bbox_texto[3] - bbox_texto[1]

    largura_caixa = largura_texto + (padding_x * 2)
    altura_caixa = altura_texto + (padding_y * 2)

    inset = borda_espessura + padding_numero

    if posicao == "superior_direito":
        x1 = w - inset - largura_caixa
        y1 = inset

    elif posicao == "inferior_esquerdo":
        x1 = inset
        y1 = h - inset - altura_caixa

    elif posicao == "inferior_direito":
        x1 = w - inset - largura_caixa
        y1 = h - inset - altura_caixa

    else:
        x1 = inset
        y1 = inset

    texto_x = x1 + padding_x
    texto_y = y1 + padding_y - 1

    camada_glow = Image.new("RGBA", img_rgba.size, (0, 0, 0, 0))
    draw_glow = ImageDraw.Draw(camada_glow)
    draw_glow.text((texto_x, texto_y), texto, fill=(255, 255, 255, 255), font=fonte)

    if glow_blur > 0:
        camada_glow = camada_glow.filter(ImageFilter.GaussianBlur(glow_blur))

    if glow_opacidade < 255:
        alpha = camada_glow.getchannel("A")
        alpha = alpha.point(lambda p: (p * glow_opacidade) // 255)
        camada_glow.putalpha(alpha)

    img_rgba.alpha_composite(camada_glow)

    draw.text(
        (texto_x, texto_y),
        texto,
        fill=str(config.get("cor_numero", "#000000")),
        font=fonte
    )


def preparar_figura(caminho_imagem, tamanho_quadrado, config, overrides=None):
    config_figura = obter_config_efetiva_imagem(caminho_imagem, config, overrides)
    numero_exibido, posicao_numero = interpretar_nome_arquivo(
        caminho_imagem,
        config_figura
    )

    with Image.open(caminho_imagem) as img:
        img = reduzir_para_processamento(img, config_figura)
        img = aplicar_remocao_fundo(img, caminho_imagem, config_figura)
        img = cortar_espacos_brancos(img, config_figura)
        img = transformar_em_quadrado_com_margem(
            img,
            tamanho_quadrado,
            config_figura
        )
        desenhar_borda_preta(img, config_figura)
        desenhar_numero_com_glow(
            img,
            numero_exibido,
            posicao_numero,
            config_figura
        )

    return img.convert("RGB")


def criar_paginas(figuras, config, overrides=None):
    paginas = []

    pagina_largura, pagina_altura = obter_tamanho_pagina(config)

    colunas, linhas = obter_grade(config["figuras_por_pagina"], config.get("orientacao"))
    figuras_por_pagina = colunas * linhas

    margem_externa = int(config["margem_externa"])
    espaco_horizontal = int(config["espaco_horizontal"])
    espaco_vertical = int(config["espaco_vertical"])

    largura_celula = (
        pagina_largura
        - 2 * margem_externa
        - (colunas - 1) * espaco_horizontal
    ) // colunas

    altura_celula = (
        pagina_altura
        - 2 * margem_externa
        - (linhas - 1) * espaco_vertical
    ) // linhas

    tamanho_quadrado = min(largura_celula, altura_celula)
    total_figuras = len(figuras)
    processadas = 0

    for inicio in range(0, len(figuras), figuras_por_pagina):
        lote = figuras[inicio:inicio + figuras_por_pagina]

        pagina = Image.new(
            "RGB",
            (pagina_largura, pagina_altura),
            (255, 255, 255)
        )

        for indice, caminho in enumerate(lote):
            figura = preparar_figura(caminho, tamanho_quadrado, config, overrides)
            processadas += 1
            exibir_progresso(processadas, total_figuras, prefixo="Gerando páginas")

            linha = indice // colunas
            coluna = indice % colunas

            x_celula = margem_externa + coluna * (
                largura_celula + espaco_horizontal
            )

            y_celula = margem_externa + linha * (
                altura_celula + espaco_vertical
            )

            x = x_celula + (largura_celula - tamanho_quadrado) // 2
            y = y_celula + (altura_celula - tamanho_quadrado) // 2

            pagina.paste(figura, (x, y))

        paginas.append(pagina)

    return paginas


def salvar_pdf(paginas, arquivo_saida):
    if not paginas:
        print("Nenhuma página foi criada.")
        return arquivo_saida

    primeira = paginas[0]
    restantes = paginas[1:]

    primeira.save(
        arquivo_saida,
        "PDF",
        resolution=300.0,
        save_all=True,
        append_images=restantes
    )
    return arquivo_saida


def obter_caminho_saida_disponivel(arquivo_saida):
    if not arquivo_saida.exists():
        return arquivo_saida

    pasta = arquivo_saida.parent
    nome_base = arquivo_saida.stem
    extensao = arquivo_saida.suffix

    for i in range(1, 10000):
        candidato = pasta / f"{nome_base}_{i:02d}{extensao}"
        if not candidato.exists():
            return candidato

    return pasta / f"{nome_base}_{Path(__file__).stat().st_mtime_ns}{extensao}"


def salvar_paginas_png(paginas, pasta_saida):
    for i, pagina in enumerate(paginas, start=1):
        nome = pasta_saida / f"pagina_{i:02d}.png"
        pagina.save(nome)


# =========================================================
# EXECUÇÃO
# =========================================================

def main():
    config = carregar_config()
    overrides = carregar_overrides_imagem()

    pasta_script = Path(__file__).resolve().parent
    pasta_imagens = Path(config["pasta_imagens"])
    if not pasta_imagens.is_absolute():
        pasta_imagens = pasta_script / pasta_imagens
    arquivo_saida = pasta_script / config["arquivo_saida_pdf"]
    if bool(config.get("evitar_sobrescrever_pdf", True)):
        arquivo_saida = obter_caminho_saida_disponivel(arquivo_saida)

    if not pasta_imagens.exists():
        print()
        print(f"A pasta '{config['pasta_imagens']}' não foi encontrada.")
        print("Crie essa pasta ao lado do script e coloque suas imagens dentro dela.")
        return

    imagens = listar_imagens(pasta_imagens)

    if not imagens:
        print()
        print("Nenhuma imagem encontrada.")
        print(f"Extensões aceitas: {', '.join(sorted(EXTENSOES_ACEITAS))}")
        return

    colunas, linhas = obter_grade(config["figuras_por_pagina"], config.get("orientacao"))

    print()
    print(f"Foram encontradas {len(imagens)} imagens.")
    print(f"Formato escolhido: {config['figuras_por_pagina']} figuras por página.")
    print(f"Grade: {colunas} colunas x {linhas} linhas.")
    print(f"Orientação: {config['orientacao']}.")
    print()

    for img in imagens:
        cfg_img = obter_config_efetiva_imagem(img, config, overrides)
        numero, posicao = interpretar_nome_arquivo(img, cfg_img)
        print(f" - {img.name}  -> número: {numero} | posição: {posicao}")

    paginas = criar_paginas(imagens, config, overrides)
    arquivo_pdf_final = salvar_pdf(paginas, arquivo_saida)

    if config["salvar_paginas_png"]:
        salvar_paginas_png(paginas, pasta_script)

    print()
    print("Concluído.")
    print(f"PDF gerado em: {arquivo_pdf_final}")


if __name__ == "__main__":
    main()
