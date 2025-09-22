# -*- coding: utf-8 -*-
# streamlit_app_video.py
# SPOOFER — Vidéo (Streamlit)
# Normal / B&W / B&W contrasté / Golden Hour (+ Miroir x2 optionnel)
# Export en ZIP, ré-encodage MP4 H.264/AAC pour compatibilité (container .mp4).
#
# UI :
# - Uploader multiple (drag & drop), sélection d’une vidéo pour aperçu
# - Rotation par vidéo (-90° / +90° / 180°)
# - Miroir (aperçu info-only) + Miroir x2 à l’export (génère les deux)
# - Redimensionnement (%) → scale
# - Strip métadonnées
# - Variantes effets
# - "Tout dans un seul dossier" (flat) ou arborescence
# - Export → ZIP (via fichiers temporaires, puis cleanup)
#
# Requis: ffmpeg installé et accessible dans le PATH.

import os, io, zipfile, tempfile, shutil, subprocess, sys
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

# ------------------- Helpers -------------------
def ext_lower(name: str) -> str:
    return os.path.splitext(name)[1].lower()

def choose_output_format(src_name: str) -> Tuple[str, List[str]]:
    """
    Par défaut: MP4 H.264 + AAC (ultra compatible).
    Retourne (ext, codec_args)
    """
    # tu peux étendre si tu veux conserver WEBM pour .webm, mais MP4 est plus universel
    ext = '.mp4'
    codec = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-profile:v", "high", "-preset", "medium", "-crf", "18",
             "-c:a", "aac", "-b:a", "192k"]
    return ext, codec

def ffmpeg_build_filtergraph(pipeline: Iterable[str], mirror: bool, rotate_deg: int, scale_pct: Optional[int]) -> str:
    """
    Construit la chaîne -vf ffmpeg (filtergraph) selon les options.
    - rotate_deg: 0 / 90 / 180 / 270
    - mirror: hflip
    - pipeline: ["normal"] | ["bw"] | ["bwcontrast"] | ["goldenhour"]
    - scale_pct: int 10..200
    """
    filters = []

    # Rotation (transpose pour 90/270, rotate pour 180)
    rd = rotate_deg % 360
    if rd == 90:
        filters.append("transpose=1")  # 90° clockwise
    elif rd == 270:
        filters.append("transpose=2")  # 90° counter-clockwise
    elif rd == 180:
        filters.append("rotate=PI")    # 180°

    # Miroir
    if mirror:
        filters.append("hflip")

    # Effets
    if pipeline and len(pipeline) > 0:
        step = pipeline[0]
        if step == "bw":
            # Noir & blanc simple
            filters.append("hue=s=0")
        elif step == "bwcontrast":
            filters.append("hue=s=0,eq=contrast=1.35:brightness=0.0")
        elif step == "goldenhour":
            # réchauffement + un peu de saturation/contraste/lumière
            # colorbalance: augmente R/G, baisse B légèrement ; hue: saturation up ; eq: légère amélioration
            filters.append("colorbalance=rs=.10:gs=.05:bs=-.05,hue=s=1.12,eq=contrast=1.06:brightness=0.03")
        else:
            # normal: rien
            pass

    # Redimensionnement
    if scale_pct is not None and 10 <= scale_pct <= 200 and scale_pct != 100:
        # scale=iw*scale:ih*scale
        scale = scale_pct / 100.0
        filters.append(f"scale=iw*{scale}:ih*{scale}")

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
                      strip_metadata: bool,
                      speed_preset: str = "medium",
                      crf: int = 18) -> Tuple[bool, str]:
    """
    Lance ffmpeg en subprocess pour transcodage + filtres vidéo.
    Retourne (ok, log_err).
    """
    # sécurité des paramètres
    crf = int(max(0, min(51, crf)))
    # Remplace preset fourni dans codec_args si nécessaire
    final_codec = []
    skip_next = False
    for i, tok in enumerate(codec_args):
        if skip_next:
            skip_next = False
            continue
        if tok == "-preset":
            # remplace la valeur suivante
            final_codec.extend(["-preset", speed_preset])
            skip_next = True
        elif tok == "-crf":
            final_codec.extend(["-crf", str(crf)])
            skip_next = True
        else:
            final_codec.append(tok)

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", input_path,
           "-vf", vf_chain] + final_codec

    if strip_metadata:
        cmd += ["-map_metadata", "-1"]

    # Force moov atom au début pour web-friendly
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
st.set_page_config(page_title="Grumtor's Videos Spoofer", page_icon="🎬", layout="wide")
st.title("Grumtor's Videos Spoofer")

