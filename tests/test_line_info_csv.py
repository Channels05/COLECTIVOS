import pytest

from app.api.v1.endpoints.line_info import _parse_row


def test_fila_completa_con_formatos_chilenos():
    code, values = _parse_row(["233035", "$900", "1.100", "6:15", "23.30"])
    assert code == "233035"
    assert values == {"fare_day": 900, "fare_night": 1100, "first_departure": "06:15", "last_departure": "23:30"}


def test_celdas_vacias_no_se_incluyen():
    assert _parse_row([" 233025 ", "", "1200"]) == ("233025", {"fare_night": 1200})


@pytest.mark.parametrize("row", [["", "900"], ["233001"], ["233001", "50000"], ["233001", "", "", "25:00"]])
def test_filas_invalidas(row):
    with pytest.raises(ValueError):
        _parse_row(row)
