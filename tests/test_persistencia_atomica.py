"""tasks.json y buckets.json: escritura atomica, copia del corrupto y bloqueo.

Incidente que lo origina: la ventana (update_task/create_task) y el daemon
(auto_sync_meeting_tasks) escriben el mismo tasks.json con write_text directo
y `_load` tragaba cualquier excepcion devolviendo vacio. Una lectura que
coincidiera con una escritura, o un fichero truncado tras un crash, daba 0
tareas y el siguiente `_save` borraba todo el tablero.
"""
import json
import threading

import pytest

import atomic_io
import buckets_store
import tasks_store

pytestmark = pytest.mark.unit


def test_write_text_atomic_escribe_y_no_deja_tmp(tmp_path):
    f = tmp_path / 'a.json'
    atomic_io.write_text_atomic(f, 'hola')
    assert f.read_text(encoding='utf-8') == 'hola'
    assert not (tmp_path / 'a.json.tmp').exists()


def test_save_interrumpido_deja_el_original_intacto(tr_dirs, monkeypatch):
    tasks_store.create_task('none', 'original')
    antes = tasks_store.TASKS_FILE.read_text(encoding='utf-8')

    def replace_roto(*_a, **_k):
        raise OSError('crash')
    monkeypatch.setattr(atomic_io.os, 'replace', replace_roto)

    with pytest.raises(OSError):
        tasks_store.create_task('none', 'nueva')

    assert tasks_store.TASKS_FILE.read_text(encoding='utf-8') == antes
    assert not tasks_store.TASKS_FILE.with_name('tasks.json.tmp').exists()
    json.loads(antes)


def test_replace_reintenta_ante_permissionerror(tmp_path, monkeypatch):
    llamadas = []
    real = atomic_io.os.replace

    def flaky(a, b):
        llamadas.append(1)
        if len(llamadas) < 3:
            raise PermissionError('en uso')
        real(a, b)
    monkeypatch.setattr(atomic_io.os, 'replace', flaky)
    atomic_io.write_text_atomic(tmp_path / 'x.txt', 'ok')
    assert (tmp_path / 'x.txt').read_text(encoding='utf-8') == 'ok'
    assert len(llamadas) == 3


def test_tasks_corrupto_deja_copia_corrupt(tr_dirs):
    tasks_store.TASKS_FILE.write_text('{"tasks": [ {"id": "a"', encoding='utf-8')
    assert tasks_store.get_tasks() == []
    copias = list(tr_dirs.glob('tasks.json.corrupt-*'))
    assert len(copias) == 1
    assert copias[0].read_text(encoding='utf-8') == '{"tasks": [ {"id": "a"'


def test_lecturas_repetidas_del_corrupto_no_multiplican_copias(tr_dirs):
    tasks_store.TASKS_FILE.write_text('basura', encoding='utf-8')
    for _ in range(5):
        tasks_store.get_tasks()
    assert len(list(tr_dirs.glob('tasks.json.corrupt-*'))) == 1


def test_tasks_sano_no_crea_copia(tr_dirs):
    tasks_store.create_task('none', 'a')
    tasks_store.get_tasks()
    assert not list(tr_dirs.glob('tasks.json.corrupt-*'))


def test_buckets_corrupto_deja_copia_corrupt(tr_dirs):
    buckets_store.BUCKETS_FILE.write_text('{roto', encoding='utf-8')
    assert buckets_store.get_buckets()  # valores por defecto
    copias = list(tr_dirs.glob('buckets.json.corrupt-*'))
    assert len(copias) == 1
    assert copias[0].read_text(encoding='utf-8') == '{roto'


def test_dos_hilos_creando_tareas_conservan_todas(tr_dirs):
    def crea(prefijo):
        for i in range(25):
            tasks_store.create_task('none', f'{prefijo}{i}')

    hilos = [threading.Thread(target=crea, args=(p,)) for p in 'AB']
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    assert len(tasks_store.get_tasks()) == 50


def test_file_lock_es_reentrante_y_no_cuelga_si_esta_tomado(tmp_path):
    lock = tmp_path / 'x.lock'
    with atomic_io.file_lock(lock, timeout=0.1):
        with atomic_io.file_lock(lock, timeout=0.1):
            pass
