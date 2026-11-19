import pytest
from pydantic import ValidationError

from app.schemas.line import LineEdit


def test_line_edit_recorta_el_nombre():
    body = LineEdit(name="  Metro Bellavista  ")
    assert body.name == "Metro Bellavista"


def test_line_edit_convierte_nombre_vacio_en_none():
    body = LineEdit(name="")
    assert body.name is None
    assert "name" in body.model_fields_set


def test_line_edit_rechaza_comuna_muy_larga():
    with pytest.raises(ValidationError):
        LineEdit(commune="x" * 61)


def test_line_edit_sin_campos_tiene_model_fields_set_vacio():
    body = LineEdit()
    assert not body.model_fields_set


def test_line_edit_rechaza_valor_no_textual():
    with pytest.raises(ValidationError):
        LineEdit.model_validate({"name": 123})
