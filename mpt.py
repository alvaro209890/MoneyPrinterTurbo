#!/usr/bin/env python3
"""
MoneyPrinterTurbo Runner para Agentes Hermes.
Local: /home/server/MoneyPrinterTurbo
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

MPT_DIR = Path("/home/server/MoneyPrinterTurbo")
MPT_PYTHON = MPT_DIR / ".venv" / "bin" / "python3"
MPT_CLI = MPT_DIR / "cli.py"


def run():
    parser = argparse.ArgumentParser(
        description="Gera vídeos completos ou etapas (script, áudio, legendas, materiais) via MoneyPrinterTurbo."
    )
    parser.add_argument("--subject", "-s", help="Tema/assunto do vídeo para gerar roteiro via LLM")
    parser.add_argument("--script", help="Roteiro pronto em texto (pula LLM)")
    parser.add_argument("--script-file", help="Caminho para arquivo com roteiro pronto")
    parser.add_argument("--terms", help="Termos de busca de materiais separados por vírgula")
    parser.add_argument(
        "--language",
        "-l",
        default="pt-BR",
        help="Idioma do roteiro (padrão: pt-BR)",
    )
    parser.add_argument(
        "--paragraphs",
        "-p",
        type=int,
        default=1,
        help="Número de parágrafos do roteiro (Shorts: 1 a 10, padrão: 1)",
    )
    parser.add_argument(
        "--voice",
        "-v",
        default="pt-BR-AntonioNeural",
        help="Voz Edge-TTS (padrão: pt-BR-AntonioNeural)",
    )
    parser.add_argument(
        "--aspect",
        "-a",
        choices=["9:16", "16:9", "1:1"],
        default=None,
        help="Formato do vídeo: 9:16 (Shorts), 16:9 (Longos/YouTube), 1:1 (Feed).",
    )
    parser.add_argument(
        "--source",
        choices=["pexels", "pixabay", "coverr", "volcengine_seedance", "local"],
        default="pexels",
        help="Fonte dos materiais (padrão: pexels)",
    )
    parser.add_argument(
        "--materials",
        help="Vídeos/imagens locais separados por vírgula (para --source local)",
    )
    parser.add_argument(
        "--custom-audio",
        help="Arquivo de áudio próprio para narração (ignora TTS)",
    )
    parser.add_argument(
        "--bgm-type",
        choices=["random", "none", "custom", "sonilo"],
        default="random",
        help="Música de fundo: random, none, custom, sonilo (padrão: random)",
    )
    parser.add_argument(
        "--bgm-volume",
        type=float,
        default=0.2,
        help="Volume da música de fundo (padrão: 0.2)",
    )
    parser.add_argument(
        "--clip-duration",
        type=int,
        default=None,
        help="Duração máxima de cada corte em segundos (padrão: 5 para Shorts, 10 para Longos)",
    )
    parser.add_argument(
        "--no-subtitle",
        action="store_true",
        help="Desativa legendas no vídeo",
    )
    parser.add_argument(
        "--stop-at",
        choices=["script", "terms", "audio", "subtitle", "materials", "video"],
        default="video",
        help="Interrompe o pipeline no estágio escolhido (padrão: video)",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=4,
        help="Threads FFmpeg (padrão: 4)",
    )
    # Flags do modo longo
    parser.add_argument(
        "--long",
        action="store_true",
        help="Ativa o modo de vídeo longo (3 a 35 minutos)",
    )
    parser.add_argument(
        "--duration",
        "-d",
        type=int,
        default=None,
        help="Duração-alvo em minutos (3 a 35). Padrão em modo longo: 10",
    )
    parser.add_argument(
        "--chapters",
        type=int,
        default=None,
        help="Quantidade de capítulos (3 a 14, ou omita para automático)",
    )
    parser.add_argument(
        "--outline",
        help="Caminho para arquivo JSON com outline de capítulos",
    )
    parser.add_argument(
        "--narrate-chapter-titles",
        action="store_true",
        help="Inclui e narra os títulos dos capítulos",
    )
    parser.add_argument(
        "--no-normalize-loudness",
        action="store_true",
        help="Desativa a normalização de loudness para -14 LUFS",
    )
    parser.add_argument(
        "--task-id",
        help="ID explícito para a tarefa (UUID)",
    )

    args = parser.parse_args()

    script_content = args.script
    if args.script_file:
        script_path = Path(args.script_file)
        if not script_path.is_file():
            parser.error(f"Arquivo de roteiro não encontrado: {args.script_file}")
        script_content = script_path.read_text(encoding="utf-8").strip()

    if not args.subject and not script_content:
        parser.error("Informe --subject ou --script/--script-file.")

    cmd = [
        str(MPT_PYTHON),
        str(MPT_CLI),
        "--video-language",
        args.language,
        "--voice-name",
        args.voice,
        "--video-source",
        args.source,
        "--bgm-type",
        args.bgm_type,
        "--bgm-volume",
        str(args.bgm_volume),
        "--n-threads",
        str(args.threads),
        "--stop-at",
        args.stop_at,
    ]

    if args.aspect:
        cmd.extend(["--video-aspect", args.aspect])
    if args.clip_duration is not None:
        cmd.extend(["--video-clip-duration", str(args.clip_duration)])

    if args.long:
        cmd.append("--long")
        if args.duration is not None:
            cmd.extend(["--duration", str(args.duration)])
        if args.chapters is not None:
            cmd.extend(["--chapters", str(args.chapters)])
        if args.outline:
            cmd.extend(["--outline", args.outline])
        if args.narrate_chapter_titles:
            cmd.append("--narrate-chapter-titles")
        if args.no_normalize_loudness:
            cmd.append("--no-normalize-loudness")
    else:
        cmd.extend(["--paragraph-number", str(args.paragraphs)])

    if args.subject:
        cmd.extend(["--video-subject", args.subject])
    if script_content:
        cmd.extend(["--video-script", script_content])
    if args.terms:
        cmd.extend(["--video-terms", args.terms])
    if args.materials:
        cmd.extend(["--video-materials", args.materials])
    if args.custom_audio:
        cmd.extend(["--custom-audio-file", args.custom_audio])
    if args.no_subtitle:
        cmd.append("--no-subtitle-enabled")
    if args.task_id:
        cmd.extend(["--task-id", args.task_id])

    env = os.environ.copy()
    env["PYTHONPATH"] = str(MPT_DIR)

    proc = subprocess.run(
        cmd,
        cwd=str(MPT_DIR),
        env=env,
        capture_output=True,
        text=True,
    )

    if proc.returncode != 0:
        sys.stderr.write(proc.stderr or proc.stdout)
        sys.exit(proc.returncode)

    stdout_lines = proc.stdout.strip().split("\n")
    for line in reversed(stdout_lines):
        if line.startswith("{") and line.endswith("}"):
            try:
                data = json.loads(line)
                print(json.dumps(data, indent=2, ensure_ascii=False))
                return
            except json.JSONDecodeError:
                pass

    print(proc.stdout)


if __name__ == "__main__":
    run()