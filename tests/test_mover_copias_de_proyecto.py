"""Reasignar una reunion a otro proyecto quita sus copias de la carpeta del viejo.

Antes, set_meeting_project solo cambiaba el project_id: el panel exportaba a la
carpeta del nuevo, y la reunion se quedaba tambien en la del viejo.
"""
import json

import pytest

import app_window as aw
import project_exporter as pe

pytestmark = pytest.mark.unit

STEM = '20260917_1616_Comite_Semanal'


@pytest.fixture(autouse=True)
def _sin_nombre(monkeypatch):
    import config
    monkeypatch.setattr(config, 'ME_NAME', '', raising=False)


@pytest.fixture
def dos_proyectos(tr_dirs):
    viejo, nuevo = tr_dirs / 'carpeta_viejo', tr_dirs / 'carpeta_nuevo'
    viejo.mkdir()
    nuevo.mkdir()
    (tr_dirs / 'projects.json').write_text(json.dumps({'projects': [
        {'id': 'viejo', 'name': 'Viejo', 'output_dir': str(viejo), 'directory': str(viejo)},
        {'id': 'nuevo', 'name': 'Nuevo', 'output_dir': str(nuevo), 'directory': str(nuevo)},
    ]}), encoding='utf-8')
    md = tr_dirs / 'minutes' / f'{STEM}.md'
    md.write_text('TITULO: Comite\n\n## Resumen\n\nSe hablo.\n', encoding='utf-8')
    md.with_suffix('.html').write_text('<html>notas</html>', encoding='utf-8')
    (md.parent / f'{STEM}_transcript.txt').write_text('texto', encoding='utf-8')
    (md.parent / f'{STEM}_actions.json').write_text(
        json.dumps({'project_id': 'viejo', 'actions': []}), encoding='utf-8')
    return {'md': md, 'viejo': viejo, 'nuevo': nuevo}


def copias(carpeta):
    return sorted(p.name for p in carpeta.rglob('*') if p.is_file())


def test_al_cambiar_de_proyecto_las_copias_dejan_la_carpeta_vieja(dos_proyectos):
    md = dos_proyectos['md']
    assert pe.export_to_project_folder(md) is True
    assert copias(dos_proyectos['viejo'])

    api = object.__new__(aw.AppAPI)
    assert api.set_meeting_project(str(md), 'nuevo') is True

    assert copias(dos_proyectos['viejo']) == []
    # Lo que hace el panel justo despues: exportar al nuevo.
    assert api.export_to_project(str(md)) == ''
    assert copias(dos_proyectos['nuevo'])


def test_sin_cambio_de_proyecto_no_se_borra_nada(dos_proyectos):
    md = dos_proyectos['md']
    pe.export_to_project_folder(md)
    antes = copias(dos_proyectos['viejo'])

    object.__new__(aw.AppAPI).set_meeting_project(str(md), 'viejo')

    assert copias(dos_proyectos['viejo']) == antes


def test_quitar_copias_solo_toca_las_de_esa_reunion(dos_proyectos, tr_dirs):
    viejo = dos_proyectos['viejo']
    (viejo / 'Notas').mkdir()
    (viejo / 'Notas' / 'otra_reunion.html').write_text('x', encoding='utf-8')
    (viejo / 'Noted').mkdir()
    (viejo / 'Noted' / f'{STEM}.pdf').write_bytes(b'%PDF')
    (viejo / 'Notas' / f'{STEM}.html').write_text('x', encoding='utf-8')

    n = pe.remove_from_project_folder(dos_proyectos['md'], {'output_dir': str(viejo)})

    assert n == 2
    assert copias(viejo) == ['otra_reunion.html']
    assert (viejo / 'Notas').is_dir(), "las carpetas no se borran"


@pytest.mark.parametrize('proyecto', [None, {}, {'output_dir': '  '}])
def test_un_proyecto_sin_carpeta_no_hace_nada(dos_proyectos, proyecto):
    assert pe.remove_from_project_folder(dos_proyectos['md'], proyecto) == 0


def test_un_fichero_bloqueado_no_rompe_el_cambio(dos_proyectos, monkeypatch):
    md = dos_proyectos['md']
    pe.export_to_project_folder(md)

    def bloqueado(self, missing_ok=False):
        raise PermissionError('abierto en otro programa')
    monkeypatch.setattr(pe.Path, 'unlink', bloqueado)

    assert object.__new__(aw.AppAPI).set_meeting_project(str(md), 'nuevo') is True
