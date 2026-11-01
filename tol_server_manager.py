#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Torrente On-Line - Administrador visual del servidor dedicado
==============================================================

Pon este archivo en la carpeta del servidor (donde estan Dedicated.exe,
vtserver.dll, server.cfg y el demo.vpk) y ejecutalo con iniciar_manager.bat
(o:  pythonw tol_server_manager.py).  Solo usa la biblioteca estandar.

Que hace
  * Lee/edita server.cfg conservando comentarios y orden (todos los parametros).
  * Ciclo de mapas = lista entre SCENE_LOOP: y END_SCENE_LOOP (agregar, quitar,
    ordenar). Los mapas disponibles salen de escanear los .scn del demo.vpk.
  * Arranca Dedicated.exe con "-p <carpeta datos>" (no usa el registro), vigila
    su consola, muestra puerto / mapa / matchmaker y manda comandos
    (timeleft, continue, nextmap, player, map, kick, votemap, headmode).
  * Respawn automatico (como respawn.exe).
  * Prepara una carpeta "runtime" con las DLL de servidor, usando las DLL del
    cliente para lo que el paquete del servidor no trae (y, si lo eliges, para
    todo menos el nucleo).  Las DLL del cliente se re-enlazan: vtKernel ->
    vtserver y Scrípt.dll -> Script.dll, con nombre interno unico.
  * Direccion del matchmaker (tol.trewq.com / 185.228.138.108 / 10.88.0.1, hasta 15
    caracteres) parcheada en Main.dll (boton "Parchear IP") y en la copia de runtime.
  * Modo de mapas ToL-PrO (%04...) o Torrente On-Line (mp_...), con visor de .tex (TeX2, sin Pillow).
  * Comparador de DLL servidor / cliente / ToL-PrO y selector de adaptador de red.

Notas de honestidad: el modulo esta probado en Linux (parsers, escaneo,
re-enlace de DLL); las partes de consola de Windows (ctypes) no se pudieron
ejecutar aqui.  Si algo falla, el panel de registro lo dice.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import queue
import re
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
import zlib
from collections import OrderedDict
from typing import Callable, Dict, List, Optional, Tuple

APP_NAME = "Torrente On-Line Server Manager v1.01"
APP_VERSION = "v1.01"
APP_URL = "https://github.com/Lahrenheit/dedicatedtol"
FROZEN = bool(getattr(sys, "frozen", False))      # compilado con auto-py-to-exe / PyInstaller
APP_DIR = os.path.dirname(os.path.abspath(sys.executable if FROZEN else __file__))   # junto al .exe (alli se guarda tol_manager.json)
RES_DIR = getattr(sys, "_MEIPASS", APP_DIR)       # recursos empaquetados (icon.ico, etc.)
REPO_URL = "https://github.com/Lahrenheit/dedicatedtol"
TEX_BOX = (340, 340)                              # tamaño fijo del visor de texturas
SETTINGS_FILE = os.path.join(APP_DIR, "tol_manager.json")
IS_WIN = sys.platform == "win32"

# ----------------------------------------------------------------------------
# 1. server.cfg  (parser/escritor que conserva comentarios)
# ----------------------------------------------------------------------------
# (clave, tipo, valor por defecto, descripcion, rango/opciones)
PARAMS = [
    ("PORT", "int", "8888", "Puerto donde se hospeda la partida (abrelo en el firewall/router).", (1, 65535)),
    ("SERVER_NAME", "str", "Unknown Server", "Nombre de la partida que ven los jugadores en la lista.", None),
    ("SERVER_NICK", "str", "Unknown", "Nick de quien crea la partida.", None),
    ("PASSWORD", "str", "", "Contrasena (maximo 8 caracteres). Vacio = sin contrasena.", None),
    ("MAX_PLAYERS", "int", "16", "Numero maximo de jugadores (1 a 32).", (1, 32)),
    ("MAX_TIME", "int", "30", "Minutos que dura cada mapa (-1 = sin limite).", (-1, 9999)),
    ("MAX_POINTS", "int", "3", "Puntos maximos en 'destruir objetivo' (-1 = sin limite).", (-1, 9999)),
    ("MAX_FRAGS", "int", "30", "Puntos maximos en deathmatch.", (-1, 9999)),
    ("MAX_TEAM_FRAGS", "int", "99", "Puntos maximos en deathmatch por equipos.", (-1, 9999)),
    ("MAX_FLAGS", "int", "3", "Puntos maximos en capturar la bandera (-1 = sin limite).", (-1, 9999)),
    ("FRIENDLY_FIRE", "bool", "1", "Fuego aliado: 1 = los de un mismo equipo se danan.", None),
    ("NET_SPEED", "choice", "1", "Tipo de red de la partida.", {"0": "0 - LAN", "1": "1 - Internet"}),
    ("COMPENSATION", "bool", "0", "Modo cabezon: la cabeza crece con cada muerte.", None),
    ("THIRD_VIEW", "bool", "1", "Permite la camara en tercera persona.", None),
    ("MAX_PING", "int", "999", "Latencia maxima (ms) para entrar a la partida.", (1, 99999)),
    ("MATRIX", "int", "100", "Velocidad de juego en % (25 a 400).", (25, 400)),
    ("SPEED", "int", "100", "Velocidad de los jugadores en % (25 a 400).", (25, 400)),
]
PARAM_KEYS = [p[0] for p in PARAMS]

_KEY_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*?)\s*$")


class ServerCfg:
    """Lee y escribe server.cfg sin perder comentarios ni claves desconocidas."""

    def __init__(self, path: str):
        self.path = path
        self.lines: List[str] = []
        self.eol = "\r\n"
        self.trailing_nl = True
        self.values: "OrderedDict[str, str]" = OrderedDict()
        self.key_lines: Dict[str, List[int]] = {}
        self.loop: List[str] = []
        self.loop_start: Optional[int] = None
        self.loop_end: Optional[int] = None
        self.loop_had_end = False

    # -- lectura ------------------------------------------------------------
    def load(self) -> "ServerCfg":
        self.values.clear()
        self.key_lines.clear()
        self.loop = []
        self.loop_start = self.loop_end = None
        self.loop_had_end = False
        if not os.path.isfile(self.path):
            self.lines = []
            return self
        with open(self.path, "rb") as f:
            data = f.read()
        self.eol = "\r\n" if b"\r\n" in data else "\n"
        text = data.decode("latin-1")
        self.trailing_nl = text.endswith("\n") or text.endswith("\r")
        parts = re.split(r"\r\n|\n|\r", text)
        if self.trailing_nl and parts and parts[-1] == "":
            parts.pop()
        self.lines = parts
        i = 0
        n = len(self.lines)
        while i < n:
            s = self.lines[i].strip()
            if not s or s.startswith("//"):
                i += 1
                continue
            m = _KEY_RE.match(self.lines[i])
            if not m:
                i += 1
                continue
            key, val = m.group(1).upper(), m.group(2)
            if key == "SCENE_LOOP":
                self.loop_start = i
                if val and not val.startswith("//"):
                    self.loop.append(val)
                j = i + 1
                end = None
                while j < n:
                    t = self.lines[j].strip()
                    if t.upper().startswith("END_SCENE_LOOP"):
                        end = j
                        self.loop_had_end = True
                        break
                    if t and not t.startswith("//"):
                        self.loop.append(t)
                    j += 1
                self.loop_end = end if end is not None else n - 1
                i = self.loop_end + 1
                continue
            if key == "END_SCENE_LOOP":
                i += 1
                continue
            if key not in self.values:
                self.values[key] = val
            self.key_lines.setdefault(key, []).append(i)
            i += 1
        return self

    # -- acceso ----------------------------------------------------------------
    def get(self, key: str, default: str = "") -> str:
        return self.values.get(key.upper(), default)

    def set(self, key: str, value: str) -> None:
        self.values[key.upper()] = str(value)

    def extra_keys(self) -> List[str]:
        return [k for k in self.values if k not in PARAM_KEYS]

    # -- escritura ------------------------------------------------------------
    def to_text(self) -> str:
        out: List[str] = []
        done = set()
        block = ["SCENE_LOOP:"] + list(self.loop) + ["END_SCENE_LOOP"]
        missing = [k for k in self.values if k not in self.key_lines]
        for i, line in enumerate(self.lines):
            if self.loop_start is not None and self.loop_start <= i <= (self.loop_end or i):
                if i == self.loop_start:
                    for k in missing:
                        out.append(f"{k}: {self.values[k]}")
                        done.add(k)
                    out.extend(block)
                continue
            m = _KEY_RE.match(line)
            if m and not line.strip().startswith("//"):
                key = m.group(1).upper()
                if key in self.values and key in self.key_lines and i in self.key_lines[key]:
                    if m.group(2) != self.values[key]:
                        line = f"{m.group(1)}: {self.values[key]}".rstrip()
            out.append(line)
        for k in missing:
            if k not in done:
                out.append(f"{k}: {self.values[k]}")
        if self.loop_start is None and self.loop:
            out.extend(block)
        text = self.eol.join(out)
        if self.trailing_nl or not self.lines:
            text += self.eol
        return text

    def save(self, backup: bool = True) -> None:
        if backup and os.path.isfile(self.path):
            bak = self.path + ".bak"
            if not os.path.exists(bak):
                shutil.copy2(self.path, bak)
        with open(self.path, "wb") as f:
            f.write(self.to_text().encode("latin-1", errors="replace"))
        self.load()

    # -- compatibilidad con Python -------------------------------------------
    def to_dict(self) -> dict:
        d = dict(self.values)
        d["SCENE_LOOP"] = list(self.loop)
        return d

    def update_from_dict(self, d: dict) -> None:
        for k, v in d.items():
            if k.upper() == "SCENE_LOOP":
                self.loop = [str(x) for x in v]
            else:
                self.set(k, str(v))


# ----------------------------------------------------------------------------
# 2. demo.vpk: indice (formato "vtPack"), escenas .scn y texturas .tex
# ----------------------------------------------------------------------------
# Logica de lectura tomada de vpk_tool v1.0 (unpackertol):
#   0x00 "vtPack" | 0x06 16 bytes | 0x16 u32 tam. cabecera | 0x1A u32 n.o registros | 0x1E u32 offset de la tabla
#   Tabla (hasta el final): u32 len+nombre, u32 len+carpeta ("a\\b\\"), i32 flag, u32 tam, u32 tam, u32 offset,
#   u32 marca, u32 tipo (1 carpeta, 2 archivo). Los datos van sin comprimir.
#   Los nombres son bytes: en los mapas del mod ToL-PrO empiezan por un byte de control (0x04), que el
#   desempaquetador muestra como "%04", y terminan con basura antes de ".scn" (p. ej. "°¤").
VPK_MAGIC = b"vtPack"
VPK_HEADER = 0x22
_LEAD_RE = re.compile(r"^(?:[\x00-\x1f]+|%[0-9A-Fa-f]{2})+")
_SCN_RE = re.compile(rb"([\x00-\x1f]+|%[0-9A-Fa-f]{2})?([A-Za-z0-9_][A-Za-z0-9_\- ]{1,62})[\x80-\xff]{0,4}\.scn", re.I)

DEFAULT_MAPS = [
    "MP_B_GARAJE", "MP_B_MARB", "MP_B_ALMACEN", "MP_B_ANDERGUATER", "MP_B_MALIBU",
    "MP_B_LABERINTO", "MP_B_INDUSTRIAL", "MP_DO_PLAZA", "MP_DO_ASALTO", "MP_DO_MADRID",
    "MP_DM_VERTIGO", "MP_DM_GARAJE", "MP_DM_MOONLIGHT", "MP_DM_METRO", "MP_DM_LOVEBOAT",
    "MP_DM_INDUSTRIAL", "MP_DM_RUINAS", "MP_DM_MARB", "MP_DMT_VERTIGO", "MP_DMT_GARAJE",
    "MP_DMT_MOONLIGHT", "MP_DMT_METRO", "MP_DMT_LOVEBOAT", "MP_DMT_INDUSTRIAL",
    "MP_DMT_RUINAS", "MP_DMT_MARB",
]


class VpkEntry:
    __slots__ = ("name", "dir", "size", "offset", "type")

    def __init__(self, name: str, dir_: str, size: int, offset: int, typ: int):
        self.name, self.dir, self.size, self.offset, self.type = name, dir_, size, offset, typ

    @property
    def path(self) -> str:
        d = self.dir.replace("\\", "/").strip("/")
        return (d + "/" + self.name) if d else self.name

    def in_folder(self, folder: str) -> bool:
        return folder.lower() in [x.lower() for x in self.dir.replace("\\", "/").split("/") if x]


