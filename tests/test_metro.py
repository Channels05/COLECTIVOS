from app.services import metro


def _station(net: metro.Network, name: str, line: str) -> metro.Station:
    return next(s for s in net.stations if s.name == name and s.line == line)


def test_la_red_carga_las_lineas_y_sus_combinaciones():
    net = metro.network()
    assert net is not None
    assert {"L1", "L2", "L3", "L4", "L4A", "L5", "L6"} <= set(net.lines)
    baquedano_l1, baquedano_l5 = _station(net, "Baquedano", "L1"), _station(net, "Baquedano", "L5")
    assert (baquedano_l5.key, metro.MIN_PER_TRANSFER) in net.edges[baquedano_l1.key]


def test_viaje_en_una_linea_sin_combinar():
    net = metro.network()
    start, end = _station(net, "Bellavista de La Florida", "L5"), _station(net, "Baquedano", "L5")
    minutes, path = net.ride(start.key, {end.key})[end.key]
    legs = net.legs(path)
    assert [(leg.ref, leg.start.name, leg.end.name) for leg in legs] == [("L5", "Bellavista de La Florida", "Baquedano")]
    assert minutes == legs[0].stops * metro.MIN_PER_HOP


def test_viaje_con_combinacion_se_separa_por_linea():
    net = metro.network()
    start, end = _station(net, "Vicente Valdés", "L5"), _station(net, "Los Dominicos", "L1")
    minutes, path = net.ride(start.key, {end.key})[end.key]
    legs = net.legs(path)
    assert len(legs) >= 2 and legs[0].start.name == "Vicente Valdés" and legs[-1].end.name == "Los Dominicos"
    assert minutes > sum(leg.stops for leg in legs) * metro.MIN_PER_HOP  # suma el tiempo de combinar


def test_estaciones_cerca_de_un_punto():
    net = metro.network()
    near = net.near(-33.5195, -70.5985, 600)  # junto a Bellavista de La Florida
    assert near and near[0][0].name == "Bellavista de La Florida"
    assert net.near(-33.30, -70.40, 800) == []  # en la cordillera no hay Metro
