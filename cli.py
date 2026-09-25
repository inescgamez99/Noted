import logging
import os
import sys
import time
from pathlib import Path

import click

from config import CLI_CONTROL_FILE
from storage import ensure_directories, get_recording_path, get_transcript_path, get_minutes_path

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger(__name__)


@click.group()
def cli():
    """Noted CLI"""
    pass


@cli.command()
def start():
    """Envía comando 'start' al daemon Noted."""
    CLI_CONTROL_FILE.write_text('start', encoding='utf-8')
    click.echo("Comando 'start' enviado al daemon.")


@cli.command()
def stop():
    """Envía comando 'stop' al daemon Noted."""
    CLI_CONTROL_FILE.write_text('stop', encoding='utf-8')
    click.echo("Comando 'stop' enviado al daemon.")


@cli.command()
@click.option('--name', default='grabacion', help='Nombre de la reunión')
@click.option('--no-minutes', is_flag=True, help='Solo grabar, sin generar minutas')
def record(name, no_minutes):
    """Graba audio hasta Ctrl+C, luego transcribe y genera minutas."""
    from audio_recorder import AudioRecorder
    from transcriber import transcribe
    from minutes_generator import generate_minutes, extract_title_from_minutes, save_minutes

    ensure_directories()
    wav_path = get_recording_path(name)
    click.echo(f"Grabando → {wav_path.name}  (Ctrl+C para detener)")

    recorder = AudioRecorder()
    recorder.start(wav_path)

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass

    click.echo("\nDeteniendo grabación...")
    recorder.stop()
    recorder.wait_for_save(timeout=60)
    click.echo(f"WAV guardado: {wav_path}")

    if no_minutes:
        return

    # Transcribir
    click.echo("Transcribiendo con Whisper...")
    transcript_path = get_transcript_path(wav_path)

    segments = []

    def on_seg(line):
        segments.append(line)
        sys.stdout.write(f"\r{len(segments)} segmentos...")
        sys.stdout.flush()

    result = transcribe(wav_path, on_segment=on_seg)
    print()

    if not result:
        click.echo("Error en transcripción.", err=True)
        return

    transcript_text, detected_language = result
    transcript_path.write_text(transcript_text, encoding='utf-8')
    click.echo(f"Transcript: {transcript_path.name} (idioma: {detected_language})")

    # Generar minutas
    click.echo("Generando minutas con Claude...")
    raw = generate_minutes(transcript_text, wav_path, language=detected_language)
    if not raw:
        click.echo("Error generando minutas.", err=True)
        return

    title, content = extract_title_from_minutes(raw)
    minutes_path = get_minutes_path(wav_path, title)
    if save_minutes(content, minutes_path):
        click.echo(f"Minutas: {minutes_path}")
        os.startfile(str(minutes_path))


@cli.command()
@click.argument('wav_file', type=click.Path(exists=True, path_type=Path))
def transcribe_cmd(wav_file):
    """Transcribe un WAV existente."""
    from transcriber import transcribe

    click.echo(f"Transcribiendo {wav_file.name}...")
    transcript_path = wav_file.parent / f"{wav_file.stem}_transcript.txt"

    segments = []

    def on_seg(line):
        segments.append(line)
        sys.stdout.write(f"\r{len(segments)} segmentos...")
        sys.stdout.flush()

    result = transcribe(wav_file, on_segment=on_seg)
    print()

    if not result:
        click.echo("Error en transcripción.", err=True)
        sys.exit(1)

    text, detected_language = result
    transcript_path.write_text(text, encoding='utf-8')
    click.echo(f"Transcript guardado: {transcript_path} (idioma: {detected_language})")
    click.echo(f"\nPreview:\n{text[:400]}...")


@cli.command(name='minutes')
@click.argument('transcript_file', type=click.Path(exists=True, path_type=Path))
def minutes_cmd(transcript_file):
    """Genera minutas de un transcript existente."""
    from minutes_generator import generate_minutes, extract_title_from_minutes, save_minutes
    from storage import get_minutes_path

    text = transcript_file.read_text(encoding='utf-8')
    # Usar el transcript como si fuera recording_path para extraer timestamp
    fake_rec = transcript_file.parent / transcript_file.name.replace('_transcript.txt', '.wav')

    click.echo("Generando minutas con Claude...")
    raw = generate_minutes(text, fake_rec)
    if not raw:
        click.echo("Error generando minutas.", err=True)
        sys.exit(1)

    title, content = extract_title_from_minutes(raw)
    minutes_path = get_minutes_path(fake_rec, title)
    if save_minutes(content, minutes_path):
        click.echo(f"Minutas: {minutes_path}")
        os.startfile(str(minutes_path))


# Registrar con nombre correcto en CLI
cli.add_command(transcribe_cmd, name='transcribe')

if __name__ == '__main__':
    cli()
