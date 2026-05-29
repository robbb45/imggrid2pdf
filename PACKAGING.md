# imgGrid2PDF Packaging Notes

This project is prepared for a small base Windows app plus optional background-removal backends.

## Portable Folder Layout

```text
imgGrid2PDF/
  imgGrid2PDF.exe
  config.json
  models/
  cache/
  deps/
```

- `models/`: downloaded model weights and checkpoints.
- `cache/`: generated previews, page cache, and temporary app cache.
- `deps/`: optional Python packages installed on demand, such as `withoutbg` or `transparent-background`/PyTorch.

The app sets model/cache environment variables at startup so model files stay beside the program instead of hidden in AppData.

## Base Build

Use `requirements.txt` for the base app:

```powershell
python -m pip install -r requirements.txt
```

The base dependency set includes `rembg[cpu]` and `withoutbg`. It intentionally does not include `transparent-background` or `torch`.

It uses `rembg[cpu]` so a clean install has a working ONNX Runtime provider. It avoids `onnxruntime-gpu`; use `requirements-rembg-gpu.txt` only when you want GPU ONNX Runtime support in a local runtime.

## Optional Backends

The UI install button installs optional backends into:

```text
deps/<backend>/
```

This avoids installing Torch or other heavy packages globally. Restart the app after installing an optional backend so Python can load the new dependency folder. `withoutbg` is included in the normal base install, but its model files still download into `models/` on demand.

## InSPyReNet / Torch

InSPyReNet is the heaviest backend because it requires PyTorch. It should stay optional and should not be bundled into the base executable.

The app installs CUDA PyTorch into `deps/inspyrenet/` using the PyTorch CUDA index. This keeps the base executable smaller and keeps the user's global Python clean.

## PyInstaller Direction

For a first Windows executable:

```powershell
python -m pip install pyinstaller
pyinstaller --noconsole --name imgGrid2PDF ui.py
```

Do not install optional backends in the build environment unless you intentionally want them bundled into the executable.
