# -*- coding: utf-8 -*-
# streamlit_app.py
# SPOOFER — Vidéo (Streamlit) — Export unique en QUALITÉ MAX
# - MP4 (H.264 lossless CRF=0) + AAC 320k, yuv420p, faststart
# - Effets: Normal / B&W / B&W contrasté / Golden Hour
# - Rotation: -90° / +90° / 180°
# - Miroir: aperçu (info) + export x2 (Normal + Miroir) si coché
# - Pas de redimensionnement, pas de réglages qualité/vitesse (verrouillés)
# - ZIP final sans écriture disque utilisateur (temp + cleanup)
#
# Déploiement Streamlit Cloud :
# - requirements.txt (streamlit)
# - packages.txt (ffmpeg)

import os, io, zipfile, tempfile, shutil, subprocess
from typing import List, Tuple, Dict, Optional, Iterable
import streamlit as st

# ------------------- Détection ffmpeg -------------------
def has_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return True
    except Exception:
        return False

FFMPEG_OK = has_ffmpeg()

# ------------------- Constantes -------------------
SUPPORTED_EXTS = {'.mp4', '.mov', '.m4v', '.webm', '.mkv', '.avi', '.mpeg', '.mpg', '.wmv'}
ROTATIONS_ALLOWED = {0, 90, 180, 270}

# Qualité maximale (verrouillée)
# - H.264 lossless: -crf 0 (équiv. -qp 0). yuv420p pour compat étendue.
# - Audio: AAC 320k @ 48 kHz
# - faststart pour streaming web
MAX_QUALITY_CODEC_ARGS = [
    "-c:v", "libx264",
    "-pix_fmt", "yuv420p",
    "-profile:v", "high",
    "-preset", "slow",       # fixe (pas d’UI)
    "-crf", "0",             # lossless
    "-c:a", "aac",
    "-b:a", "320k",
    "-ar", "48000",
]

def ext_lower(name: str) -> str:
    return os.path.splitext(name)[1].lower()

def choose_output_format(_: str) -> Tuple[str, List[str]]:
    # Toujours MP4 ultra-compatible
    return ".mp4", MAX_QUALITY_CODEC_ARGS

def ffmpeg_build_filtergraph(pipeline: Iterable[str], mirror: bool, rotate_deg: int) -> str:
    filters = []
    rd = rotate_deg % 360
    if rd == 90:
        filters.append("transpose=1")
    elif rd == 270:
        filters.append("transpose=2")
    elif rd == 180:
        filters.append("rotate=PI")

    if mirror:
        filters.append("hflip")

    if pipeline:
        step = pipeline[0]
        if step == "bw":
            filters.append("hue=s=0")
        elif step == "bwcontrast":
            filters.append("hue=s=0,eq=contrast=1.35:brightness=0.0")
        elif step == "goldenhour":
            filters.append("colorbalance=rs=.10:gs=.05:bs=-.05,hue=s=1.12,eq=contrast=1.06:brightness=0.03")

    return ",".join(filters) if filters else "null"

def apply_variant_suffix(pipeline: Iterable[str]) -> str:
    parts = list(pipeline)
    return "_" + "_".join(parts) if parts else "_normal"

def generate_variants(e_normal: bool, e_bw: bool, e_bwc: bool, e_gh: bool) -> List[List[str]]:
    variants = []
    if e_normal: variants.append(["normal"])
    if e_bw:     variants.append(["bw"])
    if e_bwc:    variants.append(["bwcontrast"])
    if e_gh:     variants.append(["goldenhour"])
    if not variants: variants.append(["normal"])
    return variants

def choose_preview_pipeline(e_normal: bool, e_bw: bool, e_bwc: bool, e_gh: bool) -> List[str]:
    if e_bwc: return ["bwcontrast"]
    if e_bw:  return ["bw"]
    if e_gh:  return ["goldenhour"]
    if e_normal: return ["normal"]
    return ["normal"]