if not FFMPEG_OK:
    st.error("⚠️ ffmpeg est introuvable. Installe-le puis relance l’app.\n"
             "- macOS: `brew install ffmpeg`\n"
             "- Windows: installe ffmpeg et ajoute-le au PATH\n"
             "- Linux: `sudo apt-get install ffmpeg`")
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

        colr1, colr2, colr3, colr4 = st.columns([1,1,1,1])
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
    st.header("Réglages export")
    mirror_all = st.checkbox("Miroir x2 à l’export")
    flat_export = st.checkbox("Tout dans un seul dossier")
    resize_export = st.checkbox("Redimensionner (%)")
    export_scale_pct = st.slider("Taille export (%)", 10, 200, 100, 1, disabled=not resize_export)

    strip_metadata = st.checkbox("Supprimer toutes les métadonnées", value=True)

    st.caption("Astuce : MP4 (H.264/AAC) pour compatibilité maximale. Le moov atom est déplacé au début du fichier (faststart) pour un streaming web fluide.")

    st.divider()
    st.header("Effets à exporter")
    eff_normal = st.checkbox("Normal", value=True)
    eff_bw = st.checkbox("Black & White")
    eff_bwc = st.checkbox("Black & White contrasté")
    eff_gh = st.checkbox("Golden Hour (chaud)")
    variants = generate_variants(eff_normal, eff_bw, eff_bwc, eff_gh)

    st.divider()
    st.header("Qualité / Vitesse")
    preset = st.selectbox("Preset (ffmpeg libx264)", ["ultrafast","superfast","veryfast","faster","fast","medium","slow","slower","veryslow"], index=5)
    crf = st.slider("CRF (qualité, 0=lossless, 18~22 recommandé)", 0, 40, 18, 1)

    # Compteur d'exports
    count_vids = len(names)
    mirror_states_count = (2 if mirror_all else 1)
    total_exports = count_vids * mirror_states_count * max(1, len(variants))
    st.markdown(f"**À exporter : {total_exports}**")

# ------------------- Preview (gauche) & Export (droite) -------------------
col_left, col_right = st.columns([5,5], gap="large")

with col_left:
    st.subheader("Aperçu")
    if files and st.session_state.selected_name:
        file = next((f for f in files if f.name == st.session_state.selected_name), None)
        if file:
            st.video(file)  # Aperçu brut (les filtres ne sont pas appliqués en temps réel)
            angle = st.session_state.rotation_map.get(file.name, 0)
            pipeline = choose_preview_pipeline(eff_normal, eff_bw, eff_bwc, eff_gh)
            st.caption(f"{file.name} | Rot {angle}° | Mir {'ON' if st.session_state.mirror_preview else 'OFF'} | Effet: {pipeline[0] if pipeline else 'normal'}")
    else:
        st.info("Ajoutez des vidéos dans la barre latérale pour afficher l’aperçu.")

with col_right:
    st.subheader("Export")
    if not files:
        st.warning("Ajoutez au moins une vidéo.")
    else:
        do_export = st.button("⟱ Exporter en ZIP")
        if do_export:
            try:
                progress = st.progress(0)
                status = st.empty()

                # Dossier temp pour outputs
                tmp_root = tempfile.mkdtemp(prefix="spoofer_video_")
                zip_buf = io.BytesIO()

                total_ops = len(files) * (2 if mirror_all else 1) * max(1, len(variants))
                done = 0

                with zipfile.ZipFile(zip_buf, mode="w", compression=zipfile.ZIP_STORED) as zf:
                    for f in files:
                        base = os.path.splitext(os.path.basename(f.name))[0]
                        # Sauver input en temp file pour ffmpeg
                        in_path = os.path.join(tmp_root, f"input__{base}")
                        # garde extension originale pour que ffmpeg détecte correctement
                        in_path += ext_lower(f.name)
                        with open(in_path, "wb") as fin:
                            fin.write(f.getvalue())

                        angle = st.session_state.rotation_map.get(f.name, 0)
                        if angle not in ROTATIONS_ALLOWED:
                            angle = 0

                        # Choix format/codec
                        out_ext, codec_args = choose_output_format(f.name)

                        mirror_states = [False, True] if mirror_all else [st.session_state.mirror_preview]
                        for mstate in mirror_states:
                            for pipeline in variants:
                                vf = ffmpeg_build_filtergraph(pipeline, mstate, angle, export_scale_pct if resize_export else None)

                                suf = ""
                                if angle: suf += f"_rot{angle}"
                                if flat_export and mstate:
                                    suf += "_mir"
                                suf += apply_variant_suffix(pipeline)
                                if resize_export and export_scale_pct != 100:
                                    suf += f"_{int(export_scale_pct)}pct"

                                out_name = f"{base}{suf}{out_ext}"
                                out_dir = os.path.join(tmp_root, "out", base, ("Miroir" if mstate else "Normal"))
                                if flat_export:
                                    # tout à la racine du zip
                                    out_dir = os.path.join(tmp_root, "out_flat")
                                os.makedirs(out_dir, exist_ok=True)
                                out_path = os.path.join(out_dir, out_name)

                                ok, log = run_ffmpeg_export(
                                    input_path=in_path,
                                    output_path=out_path,
                                    vf_chain=vf,
                                    codec_args=codec_args,
                                    strip_metadata=bool(strip_metadata),
                                    speed_preset=preset,
                                    crf=int(crf),
                                )
                                if not ok:
                                    raise RuntimeError(f"ffmpeg a échoué pour {f.name} ({out_name}) : {log}")

                                # Écrire dans le ZIP
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
                    file_name="spoofer_video_export.zip",
                    mime="application/zip"
                )

            except Exception as e:
                st.error(f"Erreur lors de l'export : {e}")
            finally:
                # Cleanup
                try:
                    shutil.rmtree(tmp_root, ignore_errors=True)
                except Exception:
                    pass

st.caption(
    "ffmpeg: ✅ | Container sortie: MP4 (H.264/AAC) | "
    "Effets: Normal / B&W / B&W contrasté / Golden Hour | "
    "Astuce: augmente le CRF (ex: 20-22) et preset 'faster' pour accélérer les exports."
)
