"""Coordenadas aproximadas (grados) de provincias y ciudades para ubicar unidades en el mapa
cuando no tienen latitud/longitud propias. Son referencias de la ciudad, no del edificio."""
import unicodedata


def _k(s):
    s = unicodedata.normalize("NFD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return " ".join(s.replace(".", " ").split())


PROVINCIAS = {
    "caba": (-34.6037, -58.3816), "ciudad autonoma de buenos aires": (-34.6037, -58.3816),
    "buenos aires": (-36.6, -60.0), "catamarca": (-28.47, -65.78), "chaco": (-27.45, -58.99),
    "chubut": (-43.3, -65.1), "cordoba": (-31.42, -64.18), "corrientes": (-27.47, -58.83),
    "entre rios": (-31.73, -60.53), "formosa": (-26.18, -58.17), "jujuy": (-24.19, -65.3),
    "la pampa": (-36.62, -64.29), "la rioja": (-29.41, -66.86), "mendoza": (-32.89, -68.84),
    "misiones": (-27.37, -55.9), "neuquen": (-38.95, -68.06), "rio negro": (-40.8, -63.0),
    "salta": (-24.79, -65.41), "san juan": (-31.54, -68.54), "san luis": (-33.3, -66.34),
    "santa cruz": (-51.62, -69.22), "santa fe": (-31.63, -60.7), "santiago del estero": (-27.78, -64.26),
    "tierra del fuego": (-54.8, -68.3), "tucuman": (-26.82, -65.22),
}
CIUDADES = {
    "ezeiza": (-34.85, -58.52), "marcos paz": (-34.78, -58.84), "rosario": (-32.95, -60.64),
    "parana": (-31.73, -60.53), "san salvador de jujuy": (-24.19, -65.3), "corrientes": (-27.47, -58.83),
    "lujan de cuyo": (-33.04, -68.88), "mendoza": (-32.89, -68.84), "general guemes": (-24.67, -65.05),
    "clorinda": (-25.29, -57.72), "san ignacio": (-27.26, -55.54), "las flores": (-36.01, -59.1),
    "senillosa": (-38.97, -68.42), "santa rosa": (-36.62, -64.29), "general roca": (-39.03, -67.58),
    "rawson": (-43.3, -65.1), "resistencia": (-27.45, -58.99), "neuquen": (-38.95, -68.06),
    "viedma": (-40.81, -62.99), "esquel": (-42.91, -71.32), "mar del plata": (-38.0, -57.55),
    "salta": (-24.79, -65.41), "general alvear": (-36.03, -60.02),
    "ciudad autonoma de buenos aires": (-34.6037, -58.3816), "caba": (-34.6037, -58.3816),
}


def ubicar(ciudad=None, provincia=None):
    """(lat, lng, precision) con precision 'ciudad' | 'provincia' | None."""
    c = CIUDADES.get(_k(ciudad))
    if c:
        return c[0], c[1], "ciudad"
    p = PROVINCIAS.get(_k(provincia))
    if p:
        return p[0], p[1], "provincia"
    return None, None, None