def read_vpk_index(path: str) -> Tuple[List[VpkEntry], str]:
    """Devuelve (entradas, informe). Lista vacia si el formato no coincide."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            total = f.tell()
            f.seek(0)
            head = f.read(VPK_HEADER)
            if len(head) < VPK_HEADER or head[:6] != VPK_MAGIC:
                return [], f"{os.path.basename(path)}: no es un vtPack (empieza por {head[:8]!r})"
            _start, count, toc_off = struct.unpack_from("<III", head, 0x16)
            if not (VPK_HEADER <= toc_off <= total):
                return [], f"vtPack: offset de tabla invalido ({toc_off})"
            f.seek(toc_off)
            toc = f.read(total - toc_off)
    except OSError as e:
        return [], str(e)
    recs: List[VpkEntry] = []
    p = 0
    try:
        while p < len(toc):
            nl, = struct.unpack_from("<I", toc, p)
            p += 4
            name = toc[p:p + nl]
            p += nl
            dl, = struct.unpack_from("<I", toc, p)
            p += 4
            dr = toc[p:p + dl]
            p += dl
            _flag, s1, _s2, off, _tag, typ = struct.unpack_from("<iIIIII", toc, p)
            p += 24
            recs.append(VpkEntry(name.split(b"\0")[0].decode("latin-1"), dr.split(b"\0")[0].decode("latin-1"), s1, off, typ))
    except struct.error:
        return recs, f"vtPack: tabla truncada ({len(recs)} registros leidos)"
    nfiles = sum(1 for r in recs if r.type == 2)
    return recs, f"vtPack: {nfiles} archivos, {len(recs) - nfiles} carpetas"


def scene_info(name: str) -> Tuple[str, bool, str]:
    """(nombre limpio, es_mod, nombre sin extension).
    '\\x04mp_b_cochera°¤.scn' o '%04mp_b_cochera°¤.scn' -> ('mp_b_cochera', True, ...)."""
    base = name.replace("\\", "/").split("/")[-1]
    stem = re.sub(r"\.(scn|tex)$", "", base, flags=re.I)
    is_mod = bool(_LEAD_RE.match(stem))
    s = _LEAD_RE.sub("", stem)
    clean = re.sub(r"[^A-Za-z0-9_\- ]", "", s).strip()
    return clean, is_mod, stem


class VpkCatalog:
    """Escenas y texturas halladas en el demo.vpk."""

    def __init__(self) -> None:
        self.path = ""
        self.info = ""
        self.entries: List[VpkEntry] = []
        self.scenes: List[Tuple[str, str, bool]] = []   # (limpio, crudo, es_mod)
        self.tex: Dict[str, VpkEntry] = {}              # nombre crudo sin extension (minusculas)
        self.tex_clean: Dict[Tuple[str, bool], VpkEntry] = {}

    def maps(self, mode: str = "") -> "OrderedDict[str, str]":
        """Escenas mp_* del demo.vpk (mods incluidos) que tienen texs\\<mapa>-tbn.tex. NOMBRE EN MAYUSCULAS -> nombre crudo."""
        res: "OrderedDict[str, str]" = OrderedDict()
        for clean, raw, _is_mod in self.scenes:
            if clean.lower().startswith("mp_") and self.tex_entry(raw, clean):   # solo mapas con vista previa -tbn.tex
                res.setdefault(clean.upper(), raw)
        return OrderedDict(sorted(res.items()))

    def tex_entry(self, raw: str, clean: str) -> Optional[VpkEntry]:
        """Vista previa: texs\\<mapa>-tbn.tex"""
        e = self.tex.get(raw.lower() + "-tbn")
        if e:
            return e
        c = clean.lower()
        for key in (c + "-tbn", c):
            for mod in (True, False):
                e = self.tex_clean.get((key, mod))
                if e:
                    return e
        return self.tex.get(raw.lower())

    def read_entry(self, e: VpkEntry, limit: int = 64 * 1024 * 1024) -> bytes:
        with open(self.path, "rb") as f:
            f.seek(e.offset)
            return f.read(min(e.size, limit))


def load_vpk(path: str, progress: Optional[Callable[[float], None]] = None) -> VpkCatalog:
    cat = VpkCatalog()
    cat.path = path
    if not os.path.isfile(path):
        cat.info = "no existe " + path
        return cat
    ents, cat.info = read_vpk_index(path)
    cat.entries = ents
    if ents:
        for e in ents:
            if e.type != 2:
                continue
            low = e.name.lower()
            if low.endswith(".scn"):
                clean, is_mod, stem = scene_info(e.name)
                if clean:
                    cat.scenes.append((clean, stem, is_mod))
            elif low.endswith(".tex"):
                clean, is_mod, stem = scene_info(e.name)
                cat.tex.setdefault(stem.lower(), e)
                if clean:
                    cat.tex_clean.setdefault((clean.lower(), is_mod), e)
        return cat
    # plan B: buscar nombres '*.scn' como texto (formato no reconocido)
    size = os.path.getsize(path)
    seen = set()
    chunk, overlap, pos, tail = 8 * 1024 * 1024, 128, 0, b""
    with open(path, "rb") as f:
        while True:
            buf = f.read(chunk)
            if not buf:
                break
            data = tail + buf
            for m in _SCN_RE.finditer(data):
                pre = (m.group(1) or b"").decode("latin-1")
                nm = m.group(2).decode("latin-1").strip()
                k = re.search(r"mp_", nm, re.I)
                if k and k.start() > 0:
                    nm = nm[k.start():]
                raw = pre + nm
                if raw.lower() not in seen:
                    seen.add(raw.lower())
                    clean, is_mod, _s = scene_info(raw + ".scn")
                    if clean:
                        cat.scenes.append((clean, raw, is_mod))
            tail = data[-overlap:]
            pos += len(buf)
            if progress and size:
                progress(min(1.0, pos / size))
    cat.info += " | plan B: busqueda de texto"
    return cat


def scan_loose_scenes(folder: str) -> List[Tuple[str, str, bool]]:
    out: List[Tuple[str, str, bool]] = []
    if folder and os.path.isdir(folder):
        for root, _d, files in os.walk(folder):
            for fn in files:
                if fn.lower().endswith(".scn"):
                    clean, is_mod, stem = scene_info(fn)
                    if clean:
                        out.append((clean, stem, is_mod))
            if root.count(os.sep) - folder.count(os.sep) > 3:
                break
    return out


def hexdump(data: bytes, n: int = 48) -> str:
    return " ".join(f"{b:02x}" for b in data[:n])
# ----------------------------------------------------------------------------
# 3. DLL: re-enlace de DLL de cliente (vtKernel -> vtserver) + preparacion runtime
# ----------------------------------------------------------------------------
class PE:
    def __init__(self, data: bytes):
        self.d = bytearray(data)
        if self.d[:2] != b"MZ":
            raise ValueError("no es un ejecutable PE")
        self.e = struct.unpack_from("<I", self.d, 0x3C)[0]
        if self.d[self.e:self.e + 4] != b"PE\0\0":
            raise ValueError("firma PE invalida")
        nsec = struct.unpack_from("<H", self.d, self.e + 6)[0]
        opt = struct.unpack_from("<H", self.d, self.e + 20)[0]
        self.opt = self.e + 24
        if struct.unpack_from("<H", self.d, self.opt)[0] != 0x10B:
            raise ValueError("solo se admiten PE de 32 bits")
        self.dd = self.opt + 96
        self.secs = []
        for i in range(nsec):
            o = self.opt + opt + i * 40
            vs, va, rs, rp = struct.unpack_from("<IIII", self.d, o + 8)
            self.secs.append((va, max(vs, rs), rp))

    def off(self, rva: int) -> Optional[int]:
        for va, sz, rp in self.secs:
            if va <= rva < va + sz:
                return rp + rva - va
        return None

    def cstr(self, off: int) -> str:
        end = self.d.index(b"\0", off)
        return self.d[off:end].decode("latin-1")

    def import_descriptors(self):
        rva, _sz = struct.unpack_from("<II", self.d, self.dd + 8)
        o = self.off(rva) if rva else None
        res = []
        while o is not None:
            oft, ts, fc, nm, ft = struct.unpack_from("<IIIII", self.d, o)
            if nm == 0:
                break
            no = self.off(nm)
            if no is None:
                break
            res.append((o, no, self.cstr(no)))
            o += 20
        return res

    def export_name_loc(self) -> Optional[Tuple[int, str]]:
        rva, _sz = struct.unpack_from("<II", self.d, self.dd)
        if not rva:
            return None
        o = self.off(rva)
        if o is None:
            return None
        nm = struct.unpack_from("<I", self.d, o + 12)[0]
        no = self.off(nm)
        if no is None:
            return None
        return no, self.cstr(no)


# ---- texturas .tex (formato 'TeX2': DXT1/DXT5), decodificador de vpk_tool v1.0 ----
def _rgb565(c):
    r, g, b = (c >> 11) & 31, (c >> 5) & 63, c & 31
    return (r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)


def decode_dxt(w, h, fmt, data, step=1):
    """Decodifica DXT1 (fmt 0) o DXT5 (fmt 1) tomando 1 de cada 'step' pixeles. Devuelve (ancho, alto, RGBA)."""
    bw, bh = (w + 3) // 4, (h + 3) // 4
    bpb = 8 if fmt == 0 else 16
    ow, oh = (w + step - 1) // step, (h + step - 1) // step
    out = bytearray(ow * oh * 4)
    for by in range(bh):
        ys = [j for j in range(4) if (by * 4 + j) % step == 0 and by * 4 + j < h]
        if not ys:
            continue
        for bx in range(bw):
            xs = [i for i in range(4) if (bx * 4 + i) % step == 0 and bx * 4 + i < w]
            if not xs:
                continue
            o = (by * bw + bx) * bpb
            blk = data[o:o + bpb]
            if len(blk) < bpb:
                continue
            if fmt == 1:
                a0, a1 = blk[0], blk[1]
                abits = int.from_bytes(blk[2:8], "little")
                if a0 > a1:
                    al = [a0, a1] + [((7 - k) * a0 + k * a1) // 7 for k in range(1, 7)]
                else:
                    al = [a0, a1] + [((5 - k) * a0 + k * a1) // 5 for k in range(1, 5)] + [0, 255]
                cb = blk[8:16]
            else:
                cb = blk
            c0, c1, bits = struct.unpack("<HHI", cb)
            r0, g0, b0 = _rgb565(c0)
            r1, g1, b1 = _rgb565(c1)
            if c0 > c1 or fmt == 1:
                pal = [(r0, g0, b0, 255), (r1, g1, b1, 255),
                       ((2 * r0 + r1) // 3, (2 * g0 + g1) // 3, (2 * b0 + b1) // 3, 255),
                       ((r0 + 2 * r1) // 3, (g0 + 2 * g1) // 3, (b0 + 2 * b1) // 3, 255)]
            else:
                pal = [(r0, g0, b0, 255), (r1, g1, b1, 255),
                       ((r0 + r1) // 2, (g0 + g1) // 2, (b0 + b1) // 2, 255), (0, 0, 0, 0)]
            for j in ys:
                for i in xs:
                    k = j * 4 + i
                    px = pal[(bits >> (2 * k)) & 3]
                    a = px[3] if fmt == 0 else al[(abits >> (3 * k)) & 7]
                    q = (((by * 4 + j) // step) * ow + (bx * 4 + i) // step) * 4
                    out[q], out[q + 1], out[q + 2], out[q + 3] = px[0], px[1], px[2], a
    return ow, oh, out


def _png_rgb(w, h, raw):
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def tex_preview(data: bytes, box: int = 340, ignore_alpha: bool = False) -> Tuple[bytes, str]:
    """Convierte un .tex (TeX2, DXT1/DXT5) en un PNG que cabe en box x box (tkinter lo muestra sin Pillow)."""
    if data[:4] != b"TeX2":
        raise ValueError("no es un TeX2 (cabecera %s)" % hexdump(data, 8))
    fmt = data[4]
    if fmt not in (0, 1):
        raise ValueError("formato %d no soportado" % fmt)
    w, h, sz = struct.unpack("<III", data[9:21])
    if w == 0 or h == 0 or w > 8192 or h > 8192:
        raise ValueError("tamano invalido %dx%d" % (w, h))
    step = 1
    while max(w, h) // step > 1024:
        step *= 2
    ow, oh, rgba = decode_dxt(w, h, fmt, data[21:21 + sz], step)
    sc = min(box / ow, box / oh, 4.0)
    nw, nh = max(1, int(ow * sc)), max(1, int(oh * sc))
    xi = [min(ow - 1, int(x / sc)) for x in range(nw)]
    raw = bytearray()
    for y in range(nh):
        base = min(oh - 1, int(y / sc)) * ow * 4
        row = bytearray(nw * 3)
        for x in range(nw):
            q = base + xi[x] * 4
            r, g, b, a = rgba[q], rgba[q + 1], rgba[q + 2], rgba[q + 3]
            if a != 255 and not ignore_alpha:
                bg = 205 if ((x >> 3) + (y >> 3)) & 1 else 140
                r, g, b = (r * a + bg * (255 - a)) // 255, (g * a + bg * (255 - a)) // 255, (b * a + bg * (255 - a)) // 255
            row[x * 3:x * 3 + 3] = bytes((r, g, b))
        raw += b"\0" + row
    return _png_rgb(nw, nh, bytes(raw)), "%dx%d  %s" % (w, h, "DXT1" if fmt == 0 else "DXT5")


def read_tex_bytes(vpk: str, e: "VpkEntry") -> bytes:
    """Lee solo lo necesario para mostrar la textura (cabecera + nivel principal)."""
    with open(vpk, "rb") as f:
        f.seek(e.offset)
        head = f.read(21)
        if len(head) < 21 or head[:4] != b"TeX2":
            return head
        w, h = struct.unpack("<II", head[9:17])
        need = ((w + 3) // 4) * ((h + 3) // 4) * (8 if head[4] == 0 else 16)
        return head + f.read(min(need, max(0, e.size - 21)))


def dll_links(data: bytes) -> List[str]:
    try:
        return [n for _o, _no, n in PE(data).import_descriptors()]
    except Exception:
        return []


def is_client_build(data: bytes) -> bool:
    return any(n.lower() in ("vtkernel", "vtkernel.dll") for n in dll_links(data))


def retarget_for_server(data: bytes, own_name: str, used_internal: set) -> Tuple[bytes, List[str]]:
    """vtKernel -> vtserver, Scrípt.dll -> Script.dll, nombre interno unico."""
    pe = PE(data)
    changes: List[str] = []
    for desc_off, name_off, name in pe.import_descriptors():
        low = name.lower()
        new = None
        if low == "vtkernel":
            new = "vtserver"
        elif low == "vtkernel.dll":
            new = "vtserver.dll"
        elif low.startswith("sc") and low.endswith("pt.dll") and len(name) == 10 and name != "Script.dll":
            new = "Script.dll"
        if new is not None:
            pe.d[name_off:name_off + len(name)] = new.encode("latin-1")
            changes.append(f"import {name} -> {new}")
        # sin 'binding' previo: obliga a Windows a resolver las funciones al cargar
        pe.d[desc_off + 4:desc_off + 12] = b"\0" * 8
    pe.d[pe.dd + 11 * 8:pe.dd + 12 * 8] = b"\0" * 8  # Bound Import Directory
    loc = pe.export_name_loc()
    if loc:
        no, old = loc
        cand = own_name
        if len(cand) > len(old):  # no cabe: acortar conservando extension
            stem = os.path.splitext(own_name)[0]
            cand = stem[: max(1, len(old) - 4)] + ".dll"
        base = os.path.splitext(cand)[0]
        k = 0
        while cand.lower() in used_internal and k < 99:
            k += 1
            suf = str(k)
            cand = base[: max(1, len(base) - len(suf))] + suf + ".dll"
        if len(cand) <= len(old):
            pe.d[no:no + len(old)] = cand.encode("latin-1").ljust(len(old), b"\0")
            used_internal.add(cand.lower())
            if cand != old:
                changes.append(f"nombre interno {old} -> {cand}")
    return bytes(pe.d), changes


_MM_ANCHOR = b"JUGADOR\0mplayer.cfg"
MM_DEFAULT = "tol.trewq.com"
MM_PRESETS = ["tol.trewq.com", "185.228.138.108", "10.88.0.1"]


def _mm_locate(data: bytes) -> Optional[Tuple[int, int, int]]:
    """(inicio, fin del texto, capacidad) del host del matchmaker dentro de Main.dll."""
    a = data.find(_MM_ANCHOR)
    if a > 0:
        j = a
        while j > 0 and data[j - 1] == 0:
            j -= 1
        k = j
        while k > 0 and 0x20 <= data[k - 1] < 0x7F:
            k -= 1
        if j - k >= 3:
            return k, j, a - k - 1
    i = data.find(MM_DEFAULT.encode() + b"\0")
    if i >= 0:
        e = i + len(MM_DEFAULT)
        z = e
        while z < len(data) and data[z] == 0:
            z += 1
        return i, e, z - i - 1
    return None


def read_matchmaker(data: bytes) -> Optional[Tuple[str, int]]:
    """(host actual, longitud maxima) o None si no es la Main.dll del servidor."""
    loc = _mm_locate(data)
    if not loc:
        return None
    k, j, cap = loc
    return data[k:j].decode("latin-1"), cap


def patch_matchmaker(data: bytes, host: str) -> Tuple[bytes, str, int]:
    """Escribe 'host' en el lugar del matchmaker. Devuelve (datos, host anterior, capacidad)."""
    loc = _mm_locate(data)
    if not loc:
        raise ValueError("No encuentro la direccion del matchmaker en esa DLL (no parece la Main.dll del servidor).")
    k, j, cap = loc
    h = host.strip().encode("latin-1")
    if not h or any(c < 0x21 or c > 0x7E for c in h):
        raise ValueError("Direccion invalida.")
    if len(h) > cap:
        raise ValueError(f"'{host}' mide {len(h)} caracteres; el maximo en Main.dll es {cap}. "
                         "Usa una IP o un nombre mas corto, o una linea en el archivo hosts de Windows.")
    old = data[k:j].decode("latin-1")
    b = bytearray(data)
    b[k:k + cap + 1] = h.ljust(cap + 1, b"\0")
    return bytes(b), old, cap


def patch_main_file(path: str, host: str) -> str:
    """Parchea Main.dll en su sitio (con copia Main.dll.bak la primera vez)."""
    with open(path, "rb") as f:
        data = f.read()
    new, old, _cap = patch_matchmaker(data, host)
    if new == data:
        return f"Main.dll ya apuntaba a {host}"
    bak = path + ".bak"
    if not os.path.exists(bak):
        shutil.copy2(path, bak)
    with open(path, "wb") as f:
        f.write(new)
    return f"Main.dll: {old} -> {host}"


CORE_SERVER_DLLS = {"main.dll", "mp_servidor.dll", "mp_cliente.dll", "mp_oponente.dll", "mp_player.dll", "script.dll"}


def norm_dll(name: str) -> str:
    n = name.lower()
    if n.startswith("sc") and n.endswith("pt.dll") and len(n) == 10:
        return "script.dll"
    return n


def list_dlls(folder: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if folder and os.path.isdir(folder):
        for fn in sorted(os.listdir(folder)):
            if fn.lower().endswith(".dll"):
                key = norm_dll(fn)
                # si hay Script.dll y Scrípt.dll se prefiere el que se llama Script.dll
                if key not in out or fn.lower() == key:
                    out[key] = os.path.join(folder, fn)
    return out


def release_dir(path: str) -> str:
    """Acepta la raiz del juego/servidor o directamente comun\\bin\\release."""
    if not path:
        return ""
    cand = os.path.join(path, "comun", "bin", "release")
    return cand if os.path.isdir(cand) else path


def client_root(path: str) -> str:
    """Carpeta del cliente que contiene demo.vpk (acepta la raiz o comun\\bin\\release)."""
    p = (path or "").strip()
    for _ in range(4):
        if not p:
            break
        if os.path.isfile(os.path.join(p, "demo.vpk")):
            return p
        np_ = os.path.dirname(p.rstrip("\\/"))
        if np_ == p:
            break
        p = np_
    return ""


def find_vtkernel(client_root: str) -> Optional[str]:
    if not client_root:
        return None
    for base in (client_root, release_dir(client_root), os.path.join(client_root, "bin"),
                 os.path.join(client_root, "comun", "bin")):
        p = os.path.join(base, "vtKernel.dll")
        if os.path.isfile(p):
            return p
    return None


def build_plan(server_rel, client_rel, preset: str, overrides: Dict[str, str]) -> "OrderedDict[str, dict]":
    """Decide de donde sale cada DLL. preset: 'auto' | 'servidor'."""
    sv = server_rel if isinstance(server_rel, dict) else list_dlls(server_rel)
    cl = client_rel if isinstance(client_rel, dict) else (list_dlls(client_rel) if client_rel else {})
    plan: "OrderedDict[str, dict]" = OrderedDict()
    for key in sorted(set(sv) | set(cl)):
        if key in sv and key in cl:
            if preset == "servidor" or key in CORE_SERVER_DLLS:
                src, why = "servidor", ("nucleo del servidor" if key in CORE_SERVER_DLLS else "preferencia: servidor")
            else:
                src, why = "cliente", "misma DLL que el cliente (re-enlazada)"
        elif key in sv:
            src, why = "servidor", "solo existe en el servidor"
        else:
            src, why = "cliente", "no existe en el servidor: se usa la del cliente (re-enlazada)"
        ov = overrides.get(key)
        if ov in ("servidor", "cliente") and ((ov == "servidor" and key in sv) or (ov == "cliente" and key in cl)):
            src, why = ov, "elegido a mano"
        plan[key] = {"source": src, "why": why,
                     "server": sv.get(key), "client": cl.get(key)}
    return plan




def _same_bytes(path: str, data: bytes) -> bool:
    try:
        if os.path.getsize(path) != len(data):
            return False
        with open(path, "rb") as f:
            return f.read() == data
    except OSError:
        return False


def _copy_if_newer(src: str, dst: str) -> None:
    if (not os.path.isfile(dst)) or os.path.getsize(src) != os.path.getsize(dst) \
            or os.path.getmtime(src) > os.path.getmtime(dst):
        shutil.copy2(src, dst)


# ----------------------------------------------------------------------------
# 4. Red: IP locales / publica
# ----------------------------------------------------------------------------
def local_ips() -> List[str]:
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    if IS_WIN:
        try:
            out = subprocess.run(["ipconfig"], capture_output=True, text=True, timeout=5,
                                 creationflags=0x08000000).stdout
            for m in re.finditer(r"IPv4[^:\n]*:\s*(\d+\.\d+\.\d+\.\d+)", out):
                ips.add(m.group(1))
        except Exception:
            pass
    return sorted(i for i in ips if not i.startswith("127."))


def public_ip(timeout: float = 6.0) -> str:
    for url in ("https://api.ipify.org", "https://ifconfig.me/ip"):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return r.read().decode().strip()
        except Exception:
            continue
    return ""


NET_KEY = r"SOFTWARE\Virtual toys\Torrente on-line"
_DP8_SERVER_CLSID = "da825e1b-6830-43d7-835d-0b5ad82956a2"
_DP8_SERVER_IID = "5102dace-241b-11d3-aea7-006097b01411"
_DP8_SP_TCPIP = "ebfe7ba0-628d-11d2-ae0f-006097b01411"
_DPNERR_BUFFERTOOSMALL = 0x80158100


def adapter_label(name: str, family: str, ip: str) -> str:
    return f"{name} - {family} - {ip}"


def _parse_ip_json(txt: str) -> List[dict]:
    """Salida JSON de PowerShell Get-NetIPAddress -> adaptadores."""
    try:
        data = json.loads(txt)
    except ValueError:
        return []
    if isinstance(data, dict):
        data = [data]
    out = []
    for d in data:
        ip = str(d.get("IPAddress", ""))
        fam = d.get("AddressFamily")
        fam = "IPv4" if fam in (2, "IPv4", "InterNetwork") else "IPv6" if fam in (23, "IPv6", "InterNetworkV6") else str(fam)
        if not ip or ip.startswith("127.") or ip == "::1" or ip.lower().startswith("fe80"):
            continue
        out.append({"name": adapter_label(str(d.get("InterfaceAlias", "?")), fam.lower(), ip),
                    "guid": None, "ip": ip, "source": "ip"})
    return out


def _parse_ip_addr_json(txt: str) -> List[dict]:
    """Salida de 'ip -j addr' (Linux, para pruebas)."""
    try:
        data = json.loads(txt)
    except ValueError:
        return []
    out = []
    for itf in data:
        if itf.get("operstate") == "DOWN":
            continue
        for a in itf.get("addr_info", []):
            ip = a.get("local", "")
            fam = "ipv4" if a.get("family") == "inet" else "ipv6"
            if ip.startswith("127.") or ip == "::1" or ip.lower().startswith("fe80"):
                continue
            out.append({"name": adapter_label(itf.get("ifname", "?"), fam, ip), "guid": None, "ip": ip, "source": "ip"})
    return out


def list_ip_adapters() -> List[dict]:
    """Adaptadores activos con IP (sin GUID de DirectPlay)."""
    try:
        if IS_WIN:
            ps = ("Get-NetIPAddress | Where-Object { $_.AddressState -eq 'Preferred' } | "
                  "Select-Object InterfaceAlias,AddressFamily,IPAddress | ConvertTo-Json")
            r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True,
                               text=True, timeout=20, creationflags=0x08000000)
            res = _parse_ip_json(r.stdout)
            if res:
                return res
        else:
            r = subprocess.run(["ip", "-j", "addr"], capture_output=True, text=True, timeout=5)
            res = _parse_ip_addr_json(r.stdout)
            if res:
                return res
    except Exception:
        pass
    return [{"name": adapter_label("red", "ipv4", ip), "guid": None, "ip": ip, "source": "ip"} for ip in local_ips()]


def enum_directplay_adapters() -> List[dict]:
    """Los mismos adaptadores (y GUID) que lista el juego mediante DirectPlay 8 (solo Windows)."""
    if not IS_WIN:
        raise RuntimeError("DirectPlay solo existe en Windows")
    import uuid
    from ctypes import wintypes  # noqa: F401

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16),
                    ("Data4", ctypes.c_ubyte * 8)]

    def mk(s: str) -> "GUID":
        g = GUID()
        ctypes.memmove(ctypes.byref(g), uuid.UUID(s).bytes_le, 16)
        return g

    class SPINFO(ctypes.Structure):
        _fields_ = [("flags", ctypes.c_uint32), ("guid", ctypes.c_ubyte * 16), ("name", ctypes.c_void_p),
                    ("reserved", ctypes.c_void_p), ("reserved2", ctypes.c_uint32)]

    ole = ctypes.windll.ole32
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    ole.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(GUID),
                                     ctypes.POINTER(ctypes.c_void_p)]
    hr0 = ole.CoInitializeEx(None, 0)  # MTA; S_OK/S_FALSE/RPC_E_CHANGED_MODE se toleran
    obj = ctypes.c_void_p()
    hr = ole.CoCreateInstance(ctypes.byref(mk(_DP8_SERVER_CLSID)), None, 1, ctypes.byref(mk(_DP8_SERVER_IID)),
                              ctypes.byref(obj)) & 0xFFFFFFFF
    if hr != 0 or not obj.value:
        raise RuntimeError(f"No se pudo crear DirectPlay8 (0x{hr:08X}). Activa 'DirectPlay' en Caracteristicas de Windows.")
    vt = ctypes.cast(ctypes.cast(obj, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
    cbtype = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p)
    cb = cbtype(lambda ctx, typ, msg: 0)
    init = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, cbtype, ctypes.c_ulong)(vt[3])
    enum = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.c_void_p, ctypes.c_void_p,
                              ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong), ctypes.c_ulong)(vt[4])
    release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vt[2])
    try:
        hr = init(obj, None, cb, 1) & 0xFFFFFFFF
        if hr != 0:
            raise RuntimeError(f"DirectPlay Initialize fallo (0x{hr:08X})")
        sp = mk(_DP8_SP_TCPIP)
        size, cnt = ctypes.c_ulong(0), ctypes.c_ulong(0)
        hr = enum(obj, ctypes.byref(sp), None, None, ctypes.byref(size), ctypes.byref(cnt), 0) & 0xFFFFFFFF
        if hr != _DPNERR_BUFFERTOOSMALL:
            raise RuntimeError(f"EnumServiceProviders devolvio 0x{hr:08X}")
        buf = ctypes.create_string_buffer(size.value)
        hr = enum(obj, ctypes.byref(sp), None, buf, ctypes.byref(size), ctypes.byref(cnt), 0) & 0xFFFFFFFF
        if hr != 0:
            raise RuntimeError(f"EnumServiceProviders fallo (0x{hr:08X})")
        out = []
        base = ctypes.addressof(buf)
        for i in range(cnt.value):
            e = SPINFO.from_address(base + i * ctypes.sizeof(SPINFO))
            nm = ctypes.wstring_at(e.name) if e.name else "?"
            out.append({"name": nm, "guid": bytes(e.guid), "ip": "", "source": "directplay"})
        return out
    finally:
        release(obj)


def list_net_adapters() -> Tuple[List[dict], str]:
    """(adaptadores, aviso). Con GUID si DirectPlay responde; si no, solo informativos."""
    if IS_WIN:
        try:
            res = enum_directplay_adapters()
            if res:
                return res, ""
        except Exception as e:  # noqa: BLE001
            return list_ip_adapters(), f"No se pudo leer DirectPlay ({e}); lista solo informativa."
    return list_ip_adapters(), ("" if IS_WIN else "Solo informativo (no es Windows).")


# Registry virtualization: el juego (32 bits, sin admin) abre HKLM con KEY_ALL_ACCESS y Windows redirige lo que escribe a
# HKCU\Software\Classes\VirtualStore\MACHINE\SOFTWARE\...; esa copia TIENE PRIORIDAD sobre HKLM cuando el juego lee.
# Por eso un netGUID escrito solo en HKLM no se aplicaba si el juego ya habia guardado su propia eleccion.
VS_KEYS = (r"Software\Classes\VirtualStore\MACHINE\SOFTWARE\WOW6432Node\Virtual toys\Torrente on-line",
           r"Software\Classes\VirtualStore\MACHINE\SOFTWARE\Virtual toys\Torrente on-line")


def vs_read(name: str):
    """(valor, tipo) guardado en el VirtualStore del usuario, o None."""
    if not IS_WIN:
        return None
    import winreg
    for key in VS_KEYS:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_READ) as k:
                return winreg.QueryValueEx(k, name)
        except OSError:
            continue
    return None


def vs_mirror(name: str, typ: int, value) -> bool:
    """Si el juego ya tiene copia virtual de la clave, la actualiza tambien (no hace falta administrador)."""
    if not IS_WIN:
        return False
    import winreg
    done = False
    for key in VS_KEYS:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
                winreg.SetValueEx(k, name, 0, typ, value)
                done = True
        except OSError:
            continue
    return done


def _read_hklm(name: str):
    import winreg
    for flag in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, NET_KEY, 0, winreg.KEY_READ | flag) as k:
                return winreg.QueryValueEx(k, name)
        except OSError:
            continue
    return None


def read_netguid(effective: bool = True) -> Optional[bytes]:
    """netGUID que vera el juego: primero el VirtualStore del usuario (si existe) y luego HKLM."""
    if not IS_WIN:
        return None
    import winreg
    for src in ((vs_read("netGUID"),) if effective else ()) + (_read_hklm("netGUID"),):
        if src and src[1] == winreg.REG_BINARY and len(src[0]) == 16:
            return bytes(src[0])
    return None


def write_netguid(guid: bytes) -> str:
    """Escribe netGUID en HKLM (vista de 32 bits, pide administrador si hace falta) y en el VirtualStore del juego."""
    if not IS_WIN:
        raise RuntimeError("Solo en Windows")
    import winreg
    mirrored = vs_mirror("netGUID", winreg.REG_BINARY, guid)
    try:
        with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, NET_KEY, 0, winreg.KEY_SET_VALUE | winreg.KEY_WOW64_32KEY) as k:
            winreg.SetValueEx(k, "netGUID", 0, winreg.REG_BINARY, guid)
        return "Adaptador guardado en el registro." + (" (tambien en la copia virtual del juego)" if mirrored else "")
    except PermissionError:
        params = (f'add "HKLM\\{NET_KEY}" /v netGUID /t REG_BINARY /d {guid.hex()} /f /reg:32')
        r = ctypes.windll.shell32.ShellExecuteW(None, "runas", "reg.exe", params, None, 0)
        if r <= 32:
            if mirrored:
                return "Adaptador guardado en la copia virtual del juego (sin administrador)."
            raise RuntimeError("Hace falta ejecutar como administrador (UAC cancelado).")
        for _ in range(40):
            time.sleep(0.25)
            if read_netguid(effective=False) == guid:
                return "Adaptador guardado (con permiso de administrador)."
        if mirrored:
            return "Adaptador guardado en la copia virtual del juego (sin administrador)."
        raise RuntimeError("No pude confirmar el cambio en el registro.")


def add_firewall_rule(exe: str) -> str:
    """Regla de entrada para Dedicated.exe (pide administrador)."""
    if not IS_WIN:
        raise RuntimeError("Solo en Windows")
    params = ('/c netsh advfirewall firewall delete rule name="TOL Dedicated (manager)" >nul 2>&1 & '
              f'netsh advfirewall firewall add rule name="TOL Dedicated (manager)" dir=in action=allow program="{exe}" '
              'enable=yes profile=any')
    r = ctypes.windll.shell32.ShellExecuteW(None, "runas", "cmd.exe", params, None, 0)
    if r <= 32:
        raise RuntimeError("Hace falta ejecutar como administrador (UAC cancelado).")
    return f"Regla de firewall creada para {exe}"


def test_matchmaker(host: str) -> str:
    try:
        ip = socket.gethostbyname(host)
    except OSError as e:
        return f"No se resuelve '{host}': {e}"
    return f"'{host}' -> {ip}" if ip != host else f"{host} (IP directa)"



def dll_info(path: Optional[str]) -> Optional[dict]:
    if not path or not os.path.isfile(path):
        return None
    with open(path, "rb") as f:
        data = f.read()
    links = [n.lower() for n in dll_links(data)]
    kind = "cliente" if any(n.startswith("vtkernel") for n in links) else \
        "servidor" if any(n.startswith("vtserver") for n in links) else "-"
    return {"path": path, "size": len(data), "md5": hashlib.md5(data).hexdigest(), "link": kind, "data_len": len(data)}


def compare_dlls(server_rel: str, client_rel: str, tolpro_rel: str) -> List[dict]:
    """Compara por nombre las DLL de servidor, cliente y ToL-PrO."""
    sets = [x if isinstance(x, dict) else list_dlls(x) for x in (server_rel, client_rel, tolpro_rel)]
    names = sorted(set().union(*[set(s) for s in sets]))
    rows = []
    for k in names:
        inf = [dll_info(s.get(k)) for s in sets]
        sv, cl, tp = inf
        disp = os.path.basename(next(i["path"] for i in inf if i))
        notes = []
        if not sv:
            notes.append("FALTA EN EL SERVIDOR")
        if sv and cl:
            notes.append("servidor=cliente" if sv["md5"] == cl["md5"] else "servidor distinta de cliente")
        if sv and tp:
            notes.append("servidor=ToL-PrO" if sv["md5"] == tp["md5"] else "servidor distinta de ToL-PrO")
        if cl and tp:
            notes.append("cliente=ToL-PrO" if cl["md5"] == tp["md5"] else "cliente distinta de ToL-PrO")
        if cl and not tp and tolpro_rel:
            notes.append("falta en ToL-PrO")
        if tp and not cl and client_rel:
            notes.append("solo ToL-PrO (no esta en cliente)")
        rows.append({"key": k, "name": disp, "server": sv, "client": cl, "tolpro": tp, "notes": "; ".join(notes),
                     "missing": sv is None})
    return rows


def rows_to_csv(rows: List[dict]) -> str:
    def col(i):
        return f"{i['size']};{i['md5'][:8]};{i['link']}" if i else ";;"
    out = ["dll;srv_size;srv_md5;srv_link;cli_size;cli_md5;cli_link;tol_size;tol_md5;tol_link;notas"]
    for r in rows:
        out.append(";".join([r["name"], col(r["server"]), col(r["client"]), col(r["tolpro"]), r["notes"]]))
    return "\n".join(out)


MANIFEST_NAME = "tol_manager_manifest.json"
BAK = ".bak"


def _md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def _load_manifest(server_dir: str) -> dict:
    try:
        with open(os.path.join(server_dir, MANIFEST_NAME), "r", encoding="utf-8") as f:
            m = json.load(f)
    except (OSError, ValueError):
        m = {}
    m.setdefault("written", {})
    m.setdefault("added", [])
    return m


def _save_manifest(server_dir: str, m: dict) -> None:
    with open(os.path.join(server_dir, MANIFEST_NAME), "w", encoding="utf-8") as f:
        json.dump(m, f, indent=1)


def server_dll_map(server_dir: str) -> Dict[str, str]:
    """DLL 'originales' del servidor: la copia .bak (Main.dll.bak) si existe, si no la DLL de comun\\bin\\release."""
    rel = os.path.join(server_dir, "comun", "bin", "release")
    out = list_dlls(rel) if os.path.isdir(rel) else {}
    if os.path.isdir(rel):
        for fn in sorted(os.listdir(rel)):
            if fn.lower().endswith(".dll" + BAK):
                out[norm_dll(fn[:-len(BAK)])] = os.path.join(rel, fn)
    return out


def _write_managed(server_dir: str, dest: str, data: bytes, man: dict, log: Callable[[str], None]) -> bool:
    """Escribe 'dest'; si ya existia y no lo puso el manager, guarda antes el original como <nombre>.bak (una sola vez)."""
    rkey = os.path.relpath(dest, server_dir).replace("\\", "/").lower()
    h = _md5(data)
    bak = dest + BAK
    if os.path.isfile(dest):
        with open(dest, "rb") as f:
            cur = _md5(f.read())
        if cur == h:
            man["written"][rkey] = h
            return False
        if man["written"].get(rkey) != cur and not os.path.exists(bak):
            shutil.copy2(dest, bak)
            log(f"[dll] copia de seguridad: {os.path.basename(bak)}")
    elif rkey not in man["added"] and not os.path.exists(bak):
        man["added"].append(rkey)
    with open(dest, "wb") as f:
        f.write(data)
    man["written"][rkey] = h
    return True


def stage_release(settings: dict, log: Callable[[str], None]) -> List[str]:
    """Deja en <servidor>\\comun\\bin\\release las DLL que usara Dedicated.exe.

    Dedicated.exe fija su PATH a '<carpeta de Dedicated.exe>\\comun\\bin\\release' (ver GetModuleFileNameA), asi que
    las DLL tienen que estar ahi. Los originales se guardan una vez como <nombre>.bak (Main.dll.bak) junto a la DLL (boton 'Restaurar')."""
    server_dir = settings["server_dir"]
    if not os.path.isfile(os.path.join(server_dir, "Dedicated.exe")):
        raise FileNotFoundError(f"No encuentro Dedicated.exe en {server_dir}")
    rel = os.path.join(server_dir, "comun", "bin", "release")
    os.makedirs(rel, exist_ok=True)
    man = _load_manifest(server_dir)
    changed: List[str] = []

    # motor: vtserver.dll del servidor, o vtKernel.dll del cliente si se pide / hace falta
    vts = os.path.join(server_dir, "vtserver.dll")
    bak_vts = vts + BAK
    mode = settings.get("use_vtkernel", "auto")
    orig_vts = bak_vts if os.path.isfile(bak_vts) else vts
    use_vtk = mode == "si" or (mode == "auto" and not os.path.isfile(orig_vts))
    vtk = find_vtkernel(settings.get("client_dir", ""))
    if use_vtk:
        if not vtk:
            raise FileNotFoundError("Se pidio usar vtKernel.dll como vtserver.dll pero no encuentro vtKernel.dll del cliente "
                                    "(indica la carpeta del cliente, o pon 'vtKernel como vtserver' en 'no').")
        with open(vtk, "rb") as f:
            if _write_managed(server_dir, vts, f.read(), man, log):
                changed.append("vtserver.dll <- vtKernel.dll")
    elif os.path.isfile(bak_vts) and os.path.isfile(vts):   # volver al motor original
        with open(bak_vts, "rb") as f:
            if _write_managed(server_dir, vts, f.read(), man, log):
                changed.append("vtserver.dll (original)")
    elif not os.path.isfile(vts):
        raise FileNotFoundError("Falta vtserver.dll en la carpeta del servidor.")

    srv = server_dll_map(server_dir)
    client_rel = release_dir(settings["client_dir"]) if settings.get("client_dir") else ""
    plan = build_plan(srv, client_rel, settings.get("preset", "auto"), settings.get("overrides", {}))
    used_internal: set = set()
    for key, ent in plan.items():
        src = ent["server"] if ent["source"] == "servidor" else ent["client"]
        out_name = os.path.basename(ent["server"]) if ent["server"] else os.path.basename(src)
        if out_name.lower().endswith(".dll" + BAK):
            out_name = out_name[:-len(BAK)]
        if key == "script.dll" and not ent["server"]:
            out_name = "Script.dll"
        with open(src, "rb") as f:
            data = f.read()
        notes: List[str] = []
        dest_rkey = os.path.relpath(os.path.join(rel, out_name), server_dir).replace("\\", "/").lower()
        if ent["source"] == "servidor" and dest_rkey not in man["written"]:
            # DLL propia del servidor que el manager nunca toco: se deja tal cual (incluida Main.dll parcheada con 'Parchear IP')
            try:
                loc = PE(data).export_name_loc()
                if loc:
                    used_internal.add(loc[1].lower())
            except Exception:
                pass
            continue
        if ent["source"] == "cliente" and is_client_build(data):
            data, ch = retarget_for_server(data, out_name, used_internal)
            notes += ch
        else:
            try:
                loc = PE(data).export_name_loc()
                if loc:
                    used_internal.add(loc[1].lower())
            except Exception:
                pass
        if _write_managed(server_dir, os.path.join(rel, out_name), data, man, log):
            changed.append(out_name)
            if notes:
                log(f"[dll] {out_name}: " + "; ".join(notes))
    for extra in settings.get("extra_dlls", []):
        if os.path.isfile(extra):
            with open(extra, "rb") as f:
                data = f.read()
            nm = os.path.basename(extra)
            if is_client_build(data):
                data, ch = retarget_for_server(data, nm, used_internal)
            if _write_managed(server_dir, os.path.join(rel, nm), data, man, log):
                changed.append(nm)
        else:
            log(f"[dll] DLL extra no encontrada: {extra}")
    _save_manifest(server_dir, man)
    return changed


def restore_release(server_dir: str, log: Callable[[str], None]) -> int:
    """Devuelve las DLL originales (<nombre>.bak -> <nombre>) y quita las que agrego el manager."""
    man = _load_manifest(server_dir)
    n = 0
    for d in (os.path.join(server_dir, "comun", "bin", "release"), server_dir):
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            if fn.lower().endswith(".dll" + BAK):
                try:
                    os.replace(os.path.join(d, fn), os.path.join(d, fn[:-len(BAK)]))
                    n += 1
                    log(f"[dll] restaurada {fn[:-len(BAK)]}")
                except OSError as e:
                    log(f"[dll] no pude restaurar {fn}: {e}")
    for rkey in man.get("added", []):
        path = os.path.join(server_dir, *rkey.split("/"))
        d, name = os.path.dirname(path), os.path.basename(path)
        for fn in os.listdir(d) if os.path.isdir(d) else []:
            if fn.lower() == name:
                try:
                    os.remove(os.path.join(d, fn))
                    n += 1
                    log(f"[dll] quitada {fn} (agregada por el manager)")
                except OSError:
                    pass
    try:
        os.remove(os.path.join(server_dir, MANIFEST_NAME))
    except OSError:
        pass
    return n


# ---- ruta de datos del juego: HKLM\SOFTWARE\Virtual toys\Torrente on-line, valor 'path' ----
def read_data_path() -> str:
    if not IS_WIN:
        return ""
    v = vs_read("path") or _read_hklm("path")      # el VirtualStore del usuario tiene prioridad (ver vs_read)
    return str(v[0]) if v else ""


def same_path(a: str, b: str) -> bool:
    n = lambda p: os.path.normcase(os.path.normpath(p.strip())) if p.strip() else ""  # noqa: E731
    return n(a) == n(b)


def write_data_path(path: str) -> str:
    """Pone 'path' (carpeta con demo.vpk) en el registro, que es de donde lo lee Dedicated.exe sin '-p'."""
    if not IS_WIN:
        raise RuntimeError("Solo en Windows")
    import winreg
    cur = read_data_path()
    val = path.rstrip("\\/") + ("\\" if cur.endswith("\\") or not cur else "")
    vs_mirror("path", winreg.REG_SZ, val)
    try:
        with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, NET_KEY, 0, winreg.KEY_SET_VALUE | winreg.KEY_WOW64_32KEY) as k:
            winreg.SetValueEx(k, "path", 0, winreg.REG_SZ, val)
        return f"Ruta de datos del registro = {val}"
    except PermissionError:
        params = f'add "HKLM\\{NET_KEY}" /v path /t REG_SZ /d "{val}" /f /reg:32'
        r = ctypes.windll.shell32.ShellExecuteW(None, "runas", "reg.exe", params, None, 0)
        if r <= 32:
            raise RuntimeError("Hace falta ejecutar como administrador (UAC cancelado).")
        for _ in range(40):
            time.sleep(0.25)
            h = _read_hklm("path")
            if h and same_path(str(h[0]), val):
                return f"Ruta de datos del registro = {val} (con permiso de administrador)"
        raise RuntimeError("No pude confirmar el cambio en el registro.")


# ----------------------------------------------------------------------------
# 5. Consola de Dedicated.exe: lectura de pantalla + envio de teclas
# ----------------------------------------------------------------------------
def new_lines(prev: List[str], cur: List[str]) -> List[str]:
    """Lineas nuevas de 'cur' respecto a 'prev' (ambas ya completas)."""
    if not prev:
        return cur
    if cur[:len(prev)] == prev:
        return cur[len(prev):]
    for k in range(1, len(prev)):  # la consola se desplazo hacia arriba
        tail = prev[k:]
        if cur[:len(tail)] == tail:
            return cur[len(tail):]
    return cur


class BridgeError(Exception):
    pass


if IS_WIN:
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _u32 = ctypes.WinDLL("user32", use_last_error=True)

    class _COORD(ctypes.Structure):
        _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]

    class _SMALL_RECT(ctypes.Structure):
        _fields_ = [("Left", ctypes.c_short), ("Top", ctypes.c_short),
                    ("Right", ctypes.c_short), ("Bottom", ctypes.c_short)]

    class _CSBI(ctypes.Structure):
        _fields_ = [("dwSize", _COORD), ("dwCursorPosition", _COORD), ("wAttributes", wintypes.WORD),
                    ("srWindow", _SMALL_RECT), ("dwMaximumWindowSize", _COORD)]

    class _KEY_EVENT(ctypes.Structure):
        _fields_ = [("bKeyDown", wintypes.BOOL), ("wRepeatCount", wintypes.WORD),
                    ("wVirtualKeyCode", wintypes.WORD), ("wVirtualScanCode", wintypes.WORD),
                    ("uChar", wintypes.WORD), ("dwControlKeyState", wintypes.DWORD)]

    class _EVENT(ctypes.Union):
        _fields_ = [("KeyEvent", _KEY_EVENT), ("_pad", ctypes.c_ubyte * 16)]

    class _INPUT_RECORD(ctypes.Structure):
        _fields_ = [("EventType", wintypes.WORD), ("Event", _EVENT)]

    _k32.AttachConsole.argtypes = [wintypes.DWORD]
    _k32.AttachConsole.restype = wintypes.BOOL
    _k32.FreeConsole.restype = wintypes.BOOL
    _k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                 wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    _k32.CreateFileW.restype = ctypes.c_void_p
    _k32.CloseHandle.argtypes = [ctypes.c_void_p]
    _k32.GetConsoleScreenBufferInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(_CSBI)]
    _k32.GetConsoleScreenBufferInfo.restype = wintypes.BOOL
    _k32.ReadConsoleOutputCharacterW.argtypes = [ctypes.c_void_p, wintypes.LPWSTR, wintypes.DWORD,
                                                 _COORD, ctypes.POINTER(wintypes.DWORD)]
    _k32.ReadConsoleOutputCharacterW.restype = wintypes.BOOL
    _k32.WriteConsoleInputW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_INPUT_RECORD), wintypes.DWORD,
                                        ctypes.POINTER(wintypes.DWORD)]
    _k32.WriteConsoleInputW.restype = wintypes.BOOL
    _k32.GetNumberOfConsoleInputEvents.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
    _k32.GetNumberOfConsoleInputEvents.restype = wintypes.BOOL
    _k32.SetConsoleCtrlHandler.argtypes = [ctypes.c_void_p, wintypes.BOOL]
    _u32.VkKeyScanW.argtypes = [ctypes.c_wchar]
    _u32.VkKeyScanW.restype = ctypes.c_short
    _u32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
    _u32.MapVirtualKeyW.restype = wintypes.UINT
    _INVALID = ctypes.c_void_p(-1).value

    def detach_own_console() -> None:
        try:
            _k32.FreeConsole()
        except Exception:
            pass

    class WinConsoleBridge:
        """Se engancha a la consola de Dedicated.exe (oculta o visible)."""

        def __init__(self, pid: int):
            self.pid = pid
            self.hin = None
            self.hout = None
            self.prev_final: List[str] = []
            self.partial = ""

        def attach(self, timeout: float = 8.0) -> None:
            t0 = time.time()
            last = 0
            while time.time() - t0 < timeout:
                _k32.FreeConsole()
                if _k32.AttachConsole(self.pid):
                    _k32.SetConsoleCtrlHandler(None, True)
                    self.hin = _k32.CreateFileW("CONIN$", 0xC0000000, 3, None, 3, 0, None)
                    self.hout = _k32.CreateFileW("CONOUT$", 0xC0000000, 3, None, 3, 0, None)
                    if self.hin in (None, _INVALID) or self.hout in (None, _INVALID):
                        raise BridgeError("no se pudo abrir CONIN$/CONOUT$")
                    return
                last = ctypes.get_last_error()
                time.sleep(0.15)
            raise BridgeError(f"AttachConsole fallo (error {last})")

        def close(self) -> None:
            for h in (self.hin, self.hout):
                if h not in (None, _INVALID):
                    _k32.CloseHandle(h)
            self.hin = self.hout = None
            _k32.FreeConsole()

        def poll(self) -> List[str]:
            info = _CSBI()
            if not _k32.GetConsoleScreenBufferInfo(self.hout, ctypes.byref(info)):
                raise BridgeError("GetConsoleScreenBufferInfo fallo")
            width = max(1, info.dwSize.X)
            rows_total = info.dwCursorPosition.Y + 1
            rows: List[str] = []
            row = 0
            while row < rows_total:
                n = min(64, rows_total - row)
                buf = ctypes.create_unicode_buffer(width * n)
                got = wintypes.DWORD(0)
                ok = _k32.ReadConsoleOutputCharacterW(self.hout, buf, width * n, _COORD(0, row), ctypes.byref(got))
                if not ok:
                    raise BridgeError("ReadConsoleOutputCharacterW fallo")
                text = buf.raw[:got.value * ctypes.sizeof(ctypes.c_wchar)] if False else buf[:got.value]
                for i in range(n):
                    rows.append(text[i * width:(i + 1) * width])
                row += n
            # La consola de Dedicated llega desplazada una columna: la 1ra letra de cada linea queda en la ultima columna
            # de la fila anterior. Si pasa en la mayoria de las filas con texto, se corrige desplazando todo un caracter.
            cand = [r for r in rows if r.strip()]
            bad = sum(1 for r in cand if len(r) >= width and r[-1] != " " and len(r) > 1 and r[-2] == " ")
            if cand and bad * 2 >= len(cand) and width > 2:
                stream = " " + "".join(r.ljust(width) for r in rows)
                rows = [stream[i * width:(i + 1) * width] for i in range(len(rows))]
            # una linea larga ocupa varias filas de la consola (cortada en la ultima columna): volver a unirlas
            joined: List[str] = []
            carry = ""
            for r in rows:
                full = len(r.rstrip()) >= width
                carry += r.rstrip() if not full else r
                if not full:
                    joined.append(carry)
                    carry = ""
            if carry:
                joined.append(carry)
            rows = joined
            self.partial = rows[-1] if rows else ""
            final = rows[:-1]
            added = new_lines(self.prev_final, final)
            self.prev_final = final
            return added

        # Varias DLL del dedicado (mp_Servidor, Main, vtserver) leen la consola con _kbhit/_getch y se "roban" teclas
        # entre si, asi que una tecla puede perderse. Por eso cada tecla se verifica mirando el cursor de la consola
        # (el dedicado hace eco de lo escrito) y se reenvia si no se movio.
        def _cursor(self) -> Tuple[int, int]:
            info = _CSBI()
            if not _k32.GetConsoleScreenBufferInfo(self.hout, ctypes.byref(info)):
                raise BridgeError("GetConsoleScreenBufferInfo fallo")
            return info.dwCursorPosition.Y, info.dwCursorPosition.X

        def _write_key(self, ch: str, vk: Optional[int] = None) -> None:
            if vk is None:
                code = _u32.VkKeyScanW(ch)
                vk = code & 0xFF if code != -1 else 0
            scan = _u32.MapVirtualKeyW(vk, 0) if vk else 0
            recs = (_INPUT_RECORD * 2)()
            for i, down in enumerate((1, 0)):
                recs[i].EventType = 1  # KEY_EVENT
                k = recs[i].Event.KeyEvent
                k.bKeyDown = down
                k.wRepeatCount = 1
                k.wVirtualKeyCode = vk
                k.wVirtualScanCode = scan
                k.uChar = ord(ch)
                k.dwControlKeyState = 0
            written = wintypes.DWORD(0)
            if not _k32.WriteConsoleInputW(self.hin, recs, 2, ctypes.byref(written)):
                raise BridgeError("WriteConsoleInputW fallo")

        def _send_key(self, ch: str, vk: Optional[int] = None, verify: bool = True) -> None:
            if not verify:
                self._write_key(ch, vk)
                return
            for _attempt in range(6):
                before = self._cursor()
                self._write_key(ch, vk)
                end = time.time() + 0.35
                while time.time() < end:
                    time.sleep(0.012)
                    if self._cursor() != before:
                        return
                # sin eco: la tecla se la llevo otro lector; reintentar

        def send_text(self, text: str) -> None:
            for ch in text:
                self._send_key(ch)
            self._send_key("\r", 0x0D)

        def send_escape(self) -> None:
            self._send_key("\x1b", 0x1B, verify=False)

else:
    def detach_own_console() -> None:
        pass


class PipeBridge:
    """Alternativa (no Windows / pruebas): lee stdout y escribe en stdin."""

    def __init__(self, proc: "subprocess.Popen[str]"):
        self.proc = proc
        self.q: "queue.Queue[str]" = queue.Queue()
        self.partial = ""
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self.q.put(line.rstrip("\r\n"))

    def attach(self, timeout: float = 0) -> None:
        pass

    def close(self) -> None:
        pass

    def poll(self) -> List[str]:
        out = []
        while True:
            try:
                out.append(self.q.get_nowait())
            except queue.Empty:
                return out

    def send_text(self, text: str) -> None:
        if self.proc.stdin:
            self.proc.stdin.write(text + "\n")
            self.proc.stdin.flush()

    def send_escape(self) -> None:
        self.send_text("\x1b")


# ----------------------------------------------------------------------------
# 6. Ejecutor del servidor (arranque, vigilancia, respawn)
# ----------------------------------------------------------------------------
class ServerRunner:
    """Hilo que lanza Dedicated.exe, lee su consola y reinicia si se cae.

    emit(kind, payload) se llama desde el hilo; la GUI lo pasa a una cola.
    kinds: 'line', 'state', 'status', 'exit', 'info'
    """

    def __init__(self, emit: Callable[[str, object], None]):
        self.emit = emit
        self.thread: Optional[threading.Thread] = None
        self.proc: Optional["subprocess.Popen"] = None
        self.bridge = None
        self.stop_flag = threading.Event()
        self.restart_flag = threading.Event()
        self.cmd_q: "queue.Queue[str]" = queue.Queue()
        self.running = False
        self.launch_fn: Optional[Callable[[], Tuple[List[str], str]]] = None
        self.respawn = True
        self.respawn_delay = 3.0
        self.show_console = False
        self.status: Dict[str, str] = {}
        self.started_at = 0.0

    # --- API publica (hilo de la GUI) ------------------------------------
    def start(self, launch_fn: Callable[[], Tuple[List[str], str]], respawn: bool, delay: float, show_console: bool) -> None:
        if self.running:
            return
        self.launch_fn, self.respawn, self.respawn_delay, self.show_console = launch_fn, respawn, delay, show_console
        self.stop_flag.clear()
        self.restart_flag.clear()
        self.thread = threading.Thread(target=self._main, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_flag.set()
        self._kill()

    def restart(self) -> None:
        self.restart_flag.set()
        self._kill()

    def send(self, cmd: str) -> bool:
        if not self.running or self.bridge is None:
            return False
        self.cmd_q.put(cmd)
        return True

    def send_escape(self) -> bool:
        if not self.running or self.bridge is None:
            return False
        self.cmd_q.put("\x1b")
        return True

    # --- interno -----------------------------------------------------------
    def _kill(self) -> None:
        p = self.proc
        if p and p.poll() is None:
            try:
                if IS_WIN:   # respawn.exe lanza Dedicated.exe como hijo: matar todo el arbol
                    subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True,
                                   creationflags=0x08000000)
                else:
                    p.terminate()
            except Exception:
                pass

            def _later(pp=p):
                time.sleep(3)
                if pp.poll() is None:
                    try:
                        pp.kill()
                    except Exception:
                        pass
            threading.Thread(target=_later, daemon=True).start()

    def _spawn(self, args: List[str], cwd: str, visible: bool):
        if IS_WIN:
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 5 if visible else 0  # SW_SHOW / SW_HIDE
            return subprocess.Popen(args, cwd=cwd, creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=si)
        return subprocess.Popen(args, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1)

    def _main(self) -> None:
        self.running = True
        quick_deaths = 0
        visible = self.show_console
        try:
            while not self.stop_flag.is_set():
                self.status = {}
                self.emit("state", "Preparando archivos...")
                try:
                    assert self.launch_fn is not None
                    args, cwd = self.launch_fn()
                except Exception as e:  # noqa: BLE001
                    self.emit("line", ("error", f"[manager] No se pudo preparar el servidor: {e}"))
                    break
                self.emit("line", ("info", "[manager] Ejecutando: " + subprocess.list2cmdline(args)))
                try:
                    self.proc = self._spawn(args, cwd, visible)
                except Exception as e:  # noqa: BLE001
                    self.emit("line", ("error", f"[manager] No se pudo lanzar Dedicated.exe: {e}"))
                    break
                self.started_at = time.time()
                self.emit("state", "Arrancando")
                self.emit("status", {"pid": str(self.proc.pid)})
                bridge = WinConsoleBridge(self.proc.pid) if IS_WIN else PipeBridge(self.proc)
                try:
                    bridge.attach()
                    self.bridge = bridge
                except BridgeError as e:
                    self.emit("line", ("error", f"[manager] No se pudo enlazar la consola ({e})."))
                    self.bridge = None
                    if IS_WIN and not visible:
                        self.emit("line", ("info", "[manager] Reintento con la ventana de consola visible (sin comandos desde el manager)."))
                        visible = True
                        try:
                            self.proc.terminate()
                        except Exception:
                            pass
                        continue
                    self.emit("info", "sin_consola")
                while self.proc.poll() is None:
                    self._pump(bridge)
                    time.sleep(0.25)
                self._pump(bridge, final=True)
                code = self.proc.returncode
                alive = time.time() - self.started_at
                if self.bridge is not None:
                    try:
                        bridge.close()
                    except Exception:
                        pass
                self.bridge = None
                self.emit("exit", code)
                if self.stop_flag.is_set():
                    break
                forced = self.restart_flag.is_set()
                self.restart_flag.clear()
                if not (self.respawn or forced):
                    break
                quick_deaths = quick_deaths + 1 if alive < 10 and not forced else 0
                if quick_deaths >= 5:
                    self.emit("line", ("error", "[manager] El servidor se cae al instante 5 veces seguidas: respawn detenido."))
                    break
                self.emit("state", f"Reiniciando en {int(self.respawn_delay)} s")
                t0 = time.time()
                while time.time() - t0 < self.respawn_delay and not self.stop_flag.is_set():
                    time.sleep(0.1)
        finally:
            self.running = False
            self.emit("state", "Detenido")

    def _pump(self, bridge, final: bool = False) -> None:
        try:
            while True:
                try:
                    c = self.cmd_q.get_nowait()
                except queue.Empty:
                    break
                if self.bridge is not None and not final:
                    if c == "\x1b":
                        bridge.send_escape()
                    else:
                        bridge.send_text(c)
            if self.bridge is not None or final:
                for line in bridge.poll():
                    self.emit("line", ("game", line))
                part = getattr(bridge, "partial", "")
                self.emit("status", {"activity": part})
        except BridgeError as e:
            # al detener/cerrarse el dedicado la consola desaparece: no es un error
            if not final and self.proc is not None and self.proc.poll() is None and not self.stop_flag.is_set():
                self.emit("line", ("error", f"[manager] Error de consola: {e}"))
        except Exception as e:  # noqa: BLE001
            self.emit("line", ("error", f"[manager] Error inesperado: {e}"))


# Interpretacion de lo que escribe el dedicado
_PATTERNS = [
    # textos reales de Main.dll / mp_Servidor.dll / vtserver.dll
    (re.compile(r"Servidor abierto en el puerto\s+(\d+)", re.I), "port", lambda m: m.group(1)),
    (re.compile(r"Servidor abierto en el puerto", re.I), "state", lambda m: "Servidor abierto - esperando jugadores"),
    (re.compile(r"Error cargando escena\s+(\S+?)\.?$", re.I), "state", lambda m: f"ERROR cargando mapa {m.group(1)}"),
    (re.compile(r"Inicializando caches", re.I), "state", lambda m: "Inicializando caches de datos"),
    (re.compile(r"Caches listos", re.I), "state", lambda m: "Caches listos"),
    (re.compile(r"Conectado al matchmaker", re.I), "mm", lambda m: "Conectado"),
    (re.compile(r"Actualizando la informacion del matchmaker", re.I), "mm", lambda m: "Conectado (actualizando)"),
    (re.compile(r"Reconectando con el matchmaker|Reintentando conectar con el matchmaker", re.I), "mm", lambda m: "Reconectando..."),
    (re.compile(r"No se (ha podido|pudo) conectar con el matchmaker|Matchmaker (connection|send) error|"
                r"Can't connect to validation server", re.I), "mm", lambda m: "ERROR: no conecta"),
    (re.compile(r"El matchmaker ha cerrado la sesion", re.I), "mm", lambda m: "Desconectado"),
    (re.compile(r"dedicated server has expired", re.I), "state", lambda m: "ERROR: version del dedicado caducada"),
    (re.compile(r"abnormal program termination", re.I), "state", lambda m: "El servidor se cerro (error)"),
    (re.compile(r"(?:escena|scene|mapa|map|carg\w*|load\w*)\W+(?:\S*?)(%[0-9A-Fa-f]{2})?(mp_[A-Za-z0-9_\-]+)", re.I),
     "map", lambda m: m.group(2)),
]
_ERR_RE = re.compile(r"error|excepci|no es posible|no se pudo|no se ha podido|abnormal|corrupt|can't connect|expired", re.I)


def interpret_line(text: str) -> Dict[str, str]:
    res: Dict[str, str] = {}
    for rx, key, fn in _PATTERNS:
        m = rx.search(text)
        if m:
            res[key] = fn(m)
    if res.get("state", "").startswith("ERROR"):
        res.pop("map", None)
    return res


# ----------------------------------------------------------------------------
# 7. Ajustes del manager
# ----------------------------------------------------------------------------
def default_settings() -> dict:
    return {
        "lang": "",                 # es / pt / en (vacio = idioma del sistema)
        "server_dir": APP_DIR,
        "data_dir": APP_DIR,        # carpeta que se pasa con -p (alli debe estar demo.vpk)
        "client_dir": "",           # carpeta del juego (cliente) para usar sus DLL
        "cfg_name": "server.cfg",
        "preset": "auto",
        "overrides": {},
        "extra_dlls": [],
        "use_vtkernel": "auto",
        "mm_host": "tol.trewq.com",
        "map_mode": "tolpro",       # 'tolpro' (%NN...) o 'torrente' (mp_...)
        "cfg_raw_names": False,     # escribir el nombre completo del vpk en SCENE_LOOP
        "tolpro_dir": "",
        "net_guid": "",             # adaptador elegido (hex del GUID)
        "net_apply": True,          # aplicar el adaptador al registro antes de iniciar
        "respawn": True,
        "respawn_delay": 3,
        "show_console": False,
        "use_p_arg": False,          # -p no es fiable: Dedicated.exe lee la ruta de datos del registro
        "use_respawn": True,         # lanzar con respawn.exe como el .bat original
        "sync_reg_path": True,       # ruta de datos del registro = carpeta del cliente (demo.vpk)
        "keep_vpk_case": True,
        "only_mp": True,
    }


def load_settings() -> dict:
    s = default_settings()
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            s.update(json.load(f))
    except (OSError, ValueError):
        pass
    return s


def save_settings(s: dict) -> None:
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=2, ensure_ascii=False)
    except OSError:
        pass


def build_launch(settings: dict, log: Callable[[str], None]) -> Tuple[List[str], str]:
    """Prepara las DLL y devuelve (argumentos, cwd) igual que el .bat original: sin -p, cwd = carpeta del servidor."""
    server_dir = settings["server_dir"]
    root = client_root(settings.get("client_dir", ""))
    if settings.get("sync_reg_path", True) and root and IS_WIN:
        try:
            if not same_path(read_data_path(), root):
                log("[datos] " + write_data_path(root))
        except Exception as e:  # noqa: BLE001
            log(f"[datos] AVISO: no pude fijar la ruta de datos del registro: {e}")
    changed = stage_release(settings, log)
    log(f"[dll] {len(changed)} DLL actualizadas en comun\\bin\\release" if changed else "[dll] DLL ya al dia")
    if not os.path.isfile(os.path.join(server_dir, settings.get("cfg_name", "server.cfg"))):
        raise FileNotFoundError(f"No existe {os.path.join(server_dir, settings.get('cfg_name', 'server.cfg'))}")
    resp = os.path.join(server_dir, "respawn.exe")
    if settings.get("use_respawn", True) and os.path.isfile(resp):
        args = [resp, "dedicated.exe"]
    else:
        args = [os.path.join(server_dir, "Dedicated.exe")]
    if settings.get("use_p_arg", False) and settings.get("client_dir"):
        args += ["-p", settings["client_dir"]]
    return args, server_dir


# ----------------------------------------------------------------------------
# 8. Interfaz grafica (tkinter)
# ----------------------------------------------------------------------------
# ----------------------------------------------------------------------------
# Idiomas (español / português / English). Los textos del programa estan en español; T() los traduce.
# ----------------------------------------------------------------------------
LANGS = OrderedDict([("es", "Español"), ("pt", "Português"), ("en", "English")])
CUR_LANG = "es"

NOTE_MM = ("La direccion esta escrita dentro de Main.dll (originalmente tol.trewq.com) y admite hasta 15 caracteres. "
           "'Parchear IP' la cambia en Main.dll del servidor (queda copia Main.dll.bak); solo se parchea al pulsar ese boton. "
           "Si el matchmaker no conecta, prueba 'Probar DNS' y 'Permitir en el Firewall'.")
NOTE_IP = ("Varias IP: Dedicated.exe no tiene opcion para elegir la IP de escucha en server.cfg. La red base se elige con el "
           "adaptador de la pestana 'Configuracion' (el mismo valor 'netGUID' del registro que usa el cliente de Torrente). "
           "Para aparecer con otra IP/puerto hay que levantar OTRA instancia (otra carpeta con su propio server.cfg y PORT "
           "distinto; el dedicado solo permite una instancia por carpeta).")

# clave (español): (English, Português).  '{}' = un valor variable (en el mismo orden en los 3 idiomas).
TR: Dict[str, Tuple[str, str]] = {
    # --- pestañas y generales
    "Servidor": ("Server", "Servidor"),
    "Mapas / Ciclo": ("Maps / Cycle", "Mapas / Ciclo"),
    "Configuracion (server.cfg)": ("Settings (server.cfg)", "Configuração (server.cfg)"),
    "Red / IP": ("Network / IP", "Rede / IP"),
    "Idioma:": ("Language:", "Idioma:"),
    # --- pestaña servidor
    "Iniciar servidor": ("Start server", "Iniciar servidor"),
    "Detener": ("Stop", "Parar"),
    "Reiniciar": ("Restart", "Reiniciar"),
    "Respawn automatico": ("Auto respawn", "Respawn automático"),
    "Mostrar consola de Dedicated": ("Show Dedicated console", "Mostrar console do Dedicated"),
    "Estado": ("Status", "Estado"),
    "Estado:": ("Status:", "Estado:"),
    "Puerto:": ("Port:", "Porta:"),
    "Mapa actual:": ("Current map:", "Mapa atual:"),
    "Matchmaker:": ("Matchmaker:", "Matchmaker:"),
    "PID:": ("PID:", "PID:"),
    "Mapa próximo:": ("Next map:", "Próximo mapa:"),
    "Tiempo activo:": ("Uptime:", "Tempo ativo:"),
    "Actividad:": ("Activity:", "Atividade:"),
    "Lo que hace el dedicado": ("What the dedicated server is doing", "O que o dedicado está fazendo"),
    "Comandos (se escriben en la consola del dedicado)": ("Commands (typed into the dedicated console)", "Comandos (digitados no console do dedicado)"),
    "ESC (cerrar partida)": ("ESC (close match)", "ESC (fechar partida)"),
    "Cabezones:": ("Big heads:", "Cabeções:"),
    "Kick ID jugador:": ("Kick player ID:", "Kick ID do jogador:"),
    "   Votemap:": ("   Votemap:", "   Votemap:"),
    "   Otro comando:": ("   Other command:", "   Outro comando:"),
    "Enviar": ("Send", "Enviar"),
    "Carpetas": ("Folders", "Pastas"),
    "Carpeta del servidor:": ("Server folder:", "Pasta do servidor:"),
    "Dedicated.exe y server.cfg": ("Dedicated.exe and server.cfg", "Dedicated.exe e server.cfg"),
    "Carpeta del cliente:": ("Client folder:", "Pasta do cliente:"),
    "raiz del juego (demo.vpk y DLL)": ("game root (demo.vpk and DLLs)", "raiz do jogo (demo.vpk e DLLs)"),
    "Re-escanear demo.vpk": ("Rescan demo.vpk", "Reescanear demo.vpk"),
    "Restaurar DLL originales": ("Restore original DLLs", "Restaurar DLLs originais"),
    # --- pestaña mapas
    "Mapas mp_* de demo.vpk (carpeta scn)": ("mp_* maps from demo.vpk (scn folder)", "Mapas mp_* do demo.vpk (pasta scn)"),
    "Ciclo de mapas (SCENE_LOOP ... END_SCENE_LOOP)": ("Map cycle (SCENE_LOOP ... END_SCENE_LOOP)", "Ciclo de mapas (SCENE_LOOP ... END_SCENE_LOOP)"),
    "Subir": ("Move up", "Subir"),
    "Bajar": ("Move down", "Descer"),
    "Quitar": ("Remove", "Remover"),
    "Duplicar": ("Duplicate", "Duplicar"),
    "Vaciar": ("Clear", "Limpar"),
    "Guardar ciclo": ("Save cycle", "Salvar ciclo"),
    "Mapas disponibles (.scn del demo.vpk)": ("Available maps (.scn in demo.vpk)", "Mapas disponíveis (.scn do demo.vpk)"),
    "Agregar al ciclo": ("Add to cycle", "Adicionar ao ciclo"),
    "Filtro:": ("Filter:", "Filtro:"),
    "Agregar seleccion": ("Add selected", "Adicionar selecionados"),
    "Agregar todos": ("Add all", "Adicionar todos"),
    "Agregar a mano:": ("Add manually:", "Adicionar manualmente:"),
    "Agregar nombre": ("Add name", "Adicionar nome"),
    "Vista previa": ("Preview", "Pré-visualização"),
    "(sin imagen)": ("(no image)", "(sem imagem)"),
    "{} mapas": ("{} maps", "{} mapas"),
    "   (no existe: se creara al guardar)": ("   (does not exist: it will be created on save)", "   (não existe: será criado ao salvar)"),
    "Leyendo demo.vpk...": ("Reading demo.vpk...", "Lendo demo.vpk..."),
    "No hay demo.vpk en: {}": ("No demo.vpk in: {}", "Não há demo.vpk em: {}"),
    " (no se hallaron .scn: lista por defecto del juego)": (" (no .scn found: game default list)", " (nenhum .scn encontrado: lista padrão do jogo)"),
    "{}  |  {} mapas mp_* ({} visibles){}": ("{}  |  {} mp_* maps ({} visible){}", "{}  |  {} mapas mp_* ({} visíveis){}"),
    "  |  No se hallaron escenas: puedes agregar nombres a mano.": ("  |  No scenes found: you can add names manually.", "  |  Nenhuma cena encontrada: você pode adicionar nomes manualmente."),
    "Sin indice del demo.vpk: no se puede buscar la textura.": ("No demo.vpk index: cannot look up the texture.", "Sem índice do demo.vpk: não é possível buscar a textura."),
    "No hay texs\\{}-tbn.tex en el demo.vpk.": ("There is no texs\\{}-tbn.tex in demo.vpk.", "Não há texs\\{}-tbn.tex no demo.vpk."),
    "Decodificando...": ("Decoding...", "Decodificando..."),
    "{} vistas previas cargadas": ("{} previews loaded", "{} pré-visualizações carregadas"),
    "{}  No se pudo mostrar: {}": ("{}  Could not display: {}", "{}  Não foi possível exibir: {}"),
    "Vaciar todo el ciclo? (vacio = el juego usa su ciclo por defecto)": ("Clear the whole cycle? (empty = the game uses its default cycle)", "Limpar todo o ciclo? (vazio = o jogo usa seu ciclo padrão)"),
    "Ciclo": ("Cycle", "Ciclo"),
    # --- pestaña configuracion
    "Red base del servidor (adaptador que usa el juego)": ("Server base network (adapter used by the game)", "Rede base do servidor (adaptador usado pelo jogo)"),
    "Actualizar lista": ("Refresh list", "Atualizar lista"),
    "Aplicar": ("Apply", "Aplicar"),
    "Aplicar al iniciar el servidor": ("Apply when starting the server", "Aplicar ao iniciar o servidor"),
    "Otros parametros de server.cfg\n(clave: valor, uno por linea)": ("Other server.cfg parameters\n(key: value, one per line)", "Outros parâmetros do server.cfg\n(chave: valor, um por linha)"),
    "Guardar server.cfg": ("Save server.cfg", "Salvar server.cfg"),
    "Exportar JSON...": ("Export JSON...", "Exportar JSON..."),
    "Importar JSON...": ("Import JSON...", "Importar JSON..."),
    "Abrir carpeta": ("Open folder", "Abrir pasta"),
    "Puerto donde se hospeda la partida (abrelo en el firewall/router).": ("Port where the match is hosted (open it in the firewall/router).", "Porta onde a partida é hospedada (abra-a no firewall/roteador)."),
    "Nombre de la partida que ven los jugadores en la lista.": ("Match name players see in the list.", "Nome da partida que os jogadores veem na lista."),
    "Nick de quien crea la partida.": ("Nickname of the match creator.", "Apelido de quem cria a partida."),
    "Contrasena (maximo 8 caracteres). Vacio = sin contrasena.": ("Password (max. 8 characters). Empty = no password.", "Senha (máximo 8 caracteres). Vazio = sem senha."),
    "Numero maximo de jugadores (1 a 32).": ("Maximum number of players (1 to 32).", "Número máximo de jogadores (1 a 32)."),
    "Minutos que dura cada mapa (-1 = sin limite).": ("Minutes each map lasts (-1 = no limit).", "Minutos que cada mapa dura (-1 = sem limite)."),
    "Puntos maximos en 'destruir objetivo' (-1 = sin limite).": ("Max. points in 'destroy the target' (-1 = no limit).", "Pontos máximos em 'destruir objetivo' (-1 = sem limite)."),
    "Puntos maximos en deathmatch.": ("Max. points in deathmatch.", "Pontos máximos em deathmatch."),
    "Puntos maximos en deathmatch por equipos.": ("Max. points in team deathmatch.", "Pontos máximos em deathmatch por equipes."),
    "Puntos maximos en capturar la bandera (-1 = sin limite).": ("Max. points in capture the flag (-1 = no limit).", "Pontos máximos em capturar a bandeira (-1 = sem limite)."),
    "Fuego aliado: 1 = los de un mismo equipo se danan.": ("Friendly fire: 1 = teammates damage each other.", "Fogo amigo: 1 = companheiros de equipe se ferem."),
    "Tipo de red de la partida.": ("Match network type.", "Tipo de rede da partida."),
    "1 - Internet": ("1 - Internet", "1 - Internet"),
    "Modo cabezon: la cabeza crece con cada muerte.": ("Big-head mode: the head grows with every death.", "Modo cabeção: a cabeça cresce a cada morte."),
    "Permite la camara en tercera persona.": ("Allows the third-person camera.", "Permite a câmera em terceira pessoa."),
    "Latencia maxima (ms) para entrar a la partida.": ("Maximum latency (ms) to join the match.", "Latência máxima (ms) para entrar na partida."),
    "Velocidad de juego en % (25 a 400).": ("Game speed in % (25 to 400).", "Velocidade do jogo em % (25 a 400)."),
    "Velocidad de los jugadores en % (25 a 400).": ("Player speed in % (25 to 400).", "Velocidade dos jogadores em % (25 a 400)."),
    "{}: '{}' no es un numero entero.": ("{}: '{}' is not an integer.", "{}: '{}' não é um número inteiro."),
    "{}: debe estar entre {} y {}.": ("{}: must be between {} and {}.", "{}: deve estar entre {} e {}."),
    "PASSWORD: maximo 8 caracteres.": ("PASSWORD: maximum 8 characters.", "PASSWORD: máximo 8 caracteres."),
    "No se pudo guardar: {}": ("Could not save: {}", "Não foi possível salvar: {}"),
    "server.cfg guardado ({})": ("server.cfg saved ({})", "server.cfg salvo ({})"),
    "JSON cargado: pulsa 'Guardar server.cfg' para aplicarlo.": ("JSON loaded: press 'Save server.cfg' to apply it.", "JSON carregado: clique em 'Salvar server.cfg' para aplicá-lo."),
    "Leyendo adaptadores...": ("Reading adapters...", "Lendo adaptadores..."),
    "El registro aun no tiene adaptador elegido: el juego usa el primero de la lista.": ("The registry has no adapter selected yet: the game uses the first in the list.", "O registro ainda não tem adaptador escolhido: o jogo usa o primeiro da lista."),
    "El adaptador del registro no coincide con ninguno de la lista.": ("The registry adapter does not match any in the list.", "O adaptador do registro não corresponde a nenhum da lista."),
    "El adaptador elegido se guarda como 'netGUID' en el registro (el mismo valor que usa el cliente de Torrente).": ("The chosen adapter is stored as 'netGUID' in the registry (the same value the Torrente client uses).", "O adaptador escolhido é salvo como 'netGUID' no registro (o mesmo valor que o cliente do Torrente usa)."),
    "Elige un adaptador de la lista.": ("Choose an adapter from the list.", "Escolha um adaptador da lista."),
    "Esta lista es solo informativa (no se pudo leer DirectPlay): no puedo guardar el adaptador.": ("This list is informational only (DirectPlay could not be read): I cannot save the adapter.", "Esta lista é apenas informativa (não foi possível ler o DirectPlay): não posso salvar o adaptador."),
    "Adaptador": ("Adapter", "Adaptador"),
    "No se pudo guardar el adaptador: {}": ("Could not save the adapter: {}", "Não foi possível salvar o adaptador: {}"),
    # --- pestaña red
    "Matchmaker (donde se anuncia el servidor)": ("Matchmaker (where the server is announced)", "Matchmaker (onde o servidor é anunciado)"),
    "Direccion:": ("Address:", "Endereço:"),
    "Parchear IP": ("Patch IP", "Aplicar patch no IP"),
    "Probar DNS": ("Test DNS", "Testar DNS"),
    "Permitir en el Firewall": ("Allow in Firewall", "Permitir no Firewall"),
    NOTE_MM: ("The address is stored inside Main.dll (originally tol.trewq.com) and allows up to 15 characters. 'Patch IP' changes it in the server's Main.dll (a Main.dll.bak copy is kept); it is only patched when you press that button. If the matchmaker does not connect, try 'Test DNS' and 'Allow in Firewall'.",
              "O endereço está dentro do Main.dll (originalmente tol.trewq.com) e admite até 15 caracteres. 'Aplicar patch no IP' o altera no Main.dll do servidor (fica uma cópia Main.dll.bak); só é alterado ao clicar nesse botão. Se o matchmaker não conectar, teste 'Testar DNS' e 'Permitir no Firewall'."),
    "IP de esta maquina": ("This machine's IP", "IP desta máquina"),
    "Actualizar IP locales": ("Refresh local IPs", "Atualizar IPs locais"),
    "Ver IP publica": ("Show public IP", "Ver IP público"),
    NOTE_IP: ("Several IPs: Dedicated.exe has no option to choose the listening IP in server.cfg. The base network is chosen with the adapter in the 'Settings' tab (the same 'netGUID' registry value the Torrente client uses). To appear with another IP/port you must start ANOTHER instance (another folder with its own server.cfg and a different PORT; the dedicated server only allows one instance per folder).",
              "Vários IPs: o Dedicated.exe não tem opção para escolher o IP de escuta no server.cfg. A rede base é escolhida com o adaptador da aba 'Configuração' (o mesmo valor 'netGUID' do registro que o cliente do Torrente usa). Para aparecer com outro IP/porta é preciso iniciar OUTRA instância (outra pasta com seu próprio server.cfg e PORT diferente; o dedicado só permite uma instância por pasta)."),
    "Puerto UDP/TCP a abrir: {}": ("UDP/TCP port to open: {}", "Porta UDP/TCP a abrir: {}"),
    "IP publica: {}": ("Public IP: {}", "IP público: {}"),
    "No se pudo obtener": ("Could not be obtained", "Não foi possível obter"),
    "consultando...": ("querying...", "consultando..."),
    "Main.dll del servidor: no encontrada": ("Server Main.dll: not found", "Main.dll do servidor: não encontrada"),
    "Main.dll: {}": ("Main.dll: {}", "Main.dll: {}"),
    "IP/host actual dentro de Main.dll del servidor: {}  (maximo {} caracteres)": ("Current IP/host inside the server's Main.dll: {}  (max. {} characters)", "IP/host atual dentro do Main.dll do servidor: {}  (máximo {} caracteres)"),
    "Main.dll del servidor: no se reconoce la direccion del matchmaker": ("Server Main.dll: matchmaker address not recognised", "Main.dll do servidor: endereço do matchmaker não reconhecido"),
    "No encuentro comun\\bin\\release\\Main.dll en la carpeta del servidor.": ("I cannot find comun\\bin\\release\\Main.dll in the server folder.", "Não encontro comun\\bin\\release\\Main.dll na pasta do servidor."),
    " (si apareció el aviso de Windows, acéptalo)": (" (if the Windows prompt appeared, accept it)", " (se apareceu o aviso do Windows, aceite)"),
    "Regla de firewall creada para {}": ("Firewall rule created for {}", "Regra de firewall criada para {}"),
    "Regla de firewall creada para {} (si apareció el aviso de Windows, acéptalo)": ("Firewall rule created for {} (if the Windows prompt appeared, accept it)", "Regra de firewall criada para {} (se apareceu o aviso do Windows, aceite)"),
    "Hace falta ejecutar como administrador (UAC cancelado).": ("Administrator rights are required (UAC cancelled).", "É necessário executar como administrador (UAC cancelado)."),
    "Adaptador guardado en el registro.": ("Adapter saved to the registry.", "Adaptador salvo no registro."),
    "Adaptador guardado (con permiso de administrador).": ("Adapter saved (with administrator permission).", "Adaptador salvo (com permissão de administrador)."),
    "Adaptador guardado en el registro. (tambien en la copia virtual del juego)": ("Adapter saved to the registry. (also in the game's virtual copy)", "Adaptador salvo no registro. (também na cópia virtual do jogo)"),
    "Adaptador guardado en la copia virtual del juego (sin administrador).": ("Adapter saved in the game's virtual copy (no administrator).", "Adaptador salvo na cópia virtual do jogo (sem administrador)."),
    "No pude confirmar el cambio en el registro.": ("I could not confirm the registry change.", "Não consegui confirmar a alteração no registro."),
    "No se resuelve '{}': {}": ("'{}' cannot be resolved: {}", "'{}' não pode ser resolvido: {}"),
    "{} (IP directa)": ("{} (direct IP)", "{} (IP direto)"),
    "DirectPlay solo existe en Windows": ("DirectPlay only exists on Windows", "O DirectPlay só existe no Windows"),
    "No se pudo crear DirectPlay8 (0x{}). Activa 'DirectPlay' en Caracteristicas de Windows.": ("DirectPlay8 could not be created (0x{}). Enable 'DirectPlay' in Windows Features.", "Não foi possível criar o DirectPlay8 (0x{}). Ative o 'DirectPlay' nos Recursos do Windows."),
    "DirectPlay Initialize fallo (0x{})": ("DirectPlay Initialize failed (0x{})", "DirectPlay Initialize falhou (0x{})"),
    "EnumServiceProviders devolvio 0x{}": ("EnumServiceProviders returned 0x{}", "EnumServiceProviders retornou 0x{}"),
    "EnumServiceProviders fallo (0x{})": ("EnumServiceProviders failed (0x{})", "EnumServiceProviders falhou (0x{})"),
    "No se pudo leer DirectPlay ({}); lista solo informativa.": ("DirectPlay could not be read ({}); informational list only.", "Não foi possível ler o DirectPlay ({}); lista apenas informativa."),
    "Solo informativo (no es Windows).": ("Informational only (not Windows).", "Apenas informativo (não é Windows)."),
    "Solo en Windows": ("Windows only", "Somente no Windows"),
    # --- servidor: estados, logs, avisos
    "Detenido": ("Stopped", "Parado"),
    "Arrancando": ("Starting", "Iniciando"),
    "Preparando archivos...": ("Preparing files...", "Preparando arquivos..."),
    "Reiniciando en {} s": ("Restarting in {} s", "Reiniciando em {} s"),
    "Servidor abierto - esperando jugadores": ("Server open - waiting for players", "Servidor aberto - aguardando jogadores"),
    "ERROR cargando mapa {}": ("ERROR loading map {}", "ERRO ao carregar o mapa {}"),
    "Inicializando caches de datos": ("Initialising data caches", "Inicializando caches de dados"),
    "Caches listos": ("Caches ready", "Caches prontos"),
    "Conectado": ("Connected", "Conectado"),
    "Conectado (actualizando)": ("Connected (updating)", "Conectado (atualizando)"),
    "Reconectando...": ("Reconnecting...", "Reconectando..."),
    "ERROR: no conecta": ("ERROR: cannot connect", "ERRO: não conecta"),
    "Desconectado": ("Disconnected", "Desconectado"),
    "ERROR: version del dedicado caducada": ("ERROR: dedicated server version expired", "ERRO: versão do dedicado expirada"),
    "El servidor se cerro (error)": ("The server closed (error)", "O servidor fechou (erro)"),
    "Cargando mapa {}": ("Loading map {}", "Carregando mapa {}"),
    "Mapa cargado ({})": ("Map loaded ({})", "Mapa carregado ({})"),
    "[manager] No se pudo preparar el servidor: {}": ("[manager] Could not prepare the server: {}", "[manager] Não foi possível preparar o servidor: {}"),
    "[manager] Ejecutando: ": ("[manager] Running: ", "[manager] Executando: "),
    "[manager] No se pudo lanzar Dedicated.exe: {}": ("[manager] Could not launch Dedicated.exe: {}", "[manager] Não foi possível iniciar o Dedicated.exe: {}"),
    "[manager] No se pudo enlazar la consola ({}).": ("[manager] Could not attach to the console ({}).", "[manager] Não foi possível vincular o console ({})."),
    "[manager] Reintento con la ventana de consola visible (sin comandos desde el manager).": ("[manager] Retrying with the console window visible (no commands from the manager).", "[manager] Nova tentativa com a janela do console visível (sem comandos pelo manager)."),
    "[manager] El servidor se cae al instante 5 veces seguidas: respawn detenido.": ("[manager] The server crashes instantly 5 times in a row: respawn stopped.", "[manager] O servidor cai imediatamente 5 vezes seguidas: respawn interrompido."),
    "[manager] Error de consola: {}": ("[manager] Console error: {}", "[manager] Erro de console: {}"),
    "[manager] Error inesperado: {}": ("[manager] Unexpected error: {}", "[manager] Erro inesperado: {}"),
    "[manager] --- inicio {} ---": ("[manager] --- start {} ---", "[manager] --- início {} ---"),
    "[manager] Deteniendo servidor...": ("[manager] Stopping server...", "[manager] Parando o servidor..."),
    "[manager] Reiniciando servidor...": ("[manager] Restarting server...", "[manager] Reiniciando o servidor..."),
    "[manager] Servidor detenido.": ("[manager] Server stopped.", "[manager] Servidor parado."),
    "[manager] Dedicated.exe termino (codigo {}).": ("[manager] Dedicated.exe exited (code {}).", "[manager] Dedicated.exe terminou (código {})."),
    "Consola no enlazada: los comandos del manager no estan disponibles.": ("Console not attached: manager commands are not available.", "Console não vinculado: os comandos do manager não estão disponíveis."),
    "[datos] AVISO: no pude fijar la ruta de datos del registro: {}": ("[data] WARNING: I could not set the data path in the registry: {}", "[dados] AVISO: não consegui definir o caminho de dados no registro: {}"),
    "[dll] {} DLL actualizadas en comun\\bin\\release": ("[dll] {} DLLs updated in comun\\bin\\release", "[dll] {} DLLs atualizadas em comun\\bin\\release"),
    "[dll] DLL ya al dia": ("[dll] DLLs already up to date", "[dll] DLLs já atualizadas"),
    "[dll] copia de seguridad: {}": ("[dll] backup: {}", "[dll] backup: {}"),
    "[dll] DLL extra no encontrada: {}": ("[dll] Extra DLL not found: {}", "[dll] DLL extra não encontrada: {}"),
    "[dll] restaurada {}": ("[dll] restored {}", "[dll] restaurada {}"),
    "[dll] no pude restaurar {}: {}": ("[dll] could not restore {}: {}", "[dll] não consegui restaurar {}: {}"),
    "[dll] quitada {} (agregada por el manager)": ("[dll] removed {} (added by the manager)", "[dll] removida {} (adicionada pelo manager)"),
    "Ruta de datos del registro = {}": ("Registry data path = {}", "Caminho de dados do registro = {}"),
    "Ruta de datos del registro = {} (con permiso de administrador)": ("Registry data path = {} (with administrator permission)", "Caminho de dados do registro = {} (com permissão de administrador)"),
    "No existe {}": ("{} does not exist", "{} não existe"),
    "No encuentro Dedicated.exe en {}": ("I cannot find Dedicated.exe in {}", "Não encontro o Dedicated.exe em {}"),
    "Falta vtserver.dll en la carpeta del servidor.": ("vtserver.dll is missing from the server folder.", "Falta o vtserver.dll na pasta do servidor."),
    "Se pidio usar vtKernel.dll como vtserver.dll pero no encuentro vtKernel.dll del cliente (indica la carpeta del cliente, o pon 'vtKernel como vtserver' en 'no').": ("Using vtKernel.dll as vtserver.dll was requested but I cannot find the client's vtKernel.dll (set the client folder, or set 'vtKernel as vtserver' to 'no').", "Foi pedido usar o vtKernel.dll como vtserver.dll, mas não encontro o vtKernel.dll do cliente (indique a pasta do cliente ou defina 'vtKernel como vtserver' como 'não')."),
    "El servidor ya esta en marcha.": ("The server is already running.", "O servidor já está em execução."),
    "No encuentro {}.\nIniciar igualmente?": ("I cannot find {}.\nStart anyway?", "Não encontro {}.\nIniciar mesmo assim?"),
    "No hay consola enlazada: inicia el servidor (y deja 'Mostrar consola' desactivado si falla el enlace).": ("No console attached: start the server (and leave 'Show console' off if attaching fails).", "Nenhum console vinculado: inicie o servidor (e deixe 'Mostrar console' desativado se a vinculação falhar)."),
    "Servidor no iniciado.": ("Server not started.", "Servidor não iniciado."),
    "Escribe el ID numerico del jugador (usa PLAYER para verlo).": ("Type the player's numeric ID (use PLAYER to see it).", "Digite o ID numérico do jogador (use PLAYER para vê-lo)."),
    "{}: escribe un valor entre 25 y 400.": ("{}: enter a value between 25 and 400.", "{}: digite um valor entre 25 e 400."),
    "Detiene el servidor primero.": ("Stop the server first.", "Pare o servidor primeiro."),
    "Devolver las DLL originales del servidor?": ("Put back the server's original DLLs?", "Devolver as DLLs originais do servidor?"),
    "{} archivos restaurados/quitados": ("{} files restored/removed", "{} arquivos restaurados/removidos"),
    "Restaurar": ("Restore", "Restaurar"),
    "Salir": ("Exit", "Sair"),
    "El servidor sigue en marcha. Detenerlo y salir?": ("The server is still running. Stop it and exit?", "O servidor continua em execução. Pará-lo e sair?"),
    # --- vpk / dll (mensajes que pueden llegar al usuario)
    "{}: no es un vtPack (empieza por {})": ("{}: not a vtPack (starts with {})", "{}: não é um vtPack (começa com {})"),
    "vtPack: offset de tabla invalido ({})": ("vtPack: invalid table offset ({})", "vtPack: offset de tabela inválido ({})"),
    "vtPack: tabla truncada ({} registros leidos)": ("vtPack: truncated table ({} records read)", "vtPack: tabela truncada ({} registros lidos)"),
    "vtPack: {} archivos, {} carpetas": ("vtPack: {} files, {} folders", "vtPack: {} arquivos, {} pastas"),
    "no existe ": ("does not exist: ", "não existe: "),
    " | plan B: busqueda de texto": (" | plan B: text search", " | plano B: busca de texto"),
    "No encuentro la direccion del matchmaker en esa DLL (no parece la Main.dll del servidor).": ("I cannot find the matchmaker address in that DLL (it does not look like the server's Main.dll).", "Não encontro o endereço do matchmaker nessa DLL (não parece ser o Main.dll do servidor)."),
    "Direccion invalida.": ("Invalid address.", "Endereço inválido."),
    "'{}' mide {} caracteres; el maximo en Main.dll es {}. Usa una IP o un nombre mas corto, o una linea en el archivo hosts de Windows.": ("'{}' is {} characters long; the maximum in Main.dll is {}. Use a shorter IP or name, or a line in the Windows hosts file.", "'{}' tem {} caracteres; o máximo no Main.dll é {}. Use um IP ou nome mais curto, ou uma linha no arquivo hosts do Windows."),
    "Main.dll ya apuntaba a {}": ("Main.dll already pointed to {}", "O Main.dll já apontava para {}"),
    "Main.dll: {} -> {}": ("Main.dll: {} -> {}", "Main.dll: {} -> {}"),
    "no es un TeX2 (cabecera %s)": ("not a TeX2 (header %s)", "não é um TeX2 (cabeçalho %s)"),
    "formato %d no soportado": ("format %d not supported", "formato %d não suportado"),
    "tamano invalido %dx%d": ("invalid size %dx%d", "tamanho inválido %dx%d"),
    "no es un ejecutable PE": ("not a PE executable", "não é um executável PE"),
    "firma PE invalida": ("invalid PE signature", "assinatura PE inválida"),
    "solo se admiten PE de 32 bits": ("only 32-bit PE files are supported", "somente PE de 32 bits são suportados"),
    "AttachConsole fallo (error {})": ("AttachConsole failed (error {})", "AttachConsole falhou (erro {})"),
    "no se pudo abrir CONIN$/CONOUT$": ("could not open CONIN$/CONOUT$", "não foi possível abrir CONIN$/CONOUT$"),
    "GetConsoleScreenBufferInfo fallo": ("GetConsoleScreenBufferInfo failed", "GetConsoleScreenBufferInfo falhou"),
    "ReadConsoleOutputCharacterW fallo": ("ReadConsoleOutputCharacterW failed", "ReadConsoleOutputCharacterW falhou"),
    "WriteConsoleInputW fallo": ("WriteConsoleInputW failed", "WriteConsoleInputW falhou"),
}

_TR_TEMPLATES: List[Tuple["re.Pattern", Tuple[str, str]]] = []
for _k, _v in sorted(TR.items(), key=lambda kv: -len(kv[0])):   # las plantillas mas especificas primero
    if "{}" in _k:
        _TR_TEMPLATES.append((re.compile("^" + "(.*?)".join(re.escape(p) for p in _k.split("{}")) + "$", re.S), _v))


def T(text: str) -> str:
    """Traduce un texto del programa (escrito en español) al idioma actual."""
    if CUR_LANG == "es" or not text or not isinstance(text, str):
        return text
    idx = 1 if CUR_LANG == "en" else 2
    v = TR.get(text)
    if v:
        return v[idx - 1]
    for rx, tv in _TR_TEMPLATES:
        m = rx.match(text)
        if m:
            parts = [T(g) for g in m.groups()]
            out = tv[idx - 1]
            for g in parts:
                out = out.replace("{}", g, 1)
            return out
    # lineas compuestas "[dll] x; y": no se traducen
    return text


def set_lang(code: str) -> None:
    global CUR_LANG
    CUR_LANG = code if code in LANGS else "es"


def default_lang() -> str:
    try:
        import locale
        loc = (locale.getdefaultlocale()[0] or "").lower()
    except Exception:  # noqa: BLE001
        loc = ""
    return "pt" if loc.startswith("pt") else "en" if loc and not loc.startswith("es") else "es"


def set_window_icon(win) -> None:
    """Icono de la ventana: icon.ico/icono.ico (junto al .exe o empaquetado) o, si es un .exe compilado, el icono del propio .exe."""
    for d in (APP_DIR, RES_DIR):
        for n in ("icon.ico", "icono.ico", "app.ico"):
            path = os.path.join(d, n)
            if os.path.isfile(path):
                try:
                    win.iconbitmap(path)
                    return
                except Exception:  # noqa: BLE001
                    pass
    if FROZEN and IS_WIN:
        try:
            large, small = ctypes.c_void_p(), ctypes.c_void_p()
            if ctypes.windll.shell32.ExtractIconExW(sys.executable, 0, ctypes.byref(large), ctypes.byref(small), 1):
                win.update_idletasks()
                hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
                if large.value:
                    ctypes.windll.user32.SendMessageW(hwnd, 0x80, 1, large.value)
                if small.value:
                    ctypes.windll.user32.SendMessageW(hwnd, 0x80, 0, small.value)
                win._icon_refs = (large, small)
        except Exception:  # noqa: BLE001
            pass


def run_gui() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    from tkinter.scrolledtext import ScrolledText

    detach_own_console()
    ev_q: "queue.Queue[Tuple[str, object]]" = queue.Queue()
    _tvars: List["TVar"] = []

    class TVar(tk.StringVar):
        """StringVar cuyo valor se guarda en español (raw) y se muestra traducido al idioma actual."""

        def __init__(self, value: str = "") -> None:
            super().__init__()
            self.raw = ""
            _tvars.append(self)
            self.set(value)

        def set(self, value) -> None:  # noqa: A003
            self.raw = str(value)
            super().set(T(self.raw))

        def get(self) -> str:
            return self.raw

        def retranslate(self) -> None:
            super().set(T(self.raw))

    for _n in ("showinfo", "showerror", "showwarning", "askyesno"):
        def _wrap(fn=getattr(messagebox, _n)):
            return lambda title=None, message=None, **kw: fn(T(title), T(message), **kw)
        setattr(messagebox, _n, _wrap())

    class App(tk.Tk):
        def __init__(self) -> None:
            super().__init__()
            self.s_lang_boot = True
            self.title(APP_NAME)
            self.geometry("1050x760")
            self.minsize(900, 640)
            self.s = load_settings()
            set_lang(self.s.get("lang") or default_lang())
            self.s["lang"] = CUR_LANG
            self.cfg = ServerCfg(os.path.join(self.s["server_dir"], self.s["cfg_name"]))
            self.runner = ServerRunner(lambda k, p: ev_q.put((k, p)))
            self.catalog = VpkCatalog()
            self.raw_all: Dict[str, str] = {}
            self.loop_orig: Dict[str, str] = {}
            self.adapters: List[dict] = []
            self.run_loop: List[str] = []
            self.loop_idx: Optional[int] = None
            self.tex_img = None
            self.tex_cache: Dict[str, tuple] = {}
            self.tex_gen = 0
            self.param_vars: Dict[str, tk.StringVar] = {}
            self.status_vars = {k: TVar("-") for k in ("estado", "puerto", "mapa", "proximo", "matchmaker", "pid", "actividad", "uptime")}
            self.status_vars["estado"].set("Detenido")
            self.plan: "OrderedDict[str, dict]" = OrderedDict()
            self._build()
            self._i18n_scan()
            self._i18n_apply()
            self.reload_cfg()
            self.rescan_vpk()
            self.refresh_ips()
            self.refresh_adapters()
            self.update_mm_current()
            # ajustar la ventana al contenido (si no, se corta a la derecha/abajo)
            self.update_idletasks()
            w = min(max(1050, self.winfo_reqwidth() + 16), self.winfo_screenwidth() - 40)
            h = min(max(760, self.winfo_reqheight() + 16), self.winfo_screenheight() - 80)
            self.geometry(f"{w}x{h}")
            set_window_icon(self)
            self.minsize(min(w, 900), min(h, 640))
            self.protocol("WM_DELETE_WINDOW", self.on_close)
            self.after(120, self.pump)
            self.after(1000, self.tick)

        # ---------------- construccion -----------------------------------
        def _build(self) -> None:
            style = ttk.Style(self)
            try:
                style.theme_use("clam")
            except tk.TclError:
                pass
            self.nb = ttk.Notebook(self)
            self.nb.pack(fill="both", expand=True, padx=6, pady=6)
            ver = ttk.Frame(self.nb)
            ttk.Label(ver, text="Idioma:").pack(side="left", padx=(0, 4))
            self.v_lang = tk.StringVar(value=LANGS[CUR_LANG])
            cb_lang = ttk.Combobox(ver, textvariable=self.v_lang, state="readonly", width=10, values=list(LANGS.values()))
            cb_lang.pack(side="left", padx=(0, 14))
            cb_lang.bind("<<ComboboxSelected>>", lambda _e: self.on_lang())
            lnk = tk.Label(ver, text=APP_URL, fg="#0b57d0", cursor="hand2", font=("TkDefaultFont", 9, "underline"),
                           bg=ttk.Style().lookup("TFrame", "background") or self.cget("bg"))
            lnk.pack(side="left")
            lnk.bind("<Button-1>", lambda _e: webbrowser.open(APP_URL))
            ver.place(relx=1.0, x=-8, y=2, anchor="ne")   # arriba a la derecha, a la altura de las pestanas
            self.tab_srv = ttk.Frame(self.nb)
            self.tab_maps = ttk.Frame(self.nb)
            self.tab_cfg = ttk.Frame(self.nb)
            self.tab_net = ttk.Frame(self.nb)
            for t, name in ((self.tab_srv, "Servidor"), (self.tab_maps, "Mapas / Ciclo"),
                            (self.tab_cfg, "Configuracion (server.cfg)"), (self.tab_net, "Red / IP")):
                self.nb.add(t, text=name)
            self._build_server()
            self._build_maps()
            self._build_cfg()
            self._build_net()
            self.bar = TVar("")
            ttk.Label(self, textvariable=self.bar, anchor="w").pack(fill="x", padx=8, pady=(0, 4))

        def _build_server(self) -> None:
            f = self.tab_srv
            self._build_folders(f)
            top = ttk.Frame(f)
            top.pack(fill="x", padx=8, pady=8)
            self.btn_start = ttk.Button(top, text="Iniciar servidor", command=self.on_start)
            self.btn_start.pack(side="left")
            ttk.Button(top, text="Detener", command=self.on_stop).pack(side="left", padx=4)
            ttk.Button(top, text="Reiniciar", command=self.on_restart).pack(side="left")
            self.v_respawn = tk.BooleanVar(value=self.s["respawn"])
            ttk.Checkbutton(top, text="Respawn automatico", variable=self.v_respawn).pack(side="left", padx=12)
            self.v_show = tk.BooleanVar(value=self.s["show_console"])
            ttk.Checkbutton(top, text="Mostrar consola de Dedicated", variable=self.v_show).pack(side="left")

            st = ttk.LabelFrame(f, text="Estado")
            st.pack(fill="x", padx=8)
            fnt = ("TkDefaultFont", 9, "bold")
            ttk.Label(st, text="Estado:").grid(row=0, column=0, sticky="e", padx=(8, 2), pady=2)
            ttk.Label(st, textvariable=self.status_vars["estado"], font=fnt, anchor="w").grid(
                row=0, column=1, columnspan=5, sticky="w")      # sin ancho fijo: los nombres largos de mapa no se cortan
            items = [("Puerto", "puerto"), ("Mapa actual", "mapa"), ("Matchmaker", "matchmaker"),
                     ("PID", "pid"), ("Mapa próximo", "proximo"), ("Tiempo activo", "uptime")]
            for i, (lab, key) in enumerate(items):
                r, c = divmod(i, 3)
                ttk.Label(st, text=lab + ":").grid(row=r + 1, column=c * 2, sticky="e", padx=(8, 2), pady=2)
                ttk.Label(st, textvariable=self.status_vars[key], font=fnt, width=30, anchor="w").grid(
                    row=r + 1, column=c * 2 + 1, sticky="w")
            ttk.Label(st, text="Actividad:").grid(row=3, column=0, sticky="e", padx=(8, 2), pady=2)
            ttk.Label(st, textvariable=self.status_vars["actividad"], font=fnt, anchor="w").grid(
                row=3, column=1, columnspan=5, sticky="w")

            logf = ttk.LabelFrame(f, text="Lo que hace el dedicado")
            logf.pack(fill="both", expand=True, padx=8, pady=6)
            self.log = ScrolledText(logf, height=6, state="disabled", font=("Consolas", 9), wrap="word")
            self.log.pack(fill="both", expand=True)
            self.log.tag_config("game", foreground="#222222")
            self.log.tag_config("info", foreground="#1060b0")
            self.log.tag_config("error", foreground="#c01818")
            self.log.tag_config("map", foreground="#107a2a")
            self.log.tag_config("cmd", foreground="#8a4b00")

            cmd = ttk.LabelFrame(f, text="Comandos (se escriben en la consola del dedicado)")
            cmd.pack(fill="x", padx=8, pady=(0, 6))
            row = ttk.Frame(cmd)
            row.pack(fill="x", padx=6, pady=4)
            for text, c in (("TIMELEFT", "timeleft"), ("CONTINUE", "continue"), ("NEXTMAP", "nextmap"),
                            ("PLAYER", "player"), ("MAP", "map")):
                ttk.Button(row, text=text, command=lambda c=c: self.send_cmd(c)).pack(side="left", padx=2)
            ttk.Button(row, text="ESC (cerrar partida)", command=self.send_esc).pack(side="left", padx=10)
            ttk.Label(row, text="Cabezones:").pack(side="left", padx=(12, 2))
            ttk.Button(row, text="ON", width=4, command=lambda: self.send_cmd("headmode 1")).pack(side="left")
            ttk.Button(row, text="OFF", width=4, command=lambda: self.send_cmd("headmode 0")).pack(side="left")

            row2 = ttk.Frame(cmd)
            row2.pack(fill="x", padx=6, pady=4)
            ttk.Label(row2, text="Kick ID jugador:").pack(side="left")
            self.v_kick = tk.StringVar()
            ttk.Entry(row2, textvariable=self.v_kick, width=6).pack(side="left", padx=4)
            ttk.Button(row2, text="KICK", command=self.do_kick).pack(side="left")
            ttk.Label(row2, text="   Votemap:").pack(side="left")
            self.v_votemap = tk.StringVar()
            self.cb_votemap = ttk.Combobox(row2, textvariable=self.v_votemap, width=26)
            self.cb_votemap.pack(side="left", padx=4)
            ttk.Button(row2, text="VOTEMAP", command=self.do_votemap).pack(side="left")
            ttk.Label(row2, text="   Otro comando:").pack(side="left")
            self.v_raw = tk.StringVar()
            e = ttk.Entry(row2, textvariable=self.v_raw, width=28)
            e.pack(side="left", padx=4)
            e.bind("<Return>", lambda _e: self.do_raw())
            ttk.Button(row2, text="Enviar", command=self.do_raw).pack(side="left")

            row3 = ttk.Frame(cmd)
            row3.pack(fill="x", padx=6, pady=4)
            ttk.Button(row3, text="CHAT", command=lambda: self.send_cmd("chat")).pack(side="left", padx=2)
            ttk.Button(row3, text="MATCH", command=lambda: self.send_cmd("match")).pack(side="left", padx=2)
            ttk.Label(row3, text="   SAY:").pack(side="left")
            self.v_say = tk.StringVar()
            es = ttk.Entry(row3, textvariable=self.v_say, width=40)
            es.pack(side="left", padx=4)
            es.bind("<Return>", lambda _e: self.do_say())
            ttk.Button(row3, text="SAY", command=self.do_say).pack(side="left")
            for lab, cmdname in (("SPEED", "speed"), ("MATRIX", "matrix")):
                ttk.Label(row3, text=f"   {lab} (25-400):").pack(side="left")
                var = tk.StringVar()
                setattr(self, "v_" + cmdname, var)
                ttk.Entry(row3, textvariable=var, width=5).pack(side="left", padx=4)
                ttk.Button(row3, text=lab, width=8, command=lambda c=cmdname: self.do_num_cmd(c)).pack(side="left")

        def _build_maps(self) -> None:
            f = self.tab_maps
            top = ttk.Frame(f)
            top.pack(fill="x", padx=8, pady=(8, 0))
            ttk.Label(top, text="Mapas mp_* de demo.vpk (carpeta scn)").pack(side="left")
            ttk.Button(top, text="Re-escanear demo.vpk", command=self.rescan_vpk).pack(side="left")
            self.vpk_info = TVar("")
            ttk.Label(f, textvariable=self.vpk_info, foreground="#555555", wraplength=1000, justify="left").pack(fill="x", padx=10)

            body = ttk.Frame(f)
            body.pack(fill="both", expand=True)
            left = ttk.LabelFrame(body, text="Ciclo de mapas (SCENE_LOOP ... END_SCENE_LOOP)")
            left.pack(side="left", fill="both", expand=True, padx=8, pady=8)
            self.lb_loop = tk.Listbox(left, selectmode="extended", height=20, width=24, exportselection=False)
            sb = ttk.Scrollbar(left, command=self.lb_loop.yview)
            self.lb_loop.configure(yscrollcommand=sb.set)
            self.lb_loop.pack(side="left", fill="both", expand=True)
            sb.pack(side="left", fill="y")
            self.lb_loop.bind("<<ListboxSelect>>", lambda _e: self.on_pick_loop())
            bt = ttk.Frame(left)
            bt.pack(side="left", fill="y", padx=6)
            for text, fn in (("Subir", self.loop_up), ("Bajar", self.loop_down), ("Quitar", self.loop_remove),
                             ("Duplicar", self.loop_dup), ("Vaciar", self.loop_clear),
                             ("Guardar ciclo", self.save_cfg)):
                ttk.Button(bt, text=text, width=14, command=fn).pack(pady=2)
            self.loop_count = TVar("0 mapas")
            ttk.Label(bt, textvariable=self.loop_count).pack(pady=8)

            mid = ttk.LabelFrame(body, text="Mapas disponibles (.scn del demo.vpk)")
            mid.pack(side="left", fill="both", expand=True, padx=4, pady=8)
            r1 = ttk.Frame(mid)
            r1.pack(fill="x", padx=4, pady=4)
            self.v_pick = tk.StringVar()
            self.cb_pick = ttk.Combobox(r1, textvariable=self.v_pick, width=26, state="readonly")
            self.cb_pick.pack(side="left")
            self.cb_pick.bind("<<ComboboxSelected>>", lambda _e: self.show_tex(self.v_pick.get()))
            ttk.Button(r1, text="Agregar al ciclo", command=self.add_pick).pack(side="left", padx=4)
            r2 = ttk.Frame(mid)
            r2.pack(fill="x", padx=4)
            self.v_find = tk.StringVar()
            self.v_find.trace_add("write", lambda *_: self.fill_scenes())
            ttk.Label(r2, text="Filtro:").pack(side="left")
            ttk.Entry(r2, textvariable=self.v_find, width=16).pack(side="left", padx=4)
            self.v_only_mp = tk.BooleanVar(value=True)
            self.lb_avail = tk.Listbox(mid, selectmode="extended", height=18, width=24, exportselection=False)
            sb2 = ttk.Scrollbar(mid, command=self.lb_avail.yview)
            self.lb_avail.configure(yscrollcommand=sb2.set)
            self.lb_avail.pack(side="left", fill="both", expand=True, padx=(4, 0), pady=4)
            sb2.pack(side="left", fill="y", pady=4)
            self.lb_avail.bind("<Double-Button-1>", lambda _e: self.add_selected())
            self.lb_avail.bind("<<ListboxSelect>>", lambda _e: self.on_pick_avail())
            rb = ttk.Frame(mid)
            rb.pack(side="left", fill="y", padx=4)
            ttk.Button(rb, text="Agregar seleccion", width=18, command=self.add_selected).pack(pady=2)
            ttk.Button(rb, text="Agregar todos", width=18, command=self.add_all).pack(pady=2)
            ttk.Label(rb, text="Agregar a mano:").pack(pady=(14, 2))
            self.v_manual = tk.StringVar()
            ttk.Entry(rb, textvariable=self.v_manual, width=20).pack()
            ttk.Button(rb, text="Agregar nombre", width=18, command=self.add_manual).pack(pady=2)

            tx = ttk.LabelFrame(body, text="Vista previa")
            tx.pack(side="left", anchor="n", padx=8, pady=8)
            self.tex_title = tk.StringVar(value="")
            box = tk.Frame(tx, width=TEX_BOX[0], height=TEX_BOX[1], bg="#202020", highlightthickness=0, bd=0)
            box.pack(padx=0, pady=0)          # el visor mide justo lo que la imagen (256x256): no cambia al elegir mapa
            box.pack_propagate(False)
            self.tex_label = tk.Label(box, text="(sin imagen)", bg="#202020", fg="#bbbbbb", bd=0, highlightthickness=0)
            self.tex_label.place(relx=0.5, rely=0.5, anchor="center")
            self.tex_info = TVar("")
            ttk.Label(tx, textvariable=self.tex_info, foreground="#555555", wraplength=TEX_BOX[0]).pack(padx=2, pady=(2, 2), anchor="w")

        def _build_cfg(self) -> None:
            f = self.tab_cfg
            nf = ttk.LabelFrame(f, text="Red base del servidor (adaptador que usa el juego)")
            nf.pack(side="top", fill="x", padx=8, pady=(8, 0))
            self.v_adapter = tk.StringVar()
            self.cb_adapter = ttk.Combobox(nf, textvariable=self.v_adapter, state="readonly", width=70)
            self.cb_adapter.grid(row=0, column=0, padx=6, pady=6, sticky="w")
            ttk.Button(nf, text="Actualizar lista", command=self.refresh_adapters).grid(row=0, column=1, padx=4)
            ttk.Button(nf, text="Aplicar", command=self.apply_adapter).grid(row=0, column=2, padx=4)
            self.v_net_apply = tk.BooleanVar(value=self.s["net_apply"])
            ttk.Checkbutton(nf, text="Aplicar al iniciar el servidor", variable=self.v_net_apply,
                            command=self.collect_settings).grid(row=0, column=3, padx=8)
            self.v_adapter_info = TVar("Leyendo adaptadores...")
            ttk.Label(nf, textvariable=self.v_adapter_info, foreground="#555555", wraplength=980, justify="left").grid(
                row=1, column=0, columnspan=4, sticky="w", padx=6, pady=(0, 6))
            canvas = tk.Canvas(f, highlightthickness=0)
            sb = ttk.Scrollbar(f, orient="vertical", command=canvas.yview)
            inner = ttk.Frame(canvas)
            inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
            win = canvas.create_window((0, 0), window=inner, anchor="nw")
            canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
            inner.columnconfigure(2, weight=1)
            canvas.configure(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y")
            canvas.pack(side="left", fill="both", expand=True)
            self.cfg_inner = inner
            r = 0
            for key, typ, default, desc, extra in PARAMS:
                ttk.Label(inner, text=key, font=("TkDefaultFont", 9, "bold")).grid(row=r, column=0, sticky="e", padx=8, pady=4)
                var = tk.StringVar(value=default)
                self.param_vars[key] = var
                if typ == "bool":
                    w = ttk.Combobox(inner, textvariable=var, values=["0", "1"], width=8, state="readonly")
                elif typ == "choice":
                    w = ttk.Combobox(inner, textvariable=var, values=list(extra.keys()), width=8, state="readonly")  # type: ignore[union-attr]
                elif typ == "int":
                    lo, hi = extra  # type: ignore[misc]
                    w = ttk.Spinbox(inner, textvariable=var, from_=lo, to=hi, width=10)
                else:
                    w = ttk.Entry(inner, textvariable=var, width=34)
                w.grid(row=r, column=1, sticky="w", padx=4)
                ttk.Label(inner, text=desc, foreground="#555555").grid(row=r, column=2, sticky="w", padx=8)
                r += 1
            ttk.Separator(inner).grid(row=r, column=0, columnspan=3, sticky="ew", pady=8)
            r += 1
            ttk.Label(inner, text="Otros parametros de server.cfg\n(clave: valor, uno por linea)").grid(row=r, column=0, sticky="ne", padx=8)
            self.txt_extra = tk.Text(inner, height=5, width=20)
            self.txt_extra.grid(row=r, column=1, columnspan=2, sticky="ew", padx=4)
            r += 1
            bt = ttk.Frame(inner)
            bt.grid(row=r, column=0, columnspan=3, pady=12)
            for text, fn in (("Guardar server.cfg", self.save_cfg), ("Recargar", self.reload_cfg),
                             ("Exportar JSON...", self.export_json), ("Importar JSON...", self.import_json),
                             ("Abrir carpeta", self.open_folder)):
                ttk.Button(bt, text=text, command=fn).pack(side="left", padx=4)
            self.cfg_path_var = TVar("")
            ttk.Label(inner, textvariable=self.cfg_path_var, foreground="#555555").grid(row=r + 1, column=0, columnspan=3)

        def _build_folders(self, f) -> None:
            pf = ttk.LabelFrame(f, text="Carpetas")
            pf.pack(side="bottom", fill="x", padx=8, pady=8)
            self.v_server_dir = tk.StringVar(value=self.s["server_dir"])
            self.v_client_dir = tk.StringVar(value=self.s["client_dir"])
            for i, (lab, var, hint) in enumerate((
                    ("Carpeta del servidor:", self.v_server_dir, "Dedicated.exe y server.cfg"),
                    ("Carpeta del cliente:", self.v_client_dir, "raiz del juego (demo.vpk y DLL)"))):
                ttk.Label(pf, text=lab).grid(row=i, column=0, sticky="e", padx=6, pady=3)
                ttk.Entry(pf, textvariable=var, width=62).grid(row=i, column=1, sticky="w")
                ttk.Button(pf, text="...", width=3, command=lambda v=var: self.pick_dir(v)).grid(row=i, column=2, padx=4)
                ttk.Label(pf, text=hint, foreground="#555555").grid(row=i, column=3, sticky="w")
            ttk.Button(pf, text="Re-escanear demo.vpk", command=self.rescan_vpk).grid(row=2, column=1, sticky="w", pady=3)
            ttk.Button(pf, text="Restaurar DLL originales", command=self.restore_now).grid(row=2, column=1, sticky="e", pady=3)

        def _build_net(self) -> None:
            f = self.tab_net
            mm = ttk.LabelFrame(f, text="Matchmaker (donde se anuncia el servidor)")
            mm.pack(fill="x", padx=8, pady=8)
            ttk.Label(mm, text="Direccion:").grid(row=0, column=0, sticky="e", padx=6, pady=4)
            self.v_mm = tk.StringVar(value=self.s["mm_host"])
            self.cb_mm = ttk.Combobox(mm, textvariable=self.v_mm, width=26, values=MM_PRESETS)
            self.cb_mm.grid(row=0, column=1, sticky="w")
            self.cb_mm.bind("<<ComboboxSelected>>", lambda _e: self.on_mm_changed())
            self.cb_mm.bind("<Return>", lambda _e: self.on_mm_changed())
            ttk.Button(mm, text="Parchear IP", command=self.patch_ip_now).grid(row=0, column=2, padx=6)
            ttk.Button(mm, text="Probar DNS", command=self.test_dns).grid(row=0, column=3, padx=2)
            ttk.Button(mm, text="Permitir en el Firewall", command=self.fw_rule).grid(row=0, column=4, padx=6)
            self.v_mm_cur = TVar("")
            ttk.Label(mm, textvariable=self.v_mm_cur, font=("TkDefaultFont", 9, "bold")).grid(row=2, column=0, columnspan=5, sticky="w", padx=6)
            ttk.Label(mm, justify="left", foreground="#555555", wraplength=980, text=NOTE_MM).grid(row=3, column=0, columnspan=5, sticky="w", padx=6, pady=4)

            ipf = ttk.LabelFrame(f, text="IP de esta maquina")
            ipf.pack(fill="both", expand=True, padx=8, pady=4)
            self.lb_ips = tk.Listbox(ipf, height=8)
            self.lb_ips.pack(side="left", fill="both", expand=True, padx=6, pady=6)
            side = ttk.Frame(ipf)
            side.pack(side="left", fill="y", padx=6)
            ttk.Button(side, text="Actualizar IP locales", command=self.refresh_ips).pack(pady=2, fill="x")
            ttk.Button(side, text="Ver IP publica", command=self.get_public_ip).pack(pady=2, fill="x")
            self.v_pub = TVar("")
            ttk.Label(side, textvariable=self.v_pub, font=("TkDefaultFont", 10, "bold")).pack(pady=6)
            self.v_port_info = TVar("")
            ttk.Label(ipf, textvariable=self.v_port_info).pack(side="bottom", anchor="w", padx=8)
            ttk.Label(f, justify="left", wraplength=980, foreground="#333333", text=NOTE_IP).pack(fill="x", padx=10, pady=8)

        # ---------------- ayudas -------------------------------------------
        def log_line(self, kind: str, text: str) -> None:
            if kind != "game":
                text = T(text)
            tag = kind
            if kind == "game":
                if _ERR_RE.search(text):
                    tag = "error"
                elif re.search(r"Cargando escena", text, re.I):
                    tag = "map"
            self.log.configure(state="normal")
            self.log.insert("end", text + "\n", tag)
            if int(self.log.index("end-1c").split(".")[0]) > 4000:
                self.log.delete("1.0", "1000.0")
            self.log.see("end")
            self.log.configure(state="disabled")

        # ---------------- idioma ---------------------------------------------
        def _i18n_scan(self) -> None:
            """Recuerda el texto original (español) de cada widget para poder re-traducirlo al cambiar de idioma."""
            self._i18n: List[Tuple[object, str]] = []

            def walk(w) -> None:
                for c in w.winfo_children():
                    try:
                        txt = str(c.cget("text"))
                    except tk.TclError:
                        txt = ""
                    if txt:
                        self._i18n.append((c, txt))
                    walk(c)
            walk(self)
            self._i18n_tabs = [(t, self.nb.tab(t, "text")) for t in self.nb.tabs()]

        def _i18n_apply(self) -> None:
            for w, orig in self._i18n:
                try:
                    if str(w.cget("image")):      # la imagen de la textura no lleva texto
                        continue
                except tk.TclError:
                    pass
                try:
                    w.configure(text=T(orig))
                except tk.TclError:
                    pass
            for t, orig in self._i18n_tabs:
                self.nb.tab(t, text=T(orig))
            for v in _tvars:
                v.retranslate()

        def on_lang(self) -> None:
            for code, name in LANGS.items():
                if name == self.v_lang.get():
                    set_lang(code)
                    self.s["lang"] = code
                    save_settings(self.s)
            self._i18n_apply()

        def set_bar(self, text: str) -> None:
            self.bar.set(text)

        def pick_dir(self, var: tk.StringVar) -> None:
            d = filedialog.askdirectory(initialdir=var.get() or APP_DIR)
            if d:
                var.set(os.path.normpath(d))

        def collect_settings(self) -> None:
            s = self.s
            s["server_dir"] = self.v_server_dir.get().strip() or APP_DIR
            s["client_dir"] = self.v_client_dir.get().strip()
            s["mm_host"] = self.v_mm.get().strip()
            s["respawn"] = bool(self.v_respawn.get())
            s["show_console"] = bool(self.v_show.get())
            s["net_apply"] = bool(self.v_net_apply.get())
            save_settings(s)

        # ---------------- server.cfg -----------------------------------------
        def cfg_path(self) -> str:
            return os.path.join(self.v_server_dir.get().strip() or APP_DIR, self.s["cfg_name"])

        def reload_cfg(self) -> None:
            self.cfg = ServerCfg(self.cfg_path()).load()
            for key, typ, default, _d, _x in PARAMS:
                self.param_vars[key].set(self.cfg.get(key, default))
            self.txt_extra.delete("1.0", "end")
            self.txt_extra.insert("1.0", "\n".join(f"{k}: {self.cfg.get(k)}" for k in self.cfg.extra_keys()))
            self.lb_loop.delete(0, "end")
            self.loop_orig = {}
            for m in self.cfg.loop:
                clean = scene_info(m)[0] or m
                self.loop_orig.setdefault(clean.lower(), m)
                self.lb_loop.insert("end", clean)
            self.loop_count.set(f"{len(self.cfg.loop)} mapas")
            self.cfg_path_var.set(self.cfg.path + ("" if os.path.isfile(self.cfg.path) else "   (no existe: se creara al guardar)"))
            self.cb_votemap["values"] = list(dict.fromkeys(self.lb_loop.get(0, "end")))

        def save_cfg(self) -> bool:
            for key, typ, default, _d, extra in PARAMS:
                val = self.param_vars[key].get().strip()
                if typ == "int":
                    try:
                        n = int(val)
                    except ValueError:
                        messagebox.showerror("server.cfg", f"{key}: '{val}' no es un numero entero.")
                        return False
                    lo, hi = extra  # type: ignore[misc]
                    if not lo <= n <= hi:
                        messagebox.showerror("server.cfg", f"{key}: debe estar entre {lo} y {hi}.")
                        return False
                    val = str(n)
                if key == "PASSWORD" and len(val) > 8:
                    messagebox.showerror("server.cfg", "PASSWORD: maximo 8 caracteres.")
                    return False
                self.cfg.set(key, val)
            for line in self.txt_extra.get("1.0", "end").splitlines():
                m = _KEY_RE.match(line)
                if m:
                    self.cfg.set(m.group(1).upper(), m.group(2))
            self.collect_settings()
            self.cfg.loop = [self.cfg_name_for(c) for c in self.lb_loop.get(0, "end")]
            try:
                self.cfg.save()
            except OSError as e:
                messagebox.showerror("server.cfg", f"No se pudo guardar: {e}")
                return False
            self.reload_cfg()
            self.set_bar(f"server.cfg guardado ({self.cfg.path})")
            return True

        def export_json(self) -> None:
            p = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")], initialfile="server_cfg.json")
            if p:
                with open(p, "w", encoding="utf-8") as f:
                    json.dump(self.cfg.to_dict(), f, indent=2, ensure_ascii=False)

        def import_json(self) -> None:
            p = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
            if not p:
                return
            try:
                with open(p, "r", encoding="utf-8") as f:
                    d = json.load(f)
                self.cfg.update_from_dict(d)
            except (OSError, ValueError) as e:
                messagebox.showerror("JSON", str(e))
                return
            for key, *_r in PARAMS:
                self.param_vars[key].set(self.cfg.get(key, self.param_vars[key].get()))
            self.lb_loop.delete(0, "end")
            self.loop_orig = {}
            for m in self.cfg.loop:
                clean = scene_info(m)[0] or m
                self.loop_orig.setdefault(clean.lower(), m)
                self.lb_loop.insert("end", clean)
            self.loop_count.set(f"{len(self.cfg.loop)} mapas")
            self.set_bar("JSON cargado: pulsa 'Guardar server.cfg' para aplicarlo.")

        def open_folder(self) -> None:
            d = os.path.dirname(self.cfg_path())
            try:
                if IS_WIN:
                    os.startfile(d)  # type: ignore[attr-defined]
                else:
                    subprocess.Popen(["xdg-open", d])
            except Exception as e:  # noqa: BLE001
                self.set_bar(str(e))

        # ---------------- mapas -------------------------------------------
        def vpk_path(self) -> str:
            root = client_root(self.v_client_dir.get())
            if root:
                return os.path.join(root, "demo.vpk")
            for d in (read_data_path(), self.v_server_dir.get().strip()):
                if d and os.path.isfile(os.path.join(d, "demo.vpk")):
                    return os.path.join(d, "demo.vpk")
            return os.path.join(self.v_client_dir.get().strip() or self.v_server_dir.get().strip() or APP_DIR, "demo.vpk")

        def rescan_vpk(self) -> None:
            path = self.vpk_path()
            self.vpk_info.set("Leyendo demo.vpk..." if os.path.isfile(path) else f"No hay demo.vpk en: {os.path.dirname(path)}")

            def work() -> None:
                ev_q.put(("scenes", load_vpk(path)))
            threading.Thread(target=work, daemon=True).start()

        def fill_scenes(self) -> None:
            maps = self.catalog.maps()
            note = ""
            if not maps:
                maps = OrderedDict((n.upper(), n) for n in DEFAULT_MAPS)
                note = " (no se hallaron .scn: lista por defecto del juego)"
            self.raw_all = {c.lower(): r for c, r in maps.items()}
            flt = self.v_find.get().strip().lower()
            items = [n for n in maps if flt in n.lower()]
            self.lb_avail.delete(0, "end")
            for n in items:
                self.lb_avail.insert("end", n)
            self.cb_pick["values"] = items
            if items and self.v_pick.get() not in items:
                self.v_pick.set(items[0])
            cat = self.catalog
            self.vpk_info.set(f"{cat.info}  |  {len(maps)} mapas mp_* ({len(items)} visibles){note}" if cat.info or maps else "")

        def cfg_name_for(self, clean: str) -> str:
            """Texto que se escribe en SCENE_LOOP para un mapa de la lista."""
            return self.loop_orig.get(clean.lower(), clean.upper())

        def _add_maps(self, names: List[str]) -> None:
            for n in names:
                n = scene_info(n.strip())[0].upper() if n.strip() else ""
                if n:
                    self.lb_loop.insert("end", n)
            self.loop_count.set(f"{self.lb_loop.size()} mapas")
            self.cb_votemap["values"] = list(dict.fromkeys(self.lb_loop.get(0, "end")))

        # ---- textura ----
        def on_pick_loop(self) -> None:
            sel = self.lb_loop.curselection()
            if sel:
                self.show_tex(self.lb_loop.get(sel[0]))

        def on_pick_avail(self) -> None:
            sel = self.lb_avail.curselection()
            if sel:
                self.show_tex(self.lb_avail.get(sel[0]))

        def show_tex(self, clean: str) -> None:
            self.tex_title.set(clean)
            self.tex_ent = None
            self.tex_req = clean
            self.tex_img = None
            self.tex_label.configure(image="", text=T("(sin imagen)"))
            cat = self.catalog
            if not cat.entries:
                self.tex_info.set("Sin indice del demo.vpk: no se puede buscar la textura.")
                return
            if clean in self.tex_cache:
                self._tex_ready(self.tex_cache[clean])
                return
            ent = cat.tex_entry(self.raw_all.get(clean.lower(), clean), clean)
            if not ent:
                self.tex_info.set(f"No hay texs\\{clean}-tbn.tex en el demo.vpk.")
                return
            self.tex_ent = ent
            self.tex_info.set("Decodificando...")

            def work() -> None:
                try:
                    data = read_tex_bytes(cat.path, ent)
                    png, info = tex_preview(data)
                    ev_q.put(("tex", (clean, ent, png, info, None)))
                except Exception as e:  # noqa: BLE001
                    ev_q.put(("tex", (clean, ent, None, "", str(e))))
            threading.Thread(target=work, daemon=True).start()

        def preload_tex(self) -> None:
            """Decodifica en segundo plano todas las vistas previas (-tbn.tex) para cambiar de mapa sin esperar."""
            self.tex_gen += 1
            gen, cat = self.tex_gen, self.catalog
            self.tex_cache = {}
            jobs = []
            for clean, raw in self.catalog.maps().items():
                ent = cat.tex_entry(raw, clean)
                if ent:
                    jobs.append((clean, ent))

            def work() -> None:
                for n, (clean, ent) in enumerate(jobs, 1):
                    if gen != self.tex_gen:
                        return
                    try:
                        png, info = tex_preview(read_tex_bytes(cat.path, ent))
                        ev_q.put(("texcache", (gen, (clean, ent, png, info, None))))
                    except Exception:  # noqa: BLE001
                        pass
                ev_q.put(("bar", f"{len(jobs)} vistas previas cargadas"))
            threading.Thread(target=work, daemon=True).start()

        def _tex_ready(self, payload) -> None:
            import base64
            clean, ent, png, info, err = payload
            if clean != self.tex_req:
                return
            self.tex_cache.setdefault(clean, payload)
            head = ent.name
            if err:
                self.tex_info.set(f"{head}  No se pudo mostrar: {err}")
                return
            img = tk.PhotoImage(data=base64.b64encode(png))
            f = max(1, -(-img.width() // TEX_BOX[0]), -(-img.height() // TEX_BOX[1]))
            self.tex_img = img.subsample(f, f) if f > 1 else img
            self.tex_label.configure(image=self.tex_img, text="")
            self.tex_info.set(f"{head}   {info}")

        def add_pick(self) -> None:
            if self.v_pick.get():
                self._add_maps([self.v_pick.get()])

        def add_selected(self) -> None:
            self._add_maps([self.lb_avail.get(i) for i in self.lb_avail.curselection()])

        def add_all(self) -> None:
            self._add_maps(list(self.lb_avail.get(0, "end")))

        def add_manual(self) -> None:
            self._add_maps([self.v_manual.get()])
            self.v_manual.set("")

        def loop_remove(self) -> None:
            for i in reversed(self.lb_loop.curselection()):
                self.lb_loop.delete(i)
            self.loop_count.set(f"{self.lb_loop.size()} mapas")

        def loop_clear(self) -> None:
            if self.lb_loop.size() and messagebox.askyesno("Ciclo", "Vaciar todo el ciclo? (vacio = el juego usa su ciclo por defecto)"):
                self.lb_loop.delete(0, "end")
                self.loop_count.set("0 mapas")

        def loop_dup(self) -> None:
            for i in reversed(self.lb_loop.curselection()):
                self.lb_loop.insert(i + 1, self.lb_loop.get(i))
            self.loop_count.set(f"{self.lb_loop.size()} mapas")

        def _move(self, d: int) -> None:
            sel = list(self.lb_loop.curselection())
            if not sel:
                return
            order = sel if d < 0 else reversed(sel)
            new_sel = []
            for i in order:
                j = i + d
                if 0 <= j < self.lb_loop.size() and j not in sel and j not in new_sel:
                    v = self.lb_loop.get(i)
                    self.lb_loop.delete(i)
                    self.lb_loop.insert(j, v)
                    new_sel.append(j)
                else:
                    new_sel.append(i)
            self.lb_loop.selection_clear(0, "end")
            for j in new_sel:
                self.lb_loop.selection_set(j)

        def loop_up(self) -> None:
            self._move(-1)

        def loop_down(self) -> None:
            self._move(1)

        # ---------------- DLL ---------------------------------------------
        def restore_now(self) -> None:
            if self.runner.running:
                messagebox.showinfo("DLL", "Detiene el servidor primero.")
                return
            if messagebox.askyesno("Restaurar", "Devolver las DLL originales del servidor?"):
                n = restore_release(self.s["server_dir"], lambda m: self.log_line("info", m))
                self.set_bar(f"{n} archivos restaurados/quitados")

        # ---------------- adaptador de red ----------------------------------
        def refresh_adapters(self) -> None:
            self.v_adapter_info.set("Leyendo adaptadores...")

            def work() -> None:
                res, warn = list_net_adapters()
                ev_q.put(("adapters", (res, warn, read_netguid())))
            threading.Thread(target=work, daemon=True).start()

        def _fill_adapters(self, res: List[dict], warn: str, cur: Optional[bytes]) -> None:
            self.adapters = res
            self.cb_adapter["values"] = [a["name"] for a in res]
            want = bytes.fromhex(self.s["net_guid"]) if self.s.get("net_guid") else cur
            idx = next((i for i, a in enumerate(res) if a["guid"] and a["guid"] == want), -1)
            if idx >= 0:
                self.cb_adapter.current(idx)
            elif res:
                self.cb_adapter.set("")
            msg = warn or ""
            if cur is None and IS_WIN:
                msg += (" " if msg else "") + "El registro aun no tiene adaptador elegido: el juego usa el primero de la lista."
            elif IS_WIN and idx < 0 and res and res[0]["guid"]:
                msg += (" " if msg else "") + "El adaptador del registro no coincide con ninguno de la lista."
            if not msg:
                msg = "El adaptador elegido se guarda como 'netGUID' en el registro (el mismo valor que usa el cliente de Torrente)."
            self.v_adapter_info.set(msg)

        def apply_adapter(self, quiet: bool = False) -> bool:
            i = self.cb_adapter.current()
            if i < 0 or i >= len(self.adapters):
                if not quiet:
                    self.set_bar("Elige un adaptador de la lista.")
                return False
            a = self.adapters[i]
            if not a["guid"]:
                if not quiet:
                    messagebox.showinfo("Adaptador", "Esta lista es solo informativa (no se pudo leer DirectPlay): no puedo guardar el adaptador.")
                return False
            try:
                msg = write_netguid(a["guid"])
            except Exception as e:  # noqa: BLE001
                self.set_bar(f"No se pudo guardar el adaptador: {e}")
                return False
            self.s["net_guid"] = a["guid"].hex()
            save_settings(self.s)
            self.set_bar(f"{msg} ({a['name']})")
            return True

        # ---------------- matchmaker / IP -------------------------------------
        def main_dll_path(self) -> str:
            rel = os.path.join(self.v_server_dir.get().strip() or APP_DIR, "comun", "bin", "release")
            return list_dlls(rel).get("main.dll", "")

        def update_mm_current(self) -> None:
            p = self.main_dll_path()
            if not p:
                self.v_mm_cur.set("Main.dll del servidor: no encontrada")
                return
            try:
                with open(p, "rb") as fh:
                    cur = read_matchmaker(fh.read())
            except OSError as e:
                self.v_mm_cur.set(f"Main.dll: {e}")
                return
            self.v_mm_cur.set(f"IP/host actual dentro de Main.dll del servidor: {cur[0]}  (maximo {cur[1]} caracteres)" if cur
                              else "Main.dll del servidor: no se reconoce la direccion del matchmaker")

        def patch_ip_now(self) -> None:
            self.collect_settings()
            p = self.main_dll_path()
            if not p:
                self.set_bar("No encuentro comun\\bin\\release\\Main.dll en la carpeta del servidor.")
                return
            try:
                msg = patch_main_file(p, self.s["mm_host"])
            except (ValueError, OSError) as e:
                messagebox.showerror("Parchear IP", str(e))
                return
            self.set_bar(msg)
            self.log_line("info", "[manager] " + msg)
            self.update_mm_current()

        def on_mm_changed(self) -> None:
            self.collect_settings()   # la IP solo se parchea al pulsar "Parchear IP"

        def test_dns(self) -> None:
            host = self.v_mm.get().strip()
            threading.Thread(target=lambda: ev_q.put(("bar", test_matchmaker(host))), daemon=True).start()

        def current_exe(self) -> str:
            return os.path.join(self.v_server_dir.get().strip() or APP_DIR, "Dedicated.exe")

        def fw_rule(self) -> None:
            try:
                msg = add_firewall_rule(self.current_exe())
            except Exception as e:  # noqa: BLE001
                messagebox.showerror("Firewall", str(e))
                return
            self.set_bar(msg + " (si apareció el aviso de Windows, acéptalo)")

        # ---------------- comparar DLL ------------------------------------------
        # ---------------- red ----------------------------------------------
        def refresh_ips(self) -> None:
            self.lb_ips.delete(0, "end")
            port = self.cfg.get("PORT", "8888") if hasattr(self, "cfg") else "8888"
            for ip in local_ips():
                self.lb_ips.insert("end", f"{ip}:{port}")
            self.v_port_info.set(f"Puerto UDP/TCP a abrir: {port}")

        def get_public_ip(self) -> None:
            self.v_pub.set("consultando...")
            threading.Thread(target=lambda: ev_q.put(("pubip", public_ip())), daemon=True).start()

        # ---------------- servidor -----------------------------------------
        def on_start(self) -> None:
            if self.runner.running:
                self.set_bar("El servidor ya esta en marcha.")
                return
            if not self.save_cfg():
                return
            self.collect_settings()
            if not os.path.isfile(self.vpk_path()):
                if not messagebox.askyesno("demo.vpk", f"No encuentro {self.vpk_path()}.\nIniciar igualmente?"):
                    return
            if self.s.get("net_apply") and self.cb_adapter.current() >= 0:
                self.apply_adapter(quiet=True)
            self.update_mm_current()
            snap = dict(self.s)
            snap["overrides"] = dict(self.s["overrides"])
            snap["extra_dlls"] = list(self.s["extra_dlls"])

            def launch() -> Tuple[List[str], str]:
                return build_launch(snap, lambda m: ev_q.put(("line", ("info", m))))
            self.run_loop = list(self.lb_loop.get(0, "end"))
            self.loop_idx = None
            self.status_vars["proximo"].set("-")
            self.runner.start(launch, bool(self.v_respawn.get()), float(self.s["respawn_delay"]), bool(self.v_show.get()))
            self.log_line("info", f"[manager] --- inicio {time.strftime('%H:%M:%S')} ---")

        def on_stop(self) -> None:
            self.runner.stop()
            self.log_line("info", "[manager] Deteniendo servidor...")

        def on_restart(self) -> None:
            if self.runner.running:
                self.runner.restart()
                self.log_line("info", "[manager] Reiniciando servidor...")
            else:
                self.on_start()

        def send_cmd(self, cmd: str) -> None:
            if self.runner.send(cmd):
                self.log_line("cmd", f"> {cmd}")
            else:
                self.set_bar("No hay consola enlazada: inicia el servidor (y deja 'Mostrar consola' desactivado si falla el enlace).")

        def send_esc(self) -> None:
            if self.runner.send_escape():
                self.log_line("cmd", "> [ESC]")
            else:
                self.set_bar("Servidor no iniciado.")

        def do_kick(self) -> None:
            v = self.v_kick.get().strip()
            if v.isdigit():
                self.send_cmd(f"kick {v}")
            else:
                self.set_bar("Escribe el ID numerico del jugador (usa PLAYER para verlo).")

        def do_say(self) -> None:
            v = self.v_say.get().strip()
            if v:
                self.send_cmd(f"say {v}")
                self.v_say.set("")

        def do_num_cmd(self, name: str) -> None:
            v = getattr(self, "v_" + name).get().strip()
            if v.isdigit() and 25 <= int(v) <= 400:
                self.send_cmd(f"{name} {v}")
            else:
                self.set_bar(f"{name.upper()}: escribe un valor entre 25 y 400.")

        def do_votemap(self) -> None:
            v = self.v_votemap.get().strip()
            self.send_cmd(f"votemap {v}".strip())

        def do_raw(self) -> None:
            v = self.v_raw.get().strip()
            if v:
                self.send_cmd(v)
                self.v_raw.set("")

        # ---------------- eventos del hilo ---------------------------------
        def pump(self) -> None:
            try:
                while True:
                    kind, payload = ev_q.get_nowait()
                    if kind == "line":
                        k, text = payload  # type: ignore[misc]
                        self.log_line(k, text)
                        if k == "game":
                            self.apply_status(interpret_line(text))
                    elif kind == "state":
                        self.status_vars["estado"].set(str(payload))
                        if payload in ("Detenido",):
                            self.status_vars["proximo"].set("-")
                            self.status_vars["mapa"].set("-")
                            self.status_vars["puerto"].set("-")
                            self.loop_idx = None
                            self.status_vars["pid"].set("-")
                            self.status_vars["actividad"].set("-")
                    elif kind == "status":
                        d = payload  # type: ignore[assignment]
                        if "pid" in d:
                            self.status_vars["pid"].set(d["pid"])
                        if "activity" in d:
                            self.status_vars["actividad"].set(d["activity"] or "-")
                    elif kind == "exit":
                        if self.runner.stop_flag.is_set():
                            self.log_line("info", "[manager] Servidor detenido.")
                        else:
                            self.log_line("info", f"[manager] Dedicated.exe termino (codigo {payload}).")
                        self.status_vars["matchmaker"].set("-")
                    elif kind == "info" and payload == "sin_consola":
                        self.set_bar("Consola no enlazada: los comandos del manager no estan disponibles.")
                    elif kind == "scenes":
                        self.catalog = payload  # type: ignore[assignment]
                        self.fill_scenes()
                        self.preload_tex()
                        if not self.catalog.scenes:
                            self.vpk_info.set(self.catalog.info + "  |  No se hallaron escenas: puedes agregar nombres a mano.")
                    elif kind == "adapters":
                        res, warn, cur = payload  # type: ignore[misc]
                        self._fill_adapters(res, warn, cur)
                    elif kind == "texcache":
                        gen, item = payload  # type: ignore[misc]
                        if gen == self.tex_gen:
                            self.tex_cache.setdefault(item[0], item)
                    elif kind == "tex":
                        self._tex_ready(payload)
                    elif kind == "bar":
                        self.set_bar(str(payload))
                    elif kind == "pubip":
                        self.v_pub.set(f"IP publica: {payload}" if payload else "No se pudo obtener")
            except queue.Empty:
                pass
            self.after(120, self.pump)

        def apply_status(self, d: Dict[str, str]) -> None:
            if "port" in d:
                self.status_vars["puerto"].set(d["port"])
            if "map" in d:
                self.status_vars["mapa"].set(d["map"])
                self.track_next_map(d["map"])
                self.status_vars["estado"].set(f"Cargando mapa {d['map']}")
            if "mm" in d:
                self.status_vars["matchmaker"].set(d["mm"])
                mp_ = self.status_vars["mapa"].get()
                if d["mm"].startswith("Conectado") and mp_ not in ("", "-"):
                    self.status_vars["estado"].set(f"Mapa cargado ({mp_})")
            if "state" in d:
                self.status_vars["estado"].set(d["state"])

        def track_next_map(self, name: str) -> None:
            """Mapa proximo = el siguiente del ciclo (SCENE_LOOP) despues del que se esta cargando."""
            loop = [m.upper() for m in self.run_loop]
            nm = name.upper()
            if not loop or nm not in loop:
                self.status_vars["proximo"].set("-")
                return
            n = len(loop)
            if self.loop_idx is None:
                idx = loop.index(nm)
            else:
                idx = next(((self.loop_idx + 1 + k) % n for k in range(n) if loop[(self.loop_idx + 1 + k) % n] == nm), loop.index(nm))
            self.loop_idx = idx
            self.status_vars["proximo"].set(loop[(idx + 1) % n])

        def tick(self) -> None:
            if self.runner.running and self.runner.started_at and self.runner.proc and self.runner.proc.poll() is None:
                s = int(time.time() - self.runner.started_at)
                self.status_vars["uptime"].set(f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}")
            else:
                self.status_vars["uptime"].set("-")
            self.after(1000, self.tick)

        def on_close(self) -> None:
            if self.runner.running and not messagebox.askyesno("Salir", "El servidor sigue en marcha. Detenerlo y salir?"):
                return
            self.collect_settings()
            self.runner.stop()
            self.destroy()

    App().mainloop()


if __name__ == "__main__":
    run_gui()
