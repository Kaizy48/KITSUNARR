import hashlib

import bencodepy


# ------------------------------------------------------------
# Calcula el info hash BitTorrent v1 desde los bytes originales del metainfo.
# Recibe un .torrent completo y devuelve SHA-1 hexadecimal en minúsculas; no
# modifica los bytes ni hace I/O. Eleva ValueError si el payload no contiene un
# diccionario info bencode válido y depende de bencodepy/hashlib.
# ------------------------------------------------------------
def calculate_metainfo_hash(torrent_bytes: bytes) -> str:
    try:
        torrent_data = bencodepy.decode(torrent_bytes)
        if not isinstance(torrent_data, dict) or b"info" not in torrent_data:
            raise ValueError("El metainfo no contiene el diccionario info.")
        info_encoded = bencodepy.encode(torrent_data[b"info"])
    except Exception as exc:
        if isinstance(exc, ValueError):
            raise
        raise ValueError("El metainfo torrent no es bencode válido.") from exc
    return hashlib.sha1(info_encoded).hexdigest().lower()
