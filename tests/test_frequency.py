"""Estimación de la frecuencia de paso a partir de las marcas "pasó un colectivo"."""

from datetime import datetime, timedelta

from app.services.frequency import SANTIAGO_TZ, estimate

# Martes 27-oct-2026 a las 18:30 en Santiago.
NOW = datetime(2026, 10, 27, 18, 30, tzinfo=SANTIAGO_TZ)


def at(day_offset: int, hour: int, minute: float) -> datetime:
    base = NOW.replace(hour=hour, minute=0, second=0) + timedelta(days=day_offset)
    return base + timedelta(minutes=minute)


def test_sin_marcas():
    h = estimate([], NOW)
    assert h.minutes is None and h.sightings == 0 and h.last_seen_at is None


def test_pocas_marcas_no_dan_cifra():
    h = estimate([at(0, 18, 0), at(0, 18, 8)], NOW)
    assert h.minutes is None and h.samples == 1 and h.sightings == 2


def test_mediana_en_la_franja_horaria():
    times = [at(0, 18, m) for m in (0, 8, 16, 25, 33)]  # intervalos 8, 8, 9, 8
    h = estimate(times, NOW)
    assert h.minutes == 8.0 and h.scope == "hour" and h.samples == 4
    assert h.last_seen_at == times[-1]


def test_mismo_auto_marcado_por_dos_personas_cuenta_una_vez():
    times = [at(0, 18, 0), at(0, 18, 0.5), at(0, 18, 10), at(0, 18, 10.8), at(0, 18, 20), at(0, 18, 30)]
    h = estimate(times, NOW)
    assert h.minutes == 10.0 and h.samples == 3 and h.sightings == 6


def test_huecos_largos_no_cuentan():
    # Entre las 18:10 y las 19:30 nadie estaba mirando: ese hueco no es un intervalo.
    times = [at(0, 18, m) for m in (0, 5, 10)] + [at(0, 19, m) for m in (30, 35)]
    h = estimate(times, NOW)
    assert h.minutes == 5.0 and h.samples == 3


def test_intervalos_no_cruzan_de_un_dia_a_otro():
    times = [at(-1, 18, 50), at(0, 18, 0), at(0, 18, 6), at(0, 18, 12)]
    h = estimate(times, NOW)
    assert h.samples == 2 and h.minutes is None


def test_sin_datos_en_la_franja_usa_todas_las_horas():
    # Marcas de la mañana: a las 18:30 no hay de la franja, se usa lo que hay.
    times = [at(0, 8, m) for m in (0, 12, 24, 36)]
    h = estimate(times, NOW)
    assert h.minutes == 12.0 and h.scope == "all"


def test_fin_de_semana_no_se_mezcla_con_dias_habiles():
    saturday = -3  # sábado 24-oct
    weekend = [at(saturday, 18, m) for m in (0, 20, 40)]
    weekday = [at(0, 18, m) for m in (0, 6, 12, 18)]
    h = estimate(weekend + weekday, NOW)
    assert h.minutes == 6.0 and h.scope == "hour"