def run_ffmpeg_export(input_path: str,
                      output_path: str,
                      vf_chain: str,
                      codec_args: List[str],
                      strip_metadata: bool) -> Tuple[bool, str]:
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", input_path, "-vf", vf_chain] + codec_args
    if strip_metadata:
        cmd += ["-map_metadata", "-1"]
    cmd += ["-movflags", "+faststart", output_path]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if res.returncode != 0:
            return False, res.stderr.decode("utf-8", errors="ignore")
        return True, ""
    except Exception as e:
        return False, str(e)

# ------------------- Session State -------------------
if "rotation_map" not in st.session_state:
    st.session_state.rotation_map: Dict[str, int] = {}
if "mirror_preview" not in st.session_state:
    st.session_state.mirror_preview = False
if "selected_name" not in st.session_state:
    st.session_state.selected_name: Optional[str] = None
if "last_uploaded_names" not in st.session_state:
    st.session_state.last_uploaded_names: List[str] = []

# ------------------- UI -------------------
st.set_page_config(page_title="SPOOFER Vidéo — Qualité MAX", page_icon="🎬", layout="wide")
st.title("SPOOFER — Vidéo (Qualité maximale)")

if not FFMPEG_OK:
    st.error("⚠️ ffmpeg est introuvable.\n"
             "- Streamlit Cloud : le dépôt doit contenir un fichier `packages.txt` avec `ffmpeg`\n"
             "- macOS : `brew install ffmpeg`\n"
             "- Windows : installe ffmpeg et ajoute-le au PATH\n"
             "- Linux : `sudo apt-get install ffmpeg`")
    st.stop()

with st.sidebar:
    st.header("Fichiers")
    files = st.file_uploader(
        "Glissez-déposez vos vidéos (multi)",
        type=[e.strip(".") for e in SUPPORTED_EXTS],
        accept_multiple_files=True
    )
    st.caption("Formats: MP4, MOV, M4V, WEBM, MKV, AVI, MPEG/MPG, WMV…")

    names = [f.name for f in files] if files else []
    if names != st.session_state.last_uploaded_names:
        st.session_state.last_uploaded_names = names
        st.session_state.rotation_map = {n: 0 for n in names}
        if names:
            st.session_state.selected_name = names[0]

    if names:
        st.write(f"**{len(names)} fichiers**")
        selected_name = st.selectbox("Aperçu du fichier", options=names, index=names.index(st.session_state.selected_name) if st.session_state.selected_name in names else 0)
        st.session_state.selected_name = selected_name

        colr1, colr2, colr3, colr4 = st.columns(4)
        with colr1:
            if st.button("⟲ -90°"):
                cur = st.session_state.rotation_map.get(selected_name, 0)
                st.session_state.rotation_map[selected_name] = (cur - 90) % 360
        with colr2:
            if st.button("⟳ +90°"):
                cur = st.session_state.rotation_map.get(selected_name, 0)
                st.session_state.rotation_map[selected_name] = (cur + 90) % 360
        with colr3:
            if st.button("⟲ 180°"):
                cur = st.session_state.rotation_map.get(selected_name, 0)
                st.session_state.rotation_map[selected_name] = (cur + 180) % 360
        with colr4:
            st.toggle("Miroir (aperçu)", key="mirror_preview")

    st.divider()
    st.header("Options export")
    mirror_all = st.checkbox("Miroir x2 à l’export (génère Normal + Miroir)")
    flat_export = st.checkbox("Tout dans un seul dossier (ZIP racine)")
    strip_metadata = st.checkbox("Supprimer toutes les métadonnées", value=True)

    st.divider()
    st.header("Effets à exporter")
    eff_normal = st.checkbox("Normal", value=True)
    eff_bw = st.checkbox("Black & White")
    eff_bwc = st.checkbox("Black & White contrasté")
    eff_gh = st.checkbox("Golden Hour (chaud)")
    variants = generate_variants(eff_normal, eff_bw, eff_bwc, eff_gh)

    # Compteur d'exports
    count_vids = len(names)
    mirror_states_count = (2 if mirror_all else 1)
    total_exports = count_vids * mirror_states_count * max(1, len(variants))
    st.markdown(f"**À exporter : {total_exports}**")

# ------------------- Preview & Export -------------------
col_left, col_right = st.columns(2, gap="large")

with col_left:
    st.subheader("Aperçu")
    if files and st.session_state.selected_name:
        file = next((f for f in files if f.name == st.session_state.selected_name), None)
        if file:
            st.video(file)  # Aperçu brut (filtres non appliqués en temps réel)
            angle = st.session_state.rotation_map.get(file.name, 0)
            pipeline = choose_preview_pipeline(eff_normal, eff_bw, eff_bwc, eff_gh)
            st.caption(f"{file.name} | Rot {angle}° | Mir {'ON' if st.session_state.mirror_preview else 'OFF'} | Effet: {pipeline[0] if pipeline else 'normal'}")
    else:
        st.info("Ajoutez des vidéos dans la barre latérale pour afficher l’aperçu.")

with col_right:
    st.subheader("Export (Qualité maximale)")
    if not files:
        st.warning("Ajoutez au moins une vidéo.")
    else:
        do_export = st.button("⟱ Exporter en ZIP")
        if do_export:
            tmp_root = None
            try:
                progress = st.progress(0)
                status = st.empty()

                tmp_root = tempfile.mkdtemp(prefix="spoofer_video_")
                zip_buf = io.BytesIO()

                total_ops = len(files) * (2 if mirror_all else 1) * max(1, len(variants))
                done = 0

                with zipfile.ZipFile(zip_buf, mode="w", compression=zipfile.ZIP_STORED) as zf:
                    for f in files:
                        base = os.path.splitext(os.path.basename(f.name))[0]
                        in_path = os.path.join(tmp_root, f"input__{base}{ext_lower(f.name)}")
                        with open(in_path, "wb") as fin:
                            fin.write(f.getvalue())

                        angle = st.session_state.rotation_map.get(f.name, 0)
                        if angle not in ROTATIONS_ALLOWED:
                            angle = 0

                        out_ext, codec_args = choose_output_format(f.name)

                        mirror_states = [False, True] if mirror_all else [st.session_state.mirror_preview]
                        for mstate in mirror_states:
                            for pipeline in variants:
                                vf = ffmpeg_build_filtergraph(pipeline, mstate, angle)

                                suf = ""
                                if angle: suf += f"_rot{angle}"
                                if mstate: suf += "_mir"
                                suf += apply_variant_suffix(pipeline)

                                out_name = f"{base}{suf}{out_ext}"
                                out_dir = os.path.join(tmp_root, "out_flat" if flat_export else os.path.join("out", base, "Miroir" if mstate else "Normal"))
                                os.makedirs(out_dir, exist_ok=True)
                                out_path = os.path.join(out_dir, out_name)

                                ok, log = run_ffmpeg_export(
                                    input_path=in_path,
                                    output_path=out_path,
                                    vf_chain=vf,
                                    codec_args=codec_args,
                                    strip_metadata=bool(strip_metadata),
                                )
                                if not ok:
                                    raise RuntimeError(f"ffmpeg a échoué pour {f.name} ({out_name}) : {log}")

                                arcname = out_name if flat_export else os.path.join(base, "Miroir" if mstate else "Normal", out_name)
                                with open(out_path, "rb") as fout:
                                    zf.writestr(arcname, fout.read())

                                done += 1
                                progress.progress(min(1.0, done / max(1, total_ops)))
                                status.write(f"Export: {arcname}")

                progress.progress(1.0)
                status.write("Export terminé ! Téléchargez ci-dessous.")

                zip_buf.seek(0)
                st.download_button(
                    label="⬇️ Télécharger le ZIP",
                    data=zip_buf,
                    file_name="spoofer_video_export_MAX_QUALITY.zip",
                    mime="application/zip"
                )

            except Exception as e:
                st.error(f"Erreur lors de l'export : {e}")
            finally:
                if tmp_root:
                    shutil.rmtree(tmp_root, ignore_errors=True)

st.caption(
    "Sortie: MP4 (H.264 lossless CRF=0, yuv420p) + AAC 320 kb/s, moov faststart.\n"
    "Remarque: le mode lossless produit des fichiers volumineux et des exports plus lents."
)
